"""Pump-fade short on Binance SPOT alt pairs.
Signal at close of hour t (spot rows only): max(7d log-return, 14d log-return) > X  AND  last-4h log-return < 0  -> SHORT.
(log returns on hourly closes: r7d = ln(c_t/c_{t-168}), r14d = ln(c_t/c_{t-336}), r4 = ln(c_t/c_{t-4}))"""
import numpy as np
X = 0.5
def signal(H, mkt="spot", X=X):
    lc = np.log(H.c.values.astype(float))
    def lag(k):
        o = np.full(len(lc), np.nan); o[k:] = lc[k:] - lc[:-k]; return o
    pk = np.fmax(lag(168), lag(336))
    m = (pk > X) & (lag(4) < 0)
    if mkt != "spot": m[:] = False
    return m, -np.ones(len(lc), int)
