"""Rebuild, from 1-minute klines, the same feature set the live scanner computes
from the !ticker@arr websocket stream.

Mapping live -> historical:
  scanner d1m  (rolling 60s turnover delta)   -> quote_volume of the 1m bar
  scanner rvol (d1m / (24h quoteVolume/1440)) -> bar qv / (trailing 1440-bar mean qv)
  scanner pct1m/pct2m                         -> close/close[-1], close/close[-2]
  scanner localStructure() over 15m of ticks  -> rolling 15-bar high/low/range
  scanner rateHistory quiet shelf (~20 min)   -> rolling 20-bar median/stdev of qv
  scanner taker ratio (kline_1m V/v)          -> taker_buy_quote / quote_volume
  scanner VWAP dev / day-range position       -> trailing 1440-bar vwap, high, low
Order-book imbalance and spread have no historical archive equivalent; they are
treated as unknown (the live engine already handles taker/imbal being null).
"""
import numpy as np
from binance_data import read_klines, months

DAY = 1440


def load_symbol(symbol, start, end):
    """Concatenated 1m bars for a symbol. Returns a dict of float64/int arrays."""
    ts, o, h, l, c, qv, nt, tbq = [], [], [], [], [], [], [], []
    for ym in months(start, end):
        rows = read_klines(symbol, "1m", ym)
        if not rows:
            continue
        for r in rows:
            try:
                t = int(r[0])
            except ValueError:
                continue
            if t > 1e15:      # newer archives use microseconds
                t //= 1000
            ts.append(t); o.append(float(r[1])); h.append(float(r[2]))
            l.append(float(r[3])); c.append(float(r[4])); qv.append(float(r[7]))
            nt.append(float(r[8])); tbq.append(float(r[10]))
    if len(ts) < DAY * 3:
        return None
    d = dict(ts=np.array(ts, dtype=np.int64), open=np.array(o), high=np.array(h),
             low=np.array(l), close=np.array(c), qv=np.array(qv),
             trades=np.array(nt), tbq=np.array(tbq))
    order = np.argsort(d["ts"], kind="stable")
    for k in d:
        d[k] = d[k][order]
    _, uniq = np.unique(d["ts"], return_index=True)
    for k in d:
        d[k] = d[k][uniq]
    return d


def _roll_sum(a, n):
    cs = np.concatenate(([0.0], np.cumsum(a)))
    out = np.full(a.shape, np.nan)
    out[n - 1:] = cs[n:] - cs[:-n]
    return out


def _roll_mean(a, n):
    return _roll_sum(a, n) / n


def _roll_std(a, n):
    m = _roll_mean(a, n)
    m2 = _roll_mean(a * a, n)
    return np.sqrt(np.maximum(m2 - m * m, 0.0))


def _roll_max(a, n):
    # O(n) sliding max via strided view (n is small here)
    out = np.full(a.shape, np.nan)
    if len(a) >= n:
        v = np.lib.stride_tricks.sliding_window_view(a, n)
        out[n - 1:] = v.max(axis=1)
    return out


def _roll_min(a, n):
    out = np.full(a.shape, np.nan)
    if len(a) >= n:
        v = np.lib.stride_tricks.sliding_window_view(a, n)
        out[n - 1:] = v.min(axis=1)
    return out


def _roll_median(a, n):
    out = np.full(a.shape, np.nan)
    if len(a) >= n:
        v = np.lib.stride_tricks.sliding_window_view(a, n)
        out[n - 1:] = np.median(v, axis=1)
    return out


def shift(a, k, fill=np.nan):
    """a shifted forward by k bars (a[i-k]); no lookahead."""
    out = np.full(a.shape, fill, dtype=float)
    if k < len(a):
        out[k:] = a[:len(a) - k]
    return out


def build(d, quiet_n=20, struct_n=15, accum_n=20):
    """Compute the live-equivalent feature matrix. Every value at index i uses
    only bars <= i (closed bars), so nothing leaks from the future."""
    c, h, l, qv, tbq, nt = d["close"], d["high"], d["low"], d["qv"], d["tbq"], d["trades"]
    f = {}

    f["close"] = c
    f["qv"] = qv

    # --- RVOL: this bar's turnover vs this symbol's own average minute of the day
    avg_min = shift(_roll_mean(qv, DAY), 1)          # trailing day, excluding current bar
    f["avg_min"] = avg_min
    f["day_qv"] = avg_min * DAY
    with np.errstate(invalid="ignore", divide="ignore"):
        f["rvol"] = np.where(avg_min > 0, qv / avg_min, np.nan)

    # --- price velocity
    f["pct1m"] = (c / shift(c, 1) - 1) * 100
    f["pct2m"] = (c / shift(c, 2) - 1) * 100
    f["pct5m"] = (c / shift(c, 5) - 1) * 100

    # --- quiet shelf baseline (scanner: median/stdev of sampled d1m over ~20 min)
    # NB: roll first, shift after — the cumsum-based rolls would propagate a
    # leading NaN through the whole array if we shifted first.
    med = shift(_roll_median(qv, quiet_n), 1)
    std = shift(_roll_std(qv, quiet_n), 1)
    f["qv_med20"] = med
    f["qv_std20"] = std
    with np.errstate(invalid="ignore", divide="ignore"):
        # relative dispersion of the shelf: low -> genuinely flat, sleepy tape
        f["shelf_cv"] = np.where(med > 0, std / med, np.nan)
        # volume anomaly z-score against the symbol's own recent baseline
        f["vol_z"] = np.where(std > 0, (qv - med) / std, np.nan)
        # robust multiple over the shelf
        f["vol_mult"] = np.where(med > 0, qv / med, np.nan)

    # --- 15m local structure (scanner localStructure())
    hi15 = shift(_roll_max(h, struct_n), 1)     # prior 15 bars, excluding current
    lo15 = shift(_roll_min(l, struct_n), 1)
    f["hi15"] = hi15
    f["lo15"] = lo15
    with np.errstate(invalid="ignore", divide="ignore"):
        f["range15"] = np.where(lo15 > 0, (hi15 - lo15) / lo15 * 100, np.nan)
        f["break15"] = (c >= hi15 * 1.0025).astype(float)

    # 60m range too: a wider view of "dead" tape
    hi60 = shift(_roll_max(h, 60), 1)
    lo60 = shift(_roll_min(l, 60), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        f["range60"] = np.where(lo60 > 0, (hi60 - lo60) / lo60 * 100, np.nan)

    # A fixed % range threshold means completely different things for a sleepy
    # $1M/day pair and a $80M/day one, so also express the range RELATIVE to what
    # this symbol's own last 24h looked like.
    with np.errstate(invalid="ignore", divide="ignore"):
        r15_day = shift(_roll_mean(np.nan_to_num(f["range15"], nan=0.0), DAY), 1)
        f["range15_rel"] = np.where(r15_day > 0, f["range15"] / r15_day, np.nan)
        r60_day = shift(_roll_mean(np.nan_to_num(f["range60"], nan=0.0), DAY), 1)
        f["range60_rel"] = np.where(r60_day > 0, f["range60"] / r60_day, np.nan)

    # --- taker buy pressure
    with np.errstate(invalid="ignore", divide="ignore"):
        taker = np.where(qv > 0, tbq / qv, np.nan)
    f["taker"] = taker
    # rolling *volume-weighted* taker ratio over the accumulation window
    tb_sum = _roll_sum(tbq, accum_n)
    qv_sum = _roll_sum(qv, accum_n)
    with np.errstate(invalid="ignore", divide="ignore"):
        f["taker_w"] = np.where(qv_sum > 0, tb_sum / qv_sum, np.nan)
        # OBV-style net taker flow over the window, normalised by the symbol's own minute
        f["net_flow_x"] = np.where(avg_min > 0, (2 * tb_sum - qv_sum) / (avg_min * accum_n), np.nan)
    # persistence: share of bars in the window with taker ratio above 0.55
    up = (taker > 0.55).astype(float)
    up[np.isnan(taker)] = np.nan
    f["taker_frac"] = _roll_mean(np.nan_to_num(up, nan=0.0), accum_n)

    # --- average trade size vs its own 20-bar median
    with np.errstate(invalid="ignore", divide="ignore"):
        ats = np.where(nt > 0, qv / nt, np.nan)
        atsmed = shift(_roll_median(np.nan_to_num(ats, nan=0.0), quiet_n), 1)
        f["ats_x"] = np.where(atsmed > 0, ats / atsmed, np.nan)

    # --- day range position / VWAP deviation (scanner uses 24h ticker fields)
    dhigh = shift(_roll_max(h, DAY), 1)
    dlow = shift(_roll_min(l, DAY), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        f["rng"] = np.where(dhigh > dlow, (c - dlow) / (dhigh - dlow), np.nan)
        vol_base = _roll_sum(qv, DAY)
        # vwap ~ sum(quote)/sum(base); base volume = qv/typical price, approximate with close
        f["vwap_dev"] = np.where(dhigh > 0, (c / shift(_roll_mean(c, DAY), 1) - 1) * 100, np.nan)
        f["dist_hi24"] = np.where(dhigh > 0, (c / dhigh - 1) * 100, np.nan)
    return f


def forward_max_gain(c, h, horizon):
    """Max % gain achievable over the next `horizon` bars (uses future highs —
    only ever used for labelling outcomes, never as a signal input)."""
    n = len(c)
    fut = np.full(n, np.nan)
    if n > horizon:
        v = np.lib.stride_tricks.sliding_window_view(h[1:], horizon)
        fut[:n - horizon] = v.max(axis=1)
    return (fut / c - 1) * 100


def forward_min_draw(c, l, horizon):
    n = len(c)
    fut = np.full(n, np.nan)
    if n > horizon:
        v = np.lib.stride_tricks.sliding_window_view(l[1:], horizon)
        fut[:n - horizon] = v.min(axis=1)
    return (fut / c - 1) * 100


def forward_close(c, horizon):
    return (shift(c, -horizon) / c - 1) * 100 if False else (
        np.concatenate((c[horizon:], np.full(horizon, np.nan))) / c - 1) * 100


def build_extra(d, f, windows=(60, 240)):
    """Longer-horizon accumulation / compression views. The 20-minute window the
    brief suggested turned out to be too short to mean anything, so the study
    also looks at 1h and 4h versions of the same idea."""
    qv, tbq, h, l, c = d["qv"], d["tbq"], d["high"], d["low"], d["close"]
    for w in windows:
        tb = _roll_sum(tbq, w)
        qs = _roll_sum(qv, w)
        with np.errstate(invalid="ignore", divide="ignore"):
            f[f"taker_w{w}"] = np.where(qs > 0, tb / qs, np.nan)
            f[f"net_flow_x{w}"] = np.where(f["avg_min"] > 0,
                                           (2 * tb - qs) / (f["avg_min"] * w), np.nan)
        hiw = shift(_roll_max(h, w), 1)
        low = shift(_roll_min(l, w), 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            rng = np.where(low > 0, (hiw - low) / low * 100, np.nan)
            f[f"range{w}"] = rng
            day = shift(_roll_mean(np.nan_to_num(rng, nan=0.0), DAY), 1)
            f[f"range{w}_rel"] = np.where(day > 0, rng / day, np.nan)
            # where in its own w-minute range is price sitting right now
            f[f"pos{w}"] = np.where(hiw > low, (c - low) / (hiw - low), np.nan)
    return f
