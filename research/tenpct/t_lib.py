import numpy as np, pandas as pd
SPLIT = pd.Timestamp("2026-05-01", tz="UTC").value // 10**6
CAPMS = 14 * 86400000

def load(cols=None):
    A = pd.read_parquet("t_feat.parquet", columns=cols)
    return A

def select(A, mask, side):
    """non-overlapping trades per mkt+sym. A must be sorted by (mkt,sym,ot) with contiguous groups.
    side: scalar or array (+1/-1). returns DataFrame of trades."""
    side = np.broadcast_to(np.asarray(side), (len(A),))
    lab = A.lab_long.values
    m = np.asarray(mask) & ~np.isnan(lab)
    idx = np.flatnonzero(m)
    if len(idx) == 0: return pd.DataFrame()
    ot = A.ot.values; u = A.up_t.values.astype(np.float64); d = A.dn_t.values.astype(np.float64)
    res = np.fmin(u, d); res = np.where(np.isnan(res), CAPMS, res * 60000.0)
    endt = ot + res.astype(np.int64)
    key = A.sym.cat.codes.values.astype(np.int64) * 2 + A.mkt.cat.codes.values.astype(np.int64)
    k = key[idx]; t = ot[idx]; e = endt[idx]
    keep = []
    prev_k = -1; block = 0
    for j in range(len(idx)):
        if k[j] != prev_k:
            prev_k = k[j]; block = -1
        if t[j] >= block:
            keep.append(idx[j]); block = e[j] + 3600000 - 1  # next entry must be at/after resolution (hour granular)
            block = e[j]
    keep = np.array(keep)
    s = side[keep]
    uu = np.nan_to_num(u[keep], nan=np.inf); dd = np.nan_to_num(d[keep], nan=np.inf)
    win = np.where(s > 0, uu < dd, dd < uu)
    loss = np.where(s > 0, (dd <= uu) & np.isfinite(dd), (uu <= dd) & np.isfinite(uu))
    T = pd.DataFrame({"ot": ot[keep], "mkt": A.mkt.values[keep], "sym": A.sym.values[keep], "side": s,
                      "win": win.astype(int), "loss": loss.astype(int)})
    T["ret"] = 0.10 * (T.win - T.loss) - 0.001
    return T

def stats(T, label=""):
    if len(T) == 0: return {"n": 0}
    r = T.win.sum() + T.loss.sum()
    o = {"n": len(T), "res": int(r), "win": T.win.sum() / max(r, 1), "ev": T.ret.mean(),
         "syms": T.sym.nunique(), "days": pd.to_datetime(T.ot, unit="ms").dt.floor("D").nunique()}
    p = T.groupby("sym", observed=True).ret.sum().sort_values(ascending=False)
    tot = T.ret.sum(); o["top5"] = p.iloc[:5].sum() / tot if tot > 0 else np.nan
    dd = T.assign(day=pd.to_datetime(T.ot, unit="ms").dt.floor("D")).groupby("day")
    dw = dd.win.sum() / (dd.win.sum() + dd.loss.sum()).replace(0, np.nan)
    o["daywin"] = (dw > 0.5).mean(); o["daymeanwin"] = dw.mean()
    mo = T.assign(m=pd.to_datetime(T.ot, unit="ms").dt.strftime("%y%m")).groupby("m").ret.agg(["sum", "count"])
    o["months"] = " ".join(f"{i}:{r['sum']:+.1f}/{int(r['count'])}" for i, r in mo.iterrows())
    o["mpos"] = (mo["sum"] > 0).sum(); o["mn"] = len(mo)
    return o

def report(T, name=""):
    tr = T[T.ot < SPLIT]; te = T[T.ot >= SPLIT]
    for nm, x in (("TRAIN", tr), ("TEST", te)):
        s = stats(x)
        if s["n"] == 0: print(name, nm, "n=0"); continue
        print(f"{name} {nm}: n={s['n']} res={s['res']} win={s['win']:.3f} ev={s['ev']*100:+.2f}% syms={s['syms']} days={s['days']} "
              f"top5={s['top5']:.2f} daywin>50%={s['daywin']:.2f} daymean={s['daymeanwin']:.3f} mpos={s['mpos']}/{s['mn']}")
        print("   ", s["months"])
        for mk in ("spot", "fut"):
            y = x[x.mkt == mk]
            if len(y): print(f"    {mk}: n={len(y)} win={y.win.sum()/max(1,y.win.sum()+y.loss.sum()):.3f} ev={y.ret.mean()*100:+.2f}%")
