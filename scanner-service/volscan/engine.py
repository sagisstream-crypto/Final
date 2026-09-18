"""Detection engine: everything the old browser build computed, plus the three
detectors that were added after calibrating against 12 months of 1-minute
Binance klines (see SIGNALS.md).

The engine is deliberately free of I/O. It eats normalised ticker snapshots and
1-minute kline updates and emits Signal objects; the feed, the database and
Telegram all live elsewhere. That is what makes it replayable and unit-testable
without a network.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .config import Config
from .indicators import (clamp, find_anchor, lin_reg_slope_per_min, mean,
                         median, pct_change, stdev, zscore)

MINUTE = 60_000.0


# --------------------------------------------------------------------------- #
#  data shapes
# --------------------------------------------------------------------------- #
@dataclass
class Tick:
    symbol: str
    price: float
    pct24: float
    quote_volume: float
    trades: Optional[float] = None
    bid_price: Optional[float] = None
    bid_qty: Optional[float] = None
    ask_price: Optional[float] = None
    ask_qty: Optional[float] = None
    vwap: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None


@dataclass
class Signal:
    key: str
    market: str
    symbol: str
    kind: str                 # ACCEL | CANDIDATE | ANOMALY | WAKE | ACCUM | SCORE
    ts: int
    price: float
    score: int
    reasons: List[str] = field(default_factory=list)
    features: Dict[str, float] = field(default_factory=dict)
    alert: bool = False       # should this go to Telegram?


@dataclass
class HourlyContext:
    """Slow-moving background state, refreshed from 1h klines via REST.

    These are the sibling backtest's precursor features (STRATEGY.md §5): on
    2.29M hourly bars its best rule was vol_z>4 AND RSI(14)<45 AND
    bb_width_percentile>0.6 — a *volatile, not-overbought, pulled-back* pair,
    which is the opposite of the classic squeeze-breakout picture.
    """
    rsi14: Optional[float] = None
    bb_width_pct: Optional[float] = None    # percentile of BB width in its own last month
    vol_z_1h: Optional[float] = None
    ret_4h: Optional[float] = None
    atr_pct: Optional[float] = None
    updated_at: int = 0

    def background_ok(self) -> bool:
        """The 'фон' filter: a pair worth having in the watch list at all."""
        if self.rsi14 is None or self.bb_width_pct is None:
            return False
        return self.rsi14 < 45.0 and self.bb_width_pct > 0.6

    def precursor(self) -> bool:
        if self.vol_z_1h is None:
            return False
        return self.vol_z_1h > 4.0 and self.background_ok()


# --------------------------------------------------------------------------- #
#  per-symbol state
# --------------------------------------------------------------------------- #
class SymbolState:
    __slots__ = ("key", "market", "symbol", "base", "quote", "session_base",
                 "base_time", "price_base", "history", "rate_samples",
                 "slots", "last_slot_id", "taker_samples", "imbal_samples",
                 "ats_samples", "day_high", "day_high_at", "day_low", "taker",
                 "taker_at", "last_fired", "cluster_at", "cluster_high",
                 "accel_streak", "accum_streak", "hourly", "last_tick",
                 "snapshot", "signal_count", "pending_hold", "episode", "ref_samples")

    def __init__(self, key: str, market: str, symbol: str, base: str, quote: str,
                 cfg: Config):
        self.key = key
        self.market = market
        self.symbol = symbol
        self.base = base
        self.quote = quote
        self.session_base: Optional[float] = None
        self.base_time = 0
        self.price_base: Optional[float] = None
        self.history: deque = deque()          # (ts, quote_volume, price, trades)
        self.rate_samples: deque = deque(maxlen=cfg.rate_baseline_max)
        self.slots: deque = deque(maxlen=64)   # (slot_id, price, turnover_rate)
        self.last_slot_id = -1
        self.taker_samples: deque = deque(maxlen=300)   # (ts, buy_quote, quote)
        self.imbal_samples: deque = deque(maxlen=64)
        self.ats_samples: deque = deque(maxlen=32)
        self.day_high: Optional[float] = None
        self.day_high_at = 0
        self.day_low: Optional[float] = None
        self.taker: Optional[float] = None
        self.taker_at = 0
        self.last_fired: Dict[str, int] = {}
        self.cluster_at = 0
        self.cluster_high: Optional[float] = None
        self.accel_streak = 0
        self.accum_streak = 0
        self.hourly = HourlyContext()
        self.last_tick = 0
        self.snapshot: Dict = {}
        self.signal_count = 0
        self.pending_hold: Optional[dict] = None
        self.episode: Optional[dict] = None
        # rolling reference for ×mult / session gain: (ts, quoteVolume, price)
        # sampled every 5 min over 6h. A FIXED session base drifts as the
        # service stays up for days, and every pair eventually looks like a
        # '×5 chase' — which is exactly what would have suppressed the EARLY
        # tier on the GENIUS pump in a long-running process.
        self.ref_samples: deque = deque(maxlen=80)

    # -- cooldown helpers ---------------------------------------------------
    def can_fire(self, kind: str, now: int, cooldown_sec: float) -> bool:
        return (now - self.last_fired.get(kind, 0)) >= cooldown_sec * 1000

    def mark(self, kind: str, now: int) -> None:
        self.last_fired[kind] = now


# --------------------------------------------------------------------------- #
#  engine
# --------------------------------------------------------------------------- #
class Engine:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.states: Dict[str, SymbolState] = {}
        self.btc_history: deque = deque()
        self.quality_count = 0

    # ---------------------------------------------------------------- feed in
    def on_kline(self, market: str, symbol: str, base_buy_quote: float,
                 quote: float, now: int) -> None:
        """Taker-buy ratio from the hot kline_1m stream."""
        key = f"{market}:{symbol}"
        st = self.states.get(key)
        if st is None or quote <= 0:
            return
        st.taker = base_buy_quote / quote
        st.taker_at = now
        st.taker_samples.append((now, base_buy_quote, quote))

    def set_hourly(self, key: str, ctx: HourlyContext) -> None:
        st = self.states.get(key)
        if st is not None:
            st.hourly = ctx

    def is_excluded(self, symbol: str) -> bool:
        if symbol in self.cfg.excluded_symbols:
            return True
        up = symbol.upper()
        return any(sub in up for sub in self.cfg.exclude_substrings)

    def on_ticker_batch(self, market: str, ticks: List[Tick], now: int,
                        resyncing: bool = False) -> List[Signal]:
        out: List[Signal] = []
        for t in ticks:
            if not t.symbol.endswith(self.cfg.quote) or self.is_excluded(t.symbol):
                continue
            if market == "SPOT" and t.symbol == "BTC" + self.cfg.quote:
                self._push_btc(t.price, now)
            sigs = self._on_tick(market, t, now, resyncing)
            out.extend(sigs)
        return out

    # ---------------------------------------------------------------- internals
    def _push_btc(self, price: float, now: int) -> None:
        self.btc_history.append((now, price))
        while self.btc_history and now - self.btc_history[0][0] > 20 * 60_000:
            self.btc_history.popleft()

    def btc_pct(self, now: int, window_ms: float) -> Optional[float]:
        if len(self.btc_history) < 2:
            return None
        anchor = find_anchor(self.btc_history, now, window_ms)
        if anchor is None:
            return None
        return pct_change(anchor[1], self.btc_history[-1][1])

    def _on_tick(self, market: str, t: Tick, now: int, resyncing: bool) -> List[Signal]:
        cfg = self.cfg
        key = f"{market}:{t.symbol}"
        st = self.states.get(key)
        if st is None:
            if t.quote_volume > cfg.max_initial_volume:
                return []
            if cfg.max_initial_price > 0 and t.price >= cfg.max_initial_price:
                return []
            base = t.symbol[: -len(cfg.quote)]
            st = SymbolState(key, market, t.symbol, base, cfg.quote, cfg)
            st.session_base = t.quote_volume
            st.base_time = now
            st.price_base = t.price
            self.states[key] = st

        st.last_tick = now
        st.history.append((now, t.quote_volume, t.price, t.trades))
        cutoff = now - cfg.history_max_sec * 1000
        while st.history and st.history[0][0] < cutoff:
            st.history.popleft()

        f = self._features(st, t, now)
        score, reasons, kind = self._score(f)
        f["score"] = score
        f["kind"] = kind
        st.snapshot = dict(f, reasons=reasons, price=t.price, ts=now,
                           symbol=t.symbol, market=market, base=st.base)

        if resyncing:
            return []
        return self._detect(st, t, f, score, reasons, kind, now)

    # ------------------------------------------------------------- features
    def _features(self, st: SymbolState, t: Tick, now: int) -> dict:
        cfg = self.cfg
        h = st.history
        f: Dict[str, Optional[float]] = {}

        def roll(window_ms, field_idx):
            a = find_anchor(h, now, window_ms)
            if a is None:
                return None
            if now - a[0] > window_ms + max(8000.0, window_ms * 0.35):
                return None
            v = a[field_idx]
            return None if v is None else v

        a1 = find_anchor(h, now, MINUTE)
        a2 = find_anchor(h, now, 2 * MINUTE)
        a5 = find_anchor(h, now, 5 * MINUTE)
        v1 = roll(MINUTE, 1)
        v2 = roll(2 * MINUTE, 1)
        v10s = roll(10_000, 1)

        f["d1m"] = None if v1 is None else t.quote_volume - v1
        f["d2m"] = None if v2 is None else t.quote_volume - v2
        f["d10s"] = None if v10s is None else t.quote_volume - v10s
        f["pct1m"] = pct_change(a1[2], t.price) if a1 else None
        f["pct2m"] = pct_change(a2[2], t.price) if a2 else None
        f["pct5m"] = pct_change(a5[2], t.price) if a5 else None

        # rolling reference (see SymbolState.ref_samples)
        if not st.ref_samples or now - st.ref_samples[-1][0] >= 5 * 60_000:
            st.ref_samples.append((now, t.quote_volume, t.price))
        old = [r for r in st.ref_samples if now - r[0] >= 30 * 60_000]
        ref_vol = median([r[1] for r in old]) if len(old) >= 3 else st.session_base
        ref_price = median([r[2] for r in old]) if len(old) >= 3 else st.price_base
        f["mult"] = (t.quote_volume / ref_vol) if ref_vol else 1.0
        f["session_gain"] = pct_change(ref_price, t.price) or 0.0
        f["base_age"] = now - st.base_time
        f["day_qv"] = t.quote_volume

        # RVOL — this minute's turnover vs this symbol's own average minute
        avg_min = t.quote_volume / 1440.0 if t.quote_volume > 0 else None
        f["avg_min"] = avg_min
        f["rvol"] = (f["d1m"] / avg_min) if (f["d1m"] is not None and avg_min) else None

        # trade size
        n1 = roll(MINUTE, 3)
        dn = (t.trades - n1) if (t.trades is not None and n1 is not None) else None
        f["ats"] = (f["d1m"] / dn) if (dn and dn >= 3 and f["d1m"] and f["d1m"] > 0) else None

        # book (spot ticker carries top-of-book; USDT-M does not)
        f["spread_pct"] = (pct_change(t.bid_price, t.ask_price)
                           if (t.bid_price and t.ask_price) else None)
        f["imbal"] = (t.bid_qty / (t.bid_qty + t.ask_qty)
                      if (t.bid_qty is not None and t.ask_qty is not None
                          and (t.bid_qty + t.ask_qty) > 0) else None)

        # 24h ratchet
        if t.high:
            if st.day_high is None:
                st.day_high = t.high
            elif t.high > st.day_high:
                st.day_high = t.high
                st.day_high_at = now
        if t.low:
            st.day_low = t.low
        f["high_fresh"] = (now - st.day_high_at) if st.day_high_at else None
        f["dist_high24"] = pct_change(st.day_high, t.price) if st.day_high else None
        f["rng"] = ((t.price - t.low) / (t.high - t.low)
                    if (t.high and t.low and t.high > t.low) else None)
        f["vwap_dev"] = pct_change(t.vwap, t.price) if t.vwap else None

        # periodic baseline sampling (quiet shelf + anomaly baseline)
        if f["d1m"] is not None and (now - st.last_fired.get("_sample", 0)) >= cfg.rate_sample_sec * 1000:
            st.rate_samples.append(f["d1m"])
            if f["ats"] is not None:
                st.ats_samples.append(f["ats"])
            if f["imbal"] is not None:
                st.imbal_samples.append((now, f["imbal"]))
            st.last_fired["_sample"] = now
        baseline = list(st.rate_samples)
        f["base_n"] = len(baseline)
        f["base_med"] = median(baseline)
        f["base_std"] = stdev(baseline)
        # floor the z denominator with the symbol's own average minute — without
        # it the z-score explodes on dust and becomes anti-predictive
        floor = (avg_min or 0.0) * 0.25
        f["vol_z"] = (zscore(f["d1m"], baseline, floor)
                      if (f["d1m"] is not None and len(baseline) >= 8) else None)
        f["ats_median"] = median(list(st.ats_samples)) if len(st.ats_samples) >= 4 else None

        # 15m structure
        struct = self._structure(st, now, t.price)
        f.update(struct)

        # taker
        taker = st.taker if (st.taker is not None and now - st.taker_at <= cfg.taker_stale_sec * 1000) else None
        f["taker"] = taker
        f.update(self._flow(st, now, avg_min))

        # acceleration slots
        f.update(self._accel(st, now, t.price, f))

        f["accel_slope"] = lin_reg_slope_per_min([(s[0], s[1]) for s in h], now)
        f["btc15"] = self.btc_pct(now, 15 * MINUTE)
        f["hourly_rsi"] = st.hourly.rsi14
        f["hourly_bb_pct"] = st.hourly.bb_width_pct
        f["hourly_vol_z"] = st.hourly.vol_z_1h
        f["background_ok"] = st.hourly.background_ok()
        f["precursor"] = st.hourly.precursor()
        return f

    def _structure(self, st: SymbolState, now: int, price: float) -> dict:
        cfg = self.cfg
        win = cfg.struct_window_sec * 1000
        pts = [(ts, p) for ts, _, p, _ in st.history if now - ts <= win]
        if len(pts) < 12 or not price:
            return dict(high15=None, low15=None, range15=None, break15=False,
                        compressed=False, high_prior=None, range15_rel=None,
                        range60=None, range60_rel=None, pos_range=None)
        hi = max(p for _, p in pts)
        lo = min(p for _, p in pts)
        prior = [p for ts, p in pts if now - ts > cfg.struct_prior_gap_sec * 1000]
        hi_prior = max(prior) if prior else None
        range15 = ((hi - lo) / lo * 100) if lo > 0 else None
        # whole-history (up to 20 min) view used as the "60m-ish" reference
        allp = [row[2] for row in st.history]
        hi_all, lo_all = (max(allp), min(allp)) if allp else (None, None)
        range_all = ((hi_all - lo_all) / lo_all * 100) if (lo_all and lo_all > 0) else None
        pos = ((price - lo_all) / (hi_all - lo_all)) if (hi_all and lo_all and hi_all > lo_all) else None
        # relative compression: this 15m range against the widest range this pair
        # has printed inside the retained history
        rel = (range15 / range_all) if (range15 is not None and range_all) else None
        return dict(
            high15=hi, low15=lo, range15=range15,
            break15=bool(prior and len(prior) >= 6 and hi_prior and price >= hi_prior * 1.0025),
            compressed=bool(rel is not None and rel <= self.cfg.compression_rel_max
                            and range15 is not None and range15 <= self.cfg.compression_abs_max_pct),
            high_prior=hi_prior, range15_rel=rel,
            range60=range_all, range60_rel=None, pos_range=pos,
        )

    def _flow(self, st: SymbolState, now: int, avg_min: Optional[float]) -> dict:
        """Accumulation: sustained buy-side taker pressure while price is
        range-bound. Kept as a *descriptive* flag — over 12 months of history it
        showed no forward edge (see SIGNALS.md), so it never adds score."""
        cfg = self.cfg
        win = cfg.accum_window_sec * 1000
        rows = [(ts, b, q) for ts, b, q in st.taker_samples if now - ts <= win]
        if len(rows) < cfg.accum_min_samples:
            return dict(taker_w=None, taker_frac=None, net_flow_x=None,
                        accumulating=False, accum_n=len(rows))
        qsum = sum(q for _, _, q in rows)
        bsum = sum(b for _, b, _ in rows)
        taker_w = (bsum / qsum) if qsum > 0 else None
        frac = sum(1 for _, b, q in rows if q > 0 and b / q > 0.55) / len(rows)
        minutes = max(1.0, (rows[-1][0] - rows[0][0]) / MINUTE)
        net_flow_x = ((2 * bsum - qsum) / (avg_min * minutes)) if avg_min else None
        return dict(taker_w=taker_w, taker_frac=frac, net_flow_x=net_flow_x,
                    accum_n=len(rows), accumulating=False)

    def _accel(self, st: SymbolState, now: int, price: float, f: dict) -> dict:
        """Sustained, simultaneous price + volume ramp.

        Ticks arrive roughly once a second and are noisy, so they are first
        collapsed onto fixed time slots. Inside the window we require a good
        straight-line fit on BOTH price and turnover *and* three rising blocks —
        which is exactly what a 1-2 tick blip cannot produce.
        """
        cfg = self.cfg
        slot_ms = cfg.accel_slot_sec * 1000
        window = cfg.accel_window_slots  # 20 slots x 30s = the calibrated 10-minute window
        slot_id = int(now // slot_ms)
        rate = None
        if f.get("d1m") is not None:
            rate = f["d1m"]             # $/minute turnover rate
        if slot_id != st.last_slot_id:
            st.slots.append([slot_id, price, rate, price, price])
            st.last_slot_id = slot_id
        else:
            cur = st.slots[-1]
            cur[1] = price
            if rate is not None:
                cur[2] = rate
            cur[3] = max(cur[3], price)
            cur[4] = min(cur[4], price)

        slots = [s for s in st.slots if s[2] is not None]
        if len(slots) < window:
            st.accel_streak = 0
            return dict(accel=False, accel_r2_p=None, accel_r2_v=None,
                        accel_gain=None, accel_streak=0, accel_slope_p=None,
                        accel_slope_v=None)
        seg = slots[-window:]
        # contiguity: the slots must actually be adjacent in time, otherwise a
        # feed gap would look like a clean ramp
        if seg[-1][0] - seg[0][0] > window * 1.8:
            st.accel_streak = 0
            return dict(accel=False, accel_r2_p=None, accel_r2_v=None,
                        accel_gain=None, accel_streak=0, accel_slope_p=None,
                        accel_slope_v=None)

        prices = [s[1] for s in seg]
        avg_min = f.get("avg_min") or 0.0
        vols = [(s[2] / avg_min) if avg_min else 0.0 for s in seg]
        sp, r2p = _fit(prices)
        sv, r2v = _fit(vols)
        gain = pct_change(prices[0], prices[-1]) or 0.0
        ok = (sp is not None and sv is not None
              and sp > 0 and sv > 0
              and r2p is not None and r2p >= cfg.accel_min_r2_price
              and r2v is not None and r2v >= cfg.accel_min_r2_volume
              and _blocks_rising(prices) and _blocks_rising(vols)
              and gain >= cfg.accel_min_gain_pct
              and (f.get("rvol") or 0) >= cfg.accel_min_rvol)
        st.accel_streak = st.accel_streak + 1 if ok else 0
        # debounce: the state must survive `accel_persist` consecutive slots
        # before the pair is flagged. Measured on 12 months of real 1m klines:
        # 0 of 40 000 injected 1-bar and 2-bar blips get through, while 40 000
        # of 40 000 genuine 10-bar ramps do.
        return dict(accel=bool(st.accel_streak >= cfg.accel_persist),
                    accel_r2_p=r2p, accel_r2_v=r2v,
                    accel_gain=gain, accel_streak=st.accel_streak,
                    accel_slope_p=sp, accel_slope_v=sv)

    # ---------------------------------------------------------------- scoring
    def _classify(self, f: dict) -> str:
        cfg = self.cfg
        g = f.get("session_gain") or 0.0
        m = f.get("mult") or 0.0
        if m >= cfg.chase_mult or g >= cfg.session_late_pct:
            return "chase"
        if m <= cfg.early_mult_max and g < 5:
            return "early"
        return "mid"

    def _score(self, f: dict):
        """Composite 0..100. Weights were re-tuned after the historical study:
        the trade-size bonus and the compression bonus were measured to *hurt*,
        the taker requirement was relaxed from 0.70 to 0.60, and a bonus was
        added for the sibling backtest's background condition."""
        parts: List[str] = []
        s = 0.0
        kind = self._classify(f)
        gain = f.get("session_gain") or 0.0

        rvol = f.get("rvol")
        if rvol is not None:
            if 25 <= rvol < 80:
                s += 26; parts.append(f"RVOL ×{rvol:.0f}")
            elif rvol >= 80:
                # A huge RVOL is only "late" if the pair has already run. Lateness
                # is measured by ×mult / session gain, not by RVOL magnitude — the
                # GENIUS pump printed RVOL ×215 in its very first minute at ×1.1.
                if (f.get("mult") or 0) < 1.5:
                    s += 26; parts.append(f"RVOL ×{rvol:.0f}")
                else:
                    s += 16; parts.append(f"RVOL ×{rvol:.0f} (уже горячо)")
            elif rvol >= 12:
                s += 22; parts.append(f"RVOL ×{rvol:.0f}")
            elif rvol >= 6:
                s += 14
            elif rvol >= 3:
                s += 7
            elif rvol < 1:
                s -= 10

        p1 = f.get("pct1m")
        if p1 is not None:
            if 2 <= p1 < 6:
                s += 22; parts.append(f"+{p1:.1f}%/1м")
            elif p1 >= 6:
                s += 10; parts.append(f"свеча уже +{p1:.1f}%")
            elif p1 >= 1:
                s += 18; parts.append(f"+{p1:.1f}%/1м")
            elif p1 >= 0.4:
                s += 10
            elif p1 <= -0.4:
                s -= 22; parts.append("цена вниз")

        p5 = f.get("pct5m")
        if p5 is not None and p5 >= 2.0:
            s += 12; parts.append(f"+{p5:.1f}%/5м")

        if f.get("accel"):
            s += 20; parts.append("УСКОРЕНИЕ")

        hf = f.get("high_fresh")
        if hf is not None and hf <= 120_000:
            s += 14; parts.append("новый хай 24ч")
        elif f.get("rng") is not None and f["rng"] >= 0.9:
            s += 8
        elif f.get("rng") is not None and f["rng"] <= 0.35:
            s -= 8

        vd = f.get("vwap_dev")
        if vd is not None:
            if vd >= 3:
                s += 10; parts.append("выше VWAP")
            elif vd >= 0.5:
                s += 5
            elif vd <= -3:
                s -= 8

        # Trade-size spikes were measured to REDUCE the +10% hit rate
        # (15.9% -> 11.1%), so the old +12 bonus is now a small penalty.
        ats, atsm = f.get("ats"), f.get("ats_median")
        if ats is not None and atsm:
            k = ats / atsm
            if k >= 6:
                s -= 6; parts.append(f"чек ×{k:.1f} (разовый принт)")

        imbal = f.get("imbal")
        if imbal is not None:
            if imbal >= 0.75:
                s += 8; parts.append("бид давит")
            elif imbal >= 0.6:
                s += 4
            elif imbal <= 0.25:
                s -= 10; parts.append("аск давит")
        elif f.get("market") == "USDT-M":
            s -= 4

        taker = f.get("taker")
        if taker is not None:
            if taker >= 0.70:
                s += 14; parts.append(f"тейкер {taker*100:.0f}%")
            elif taker >= 0.60:
                s += 12; parts.append(f"тейкер {taker*100:.0f}%")
            elif taker <= 0.35:
                s -= 16; parts.append("тейкер продаёт")
        else:
            s -= 6

        # Background condition from the sibling backtest (2.29M hourly bars):
        # not overbought + already volatile is where moves are actually born.
        if f.get("precursor"):
            s += 12; parts.append("предвестник (vol_z/RSI/BB)")
        elif f.get("background_ok"):
            s += 6; parts.append("фон: RSI<45, канал широкий")
        elif f.get("hourly_rsi") is not None and f["hourly_rsi"] > 65:
            s -= 8; parts.append("RSI 1ч перекуплен")

        # A tight 15m range was measured to LOWER the +10% odds, so it is no
        # longer a bonus; range expansion is.
        r15r = f.get("range15_rel")
        if r15r is not None and r15r >= 0.8:
            s += 6; parts.append("расширение диапазона")
        if f.get("break15"):
            s += 8; parts.append("пробой 15м")

        mult = f.get("mult")
        if mult:
            if mult < 1.35:
                s += 10; parts.append(f"рано ×{mult:.2f}")
            elif mult < 2.0:
                s += 4
            elif mult >= 5:
                s -= 22; parts.append(f"поздно ×{mult:.1f}")
            elif mult >= 3:
                s -= 14; parts.append(f"поздно ×{mult:.1f}")

        if gain >= 15:
            s -= 18; parts.append(f"session +{gain:.0f}%")
        elif gain >= self.cfg.session_late_pct:
            s -= 10; parts.append(f"session +{gain:.0f}%")

        p2 = f.get("pct2m")
        if p2 is not None and p1 is not None and p1 > 0:
            if p2 >= 0.8:
                s += 5
            elif p2 < 0:
                s -= 14; parts.append("2м вниз")

        sp = f.get("spread_pct")
        if sp is not None and sp >= 1.5:
            s -= 10; parts.append("широкий спред")

        btc = f.get("btc15")
        if btc is not None and btc <= -0.35:
            s -= 10; parts.append("BTC−")
        elif btc is not None and btc >= 0.25:
            s += 3

        if kind == "chase":
            s *= 0.45
            parts.append("CHASE")
        elif kind == "early":
            parts.insert(0, "EARLY")
        else:
            parts.append("MID")
        return int(clamp(round(s), 0, 100)), parts, kind

    # --------------------------------------------------------------- detectors
    def _detect(self, st, t, f, score, reasons, kind, now) -> List[Signal]:
        """Detectors + the escalating, de-duplicated alert ladder.

        The old build sent EARLY, RVOL100+ and SCORE×2 within seconds of each
        other for one and the same pump — three messages that say the same
        thing. Here every pair has at most one OPEN EPISODE, and inside an
        episode Telegram only ever hears about a *strictly higher* tier:

            1  ⚡ РАННИЙ        first qualifying entry (score + price + volume)
            2  🚀 УСКОРЕНИЕ     sustained price+volume ramp confirmed
            3  🔥 АНОМАЛЬНЫЙ ВЫНОС — УСКОРЯЕТСЯ
                               RVOL and Δ1м keep climbing after tier 2 without
                               rolling back — conviction is still building

        Worked example (GENIUS/USDT, 2026-09-17, verified against the user's own
        phone alerts and the 1m archive): tier 1 at 11:33 UTC, tier 2 at 11:38,
        tier 3 at 11:41 — versus nine near-identical messages from the old
        build over the same 13 minutes.
        """
        cfg = self.cfg
        sigs: List[Signal] = []
        if f["base_age"] < cfg.min_base_age_sec * 1000:
            return sigs

        def mk(k, alert=False, extra=None, tier=0):
            return Signal(key=st.key, market=st.market, symbol=st.symbol, kind=k,
                          ts=now, price=t.price, score=score,
                          reasons=(extra or []) + reasons[:4],
                          features=dict(_slim(f), tier=tier), alert=alert)

        rvol = f.get("rvol") or 0.0
        p1 = f.get("pct1m")
        p5 = f.get("pct5m")
        taker = f.get("taker")

        ep = st.episode
        if ep and now - ep["last_ts"] > cfg.episode_idle_sec * 1000:
            ep = st.episode = None      # episode went cold, allow a fresh one

        # ---- tier 1: first qualifying entry -------------------------------
        tier1 = (score >= cfg.score_threshold
                 and rvol >= cfg.early_rvol_min
                 and p1 is not None and p1 >= cfg.early_pct1m_min
                 and (f.get("pct2m") is None or f["pct2m"] >= 0)
                 and kind != "chase")
        if tier1 and ep is None and st.can_fire("EPISODE", now, cfg.episode_cooldown_sec):
            st.mark("EPISODE", now)
            ep = st.episode = {
                "tier": 1, "t0": now, "last_ts": now, "price0": t.price,
                "rvol0": rvol, "d1m0": f.get("d1m") or 0.0,
                "rvol_peak": rvol, "d1m_peak": f.get("d1m") or 0.0,
                "rising": 0, "last_rvol": rvol, "last_d1m": f.get("d1m") or 0.0,
            }
            st.signal_count += 1
            sigs.append(mk("EARLY", alert=True, tier=1, extra=[
                f"⚡ РАННИЙ · score {score}",
                f"RVOL ×{rvol:.0f} · +{p1:.2f}%/1м · ×{f.get('mult') or 0:.2f} от базы",
            ]))

        if ep is not None:
            # Only *activity* keeps an episode alive. Refreshing it on every tick
            # would keep one pair locked in its episode forever and silently
            # swallow the next real move on that pair.
            if rvol >= cfg.early_rvol_min * 0.5 or f.get("accel"):
                ep["last_ts"] = now
            d1m = f.get("d1m") or 0.0
            # "climbing without rolling back": both RVOL and Δ1м at least hold
            # their level while at least one of them makes a new episode high
            holds = rvol >= ep["last_rvol"] * cfg.escalate_hold_ratio and d1m >= ep["last_d1m"] * cfg.escalate_hold_ratio
            news = rvol > ep["rvol_peak"] or d1m > ep["d1m_peak"]
            ep["rising"] = ep["rising"] + 1 if (holds and news) else 0
            ep["rvol_peak"] = max(ep["rvol_peak"], rvol)
            ep["d1m_peak"] = max(ep["d1m_peak"], d1m)
            ep["last_rvol"], ep["last_d1m"] = rvol, d1m

        # ---- tier 2: sustained acceleration -------------------------------
        if f.get("accel"):
            if ep is None and st.can_fire("EPISODE", now, cfg.episode_cooldown_sec):
                st.mark("EPISODE", now)
                ep = st.episode = {
                    "tier": 1, "t0": now, "last_ts": now, "price0": t.price,
                    "rvol0": rvol, "d1m0": f.get("d1m") or 0.0,
                    "rvol_peak": rvol, "d1m_peak": f.get("d1m") or 0.0,
                    "rising": 0, "last_rvol": rvol, "last_d1m": f.get("d1m") or 0.0,
                }
            late = score < cfg.accel_alert_min_score
            if ep is not None and ep["tier"] < 2 and not late:
                ep["tier"] = 2
                st.signal_count += 1
                sigs.append(mk("ACCEL", alert=True, tier=2, extra=[
                    f"🚀 УСКОРЕНИЕ · score {score}",
                    f"цена и объём растут {cfg.accel_window_slots * cfg.accel_slot_sec / 60:.0f} мин подряд: "
                    f"+{f['accel_gain']:.1f}%, R² цена {f['accel_r2_p']:.2f} / объём {f['accel_r2_v']:.2f}",
                    f"RVOL ×{rvol:.0f} · Δ1м ${f.get('d1m') or 0:,.0f}",
                ]))
            elif ep is None or late:
                sigs.append(mk("ACCEL", alert=False, tier=2))

        # ---- tier 3: it is still accelerating -----------------------------
        if (ep is not None and ep["tier"] == 2 and f.get("accel")
                and ep["rising"] >= cfg.escalate_rising_samples
                and ep["rvol_peak"] >= ep["rvol0"] * cfg.escalate_rvol_mult):
            ep["tier"] = 3
            st.signal_count += 1
            gain = pct_change(ep["price0"], t.price) or 0.0
            sigs.append(mk("BLOWOFF", alert=True, tier=3, extra=[
                f"🔥 АНОМАЛЬНЫЙ ВЫНОС — УСКОРЯЕТСЯ · score {score}",
                f"RVOL ×{ep['rvol0']:.0f} → ×{ep['rvol_peak']:.0f}, "
                f"Δ1м ${ep['d1m0']:,.0f} → ${ep['d1m_peak']:,.0f} без отката",
                f"с первого сигнала {gain:+.1f}% за {(now - ep['t0'])/60000:.0f} мин",
            ]))

        # ---- journal-only detectors (never alert; they were the spam) -----
        # +10% candidate: calibrated at 18.9% of fires reaching +10% within 4h
        cand = (rvol >= cfg.cand_rvol_min
                and p5 is not None and p5 >= cfg.cand_pct5m_min
                and f.get("range15_rel") is not None and f["range15_rel"] >= cfg.cand_range15_rel_min
                and f.get("dist_high24") is not None and f["dist_high24"] >= cfg.cand_dist_high24_min
                and f.get("pos_range") is not None and f["pos_range"] >= cfg.cand_pos_range_min
                and (taker is None or taker >= cfg.cand_taker_min)
                and kind != "chase")
        f["candidate"] = bool(cand)
        if cand and st.can_fire("CANDIDATE", now, cfg.cand_cooldown_sec):
            st.mark("CANDIDATE", now)
            sigs.append(mk("CANDIDATE", alert=(ep is None and cfg.alert_candidate),
                           tier=1 if ep is None else 0, extra=[
                f"🎯 КАНДИДАТ +10% · RVOL ×{rvol:.0f} · +{p5:.1f}%/5м"]))

        # volume anomaly: z-score against the pair's own baseline, with the
        # absolute floor that stops dust from scoring z=300
        z = f.get("vol_z")
        if (z is not None and z >= cfg.anomaly_z_min and rvol >= cfg.anomaly_rvol_min
                and p1 is not None and p1 >= cfg.anomaly_pct1m_min
                and st.can_fire("ANOMALY", now, cfg.anomaly_cooldown_sec)):
            st.mark("ANOMALY", now)
            sigs.append(mk("ANOMALY", alert=cfg.alert_anomaly, extra=[
                f"💥 ВЗРЫВ ОБЪЁМА z={z:.1f} · RVOL ×{rvol:.0f}"]))

        # compression wake-up: logged, deliberately NOT alerted — measured
        # precision 2.0% against a 0.83% base rate, and the sibling backtest
        # found squeeze setups to be worse than random (lift 0.49)
        if (f.get("compressed") and f.get("break15") and rvol >= cfg.wake_rvol_min
                and p1 is not None and p1 >= cfg.wake_pct1m_min
                and st.can_fire("WAKE", now, cfg.anomaly_cooldown_sec)):
            st.mark("WAKE", now)
            sigs.append(mk("WAKE", alert=cfg.alert_wake, extra=["😴→⚡ выход из сжатия"]))

        # accumulation: descriptive state, never alerted
        acc = (f.get("taker_w") is not None and f["taker_w"] >= cfg.accum_taker_min
               and f.get("taker_frac") is not None and f["taker_frac"] >= cfg.accum_taker_frac_min
               and f.get("net_flow_x") is not None and f["net_flow_x"] >= cfg.accum_net_flow_min
               and f.get("range15_rel") is not None and f["range15_rel"] <= cfg.accum_range_rel_max)
        st.accum_streak = st.accum_streak + 1 if acc else 0
        f["accumulating"] = st.accum_streak >= 5
        st.snapshot["accumulating"] = f["accumulating"]
        if f["accumulating"] and st.can_fire("ACCUM", now, cfg.accum_cooldown_sec):
            st.mark("ACCUM", now)
            sigs.append(mk("ACCUM", alert=cfg.alert_accumulation, extra=["🧲 накопление"]))

        # plain high score — journal only
        if (score >= cfg.log_score_min and not tier1
                and st.can_fire("SCORE", now, cfg.cluster_cooldown_sec)):
            st.mark("SCORE", now)
            sigs.append(mk("SCORE", alert=cfg.alert_score))
        return sigs

    # ------------------------------------------------------------------ views
    def top_rows(self, limit: int = 200) -> List[dict]:
        """Accelerating pairs pinned to the top, then score, then Δ volume."""
        rows = [s.snapshot for s in self.states.values() if s.snapshot]
        rows.sort(key=lambda r: (
            0 if r.get("accel") else 1,
            -(r.get("score") or 0),
            -(r.get("d1m") or 0),
        ))
        return rows[:limit]


# --------------------------------------------------------------------------- #
#  small numeric helpers used by the accel detector
# --------------------------------------------------------------------------- #
def _fit(y: List[float]):
    """Least-squares slope (per sample) and R^2 for evenly spaced samples."""
    n = len(y)
    if n < 4:
        return None, None
    xs = list(range(n))
    xm = (n - 1) / 2.0
    ym = sum(y) / n
    sxx = sum((x - xm) ** 2 for x in xs)
    if sxx == 0:
        return None, None
    sxy = sum((x - xm) * (v - ym) for x, v in zip(xs, y))
    slope = sxy / sxx
    ssres = sum((v - (ym + slope * (x - xm))) ** 2 for x, v in zip(xs, y))
    sstot = sum((v - ym) ** 2 for v in y)
    if sstot <= 0:
        return slope, None
    return slope, 1.0 - ssres / sstot


def _blocks_rising(y: List[float], ratio: float = 1.0) -> bool:
    """Three equal blocks of the window must have strictly rising means."""
    n = len(y)
    k = n // 3
    if k < 2:
        return False
    b1 = sum(y[:k]) / k
    b2 = sum(y[k:2 * k]) / k
    tail = y[2 * k:]
    b3 = sum(tail) / len(tail)
    return b2 > b1 * ratio and b3 > b2 * ratio


_SLIM = ("rvol", "vol_z", "pct1m", "pct2m", "pct5m", "d1m", "d2m", "mult",
         "taker", "taker_w", "net_flow_x", "imbal", "spread_pct", "vwap_dev",
         "rng", "range15", "range15_rel", "dist_high24", "pos_range",
         "session_gain", "accel_gain", "accel_r2_p", "accel_r2_v",
         "hourly_rsi", "hourly_bb_pct", "hourly_vol_z", "day_qv")


def _slim(f: dict) -> Dict[str, float]:
    out = {}
    for k in _SLIM:
        v = f.get(k)
        if isinstance(v, (int, float)) and not (isinstance(v, float) and math.isnan(v)):
            out[k] = round(float(v), 6)
    for k in ("accel", "break15", "compressed", "accumulating", "precursor", "background_ok"):
        if f.get(k):
            out[k] = 1
    return out
