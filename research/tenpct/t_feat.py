"""Build multi-timeframe technical features for every (mkt,sym) hourly row -> t_feat.parquet (float32)."""
import numpy as np, pandas as pd, lab10, sys, multiprocessing as mp

def feats(ms):
    mkt, sym = ms
    H = lab10.load(mkt, sym)
    if len(H) < 24 * 20: return None
    c = H.c; h = H.h; l = H.l; qv = H.qv
    lc = np.log(c)
    F = pd.DataFrame({"ot": H.ot.values})
    for k, w in (("r1", 1), ("r4", 4), ("r24", 24), ("r3d", 72), ("r7d", 168), ("r14d", 336), ("r30d", 720)):
        F[k] = (lc - lc.shift(w)).values
    for k, w in (("7", 168), ("30", 720), ("90", 2160)):
        hh = h.rolling(w, min_periods=w // 2).max(); ll = l.rolling(w, min_periods=w // 2).min()
        F["dh" + k] = np.log(c / hh).values; F["dl" + k] = np.log(c / ll).values
        # breakout vs prior window (excluding last 24h)
        F["bo" + k] = np.log(c / h.shift(24).rolling(w, min_periods=w // 2).max()).values
        F["bd" + k] = np.log(c / l.shift(24).rolling(w, min_periods=w // 2).min()).values
    F["dlist"] = (lc - lc.iloc[0]).values
    F["age"] = (np.arange(len(H)) / 24.0)
    for k, s in (("e24", 24), ("e72", 72), ("e168", 168), ("e720", 720)):
        e = c.ewm(span=s, adjust=False).mean()
        F["c_" + k] = np.log(c / e).values
        F["s_" + k] = np.log(e / e.shift(24)).values
    r = lc.diff()
    v24 = r.rolling(24, min_periods=12).std(); v168 = r.rolling(168, min_periods=84).std(); v720 = r.rolling(720, min_periods=300).std()
    F["v24"] = v24.values; F["v168"] = v168.values; F["v720"] = v720.values
    F["vc"] = np.log(v24 / v168).values; F["vc2"] = np.log(v168 / v720).values
    rng7 = np.log(h.rolling(168).max() / l.rolling(168).min()); rng30 = np.log(h.rolling(720).max() / l.rolling(720).min())
    F["rng7"] = rng7.values; F["rngc"] = (rng7 / rng30).values
    q24 = qv.rolling(24, min_periods=12).sum(); q30 = qv.rolling(720, min_periods=300).mean() * 24
    F["lq24"] = np.log1p(q24).values
    F["vs24"] = np.log(q24 / q30).values
    F["vs1"] = np.log(qv / qv.rolling(168, min_periods=84).mean()).values
    F["vs7"] = np.log(qv.rolling(168, min_periods=84).sum() / 7 / q30).values
    F["tb1"] = (H.tbqv / qv).values
    F["tb24"] = (H.tbqv.rolling(24).sum() / q24).values
    F["tb7"] = (H.tbqv.rolling(168).sum() / qv.rolling(168).sum()).values
    # last 24h candle structure
    h24 = h.rolling(24).max(); l24 = l.rolling(24).min(); o24 = H.o.shift(23)
    rg = (h24 - l24)
    F["uw24"] = ((h24 - np.maximum(c, o24)) / rg).values
    F["lw24"] = ((np.minimum(c, o24) - l24) / rg).values
    F["pos24"] = ((c - l24) / rg).values
    # consecutive daily (24h-block) up closes
    d = np.sign(lc - lc.shift(24)).values
    cu = np.zeros(len(H)); cd = np.zeros(len(H))
    for j in range(1, 8):
        dj = np.sign(lc.shift(24 * (j - 1)) - lc.shift(24 * j)).values
        if j == 1: au = dj > 0; ad = dj < 0
        else: au = au & (dj > 0); ad = ad & (dj < 0)
        cu += au; cd += ad
    F["cup"] = cu; F["cdn"] = cd
    # higher highs: 7d-high vs prior 7d-high
    F["hh"] = np.log(h.rolling(168).max() / h.shift(168).rolling(168).max()).values
    F["ll"] = np.log(l.rolling(168).min() / l.shift(168).rolling(168).min()).values
    F["hr"] = ((H.ot.values // 3600000) % 24)
    F = F.astype(np.float32); F["ot"] = H.ot.values
    F["lab_long"] = H.lab_long.values.astype(np.float32)
    F["up_t"] = H.up_t.values.astype(np.float32); F["dn_t"] = H.dn_t.values.astype(np.float32)
    F["mkt"] = mkt; F["sym"] = sym
    F = F.iloc[168:]  # need a week of history
    return F

if __name__ == "__main__":
    S = lab10.all_syms()
    out = []
    with mp.Pool(2) as p:
        for i, F in enumerate(p.imap(feats, S, chunksize=8)):
            if F is not None: out.append(F)
            if i % 100 == 0: print(i, flush=True)
    A = pd.concat(out, ignore_index=True)
    A["mkt"] = A.mkt.astype("category"); A["sym"] = A.sym.astype("category")
    # market breadth features
    g = A.groupby("ot")
    A["m_r24"] = g.r24.transform("median").astype(np.float32)
    A["m_r7d"] = g.r7d.transform("median").astype(np.float32)
    A.to_parquet("t_feat.parquet")
    print(A.shape)
