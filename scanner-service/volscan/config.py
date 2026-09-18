"""All tunables in one place.

Values are loaded from a JSON file (``--config``) on top of these defaults, and
any of them can be overridden from the environment with a ``VOLSCAN_`` prefix
(``VOLSCAN_TELEGRAM_TOKEN``, ``VOLSCAN_MAX_INITIAL_VOLUME`` ...).

Thresholds carrying a "calibrated" note were fitted on 12 months of Binance
1-minute klines for 82 spot USDT alts; see ``SIGNALS.md`` in the repo root for
the measured hit rates and the walk-forward splits behind each number.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict, fields, field
from typing import Optional


@dataclass
class Config:
    # ---------------- universe ----------------
    markets: tuple = ("SPOT", "USDT-M")
    quote: str = "USDT"
    # A symbol only enters the tracked set if its 24h quote volume is below this
    # when first seen. 500M covers essentially the whole alt market on Binance
    # (in the study basket the largest non-major alt averaged ~$83M/day), i.e.
    # everything except BTC/ETH/BNB/SOL/XRP.
    max_initial_volume: float = 500_000_000.0
    # 0 disables the price ceiling. The old $10 cap silently excluded BNB, LTC,
    # BCH, AAVE, TAO, ZEC, PAXG and friends, which contradicts a $500M volume
    # ceiling; price per unit carries no information about move probability.
    max_initial_price: float = 0.0
    excluded_symbols: tuple = (
        "UTKUSDT", "ONTUSDT", "IDEXUSDT", "MBOXUSDT", "FARMUSDT", "MLNUSDT",
        "ATAUSDT", "FORTHUSDT", "DEGOUSDT", "TRUUSDT", "HOOKUSDT", "PHBUSDT",
        "HIGHUSDT", "SYSUSDT", "MDTUSDT", "FUNUSDT", "DENTUSDT", "COSUSDT",
        "OXTUSDT", "FIOUSDT", "SXPUSDT", "LRCUSDT", "A2ZUSDT", "DUSDT",
        "NTRNUSDT", "RDNTUSDT", "WANUSDT",
    )
    exclude_substrings: tuple = ("ALPHA",)

    # ---------------- feed ----------------
    ws_stale_sec: float = 12.0
    ws_reconnect_base_sec: float = 0.8
    ws_reconnect_max_sec: float = 15.0
    rest_poll_sec: float = 3.0
    rest_probe_ws_sec: float = 45.0
    hot_symbols: int = 24            # kline_1m stream for taker-buy ratio
    hot_rebuild_sec: float = 45.0

    # ---------------- windows ----------------
    history_max_sec: float = 20 * 60
    rate_sample_sec: float = 55.0    # cadence of the d1m baseline sampler
    rate_baseline_max: int = 32      # ~30 min of baseline samples
    struct_window_sec: float = 15 * 60
    struct_prior_gap_sec: float = 45.0
    accum_window_sec: float = 20 * 60
    accum_long_window_sec: float = 4 * 3600
    taker_stale_sec: float = 180.0
    min_base_age_sec: float = 90.0

    # ---------------- legacy $ thresholds (journal only) ----------------
    threshold_10s: float = 50_000.0
    threshold_1m: float = 30_000.0
    threshold_2m: float = 50_000.0
    rvol_min_abs: float = 1_500.0

    # ---------------- detector: compression / wake-up ----------------
    # A fixed 1.4%/15min "compression" test was true for 94% of all minutes in
    # the study basket, so it carried no information. Compression is now measured
    # RELATIVE to the symbol's own last 24h of 15-minute ranges.
    compression_rel_max: float = 0.70      # calibrated: ~lowest quartile of a symbol's own ranges
    compression_abs_max_pct: float = 1.40  # kept as a sanity floor only
    quiet_std_factor: float = 0.6
    quiet_break_mult: float = 3.0
    wake_rvol_min: float = 15.0
    wake_pct1m_min: float = 0.4

    # ---------------- detector: sustained acceleration ----------------
    # The headline detector. Over a window of `accel_window_slots` fixed time
    # slots, BOTH price and turnover must fit a rising straight line well AND
    # rise across three consecutive thirds of the window. Calibrated on 12
    # months of real 1m klines (82 coins): 17.5% of fires reached +10% within
    # 4h against a 0.83% base rate, and 0 of 40 000 injected 1-2 bar blips got
    # through while 40 000 of 40 000 genuine 10-bar ramps did.
    accel_slot_sec: float = 30.0
    accel_window_slots: int = 20        # 20 x 30s = the calibrated 10-minute window
    accel_persist: int = 2             # debounce: hold for 2 slots (~1 min) before flagging
    accel_min_r2_price: float = 0.55
    accel_min_r2_volume: float = 0.30
    accel_min_gain_pct: float = 2.0
    accel_min_rvol: float = 12.0

    # ---------------- alert ladder ----------------
    early_rvol_min: float = 12.0
    early_pct1m_min: float = 0.5
    episode_cooldown_sec: float = 120 * 60   # one episode per pair per 2h
    episode_idle_sec: float = 45 * 60        # episode goes cold after 45 quiet minutes
    escalate_rising_samples: int = 3         # consecutive non-reversing climbs for tier 3
    escalate_rvol_mult: float = 2.0          # RVOL must at least double since tier 1
    escalate_hold_ratio: float = 0.75        # "no rollback" tolerance between samples
    # A ramp that only starts after the pair has already run prints a low
    # score (kind='chase'). Those stay in the journal but do not buzz the phone.
    accel_alert_min_score: float = 45.0
    alert_candidate: bool = True
    alert_accumulation: bool = False   # measured: no forward edge, so off by default
    alert_anomaly: bool = False

    # ---------------- detector: volume anomaly ----------------
    # z-score of the current 1-minute turnover against the symbol's own rolling
    # baseline. On its own the z-score is NOT predictive (it explodes on dead
    # tape where the baseline is dust), so an absolute RVOL floor is mandatory.
    anomaly_z_min: float = 6.0
    anomaly_rvol_min: float = 30.0
    anomaly_pct1m_min: float = 0.6         # calibrated: price confirmation is what carries the edge
    anomaly_cooldown_sec: float = 15 * 60

    # ---------------- detector: accumulation ----------------
    # Descriptive state flag. Measured over 12 months it has NO forward edge for
    # +10% moves, so it is displayed and logged but deliberately contributes
    # nothing to the candidate signal.
    accum_taker_min: float = 0.58
    accum_taker_frac_min: float = 0.65
    accum_net_flow_min: float = 1.0
    accum_range_rel_max: float = 0.90
    accum_min_samples: int = 12
    accum_cooldown_sec: float = 30 * 60

    # ---------------- detector: +10% candidate ----------------
    # Calibrated rule (walk-forward across a 50/50 coin split and an 8m/4m time
    # split). Measured: 18.9% of fired events reached +10% within 4h vs a 0.83%
    # unconditional base rate.
    cand_rvol_min: float = 20.0
    cand_pct5m_min: float = 2.0
    cand_range_expansion_min: float = 2.5   # 60m range vs the symbol's own 24h norm
    cand_dist_high24_min: float = -1.0      # within 1% of (or above) the 24h high
    cand_pos_range_min: float = 0.90        # sitting at the top of its own 4h range
    cand_taker_min: float = 0.60            # calibrated: 0.70 added nothing over 0.60
    cand_cooldown_sec: float = 120 * 60

    # ---------------- composite score ----------------
    score_threshold: float = 60.0
    score_cooldown_sec: float = 5.0
    log_score_min: float = 70.0
    cluster_cooldown_sec: float = 120 * 60
    hold_confirm_sec: float = 180.0
    hold_max_mae_pct: float = -1.5
    early_mult_max: float = 2.0
    chase_mult: float = 3.0
    session_late_pct: float = 8.0

    # ---------------- telegram ----------------
    telegram_token: str = ""
    telegram_chat_id: str = ""
    tg_cooldown_sec: float = 8 * 60
    tg_candidate_only: bool = False   # True -> only the +10% candidate alerts

    # ---------------- runtime ----------------
    db_path: str = "volscan.db"
    flush_sec: float = 10.0           # batched SQLite commit cadence
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8787
    dashboard_enabled: bool = True
    log_level: str = "INFO"
    outcome_checkpoints_min: tuple = (5, 15, 30, 60, 240)

    # ---------------- helpers ----------------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "Config":
        data = {}
        if path and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        known = {f.name: f for f in fields(cls)}
        kwargs = {}
        for name, spec in known.items():
            raw = data.get(name, None)
            env = os.environ.get("VOLSCAN_" + name.upper())
            if env is not None:
                raw = env
            if raw is None:
                continue
            kwargs[name] = _coerce(raw, spec.type)
        return cls(**kwargs)

    def dump(self) -> dict:
        return asdict(self)


def _coerce(value, typ):
    if typ is float or typ == "float":
        return float(value)
    if typ is int or typ == "int":
        return int(value)
    if typ is bool or typ == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(value)
    if typ is tuple or typ == "tuple":
        if isinstance(value, str):
            return tuple(x.strip() for x in value.split(",") if x.strip())
        return tuple(value)
    return value
