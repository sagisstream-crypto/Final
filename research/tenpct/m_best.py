"""FINAL: '+10% before -10%' ML model on Binance alts.
Features: m_feat.sym_feats (per-symbol, point-in-time) + cross-sectional ranks/excess returns (m_feat.build), 4h subsample.
Universe filter & labels & trade accounting: m_lib (24h qv >= $250k, vol72 > 0.1%/h; non-overlapping per symbol).
Model: LightGBM binary, cross-sectional features only (market-level/time features removed: they encode regime and failed on VAL),
trained on ot < 2026-02-15 (14d purge), early-stopped on 2026-03-01..04-16 (purged), thresholds from VAL. TEST = ot >= 2026-05-01.
Distilled rules (depth-2 tree on model preds, TRAIN only):
  LONG-ish : dath <= -0.632 (>47% below high-since-listing/data-start) & r720 <= -0.049 (30d return < -4.8%)
  SHORT-ish: dath >  -0.632 & vol72 <= 0.01224 (quiet, near highs)
usage: python m_best.py [test]   (without 'test' only TRAIN/VAL are evaluated)"""
import os, sys, pickle, numpy as np, pandas as pd, lightgbm as lgb
from m_lib import *
from m_eval import report, hour_auc
if not os.path.exists(f"{HERE}/m_feat.parquet"):
    import m_feat; m_feat.build()
PARAMS = dict(objective="binary", learning_rate=0.02, num_leaves=15, min_data_in_leaf=5000, feature_fraction=0.7,
              bagging_fraction=0.7, bagging_freq=1, lambda_l2=10, n_jobs=2, verbose=-1, seed=1)
RULES = {"LONG dath<=-0.632 & r720<=-0.049": ("ll", lambda X: (X.dath <= -0.632235) & (X.r720 <= -0.048993)),
         "SHORT dath>-0.632 & vol72<=0.01224": ("ls", lambda X: (X.dath > -0.632235) & (X.vol72 <= 0.012243))}

def train(D):
    F0 = feats(D); F = [f for f in F0 if not (f.startswith("m_") or f in ("hod", "dow", "rk_age"))]
    R = D[D.res & (D.ot < TEST0)]; tr, va = R[R.split == 0], R[R.split == 1]
    m = lgb.train(PARAMS, lgb.Dataset(tr[F], tr.y), 3000, valid_sets=[lgb.Dataset(va[F], va.y)],
                  callbacks=[lgb.early_stopping(200, verbose=False)])
    pickle.dump((m, F), open(f"{HERE}/m_best_model.pkl", "wb"))
    return m, F

if __name__ == "__main__":
    look_test = len(sys.argv) > 1 and sys.argv[1] == "test"
    D = load()
    if not look_test: D = D[D.ot < TEST0]
    m, F = train(D)
    imp = pd.Series(m.feature_importance("gain"), F).sort_values(ascending=False)
    print("iters", m.best_iteration, "importance", (imp / imp.sum()).head(12).round(3).to_dict())
    for sp in ((0, 1, 2) if look_test else (0, 1)):
        report(D, m, F, sp)
        X = D[(D.split == sp) & D.ll.notna()]
        for nm, (side, f) in RULES.items():
            s, S = stats(X[f(X)], side)
            print(f"  RULE {nm}: n={s['n']} win={s['win']:.3f} ev={s['ev']:.2f}% syms={s['syms']} coins={s['coins']} days={s['days']} monthly={monthly(S, side)}")
