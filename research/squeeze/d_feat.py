"""Build hourly derivatives-positioning features for every fut symbol -> data_extra_d/feat.parquet
All features are known at the CLOSE of hour t (entry time used by lab10 labels)."""
import os, numpy as np, pandas as pd, lab10
D = os.path.join(lab10.HERE, "data_extra_d")
HR = 3600000

def rd(p):
    return pd.read_parquet(p) if os.path.exists(p) else None

def load_fund(s):
    """monthly archive + Sep-2026 from www.binance.com/fapi (interval ih inferred from settlement spacing)."""
    f = rd(f"{D}/fund_{s}.parquet"); g = rd(f"{D}/fundsep_{s}.parquet")
    if f is None or not len(f): f = pd.DataFrame({"t": [], "ih": [], "fr": []})
    if g is not None and len(g):
        g = g[g.t > (f.t.max() if len(f) else 0)].copy()
        if len(g):
            g = g.sort_values("t"); dh = np.round(np.diff(np.r_[f.t.max() if len(f) else g.t.iloc[0] - 8 * HR, g.t.values]) / HR)
            g["ih"] = np.where(np.isin(dh, [1, 2, 4, 8]), dh, np.nan); g["ih"] = g.ih.ffill().fillna(f.ih.iloc[-1] if len(f) else 8)
            f = pd.concat([f, g[["t", "ih", "fr"]]], ignore_index=True)
    f["t"] = f.t.astype(np.int64)
    return f

def feats(s):
    H = lab10.load("fut", s)
    ot = H.ot.values
    c = H.c.values
    out = pd.DataFrame({"ot": ot})
    out["sym"] = s
    lc = np.log(c)
    for k in (1, 4, 24, 72, 168):
        out[f"r{k}"] = lc - np.r_[np.full(k, np.nan), lc[:-k]]
    rng = (np.log(H.h) - np.log(H.l)).rolling(24, min_periods=12).mean().values
    out["vol24"] = rng
    qv = H.qv.values
    out["qvr"] = pd.Series(qv).rolling(24, min_periods=12).mean().values / pd.Series(qv).rolling(24 * 14, min_periods=48).mean().values
    out["tbr24"] = (H.tbqv.rolling(24, min_periods=12).sum() / H.qv.rolling(24, min_periods=12).sum()).values
    out["qv24"] = pd.Series(qv).rolling(24, min_periods=12).sum().values
    # funding: realized rate known at calc_time (normalised to 8h). hour ot close = ot+HR
    f = load_fund(s)
    if f is not None and len(f):
        f = f.sort_values("t"); fr8 = (f.fr * 8 / f.ih.clip(lower=1)).values
        idx = np.searchsorted(f.t.values, ot + HR, side="right") - 1
        v = np.where(idx >= 0, fr8[np.maximum(idx, 0)], np.nan)
        stale = (ot + HR) - np.where(idx >= 0, f.t.values[np.maximum(idx, 0)], 0) > 9 * HR
        out["fr"] = np.where(stale, np.nan, v)
        # cumulative funding over last 3 days (sum of settlements)
        cs = np.r_[0, np.cumsum(f.fr.values)]
        idx3 = np.searchsorted(f.t.values, ot + HR - 72 * HR, side="right")
        out["fr3d"] = np.where(stale, np.nan, cs[idx + 1] - cs[idx3])
        out["ih"] = np.where(idx >= 0, f.ih.values[np.maximum(idx, 0)], np.nan)
    p = rd(f"{D}/prem_{s}.parquet")
    if p is not None and len(p):
        p = p.set_index("ot").reindex(ot)
        out["prem"] = p.pc.values
        out["prem8"] = p.pc.rolling(8, min_periods=4).mean().values
        out["prem24"] = p.pc.rolling(24, min_periods=12).mean().values
    m = rd(f"{D}/met_{s}.parquet")
    if m is not None and len(m):
        m = m.set_index("ot").reindex(ot)
        loi = np.log(m.oi.values)
        for k in (1, 4, 24, 72):
            out[f"oi{k}"] = loi - np.r_[np.full(k, np.nan), loi[:-k]]
        out["oiv"] = m.oiv.values
        for cc in ("tt_acc", "tt_pos", "gl_acc"):
            x = np.log(m[cc].values); out[cc] = x
            s_ = pd.Series(x)
            mu = s_.rolling(24 * 14, min_periods=72).mean(); sd = s_.rolling(24 * 14, min_periods=72).std()
            out[cc + "_z"] = ((s_ - mu) / sd).values
            out[cc + "_d24"] = x - np.r_[np.full(24, np.nan), x[:-24]]
        out["taker24"] = np.log(m.taker).rolling(24, min_periods=12).mean().values
        out["oi_to_vol"] = m.oiv.values / out["qv24"].values
    sp = f"{lab10.DST}/spot_{s}.parquet"
    if os.path.exists(sp):
        S = pd.read_parquet(sp, columns=["ot", "c"]).set_index("ot").reindex(ot)
        out["basis"] = c / S.c.values - 1
    for col in ("lab_long", "lab_short", "up_t", "dn_t"):
        out[col] = H[col].values
    for col in out.columns:
        if out[col].dtype == np.float64: out[col] = out[col].astype(np.float32)
    return out

if __name__ == "__main__":
    L = [s for m, s in lab10.all_syms() if m == "fut"]
    fs = [feats(s) for s in L]
    F = pd.concat(fs, ignore_index=True)
    F.to_parquet(f"{D}/feat.parquet")
    print(F.shape); print(F.notna().mean().round(3).to_string())
