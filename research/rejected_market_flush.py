"""'Вынос' = market-wide capitulation flush bounce (long only).
Signal at close of bar i (5m):
  1. flush depth: (min low of last 6 bars incl. i) is >= X ATR below the max high of the same 6 bars
     (ATR = 96-bar mean true range, lagged 1 bar);
  2. the flush low was printed 1-2 bars ago (not on bar i) -> bar i did not make a new low;
  3. bar i is green (c > o) and has retraced <= RMAX of the 6-bar high-low range;
  4. market-wide: equal-weight alt index (mean clipped 5m log-return over all loaded symbols)
     fell >= IX over the last 6 bars.
Entry next open e. TP = e + TPF*(hh6-ll6); SL = ll6 - SLA*ATR. Timeout HOLD bars.
"""
import numpy as np, pandas as pd
from lib import symbols, load
X, IX, RMAX, TPF, SLA, HOLD = 8.0, -0.010, 0.6, 0.25, 8.0, 48
_IDX = None
def market_index():
    global _IDX
    if _IDX is None:
        rs = {}
        for m, s in symbols():
            d = load(m, s); rs[(m, s)] = pd.Series(np.log(d.c.values), index=d.ot.values)
        lr = pd.DataFrame(rs).diff()
        _IDX = lr.clip(-0.1, 0.1).mean(axis=1)
    return _IDX
def make(X=X, IX=IX, RMAX=RMAX, TPF=TPF, SLA=SLA):
    def signal(df):
        o, h, l, c = (df[x].values for x in "ohlc"); S = pd.Series
        pc = np.r_[c[0], c[:-1]]
        atr = S(np.maximum(h, pc) - np.minimum(l, pc)).rolling(96).mean().shift(1).values
        hh = S(h).rolling(6).max().values; ll = S(l).rolling(6).min().values
        lowage = 5 - S(l).rolling(6).apply(np.argmin, raw=True).values
        ix = market_index().reindex(df.ot.values).fillna(0).values
        cix = np.cumsum(ix); ix6 = cix - np.r_[np.full(6, np.nan), cix[:-6]]
        with np.errstate(all="ignore"):
            depth = (hh - ll) / atr; retr = (c - ll) / (hh - ll)
            sig = (depth >= X) & (lowage >= 1) & (lowage <= 2) & (c > o) & (retr <= RMAX) & (ix6 <= IX)
            e = np.r_[o[1:], o[-1]]           # entry = next open (TP/SL are absolute levels set at fill)
            tp = np.maximum(TPF * (hh - ll) / e, 1e-4)
            sl = np.maximum((e - ll) / e, 0) + SLA * atr / e
        sig = np.nan_to_num(sig).astype(bool)
        return sig, 1, np.nan_to_num(tp, nan=0.01), np.nan_to_num(sl, nan=0.05)
    return signal
signal = make()
