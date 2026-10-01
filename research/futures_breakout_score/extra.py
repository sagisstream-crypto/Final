from load import load
import numpy as np, pandas as pd, glob, os
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score
d=load(); Y="y10_5_60"; d=d.replace([np.inf,-np.inf],np.nan)
# listing age
first={os.path.basename(f)[:-4]:pd.read_csv(f,header=None,nrows=1)[0].iloc[0] for f in glob.glob("d1/*.csv")}
d["age"]=((d.t-d.sym.astype(str).map(first))/86400000).clip(upper=400)
d["hour"]=d.dt.dt.hour
# market breadth: number of symbols with pct15m>=2 in the same minute
br=d[d.pct15m>=2].groupby("t").sym.nunique()
d["breadth"]=d.t.map(br).fillna(0)
# past pump episodes per sym in prior 30d (episode end lag 2h)
d=d.sort_values(["sym","t"]).reset_index(drop=True)
p=d[d[Y]][["sym","t"]].copy()
p["ep"]=((p.sym!=p.sym.shift())|(p.t-p.t.shift()>120*60000)).cumsum()
eps=p.groupby("ep").agg(sym=("sym","first"),t0=("t","min"))
ep30=np.zeros(len(d))
for s,g in d.groupby("sym",observed=True):
    e=np.sort(eps[eps.sym==s].t0.values)
    if len(e)==0: continue
    t=g.t.values
    hi=np.searchsorted(e,t-180*60000); lo=np.searchsorted(e,t-30*86400000)
    ep30[g.index.values]=hi-lo
d["ep30"]=ep30
d.to_parquet("all_extra.parquet")
F=["pct60m","pct15m","pct5m","pct1m","vwapDev","sig60","rng","vol24","tradesK","rvol_pre20","rvol5","pct24h"]
cut=pd.Timestamp("2026-05-01"); tr=d[d.dt<cut]; te=d[d.dt>=cut]
for extra in [[],["ep30"],["age"],["hour"],["breadth"],["ep30","age","hour","breadth"]]:
    FF=F+extra
    m=HistGradientBoostingClassifier(max_iter=250,learning_rate=0.05,min_samples_leaf=500,l2_regularization=1.0).fit(tr[FF],tr[Y])
    pp=m.predict_proba(te[FF])[:,1]
    th=np.quantile(pp,0.999)
    print(extra,"AUC %.4f AP %.4f prec@0.1%% %.3f"%(roc_auc_score(te[Y],pp),average_precision_score(te[Y],pp),te[Y][pp>=th].mean()))
for f in ["ep30","age","breadth","hour"]:
    b=pd.qcut(d[f],8,duplicates="drop"); print(d.groupby(b,observed=True)[Y].agg(["mean","size"]).round(4).to_string())
