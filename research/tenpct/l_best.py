"""l_best: FUTURES-DISCOUNT SHORT (spot-vs-futures basis).
Rule (USDT-M futures leg only, side = -1 short):
    prem_t  = fut_close_t / spot_close_t - 1          (same symbol, same hour close)
    p24_t   = mean(prem over last 24 hourly bars, >=12 present)
    signal  = p24_t < -0.6%  AND  |prem_t - p24_t| < 1%   (persistent, non-glitchy discount)
Entry at close of hour t (lab10 convention), trades non-overlapping per symbol.
EXTRA DATA: needs the SPOT hourly bars of the same symbol -> read via lab10.load('spot', sym) from h10/.
Symbols with no spot counterpart (or spot markets) return an all-False mask.
Caveat: a persistent negative basis usually coincides with negative funding (shorts pay); funding is not in the labels.
"""
import os, numpy as np, pandas as pd, lab10

THR, DEV = -0.006, 0.01

def signal(H, mkt, sym):
    mask = np.zeros(len(H), bool)
    if mkt != "fut" or not os.path.exists(f"{lab10.DST}/spot_{sym}.parquet"):
        return mask, -1
    S = pd.read_parquet(f"{lab10.DST}/spot_{sym}.parquet", columns=["ot", "c"]).set_index("ot").c
    f = H.set_index("ot").c
    grid = np.arange(min(f.index.min(), S.index.min()), max(f.index.max(), S.index.max()) + 1, 3600000)
    prem = (f.reindex(grid) / S.reindex(grid) - 1)
    p24 = prem.rolling(24, min_periods=12).mean()
    sig = (p24 < THR) & ((prem - p24).abs() < DEV)
    mask = sig.reindex(H.ot.values).fillna(False).values.astype(bool)
    return mask, -1
