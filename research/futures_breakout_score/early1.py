from load2 import load2
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score
from sklearn.inspection import permutation_importance
d=load2(); Y="y10_5_60"
e=d[(d.pct60m<=3)&(d.pct15m<=2)]
cut=pd.Timestamp("2026-05-01"); tr=e[e.dt<cut]; te=e[e.dt>=cut]
print("early train",len(tr),tr[Y].mean().round(4),"test",len(te),te[Y].mean().round(4))
base=["pct1m","pct5m","pct15m","pct60m","pct24h","rng","vwapDev","rvol","rvol5","rvol_pre20","rvol_pre60","rvol_pre240","range60","range240","range60_rel","sig60","sig60_rel","sig240_rel","brk60","brk240","qcv60"]
candle=["taker","atsK","closePos","body","wick","tradesK"]
day=["sig1440"]
for name,F in [("base",base),("base+candle",base+candle),("base+candle+sig1440",base+candle+day),("no shelf",[f for f in base if f not in("range60","range240","range60_rel","sig60_rel","sig240_rel","brk60","brk240","qcv60","rvol_pre60","rvol_pre240")])]:
    m=HistGradientBoostingClassifier(max_iter=300,learning_rate=0.05,min_samples_leaf=500,l2_regularization=1.0).fit(tr[F],tr[Y])
    p=m.predict_proba(te[F])[:,1]
    th=np.quantile(p,0.999)
    print(f"{name:22s} AUC {roc_auc_score(te[Y],p):.4f} AP {average_precision_score(te[Y],p):.4f} prec@0.1% {te[Y][p>=th].mean():.3f}")
    if name=="base+candle":
        sub=te.sample(400000,random_state=0)
        pi=permutation_importance(m,sub[F],sub[Y],scoring="average_precision",n_repeats=2,random_state=0)
        print(pd.Series(pi.importances_mean,F).sort_values(ascending=False).round(4).to_string())
