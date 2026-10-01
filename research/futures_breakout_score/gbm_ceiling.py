from load import load
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score
d=load()
print("rows",len(d),"syms",d.sym.nunique())
Y="y10_5_60"
print("base",d[Y].mean(), "positives",d[Y].sum())
F=["rvol","rvol5","rvol_pre20","pct1m","pct2m","pct5m","pct15m","pct60m","pct24h","rng","vwapDev","atsK","taker","closePos","wick","body","quietCv","tradesK","sig60","newhi3","greens","vol24"]
cut=pd.Timestamp("2026-05-01")
tr=d[d.dt<cut]; te=d[d.dt>=cut]
print("train",len(tr),tr[Y].mean(),"test",len(te),te[Y].mean())
m=HistGradientBoostingClassifier(max_iter=300,learning_rate=0.05,max_leaf_nodes=31,min_samples_leaf=500,l2_regularization=1.0)
X=tr[F].astype(float).replace([np.inf,-np.inf],np.nan)
m.fit(X,tr[Y])
p=m.predict_proba(te[F].astype(float).replace([np.inf,-np.inf],np.nan))[:,1]
print("GBM AUC",roc_auc_score(te[Y],p),"AP",average_precision_score(te[Y],p))
te=te.assign(p=p)
for q in [0.99,0.995,0.999,0.9995]:
    th=np.quantile(p,q); s=te[te.p>=th]
    print(q, len(s), "prec",s[Y].mean().round(3), "avg r10_5",s.r10_5_60.mean().round(2),"r16_8",s.r16_8_120.mean().round(2),"mfe60 med",s.mfe60.median().round(2))
from sklearn.inspection import permutation_importance
sub=te.sample(300000,random_state=0)
pi=permutation_importance(m,sub[F].astype(float).replace([np.inf,-np.inf],np.nan),sub[Y],scoring="average_precision",n_repeats=2,random_state=0)
print(pd.Series(pi.importances_mean,F).sort_values(ascending=False).round(4).to_string())
