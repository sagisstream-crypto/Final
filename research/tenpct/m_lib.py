import numpy as np, pandas as pd, os
HERE = os.path.dirname(os.path.abspath(__file__))
T = lambda s: pd.Timestamp(s, tz="UTC").value // 10**6
VAL0, TEST0, CENS = T("2026-03-01"), T("2026-05-01"), T("2026-09-14")
EMB = 14 * 86400000
DROP = {"ot", "sym", "lab_long", "up_t", "dn_t", "lprice", "age"}  # lprice/age: raw nonstationary

def load():
    D = pd.read_parquet(f"{HERE}/m_feat.parquet")
    D = D[(D.lqv24 >= np.log1p(250_000)) & (D.vol72 > 0.001) & (D.qvz1.notna())].reset_index(drop=True)  # tradable, live universe
    D["sym"] = D.sym.cat.remove_unused_categories()
    u = D.up_t.fillna(np.inf).values; d = D.dn_t.fillna(np.inf).values
    D["ll"] = np.where(u < d, 1, np.where(np.isfinite(u) | np.isfinite(d), -1, 0)).astype(float)
    D["ls"] = np.where(d < u, 1, np.where(np.isfinite(u) | np.isfinite(d), -1, 0)).astype(float)
    cens = (D.ll == 0) & (D.ot > CENS)
    D.loc[cens, ["ll", "ls"]] = np.nan
    D["exit_ms"] = D.ot + 3600000 + np.minimum(np.minimum(u, d), 14 * 1440) * 60000
    D["split"] = np.where(D.ot < VAL0 - EMB, 0, np.where(D.ot < VAL0, -1, np.where(D.ot < TEST0 - EMB, 1, np.where(D.ot < TEST0, -1, 2))))
    D["y"] = (D.ll == 1).astype(int)
    D["res"] = D.ll.isin([1, -1])
    D["month"] = pd.to_datetime(D.ot, unit="ms").dt.strftime("%Y-%m")
    return D

def feats(D):
    return [c for c in D.columns if c not in DROP | {"ll", "ls", "exit_ms", "split", "y", "res", "month"}]

def nonoverlap(S):
    """S: selected rows; keep first entry per symbol then skip until exit."""
    S = S.sort_values(["sym", "ot"])
    keep = np.zeros(len(S), bool)
    sy = S.sym.cat.codes.values if hasattr(S.sym, "cat") else pd.factorize(S.sym)[0]
    ot = S.ot.values; ex = S.exit_ms.values
    last_sym, free = -1, -1
    for i in range(len(S)):
        if sy[i] != last_sym: last_sym, free = sy[i], -1
        if ot[i] >= free:
            keep[i] = True; free = ex[i]
    return S[keep]

def stats(S, side):
    S = S[S[side].notna()]
    S = nonoverlap(S)
    w = (S[side] == 1).sum(); l = (S[side] == -1).sum(); n = len(S)
    return dict(n=n, win=w / max(w + l, 1), ev=(0.10 * (w - l) / max(n, 1) - 0.001) * 100,
                syms=S.sym.nunique(), coins=S.sym.astype(str).str.split("_", n=1).str[1].nunique(),
                days=pd.to_datetime(S.ot, unit="ms").dt.date.nunique()), S

def monthly(S, side):
    out = {}
    for m, g in S.groupby("month"):
        w = (g[side] == 1).sum(); l = (g[side] == -1).sum()
        out[m] = (len(g), round(w / max(w + l, 1), 3))
    return out

def roc_auc_score(y, p):
    from scipy.stats import rankdata
    y = np.asarray(y); r = rankdata(p); n1 = y.sum(); n0 = len(y) - n1
    return (r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)
