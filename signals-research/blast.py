"""The "explosive launch" detector: a dead pair that wakes up in ONE candle.

Shape taken from three live USDT-M perp screenshots (MYX, EVAA, G — 2026-09-18):
a long flat base, then a single candle where the TRADE COUNT explodes by orders
of magnitude, the candle's own range is 4-15%, and RSI(6) saturates at 94-99.

ACCEL cannot catch this: it needs ~10 minutes of ramp. Here there is no ramp at
all, so temporal persistence is unavailable as a noise filter and the severity of
the single sample has to do that job instead.

Primary new axis: trades per minute against the pair's own QUIET baseline — not
dollar volume. A pair can print a big $ number from one whale fill; hundreds of
thousands of separate trades in five minutes on a dead pair cannot be faked by
one participant.
"""
import numpy as np
import features as F


def _roll_median_shift(a, n, k=1):
    return F.shift(F._roll_median(a, n), k)


def build_blast(d, f, base_n=60):
    """Per-bar features for the single-candle launch rule."""
    tr = d["trades"].astype(float)
    hi, lo, cl, op = d["high"], d["low"], d["close"], d["open"]
    out = {}

    # --- trade-count anomaly against the pair's own trailing-hour baseline
    med = _roll_median_shift(tr, base_n)
    std = F.shift(F._roll_std(tr, base_n), 1)
    out["tr_med"] = med
    with np.errstate(invalid="ignore", divide="ignore"):
        # floor of 5 trades/min keeps a truly dead pair from scoring x1000 on
        # three trades, the same trap the volume z-score fell into
        out["trades_x"] = tr / np.maximum(med, 5.0)
        out["trades_z"] = (tr - med) / np.maximum(std, 2.0)

    # --- the candle's own range and where it closed inside it
    with np.errstate(invalid="ignore", divide="ignore"):
        out["bar_range"] = np.where(lo > 0, (hi - lo) / lo * 100, np.nan)
        rngv = hi - lo
        out["close_pos"] = np.where(rngv > 0, (cl - lo) / rngv, np.nan)
        out["bar_ret"] = np.where(op > 0, (cl - op) / op * 100, np.nan)

    # --- how quiet was the base the candle broke out of
    #     (trailing hour, excluding the candle itself)
    r = np.nan_to_num(out["bar_range"], nan=0.0)
    out["base_range"] = F.shift(F._roll_mean(r, base_n), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        out["range_x"] = out["bar_range"] / np.maximum(out["base_range"], 0.05)

    # --- RSI(6) on closes, the saturation the screenshots showed
    out["rsi6"] = rsi(cl, 6)
    return out


def rsi(close, n=14):
    """Wilder RSI, causal, NaN until it has n+1 bars."""
    d = np.diff(close, prepend=close[0])
    up = np.maximum(d, 0.0)
    dn = np.maximum(-d, 0.0)
    au = np.full(len(close), np.nan)
    ad = np.full(len(close), np.nan)
    if len(close) <= n:
        return au
    au[n] = up[1:n + 1].mean()
    ad[n] = dn[1:n + 1].mean()
    a = (n - 1) / n
    b = 1.0 / n
    for i in range(n + 1, len(close)):
        au[i] = au[i - 1] * a + up[i] * b
        ad[i] = ad[i - 1] * a + dn[i] * b
    with np.errstate(invalid="ignore", divide="ignore"):
        rs = np.where(ad > 0, au / ad, np.inf)
        out = 100.0 - 100.0 / (1.0 + rs)
    return out


def blast_mask(f, b, trades_x=25.0, bar_range=1.5, rvol=25.0,
               close_pos=0.60, range_x=4.0, rsi_min=0.0):
    """The rule itself. Every term is a single-sample severity test."""
    return ((np.nan_to_num(b["trades_x"], nan=0) >= trades_x)
            & (np.nan_to_num(b["bar_range"], nan=0) >= bar_range)
            & (np.nan_to_num(b["range_x"], nan=0) >= range_x)
            & (np.nan_to_num(f["rvol"], nan=0) >= rvol)
            & (np.nan_to_num(b["close_pos"], nan=0) >= close_pos)
            & (np.nan_to_num(b["bar_ret"], nan=-9) > 0)
            & (np.nan_to_num(b["rsi6"], nan=0) >= rsi_min))


def resample(d, k=5):
    """Aggregate 1m bars into k-minute bars (the screenshots are 5m)."""
    n = (len(d["ts"]) // k) * k
    sl = lambda a: a[:n].reshape(-1, k)
    return dict(ts=d["ts"][:n].reshape(-1, k)[:, 0],
                open=sl(d["open"])[:, 0], close=sl(d["close"])[:, -1],
                high=sl(d["high"]).max(axis=1), low=sl(d["low"]).min(axis=1),
                qv=sl(d["qv"]).sum(axis=1), trades=sl(d["trades"]).sum(axis=1),
                tbq=sl(d["tbq"]).sum(axis=1))
