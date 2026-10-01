import numpy as np, pandas as pd, lightgbm as lgb
from sklearn.metrics import roc_auc_score
from explore_r import st
E = pd.read_parquet("data/events_r.parquet")
SPLIT = pd.Timestamp("2026-06-01")
COIN = ["r5","r5_z","rvol5","rtrades5","ats_ratio","taker5","clv","upper_wick","brk24h","brk7d","r1h_pre","r4h_pre",
        "r24h_pre","r7d_pre","dist_hi30d","dist_hi7d","rng72_pre","sd5_pre","quiet6h","v24","v24_vs_7d","prev_trig_7d","a_risk"]
MKT = ["btc_r1h","btc_r24h","breadth"]
CONF = ["c_ret","c_pull","c_newhigh","c_vol","c_rvol","c_taker","c_green","b_risk"]
def run(feats, p, target, name):
    tr, te = E[E.t < SPLIT], E[E.t >= SPLIT]
    m = lgb.LGBMRegressor(n_estimators=300, learning_rate=0.02, num_leaves=15, min_child_samples=200,
                          subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=10, verbose=-1) if target.endswith("evR") else \
        lgb.LGBMClassifier(n_estimators=300, learning_rate=0.02, num_leaves=15, min_child_samples=200,
                          subsample=0.8, subsample_freq=1, colsample_bytree=0.7, reg_lambda=10, verbose=-1)
    ytr = tr[target]
    m.fit(tr[feats], ytr)
    pr = (m.predict(te[feats]) if target.endswith("evR") else m.predict_proba(te[feats])[:, 1])
    te = te.assign(pr=pr)
    q = pd.qcut(te.pr, 10, labels=False, duplicates="drop")
    print(f"\n== {name} target={target}" + ("" if target.endswith("evR") else f" AUC={roc_auc_score(te[target], pr):.3f}"))
    for d in sorted(q.unique()): print("   dec", d + 1, st(te[q == d], p))
    imp = sorted(zip(feats, m.booster_.feature_importance("gain")), key=lambda x: -x[1]); tot = sum(v for _, v in imp)
    print("   imp", [(f, round(v / tot * 100)) for f, v in imp[:10]])
    return m
if __name__ == "__main__":
    run(COIN, "a_", "a_real", "A coin-only real")
    run(COIN, "a_", "a_noise", "A coin-only noise")
    run(COIN + MKT, "a_", "a_evR", "A coin+mkt evR")
    run(COIN + MKT + CONF, "b_", "b_evR", "B +confirm evR")
