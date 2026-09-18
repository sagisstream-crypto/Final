"""Offline sanity checks — no network, no database, no event loop needed.

    python3 -m unittest discover -s tests -v

The acceleration tests are the important ones: they encode the promise that a
1-2 tick blip cannot light the 🚀 badge, which is the whole point of the
debounce.
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from volscan.config import Config
from volscan.engine import Engine, Tick, _blocks_rising, _fit
from volscan.indicators import median, stdev, zscore, lin_reg_slope_per_min
from volscan.storage import Storage
from volscan.alerts import format_signal, TYPES
from volscan.config import (EDITABLE, GROUPS, coerce_setting, parse_amount,
                            settings_view)
from volscan.feed import hourly_context, parse_ws_ticker, rsi

MIN = 60_000


def feed(engine, symbol, series, start=1_700_000_000_000, step=30_000,
         market="SPOT", day_qv_mult=1440.0):
    """series: list of (price, minute_turnover). Turnover is converted into the
    cumulative 24h quote volume the real ticker reports."""
    sigs = []
    base_24h = series[0][1] * day_qv_mult
    for i, (price, rate) in enumerate(series):
        ts = start + i * step
        t = Tick(symbol=symbol, price=price, pct24=0.0,
                 quote_volume=max(base_24h + rate * 0.0, 1.0),
                 trades=None, high=price * 1.02, low=price * 0.98, vwap=price)
        # the engine derives d1m from the *difference* of cumulative volume, so
        # walk the cumulative figure forward by the per-slot share of the rate
        base_24h += rate * (step / MIN)
        t.quote_volume = base_24h
        sigs += engine.on_ticker_batch(market, [t], ts)
    return sigs


class TestIndicators(unittest.TestCase):
    def test_median_and_stdev(self):
        self.assertEqual(median([3, 1, 2]), 2)
        self.assertEqual(median([4, 1, 2, 3]), 2.5)
        self.assertIsNone(median([]))
        self.assertAlmostEqual(stdev([2, 4, 4, 4, 5, 5, 7, 9]), 2.0)
        self.assertIsNone(stdev([1]))

    def test_zscore_floor_stops_dust_blowups(self):
        # a dead pair: baseline is essentially zero, so an unfloored z-score
        # would report a huge anomaly for a $300 print
        dust = [1.0, 0.0, 2.0, 1.0, 0.0, 1.0, 2.0, 0.0]
        self.assertGreater(zscore(300.0, dust, floor=0.0), 100)
        self.assertLess(zscore(300.0, dust, floor=5000.0), 1.0)

    def test_linreg_slope(self):
        pts = [(0, 0.0), (60_000, 100.0), (120_000, 200.0), (180_000, 300.0)]
        self.assertAlmostEqual(lin_reg_slope_per_min(pts, 180_000), 100.0, places=6)
        self.assertIsNone(lin_reg_slope_per_min(pts[:2], 60_000))

    def test_rsi(self):
        up = [100 + i for i in range(40)]
        self.assertGreater(rsi(up), 95)
        down = [100 - i for i in range(40)]
        self.assertLess(rsi(down), 5)


class TestAccelMath(unittest.TestCase):
    def test_fit_perfect_line(self):
        slope, r2 = _fit([1, 2, 3, 4, 5, 6])
        self.assertAlmostEqual(slope, 1.0)
        self.assertAlmostEqual(r2, 1.0)

    def test_fit_single_spike_has_poor_r2(self):
        _, r2 = _fit([1, 1, 1, 1, 1, 1, 1, 1, 1, 40])
        self.assertLess(r2, 0.55, "a lone spike must not look like a clean ramp")

    def test_blocks_rising(self):
        self.assertTrue(_blocks_rising([1, 1, 2, 2, 3, 3, 4, 4, 5]))
        self.assertFalse(_blocks_rising([1, 1, 1, 1, 1, 1, 1, 1, 9]))
        self.assertFalse(_blocks_rising([5, 5, 4, 4, 3, 3, 2, 2, 1]))


class TestAccelDetector(unittest.TestCase):
    """The headline promise: blips out, real ramps in."""

    def _engine(self):
        cfg = Config()
        cfg.min_base_age_sec = 0.0
        return Engine(cfg)

    def test_single_tick_blip_does_not_fire(self):
        e = self._engine()
        flat = [(1.0, 1000.0)] * 60
        series = flat + [(1.03, 60_000.0)]          # one violent tick
        sigs = feed(e, "BLIPUSDT", series)
        self.assertFalse(any(s.kind in ("ACCEL", "BLOWOFF") for s in sigs))

    def test_two_tick_blip_does_not_fire(self):
        e = self._engine()
        series = [(1.0, 1000.0)] * 60 + [(1.015, 40_000.0), (1.03, 60_000.0)]
        sigs = feed(e, "BLIP2USDT", series)
        self.assertFalse(any(s.kind in ("ACCEL", "BLOWOFF") for s in sigs))

    def test_sustained_ramp_fires(self):
        e = self._engine()
        series = [(1.0, 1000.0)] * 60
        for i in range(1, 31):                       # 30 slots = 15 minutes of ramp
            series.append((1.0 * (1 + 0.0025 * i), 1000.0 * (1 + 0.9 * i)))
        sigs = feed(e, "RAMPUSDT", series)
        self.assertTrue(any(s.kind == "ACCEL" for s in sigs),
                        "a clean multi-minute price+volume ramp must fire")

    def test_flat_tape_never_fires(self):
        e = self._engine()
        sigs = feed(e, "DEADUSDT", [(1.0, 1000.0)] * 200)
        self.assertFalse(any(s.alert for s in sigs))

    def test_price_up_but_volume_flat_does_not_fire(self):
        e = self._engine()
        series = [(1.0, 1000.0)] * 60
        for i in range(1, 31):
            series.append((1.0 * (1 + 0.0025 * i), 1000.0))
        sigs = feed(e, "NOVOLUSDT", series)
        self.assertFalse(any(s.kind == "ACCEL" for s in sigs))


class TestAlertLadder(unittest.TestCase):
    def test_escalation_is_monotonic_and_deduped(self):
        cfg = Config()
        cfg.min_base_age_sec = 0.0
        e = Engine(cfg)
        series = [(1.0, 1000.0)] * 60
        for i in range(1, 61):                       # a long, still-accelerating run
            series.append((1.0 * (1 + 0.004 * i), 1000.0 * (1 + 1.4 * i)))
        sigs = [s for s in feed(e, "LADDERUSDT", series) if s.alert]
        kinds = [s.kind for s in sigs]
        for k in ("EARLY", "ACCEL", "BLOWOFF"):
            self.assertLessEqual(kinds.count(k), 1,
                                 f"{k} must alert at most once per episode")
        order = [k for k in kinds if k in ("EARLY", "ACCEL", "BLOWOFF")]
        rank = {"EARLY": 1, "ACCEL": 2, "BLOWOFF": 3}
        self.assertEqual(order, sorted(order, key=lambda k: rank[k]),
                         "tiers must only ever escalate")

    def test_every_alert_type_names_itself_in_russian(self):
        for kind, (header, explain) in TYPES.items():
            self.assertTrue(header.strip(), kind)
            self.assertTrue(explain.strip(), kind)

    def test_message_leads_with_the_type(self):
        from volscan.engine import Signal
        sig = Signal(key="SPOT:XUSDT", market="SPOT", symbol="XUSDT", kind="ACCUM",
                     ts=0, price=1.2345, score=71, reasons=["🧲 накопление"],
                     features={"rvol": 9.0, "d1m": 12345.0, "taker": 0.63})
        msg = format_signal(sig, Config())
        self.assertTrue(msg.startswith("🐋 НАКОПЛЕНИЕ"))
        self.assertIn("покупатели давят", msg)
        self.assertIn("XUSDT", msg)


class TestFilters(unittest.TestCase):
    def test_volume_ceiling_is_500m_and_price_ceiling_is_off(self):
        cfg = Config()
        self.assertEqual(cfg.max_initial_volume, 500_000_000)
        self.assertEqual(cfg.max_initial_price, 0.0)
        e = Engine(cfg)
        e.on_ticker_batch("SPOT", [Tick("BIGUSDT", 1.0, 0, 9e8)], 1)
        self.assertNotIn("SPOT:BIGUSDT", e.states)      # above the ceiling
        e.on_ticker_batch("SPOT", [Tick("MIDUSDT", 4200.0, 0, 2e8)], 1)
        self.assertIn("SPOT:MIDUSDT", e.states)         # expensive but allowed

    def test_excluded_symbols(self):
        e = Engine(Config())
        self.assertTrue(e.is_excluded("DENTUSDT"))
        self.assertTrue(e.is_excluded("SOMEALPHAUSDT"))
        self.assertFalse(e.is_excluded("INJUSDT"))


class TestStorage(unittest.TestCase):
    def test_roundtrip_and_restart(self):
        from volscan.engine import Signal
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "t.db")
            s = Storage(path)
            sig = Signal(key="SPOT:AUSDT", market="SPOT", symbol="AUSDT", kind="ACCEL",
                         ts=1000, price=2.0, score=90, reasons=["x"], features={"rvol": 5})
            s.queue_signal(sig, True)
            s.queue_sample("SPOT:AUSDT", 1000, 2.0, 1e6, 500.0, 5.0, 90, True)
            s.queue_live("SPOT:AUSDT", 1000, {"key": "SPOT:AUSDT", "score": 90})
            self.assertGreater(s.flush(), 0)
            s.close()

            s2 = Storage(path)                      # simulate a restart
            self.assertEqual(len(s2.recent_signals()), 1)
            self.assertEqual(len(s2.history("SPOT:AUSDT", 0)), 1)
            self.assertEqual(s2.live_rows()[0]["score"], 90)
            s2.close()


class TestFeedParsing(unittest.TestCase):
    def test_ws_ticker(self):
        t = parse_ws_ticker({"s": "AUSDT", "c": "1.5", "P": "3", "q": "1000",
                             "n": 10, "b": "1.49", "B": "5", "a": "1.51", "A": "4",
                             "w": "1.48", "h": "1.6", "l": "1.4"})
        self.assertEqual(t.symbol, "AUSDT")
        self.assertAlmostEqual(t.price, 1.5)
        self.assertIsNone(parse_ws_ticker({"nope": 1}))

    def test_hourly_context(self):
        rows = [[i, "1", "1.1", "0.9", str(1 + i * 0.001), "1", i, str(1000 + i),
                 5, "1", "1", "0"] for i in range(200)]
        ctx = hourly_context(rows, 0)
        self.assertIsNotNone(ctx.rsi14)
        self.assertIsNotNone(ctx.bb_width_pct)
        self.assertTrue(0.0 <= ctx.bb_width_pct <= 1.0)


class TestSettings(unittest.TestCase):
    """The dashboard's filter panel writes to the LIVE config, so the validation
    in front of it is the only thing standing between a typo and a broken run."""

    def test_old_html_filters_are_all_exposed(self):
        names = {e[0] for e in EDITABLE}
        for old in ("threshold_10s", "threshold_1m", "threshold_2m",
                    "max_initial_price", "max_initial_volume", "score_threshold",
                    "telegram_token", "telegram_chat_id"):
            self.assertIn(old, names, f"{old} must stay editable from the browser")

    def test_new_thresholds_are_exposed(self):
        names = {e[0] for e in EDITABLE}
        for new in ("accel_min_r2_price", "accel_min_r2_volume", "accel_min_rvol",
                    "accel_min_gain_pct", "accel_persist", "accel_alert_min_score",
                    "cand_rvol_min", "cand_pct5m_min", "cand_range15_rel_min",
                    "cand_dist_high24_min", "cand_pos_range_min", "cand_taker_min",
                    "alert_candidate", "alert_anomaly", "alert_accumulation",
                    "alert_wake", "alert_score", "tg_max_per_hour",
                    "episode_cooldown_sec", "episode_idle_sec"):
            self.assertIn(new, names, f"{new} must be editable from the browser")

    def test_every_editable_field_exists_on_config(self):
        cfg = Config()
        for name, label, kind, group, hint in EDITABLE:
            self.assertTrue(hasattr(cfg, name), name)
            self.assertIn(group, {g[0] for g in GROUPS}, name)
            self.assertIn(kind, ("num", "int", "bool", "text", "secret"), name)

    def test_shorthand_amounts(self):
        self.assertEqual(parse_amount("500m"), 500_000_000)
        self.assertEqual(parse_amount("3млн"), 3_000_000)
        self.assertEqual(parse_amount("50k"), 50_000)
        self.assertEqual(parse_amount("1 000 000"), 1_000_000)

    def test_validation_rejects_bad_values(self):
        with self.assertRaises(ValueError):
            coerce_setting("accel_min_r2_price", "5")        # out of 0..1
        with self.assertRaises(ValueError):
            coerce_setting("score_threshold", "abc")         # not a number
        with self.assertRaises(ValueError):
            coerce_setting("db_path", "/etc/passwd")         # not editable at all
        with self.assertRaises(ValueError):
            coerce_setting("accel_min_rvol", "-5")           # negative

    def test_validation_accepts_good_values(self):
        self.assertEqual(coerce_setting("max_initial_volume", "250m"), 250_000_000)
        self.assertEqual(coerce_setting("accel_persist", "3"), 3)
        self.assertIs(coerce_setting("alert_accumulation", "да"), True)
        self.assertIs(coerce_setting("alert_accumulation", "нет"), False)
        self.assertEqual(coerce_setting("cand_dist_high24_min", "-1.5"), -1.5)

    def test_secrets_are_masked_in_the_view(self):
        cfg = Config()
        cfg.telegram_token = "123456789:AAHsecretpart"
        for g in settings_view(cfg):
            for it in g["items"]:
                if it["name"] == "telegram_token":
                    self.assertNotIn("secretpart", it["value"])
                    self.assertTrue(it["value"].startswith("•"))

    def test_applied_setting_changes_engine_behaviour_immediately(self):
        cfg = Config()
        cfg.min_base_age_sec = 0.0
        e = Engine(cfg)
        e.on_ticker_batch("SPOT", [Tick("AUSDT", 1.0, 0, 4e8)], 1)
        self.assertIn("SPOT:AUSDT", e.states)      # under the 500M ceiling
        setattr(cfg, "max_initial_volume", coerce_setting("max_initial_volume", "100m"))
        e.on_ticker_batch("SPOT", [Tick("BUSDT", 1.0, 0, 4e8)], 2)
        self.assertNotIn("SPOT:BUSDT", e.states)   # same engine, new ceiling, no restart

    def test_settings_round_trip_through_storage(self):
        import json as _json
        with tempfile.TemporaryDirectory() as d:
            st = Storage(os.path.join(d, "s.db"))
            st.save_settings({"score_threshold": 72.0, "alert_wake": True})
            self.assertEqual(_json.loads(st.load_settings()["score_threshold"]), 72.0)
            st.close()


if __name__ == "__main__":
    unittest.main()
