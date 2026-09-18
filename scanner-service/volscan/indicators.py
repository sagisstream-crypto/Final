"""Pure, dependency-free numeric helpers.

Everything here is a plain function over plain lists so it can be unit-tested
without a websocket, a database or an event loop. These are the direct
translations of the helpers that used to live inside the browser build.
"""
from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence


def median(values: Sequence[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return None
    vals.sort()
    mid = len(vals) // 2
    if len(vals) % 2:
        return vals[mid]
    return (vals[mid - 1] + vals[mid]) / 2.0


def mean(values: Sequence[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if not vals:
        return None
    return sum(vals) / len(vals)


def stdev(values: Sequence[float]) -> Optional[float]:
    vals = [v for v in values if v is not None and not math.isnan(v)]
    if len(vals) < 2:
        return None
    m = sum(vals) / len(vals)
    var = sum((v - m) ** 2 for v in vals) / len(vals)
    return math.sqrt(var)


def zscore(value: float, baseline: Sequence[float], floor: float = 0.0) -> Optional[float]:
    """Anomaly score of `value` against its own recent baseline.

    `floor` is what keeps this honest: on a dead pair the baseline median and
    stdev collapse towards zero and a single $200 trade would otherwise score
    z = 300. Calibration showed the unfloored z-score to be *anti*-predictive
    for exactly that reason, so callers pass a floor derived from the symbol's
    own average minute of turnover.
    """
    med = median(baseline)
    sd = stdev(baseline)
    if med is None or sd is None:
        return None
    denom = max(sd, floor)
    if denom <= 0:
        return None
    return (value - med) / denom


def lin_reg_slope_per_min(points: Sequence[tuple], now_ms: float) -> Optional[float]:
    """Least-squares slope of cumulative volume over time, in $/minute.

    A straight-line fit stays stable when the previous window was flat, unlike
    the ratio-of-windows acceleration it replaced.
    """
    pts = [(t, v) for t, v in points]
    if len(pts) < 4:
        return None
    n = len(pts)
    sx = sy = sxy = sxx = 0.0
    for t, v in pts:
        x = (t - now_ms) / 1000.0
        sx += x
        sy += v
        sxy += x * v
        sxx += x * x
    denom = n * sxx - sx * sx
    if denom == 0:
        return None
    return ((n * sxy - sx * sy) / denom) * 60.0


def find_anchor(samples: Sequence, now_ms: float, window_ms: float,
                key=lambda s: s[0]):
    """Sample closest to `window_ms` old (not merely the oldest one past it)."""
    best = None
    best_diff = float("inf")
    for s in samples:
        age = now_ms - key(s)
        if age < window_ms * 0.55:
            continue
        diff = abs(age - window_ms)
        if diff < best_diff:
            best_diff = diff
            best = s
    if best is None:
        for s in samples:
            if now_ms - key(s) >= window_ms:
                best = s
    return best


def pct_change(old: Optional[float], new: Optional[float]) -> Optional[float]:
    if old is None or new is None or old <= 0:
        return None
    return (new - old) / old * 100.0


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def fmt_money(n: Optional[float]) -> str:
    if n is None or (isinstance(n, float) and math.isnan(n)):
        return "—"
    a = abs(n)
    if a >= 1e9:
        return f"{n / 1e9:.2f}B"
    if a >= 1e6:
        return f"{n / 1e6:.2f}M"
    if a >= 1e3:
        return f"{n / 1e3:.1f}K"
    return f"{n:.0f}"
