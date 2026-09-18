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
    alert_wake: bool = False
    alert_score: bool = False
    alert_blast: bool = True
    tg_max_per_hour: int = 30          # hard ceiling across all pairs

    # ---------------- detector: explosive launch (single candle) ----------------
    # ACCEL needs ~10 minutes of ramp. Some moves have no ramp at all: a dead
    # pair prints ONE candle where the trade COUNT explodes, the candle's own
    # range is 4-15% and RSI(6) saturates. To fire on sample 1 there is no
    # temporal persistence available as a noise filter, so severity does that
    # job: every term below is a single-sample outlier test against the pair's
    # own quiet baseline.
    blast_window_sec: float = 300.0        # the "candle" this looks at (5 min, like the charts)
    blast_baseline_sec: float = 3600.0     # the quiet base it is compared against
    # Measured A/B on 110 perps over 8 months: the trade-count axis turned out to
    # be largely REDUNDANT with RVOL — leaning on it (x60, x120) looked better
    # in-sample and got worse out-of-time, the signature of a curve fit. It ships
    # as a modest sanity floor instead: it costs nothing (205 vs 206 events) and
    # still rejects the case it was meant for, one whale print faking a candle.
    blast_trades_x_min: float = 8.0        # trades/min vs the pair's own quiet median
    blast_trades_floor: float = 5.0        # trades/min floor so a dead tape can't score x1000
    blast_range_pct_min: float = 2.5       # the candle's own high-low range
    blast_range_x_min: float = 6.0         # ... and how much wider than the base that is
    blast_rvol_min: float = 60.0
    blast_close_pos_min: float = 0.60      # closed in the upper part of its own range
    # RSI(6) saturates at 94-99 on these candles by construction, so gating on it
    # was measured to change nothing (25.4% -> 25.0% at >=80). Ships off; the
    # value is still computed, shown and alertable.
    blast_rsi_min: float = 0.0             # RSI(6) on the recent price track
    blast_cooldown_sec: float = 120 * 60

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
    cand_range15_rel_min: float = 0.55      # 15m range vs the symbol's own norm (expansion, not squeeze)
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
    # Optional shared secret for the settings WRITE endpoint. Empty = anyone who
    # can open the dashboard can also change thresholds. See README.
    dashboard_token: str = ""
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


# --------------------------------------------------------------------------- #
#  What the dashboard is allowed to edit at runtime
# --------------------------------------------------------------------------- #
# The old browser scanner kept its filters in localStorage, so every device had
# its own copy. Here one backend serves the PC and the phone, so the settings
# live server-side: a change made on either one takes effect for both, and for
# the engine, within a second.
#
# (field, label, kind, group, hint). `kind` is num | int | bool | text | secret.
# `live=False` marks a value that is only read when a structure is built, so it
# needs a restart to take full effect — the UI says so instead of pretending.
EDITABLE = [
    # ---- старые фильтры (те же, что были в HTML-сканере) ----
    ("threshold_10s",        "порог 10с $",          "num",    "old", "всплеск оборота за 10 секунд"),
    ("threshold_1m",         "порог Δ1м $",          "num",    "old", "прирост оборота за минуту"),
    ("threshold_2m",         "порог Δ2м $",          "num",    "old", "прирост оборота за две минуты"),
    ("max_initial_price",    "макс. цена $",         "num",    "old", "0 = без ограничения"),
    ("max_initial_volume",   "макс. нач. объём $",   "num",    "old", "не брать пару с суточным оборотом выше"),
    ("score_threshold",      "score ≥",              "num",    "old", "порог составной оценки"),
    ("log_score_min",        "лог score ≥",          "num",    "old", "с какого score писать в журнал"),
    ("telegram_token",       "TG bot token",         "secret", "old", "от @BotFather"),
    ("telegram_chat_id",     "TG chat id",           "text",   "old", "у групп и каналов с минусом"),

    # ---- ускорение ----
    ("accel_min_gain_pct",   "мин. рост за окно, %", "num",  "accel", "сколько цена должна прибавить"),
    ("accel_min_rvol",       "мин. RVOL",            "num",  "accel", "абсолютный пол по объёму"),
    ("accel_min_r2_price",   "R² цены ≥",            "num",  "accel", "насколько ровно растёт цена (0..1)"),
    ("accel_min_r2_volume",  "R² объёма ≥",          "num",  "accel", "насколько ровно растёт объём (0..1)"),
    ("accel_persist",        "дебаунс, сэмплов",     "int",  "accel", "сколько подряд держать состояние"),
    ("accel_window_slots",   "окно, слотов",         "int",  "accel", "20 слотов × 30с = 10 минут"),
    ("accel_slot_sec",       "слот, сек",            "num",  "accel", "шаг сетки усреднения"),
    ("accel_alert_min_score", "🚀 в TG при score ≥", "num",  "accel", "ниже — только в журнал"),

    # ---- кандидат +10% ----
    ("cand_rvol_min",        "RVOL ≥",               "num",  "cand", "объём против своей средней минуты"),
    ("cand_pct5m_min",       "Δ% за 5 мин ≥",        "num",  "cand", "импульс цены"),
    ("cand_range15_rel_min", "диапазон 15м / норма ≥", "num", "cand", "расширение, а не сжатие"),
    ("cand_dist_high24_min", "до 24ч хая ≥, %",      "num",  "cand", "−1 = в пределах 1% от хая"),
    ("cand_pos_range_min",   "позиция в 4ч диапазоне ≥", "num", "cand", "0.90 = в верхних 10%"),
    ("cand_taker_min",       "доля покупок ≥",       "num",  "cand", "0.60 по калибровке"),
    ("cand_cooldown_sec",    "кулдаун, сек",         "num",  "cand", "не чаще одного на пару"),

    # ---- алерты ----
    ("alert_blast",          "💥 ВЗРЫВНОЙ СТАРТ в Telegram", "bool", "alerts", "вход на первой свече, догоняющий"),
    ("alert_candidate",      "🎯 КАНДИДАТ в Telegram",   "bool", "alerts", "есть замеренное преимущество"),
    ("alert_anomaly",        "🔥 АНОМ. ОБЪЁМ в Telegram", "bool", "alerts", "обычно дублирует ⚡/🚀"),
    ("alert_accumulation",   "🐋 НАКОПЛЕНИЕ в Telegram", "bool", "alerts", "преимущества не замерено, будет много"),
    ("alert_wake",           "😴→⚡ СЖАТИЕ в Telegram",  "bool", "alerts", "хуже случайного, по умолчанию выкл"),
    ("alert_score",          "⭐ ВЫСОКИЙ SCORE в Telegram", "bool", "alerts", "по умолчанию только в журнал"),
    ("tg_max_per_hour",      "потолок сообщений в час",  "int",  "alerts", "жёсткий лимит на всё"),
    ("episode_cooldown_sec", "кулдаун эпизода, сек",     "num",  "alerts", "один эпизод на пару за это время"),
    ("episode_idle_sec",     "эпизод остывает за, сек",  "num",  "alerts", "после тишины эпизод закрывается"),
    ("escalate_rising_samples", "сэмплов роста для 🔥",  "int",  "alerts", "подряд, без отката"),
    ("escalate_rvol_mult",   "во сколько раз RVOL для 🔥", "num", "alerts", "с момента ⚡"),

    # ---- взрывной старт ----
    ("blast_trades_x_min",   "сделок ×к тишине ≥",   "num", "blast", "главная ось: число сделок, не $"),
    ("blast_range_pct_min",  "диапазон свечи ≥, %",  "num", "blast", "размах одной свечи"),
    ("blast_range_x_min",    "диапазон ×к обычному ≥", "num", "blast", "во сколько раз шире базы"),
    ("blast_rvol_min",       "RVOL ≥",               "num", "blast", "оборот против своей средней минуты"),
    ("blast_close_pos_min",  "закрытие в верхней части ≥", "num", "blast", "0.60 = верхние 40% свечи"),
    ("blast_rsi_min",        "RSI(6) ≥",             "num", "blast", "перегрев на коротком RSI"),
    ("blast_window_sec",     "окно свечи, сек",      "num", "blast", "300 = 5 минут"),
    ("blast_baseline_sec",   "база сравнения, сек",  "num", "blast", "3600 = час тишины"),
    ("blast_cooldown_sec",   "кулдаун, сек",         "num", "blast", ""),

    # ---- прочие детекторы ----
    ("anomaly_z_min",        "аномалия: z ≥",        "num", "other", "z-оценка минутного оборота"),
    ("anomaly_rvol_min",     "аномалия: RVOL ≥",     "num", "other", "обязательный абсолютный пол"),
    ("anomaly_pct1m_min",    "аномалия: Δ%1м ≥",     "num", "other", "подтверждение ценой"),
    ("accum_taker_min",      "накопление: доля покупок ≥", "num", "other", "за окно 20 минут"),
    ("accum_taker_frac_min", "накопление: доля бычьих минут ≥", "num", "other", ""),
    ("accum_net_flow_min",   "накопление: чистый поток ≥", "num", "other", "× своей средней минуты"),
    ("accum_range_rel_max",  "накопление: диапазон ≤",  "num", "other", "цена должна стоять"),
    ("compression_rel_max",  "сжатие: диапазон / норма ≤", "num", "other", "относительный, не фиксированный %"),
    ("wake_rvol_min",        "выход из сжатия: RVOL ≥", "num", "other", ""),
    ("wake_pct1m_min",       "выход из сжатия: Δ%1м ≥", "num", "other", ""),
    ("early_rvol_min",       "⚡ ранний: RVOL ≥",     "num", "other", ""),
    ("early_pct1m_min",      "⚡ ранний: Δ%1м ≥",     "num", "other", ""),
]

GROUPS = [
    ("old",    "Старые фильтры"),
    ("accel",  "🚀 Ускорение"),
    ("cand",   "🎯 Кандидат +10%"),
    ("blast",  "💥 Взрывной старт (вход на 1-й свече)"),
    ("alerts", "Алерты и Telegram"),
    ("other",  "Остальные детекторы"),
]

# Changing these rebuilds nothing by itself; the engine reads them per tick.
# The two exceptions below are read when a per-symbol structure is created.
NEEDS_RESTART = {"accel_window_slots", "accel_slot_sec"}

_LIMITS = {
    "accel_min_r2_price": (0.0, 1.0), "accel_min_r2_volume": (0.0, 1.0),
    "cand_taker_min": (0.0, 1.0), "cand_pos_range_min": (0.0, 1.0),
    "accum_taker_min": (0.0, 1.0), "accum_taker_frac_min": (0.0, 1.0),
    "accum_range_rel_max": (0.0, 10.0), "compression_rel_max": (0.0, 10.0),
    "score_threshold": (0.0, 100.0), "log_score_min": (0.0, 100.0),
    "accel_alert_min_score": (0.0, 100.0),
    "blast_close_pos_min": (0.0, 1.0), "blast_rsi_min": (0.0, 100.0),
    "blast_window_sec": (60.0, 1800.0), "blast_baseline_sec": (600.0, 21600.0),
    "blast_cooldown_sec": (60.0, 86400.0),
    "accel_persist": (1, 20), "accel_window_slots": (6, 60),
    "accel_slot_sec": (5.0, 300.0), "tg_max_per_hour": (1, 500),
    "escalate_rising_samples": (1, 50),
    "episode_cooldown_sec": (60.0, 86400.0), "episode_idle_sec": (60.0, 86400.0),
    "cand_cooldown_sec": (60.0, 86400.0),
    "cand_dist_high24_min": (-100.0, 100.0),
    "max_initial_price": (0.0, 1e9), "max_initial_volume": (0.0, 1e13),
}

_EDITABLE_BY_NAME = {e[0]: e for e in EDITABLE}


def parse_amount(raw):
    """Accept the shorthand the old HTML panel accepted: 500k, 3млн, 1 000 000."""
    if isinstance(raw, (int, float)):
        return float(raw)
    s = str(raw).strip().lower().replace(" ", "").replace(",", ".").replace("_", "").replace(" ", "")
    mult = 1.0
    for suffix, m in (("млн", 1e6), ("mln", 1e6), ("m", 1e6),
                      ("тыс", 1e3), ("k", 1e3), ("к", 1e3)):
        if s.endswith(suffix):
            mult = m
            s = s[: -len(suffix)]
            break
    return float(s) * mult


def coerce_setting(name: str, raw):
    """Validate and normalise one incoming settings value.

    Raises ValueError with a message meant to be shown to the person typing.
    """
    spec = _EDITABLE_BY_NAME.get(name)
    if spec is None:
        raise ValueError(f"{name}: эта настройка не редактируется")
    kind = spec[2]
    if kind == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on", "да")
    if kind in ("text", "secret"):
        return str(raw).strip()
    try:
        val = parse_amount(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{spec[1]}: «{raw}» — не число")
    if kind == "int":
        val = int(round(val))
    lo, hi = _LIMITS.get(name, (None, None))
    if lo is not None and not (lo <= val <= hi):
        raise ValueError(f"{spec[1]}: допустимо от {lo} до {hi}")
    if lo is None and val < 0 and name != "cand_dist_high24_min":
        raise ValueError(f"{spec[1]}: не может быть отрицательным")
    return val


def settings_view(cfg: "Config"):
    """The payload the dashboard renders its panel from."""
    groups = []
    for gid, title in GROUPS:
        items = []
        for name, label, kind, group, hint in EDITABLE:
            if group != gid:
                continue
            value = getattr(cfg, name)
            if kind == "secret":
                value = ("•" * 8 + str(value)[-4:]) if value else ""
            items.append({"name": name, "label": label, "kind": kind,
                          "hint": hint, "value": value,
                          "restart": name in NEEDS_RESTART})
        groups.append({"id": gid, "title": title, "items": items})
    return groups
