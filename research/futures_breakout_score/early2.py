from load2 import load2, episodes
import numpy as np, pandas as pd, json
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score
d=load2(); Y="y10_5_60"; d["ep"]=episodes(d)
EG=(d.pct60m<=3)&(d.pct15m<=2)
e=d[EG]
cut=pd.Timestamp("2026-05-01")
B={
 "pct1m":[0.1,0.2,0.3,0.5,1,2],
 "pct5m":[-1,0,0.3,0.6,1,2],
 "pct15m":[-2,-1,0,0.5,1,1.5],
 "pct60m":[-5,-3,-1,0,1,2],
 "pct24h":[-10,-5,-2,0,2,4,8,15],
 "rng":[0.2,0.4,0.6,0.8,0.9],
 "vwapDev":[-3,-1,0,1,2,4],
 "sig60":[0.07,0.1,0.13,0.17,0.22,0.3,0.45],
 "rvol5":[1.5,2.5,4,6,10,20],
 "rvol":[4,6,10,20,40],
 "rvol_pre20":[0.5,1,2,3,5,10],
 "rvol_pre60":[0.5,1,1.5,2.5,4],
 "range60":[0.8,1.2,1.8,2.5,3.5,5],
 "range240":[1.5,2.5,4,6,9],
 "rvol_pre240":[0.7,1,1.4,2,3],
}
def design(df,feats):
    cols=[];names=[]
    for f in feats:
        x=df[f].values; b=np.digitize(x,B[f]); b[np.isnan(x)]=-1
        for k in range(len(B[f])+1): cols.append((b==k).astype(np.float32)); names.append(f"{f}#{k}")
    return np.column_stack(cols),names
def pts(lr,names,feats):
    co=pd.Series(lr.coef_[0],names); P={}; tot=0
    for f in feats:
        v=co[[n for n in names if n.startswith(f+"#")]].values; v=v-v.min(); P[f]=v; tot+=v.max()
    return {f:[int(round(x*100/tot)) for x in v] for f,v in P.items()}
def score(df,P,feats):
    s=np.zeros(len(df))
    for f in feats:
        x=df[f].values; v=np.array(P[f])[np.digitize(x,B[f])]; v[np.isnan(x)]=0; s+=v
    return s
A=["pct1m","pct5m","pct15m","pct60m","pct24h","rng","vwapDev","sig60","rvol5","rvol","rvol_pre20","rvol_pre60","range60"]
AB=A+["range240","rvol_pre240"]
tr=e[e.dt<cut]; te=e[e.dt>=cut]
res={}
for nm,F in [("A",A),("A+240",AB)]:
    X,n=design(tr,F); lr=LogisticRegression(C=0.05,max_iter=3000).fit(X,tr[Y])
    P=pts(lr,n,F); s=score(te,P,F)
    print(nm,"AUC",round(roc_auc_score(te[Y],s),4),"AP",round(average_precision_score(te[Y],s),4))
    res[nm]=(F,P)
F,P=res["A"]
for f in F: print(f,P[f],B[f])
json.dump({"bins":{f:B[f] for f in F},"points_train":P},open("early_points_train.json","w"))
# full-year refit
X,n=design(e,F); lr=LogisticRegression(C=0.05,max_iter=3000).fit(X,e[Y]); Pf=pts(lr,n,F)
json.dump({"bins":{f:B[f] for f in F},"points":Pf,"points_train":P},open("early_points.json","w"))
print("full:",json.dumps(Pf))
