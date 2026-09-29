"""Long-only 'vynos' (stop-hunt) reclaim with confirmation.
Bar i  (sweep): low[i] < min(low[i-48..i-1]) (4h low swept) and close[i] > that 4h low (closed back inside).
         No prior dump: (close[i]/close[i-12]-1) / ATRp > RET12_MIN   (ATRp = 48-bar mean true range / close, known before bar i)
Bar i+1 (confirmation): signal at close of i+1 if bounce off the sweep extreme
         w = close[i+1]/low[i] - 1  in [W_LO, W_HI).
Entry: open of bar i+2 (harness).  TP = TPK*w, SL = SLK*w (fractions of entry), HOLD bars max.
"""
import numpy as np, pandas as pd

MIN_BARS = 2016  # need 7 days of history (skips warm-up / freshly listed symbols)
N = 48; W_LO = 0.015; W_HI = 0.10; RET12_MIN = -2.0; VOLK = 0.0; H_FROM = 12; TPK = 0.5; SLK = 2.0; HOLD = 48

def signal(df, N=N, W_LO=W_LO, W_HI=W_HI, RET12_MIN=RET12_MIN, VOLK=VOLK, H_FROM=H_FROM, TPK=TPK, SLK=SLK):
    h, l, c, qv = (df[x].values.astype(float) for x in ("h", "l", "c", "qv"))
    S = pd.Series
    pc = S(c).shift(1).values
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    atrp = S(tr).rolling(48).mean().shift(1).values / c
    lowN = S(l).shift(1).rolling(N).min().values
    with np.errstate(all='ignore'):
        ret12 = (c / S(c).shift(12).values - 1) / atrp
    vmed = S(qv).shift(1).rolling(288).median().values
    with np.errstate(all='ignore'):
        vr = qv / vmed
    sweep = (vr >= VOLK) & (l < lowN) & (c > lowN) & (ret12 > RET12_MIN)
    sw_prev = np.r_[False, sweep[:-1]]            # sweep happened on previous bar
    l_prev = np.r_[np.nan, l[:-1]]
    w = c / l_prev - 1                            # bounce from sweep extreme at confirmation close
    sig = sw_prev & (w >= W_LO) & (w < W_HI)
    sig = np.nan_to_num(sig, nan=0).astype(bool)
    sig[:MIN_BARS] = False
    hour = (df['ot'].values // 3600000) % 24     # UTC hour of the signal (confirmation) bar
    sig &= hour >= H_FROM
    w = np.where(np.isfinite(w) & (w > 0), w, 0.01)
    return sig, 1, TPK * w, SLK * w
