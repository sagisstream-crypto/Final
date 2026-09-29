"""Point-in-time feature builder for '+10% before -10%' ML study. Output: m_feat.parquet (every 4h per symbol)."""
import numpy as np, pandas as pd, multiprocessing as mp, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import lab10
STEP = 4  # hours
HERE = os.path.dirname(os.path.abspath(__file__))

def sym_feats(H, mkt):
    c = H.c.values; lc = np.log(c)
    s = pd.Series(lc)
    lr = s.diff()
    F = {}
    for k in (1, 4, 24, 72, 168, 720):
        F[f"r{k}"] = s - s.shift(k)
    for w in (24, 72, 168, 720):
        F[f"vol{w}"] = lr.rolling(w, min_periods=w // 2).std()
    v24 = F["vol24"]
    F["vov"] = v24.rolling(168, min_periods=84).std() / F["vol168"]
    F["volratio"] = F["vol24"] / F["vol720"]
    F["volratio2"] = F["vol72"] / F["vol720"]
    hh = pd.Series(np.log(H.h.values)); ll = pd.Series(np.log(H.l.values))
    F["rng24"] = hh.rolling(24, min_periods=12).max() - ll.rolling(24, min_periods=12).min()
    for w in (168, 720):
        F[f"dhi{w}"] = s - hh.rolling(w, min_periods=24).max()
        F[f"dlo{w}"] = s - ll.rolling(w, min_periods=24).min()
    F["dath"] = s - hh.cummax()
    F["datl"] = s - ll.cummin()
    lq = pd.Series(np.log1p(H.qv.values))
    F["lqv24"] = np.log1p(pd.Series(H.qv.values).rolling(24, min_periods=12).sum())
    F["qvz1"] = (lq - lq.rolling(168, min_periods=48).mean()) / lq.rolling(168, min_periods=48).std()
    q24 = F["lqv24"]
    F["qvz24"] = (q24 - q24.rolling(720, min_periods=168).mean()) / q24.rolling(720, min_periods=168).std()
    F["qv24_168"] = q24 - np.log1p(pd.Series(H.qv.values).rolling(168, min_periods=48).sum() / 7)
    qv = pd.Series(H.qv.values); tb = pd.Series(H.tbqv.values)
    F["tb1"] = tb / qv.replace(0, np.nan)
    F["tb24"] = tb.rolling(24).sum() / qv.rolling(24).sum().replace(0, np.nan)
    F["tb168"] = tb.rolling(168).sum() / qv.rolling(168).sum().replace(0, np.nan)
    F["tbd"] = F["tb24"] - F["tb168"]
    F["tsize"] = np.log1p(qv.rolling(24).sum() / pd.Series(H.n.values).rolling(24).sum().replace(0, np.nan))
    F["age"] = pd.Series(np.arange(len(H), dtype=float))
    F["lprice"] = s
    # barrier distance in vol units
    F["bvol"] = 0.10 / (F["vol168"] * np.sqrt(24))
    X = pd.DataFrame(F)
    X["ot"] = H.ot.values
    X["spot"] = int(mkt == "spot")
    X["lab_long"] = H.lab_long.values
    X["up_t"] = H.up_t.values; X["dn_t"] = H.dn_t.values
    return X

def one(ms):
    mkt, sym = ms
    H = lab10.load(mkt, sym)
    X = sym_feats(H, mkt)
    keep = (X.ot // 3600000) % STEP == 0
    X = X[keep & (X.age >= 24)].copy()
    X["sym"] = f"{mkt}_{sym}"
    return X.astype({c: "float32" for c in X.columns if X[c].dtype == np.float64 and c not in ("up_t", "dn_t")})

def build():
    syms = lab10.all_syms()
    with mp.Pool(2) as p:
        parts = p.map(one, syms, chunksize=8)
    D = pd.concat(parts, ignore_index=True)
    # market-wide & cross-sectional at same hour
    g = D.groupby("ot")
    for k in ("r1", "r4", "r24", "r168"):
        D[f"m_{k}"] = g[k].transform("mean")
        D[f"x_{k}"] = D[k] - D[f"m_{k}"]
        D[f"rk_{k}"] = g[k].rank(pct=True)
    D["m_br24"] = g["r24"].transform(lambda x: (x > 0).mean())
    D["m_br168"] = g["r168"].transform(lambda x: (x > 0).mean())
    D["m_vol24"] = g["vol24"].transform("median")
    D["m_n"] = g["r1"].transform("size")
    for k in ("vol72", "lqv24", "dhi720", "qvz24", "tb24", "age"):
        D[f"rk_{k}"] = g[k].rank(pct=True)
    D["hod"] = (D.ot // 3600000) % 24
    D["dow"] = (D.ot // 86400000 + 3) % 7
    D["sym"] = D["sym"].astype("category")
    D.to_parquet(f"{HERE}/m_feat.parquet")
    print(D.shape)

if __name__ == "__main__":
    build()
