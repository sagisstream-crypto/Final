"""'Vynos' exhaustion fade (ignition bar that completes an extreme 1h move).
Short: fade an alt pump bar (volume burst, bar >1 ATR up) that finishes a >X ATR 1h rise and a >D% 24h rise,
       while taker-buy share of the last 3 bars is weak (<52%: pump not backed by aggressive buyers).
Long : fade a dump bar with the same shape ONLY when the whole alt market dumps at the same time
       (cross-sectional median 1h move of all alts < -BMIN ATR) = capitulation bounce.
Alt-only dumps without market-wide confirmation keep going, so they are NOT bought.
Signal at close of bar i -> entry next open. TP/SL scaled by prior 4h ATR.
"""
import os, glob, numpy as np, pandas as pd
from lib import load, symbols

P = dict(s_x12=6, s_v=3, s_d=0.10, s_imb3=0.52, l_x12=8, l_v=10, l_d=0.05, bmin=4.0,
         tp_s=2.0, tp_l=2.0, sl=12.0)
HOLD = 48

def _base(df):
    s = pd.Series
    o, h, l, c, qv, tb = (df[x].values for x in "o h l c qv tbqv".split())
    pc = s(c).shift(1).values
    tr = np.maximum(h, pc) - np.minimum(l, pc); tr[0] = h[0] - l[0]
    atr = s(tr).shift(1).rolling(48).mean().values
    vmed = s(qv).shift(1).rolling(288).median().values
    with np.errstate(all="ignore"):
        volr = qv / (vmed + 1e-9)
        r1 = (c / o - 1) / (atr / c)
        r12 = (c / s(c).shift(12).values - 1) / (atr / c)
        r288 = c / s(c).shift(288).values - 1
        imb3 = s(tb).rolling(3).sum().values / (s(qv).rolling(3).sum().values + 1e-9)
    return atr / c, volr, r1, r12, r288, imb3

_BR = None
def breadth():
    """cross-sectional median of r12 over all alt files (spot+fut) per bar open time."""
    global _BR
    if _BR is None:
        parts = []
        for m, sym in symbols():
            df = load(m, sym); r12 = _base(df)[3]
            parts.append(pd.DataFrame({"ot": df.ot.values, "r12": r12}))
        A = pd.concat(parts); A = A[np.isfinite(A.r12)]
        _BR = A.groupby("ot").r12.median()
    return _BR

def signal(df, p=None):
    p = p or P
    atrp, volr, r1, r12, r288, imb3 = _base(df)
    br = breadth().reindex(df.ot.values).values
    ok = np.isfinite(atrp) & (atrp > 0.001) & np.isfinite(r12) & np.isfinite(r288)
    with np.errstate(all="ignore"):
        short = ok & (r1 > 1) & (r12 > p["s_x12"]) & (volr > p["s_v"]) & (r288 > p["s_d"]) & (imb3 < p.get("s_imb3", 9))
        long_ = ok & (r1 < -1) & (r12 < -p["l_x12"]) & (volr > p["l_v"]) & (r288 < -p["l_d"]) & (br < -p["bmin"])
    sig = short | long_
    side = np.where(long_, 1, -1)
    tp = np.where(long_, p["tp_l"], p["tp_s"]) * np.nan_to_num(atrp, nan=0.01)
    sl = p["sl"] * np.nan_to_num(atrp, nan=0.01)
    tp = np.maximum(tp, 1e-4); sl = np.maximum(sl, 1e-4)
    return sig, side, tp, sl
