"""Non-overlapping per-symbol evaluation of a (mask, side) signal on the feature table."""
import numpy as np, pandas as pd, lab10
SPLIT = lab10.SPLIT
CAPH = 14 * 24

def trades(F, mask, side):
    """F sorted by sym, ot (as in feat.parquet). mask bool array, side +1/-1 scalar or array.
    returns DataFrame of trades (sym, ot, side, res) with res in {1,-1,0}; skips until resolution/14d."""
    side = np.broadcast_to(np.asarray(side), mask.shape)
    ii = np.flatnonzero(mask)
    if len(ii) == 0: return pd.DataFrame(columns=["sym", "ot", "side", "res"])
    sym = F.sym.values; ot = F.ot.values
    ll = F.lab_long.values; ls = F.lab_short.values; ut = F.up_t.values; dt = F.dn_t.values
    rows = []; last_sym = None; nxt = -1
    for i in ii:
        s = sym[i]
        if s != last_sym: last_sym = s; nxt = -1
        if ot[i] < nxt: continue
        lab = ll[i] if side[i] > 0 else ls[i]
        if np.isnan(lab): continue
        t = np.nanmin([ut[i], dt[i]]) if not (np.isnan(ut[i]) and np.isnan(dt[i])) else CAPH * 60
        nxt = ot[i] + 3600000 + t * 60000
        rows.append((s, ot[i], side[i], lab, ut[i], dt[i]))
    return pd.DataFrame(rows, columns=["sym", "ot", "side", "res", "ut", "dt"])

def stats(T, label=""):
    if len(T) == 0: return {"n": 0}
    w = (T.res == 1).sum(); l = (T.res == -1).sum(); n = len(T)
    ev = 10 * (w - l) / n - 0.1
    T = T.assign(pnl=10 * T.res - 0.1, day=pd.to_datetime(T.ot, unit="ms").dt.floor("D"),
                 mo=pd.to_datetime(T.ot, unit="ms").dt.strftime("%y%m"))
    ps = T.groupby("sym").pnl.sum().sort_values(ascending=False)
    top5 = ps.head(5).sum() / T.pnl.sum() if T.pnl.sum() > 0 else np.nan
    r = T[T.res != 0]
    dw = r.groupby("day").res.apply(lambda x: (x == 1).mean())
    mo = T.groupby("mo").pnl.sum().round(0).astype(int).to_dict()
    return dict(n=n, res=w + l, win=round(w / max(w + l, 1), 3), ev=round(ev, 2), syms=T.sym.nunique(),
                days=T.day.nunique(), daywin=round(dw.mean(), 3) if len(dw) else np.nan,
                top5=round(top5, 2) if top5 == top5 else None, mo=mo)

def split_stats(T):
    tr = T[T.ot < SPLIT]; te = T[T.ot >= SPLIT]
    return stats(tr), stats(te)

_FR = {}
def funding_cost(T, F=None):
    """adds columns hold_h and fpnl (% funding P&L for the position: long pays fr>0, short receives fr>0)."""
    import os
    hold = []; fp = []
    D = f"{lab10.HERE}/data_extra_d"
    for s, ot, sd, ut, dt in zip(T.sym, T.ot, T.side, T.ut, T.dt):
        if s not in _FR:
            from d_feat import load_fund
            f = load_fund(s).sort_values("t")
            _FR[s] = (f.t.values.astype(np.int64), f.fr.values.astype(float))
        t, fr = _FR[s]
        m = np.nanmin([ut, dt]) if not (np.isnan(ut) and np.isnan(dt)) else CAPH * 60
        a = ot + 3600000; b = a + m * 60000
        i0, i1 = np.searchsorted(t, a, "right"), np.searchsorted(t, b, "right")
        hold.append(m / 60); fp.append(-sd * fr[i0:i1].sum() * 100)
    return T.assign(hold_h=hold, fpnl=fp)
