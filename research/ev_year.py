"""Event table: every 4h-low sweep + next-bar confirmation (loose), with features and outcomes on an exit grid.
Outcome convention = lib.simulate: entry open[k+1], bars k+1..k+HOLD, same-bar TP&SL -> SL, fee 0.1%."""
import glob, os, sys, numpy as np, pandas as pd, multiprocessing as mp
HOLD = 48; FEE = 0.001
TPK = [0.3, 0.5, 0.75, 1.0, 1.5]; SLK = [1.0, 1.5, 2.0, 3.0]
def feats(path):
    mkt, sym = os.path.basename(path)[:-8].split("_", 1)
    df = pd.read_parquet(path); n = len(df)
    o, h, l, c, qv, ot, nt, tb = (df[x].values for x in ("o","h","l","c","qv","ot","n","tbqv"))
    S = pd.Series
    pc = S(c).shift(1).values
    tr = np.nanmax(np.vstack([h - l, np.abs(h - pc), np.abs(l - pc)]), axis=0)
    atrp = S(tr).rolling(48).mean().shift(1).values / c
    lowN = S(l).shift(1).rolling(48).min().values
    low288 = S(l).shift(1).rolling(288).min().values
    qv24 = S(qv).shift(1).rolling(288, min_periods=200).sum().values
    vmed = S(qv).shift(1).rolling(288, min_periods=200).median().values
    with np.errstate(all="ignore"):
        ret12 = (c / S(c).shift(12).values - 1) / atrp
        ret288 = c / S(c).shift(288).values - 1
        sweep = (l < lowN) & (c > lowN)
    ev = []
    for i in np.flatnonzero(np.nan_to_num(sweep).astype(bool)):
        k = i + 1
        if k + 1 >= n or i < 300: continue
        w = c[k] / l[i] - 1
        if not (0.01 <= w < 0.15): continue
        e = o[k + 1]; end = min(n - 1, k + HOLD)
        if k + HOLD > n - 1: continue
        hh = h[k+1:end+1]; ll = l[k+1:end+1]
        row = dict(mkt=mkt, sym=sym, ot=ot[k], k=k, age=k, w=w, ret12=ret12[i], ret288=ret288[i], atrp=atrp[i],
                   depth=(lowN[i] - l[i]) / (atrp[i] * c[i]), sw24=l[i] < low288[i], qv24=qv24[i], vr=qv[i] / vmed[i],
                   vrk=qv[k] / vmed[i], tbr=tb[k] / qv[k] if qv[k] > 0 else np.nan, hour=(ot[k] // 3600000) % 24,
                   gap=(e / c[k] - 1), entry=e)
        for tk in TPK:
            for sk in SLK:
                tp, sl = tk * w, sk * w
                slh = np.flatnonzero(ll <= e * (1 - sl)); tph = np.flatnonzero(hh >= e * (1 + tp))
                a = slh[0] if len(slh) else 10**9; b = tph[0] if len(tph) else 10**9
                if a <= b and a < 10**9: r, x = -sl, a
                elif b < 10**9: r, x = tp, b
                else: r, x = c[end] / e - 1, len(hh) - 1
                row[f"r_{tk}_{sk}"] = r - FEE; row[f"x_{tk}_{sk}"] = k + 1 + x
        ev.append(row)
    return ev
if __name__ == "__main__":
    fs = sorted(glob.glob(sys.argv[1] + "/*.parquet"))
    with mp.Pool(4) as p: rows = [r for rs in p.imap_unordered(feats, fs, chunksize=4) for r in rs]
    ev = pd.DataFrame(rows); ev.to_parquet(sys.argv[2]); print(len(ev), ev.sym.nunique())
