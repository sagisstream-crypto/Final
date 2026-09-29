"""Evaluate a saved model on a split: AUC, hour-AUC, calibration, top/bottom 1/5/10% trades (thresholds from VAL), monthly.
usage: python m_eval.py model.pkl val|test"""
import sys, pickle, numpy as np, pandas as pd
from m_lib import *

def hour_auc(X, p):
    X = X.assign(p=p); a = []; w = []
    for _, g in X.groupby("ot"):
        if g.y.nunique() == 2 and len(g) >= 20:
            a.append(roc_auc_score(g.y.values, g.p.values)); w.append(len(g))
    return np.average(a, weights=w)

def report(D, m, F, split, thr_from=1):
    D = D.copy(); D["p"] = m.predict(D[F])
    ref = D[(D.split == thr_from) & D.ll.notna()]
    X = D[(D.split == split) & D.ll.notna()]
    R = X[X.res]
    print(f"== split {split}: rows {len(X)} resolved {len(R)} base {R.y.mean():.3f}  AUC {roc_auc_score(R.y, R.p):.4f}  hourAUC {hour_auc(R, R.p.values):.4f}")
    R = R.assign(dec=pd.cut(R.p, np.r_[-1, ref.p.quantile(np.arange(.1, 1, .1)).values, 2], labels=False))
    print("calibration (VAL-decile bins):"); print(R.groupby("dec").agg(p=("p", "mean"), y=("y", "mean"), n=("y", "size")).round(3).T.to_string())
    out = {}
    for q in (0.01, 0.05, 0.10):
        hi = X[X.p >= ref.p.quantile(1 - q)]; lo = X[X.p <= ref.p.quantile(q)]
        sl, SL = stats(hi, "ll"); ss, SS = stats(lo, "ls")
        f = lambda s: " ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in s.items())
        print(f"top{q:.0%} LONG  {f(sl)}\n   monthly {monthly(SL, 'll')}")
        print(f"bot{q:.0%} SHORT {f(ss)}\n   monthly {monthly(SS, 'ls')}")
        out[q] = (sl, ss)
    return out

if __name__ == "__main__":
    m, F = pickle.load(open(sys.argv[1], "rb"))
    sp = {"train": 0, "val": 1, "test": 2}[sys.argv[2]]
    D = load()
    if sp < 2: D = D[D.ot < TEST0]
    report(D, m, F, sp)
