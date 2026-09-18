"""The sustained price+volume acceleration detector, and its calibration.

Shape of the rule (identical maths to volscan/engine.py, only the sample period
differs: 60s here because that is the resolution the historical archive offers,
15-20s in the live service):

  over a window of W consecutive samples
    * least-squares fit of price vs time      -> slope_p (%/sample), r2_p
    * least-squares fit of turnover vs time   -> slope_v (x avg-minute/sample), r2_v
    * the window is split into 3 equal blocks; the mean price AND the mean
      turnover must rise from block 1 -> 2 -> 3
    * total price gain across the window >= min_gain
    * current turnover >= rvol floor
  and the whole condition must hold for `persist` consecutive samples.

The r2 terms plus the 3-block monotonicity are what make a 1-2 sample blip
impossible to pass: a single spike at the end of an otherwise flat window fits a
straight line badly and leaves blocks 1 and 2 identical.
"""
import numpy as np


def _windows(a, w):
    return np.lib.stride_tricks.sliding_window_view(a, w)


def fit(a, w):
    """Rolling least-squares slope (per sample) and R^2 of `a` over window w.
    Returns arrays aligned so index i uses samples [i-w+1 .. i]."""
    n = len(a)
    slope = np.full(n, np.nan)
    r2 = np.full(n, np.nan)
    if n < w:
        return slope, r2
    v = _windows(a, w)
    x = np.arange(w, dtype=float)
    xm = x.mean()
    sxx = ((x - xm) ** 2).sum()
    ym = v.mean(axis=1)
    sxy = ((x - xm) * (v - ym[:, None])).sum(axis=1)
    s = sxy / sxx
    pred = ym[:, None] + s[:, None] * (x - xm)[None, :]
    ssres = ((v - pred) ** 2).sum(axis=1)
    sstot = ((v - ym[:, None]) ** 2).sum(axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        rr = np.where(sstot > 0, 1.0 - ssres / sstot, np.nan)
    slope[w - 1:] = s
    r2[w - 1:] = rr
    return slope, r2


def blocks_rising(a, w, strict_ratio=1.0):
    """True when the 3 equal blocks of the window have strictly rising means."""
    n = len(a)
    out = np.zeros(n, dtype=bool)
    if n < w:
        return out
    v = _windows(a, w)
    k = w // 3
    b1 = v[:, :k].mean(axis=1)
    b2 = v[:, k:2 * k].mean(axis=1)
    b3 = v[:, 2 * k:].mean(axis=1)
    out[w - 1:] = (b2 > b1 * strict_ratio) & (b3 > b2 * strict_ratio)
    return out


def accel_state(close, qv, avg_min, w=10, min_r2_p=0.55, min_r2_v=0.30,
                min_gain=1.0, min_rvol=8.0, min_slope_v=0.4):
    """Per-sample boolean: is this pair in a sustained price+volume ramp?"""
    n = len(close)
    with np.errstate(invalid="ignore", divide="ignore"):
        rel_p = close / np.where(avg_min > 0, 1.0, 1.0)
        vx = np.where(avg_min > 0, qv / avg_min, np.nan)     # turnover in RVOL units
    sp, r2p = fit(np.log(np.maximum(close, 1e-12)), w)
    sv, r2v = fit(np.nan_to_num(vx, nan=0.0), w)
    gain = np.full(n, np.nan)
    gain[w - 1:] = (close[w - 1:] / close[:n - w + 1] - 1.0) * 100
    rise_p = blocks_rising(close, w)
    rise_v = blocks_rising(np.nan_to_num(vx, nan=0.0), w)
    state = ((sp > 0) & (r2p >= min_r2_p)
             & (sv > 0) & (r2v >= min_r2_v) & (sv >= min_slope_v)
             & rise_p & rise_v
             & (gain >= min_gain)
             & (np.nan_to_num(vx, nan=0) >= min_rvol))
    return dict(state=state, slope_p=sp, r2_p=r2p, slope_v=sv, r2_v=r2v,
                gain=gain, vx=vx, rise_p=rise_p, rise_v=rise_v)


def persist(state, k):
    """Require the state to hold for k consecutive samples before firing."""
    if k <= 1:
        return state.copy()
    out = state.copy()
    for i in range(1, k):
        out[i:] &= state[:-i]
        out[:i] = False
    return out
