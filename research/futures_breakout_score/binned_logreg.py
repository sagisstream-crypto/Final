from load import load
import numpy as np, pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score
d=load(); Y="y10_5_60"
d=d.replace([np.inf,-np.inf],np.nan)
B={ # JS-friendly bin edges
 "pct60m":[-3,-1,0,0.5,1,2,3,5,8],
 "pct15m":[-1,0,0.3,0.7,1.2,2,3,5],
 "pct5m":[0,0.3,0.6,1,2,3],
 "pct1m":[0.1,0.2,0.35,0.5,1,2],
 "vwapDev":[-2,0,1,2,3,5,8],
 "sig60":[0.07,0.1,0.13,0.17,0.22,0.3,0.45],
 "rng":[0.3,0.5,0.7,0.85,0.95],
 "vol24":[5e5,1e6,1.5e6,2e6,2.5e6],
 "tradesK":[1.5,3,6,10,15,30],
 "rvol_pre20":[0.5,1,2,3,5,10],
 "rvol5":[1.5,2.5,4,6,10,20],
 "rvol":[4,6,10,15,25,50],
 "quietCv":[1.2,2,3,5],
 "pct24h":[-5,-2,0,2,5,10,20],
 "greens":[1,2,3],
 "body":[0.5,0.875],
 "taker":[0.35,0.5,0.7,0.9,0.98],
 "atsK":[1.2,1.8,2.5,4],
 "closePos":[0.4,0.75,0.9],
}
def design(df,feats):
    cols=[]
    for f in feats:
        x=df[f].values; e=B[f]
        b=np.digitize(x,e); b[np.isnan(x)]=-1
        for k in range(len(e)+1):
            cols.append((f"{f}#{k}",(b==k).astype(np.float32)))
    names=[c[0] for c in cols]; X=np.column_stack([c[1] for c in cols])
    return X,names
cut=pd.Timestamp("2026-05-01")
tr=d[d.dt<cut]; te=d[d.dt>=cut]
for feats in [list(B), ["pct60m","pct15m","pct5m","pct1m","vwapDev","sig60","rng","vol24","tradesK","rvol_pre20","rvol5","pct24h"]]:
    Xtr,names=design(tr,feats); Xte,_=design(te,feats)
    lr=LogisticRegression(C=0.05,max_iter=2000)
    lr.fit(Xtr,tr[Y])
    p=lr.predict_proba(Xte)[:,1]
    print(len(feats),"LR AUC",roc_auc_score(te[Y],p),"AP",average_precision_score(te[Y],p))
    co=pd.Series(lr.coef_[0],names)
    for f in feats:
        print(f, " ".join(f"{v:+.2f}" for v in co[[n for n in names if n.startswith(f+"#")]].values), " edges",B[f])
