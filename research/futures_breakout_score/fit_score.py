"""Fit the v16 points score and compare it with the v15 score out of sample.
Run after proc.py has filled ev/. Writes points10.json (bins + integer points)."""
from load import load
import numpy as np, pandas as pd, json
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
d=load(); Y="y10_5_60"; d=d.replace([np.inf,-np.inf],np.nan)
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

FE=["pct60m","pct15m","pct5m","pct1m","vwapDev","sig60","rng","rvol_pre20","rvol5","pct24h"]
def fit(df):
    X,names=design(df,FE); lr=LogisticRegression(C=0.05,max_iter=3000); lr.fit(X,df[Y]); return lr,names
def to_points(lr,names,scale=None):
    co=pd.Series(lr.coef_[0],names)
    pts={}; mins=0; maxs=0
    for f in FE:
        v=co[[n for n in names if n.startswith(f+"#")]].values
        # unknown -> min bin
        v=v-v.min(); pts[f]=v; maxs+=v.max()
    sc=100/maxs if scale is None else scale
    return {f:[int(round(x*sc)) for x in v] for f,v in pts.items()}, sc
def score(df,P):
    s=np.zeros(len(df))
    for f in FE:
        x=df[f].values; b=np.digitize(x,B[f]); arr=np.array(P[f]); val=arr[b]; val[np.isnan(x)]=0; s+=val
    return s
def old_score(df):
    s=np.zeros(len(df)); r=df.rvol.values
    s+=np.select([r>=25,r>=12,r>=6,r>=3,r<1],[30,22,14,7,-10],0)
    p=df.pct1m.values; s+=np.select([p>=2,p>=1,p>=0.4,p<=-0.4],[25,18,10,-18],0)
    nh=df.newhi3.values>0; rg=df.rng.values
    s+=np.where(nh,18,np.select([rg>=0.9,rg<=0.35],[8,-8],0))
    v=df.vwapDev.values; s+=np.select([v>=3,v>=0.5,v<=-3],[10,5,-5],0)
    k=np.nan_to_num(df.atsK.values); s+=np.select([k>=4,k>=2],[15,8],0)
    t=df.taker.values; s+=np.select([t>=0.7,t>=0.58,t<=0.35],[5,2,-4],0)
    cp=df.closePos.values; w=df.wick.values
    s+=np.select([(cp>=0.75)&(w<=0.15),(cp>=0.55)&(w<=0.3),(cp<=0.35)&(w>=0.4)],[14,6,-18],0)
    return np.clip(s,0,100)
def sim(df,sc,T,cool=15):
    # fire when score>=T, per-symbol cooldown (minutes)
    m=df[sc>=T][["sym","t",Y,"r16_8_120","r10_5_60","mfe60","mfe120","y20_8_120"]].sort_values(["sym","t"])
    keep=[];last={}
    for s,t in zip(m.sym.values,m.t.values):
        if s in last and t-last[s]<cool*60000: keep.append(False); continue
        last[s]=t; keep.append(True)
    return m[np.array(keep,bool)]
def episodes(df):
    p=df[df[Y]][["sym","t"]].sort_values(["sym","t"])
    p["ep"]=((p.sym!=p.sym.shift())|(p.t-p.t.shift()>120*60000)).cumsum()
    return p
def report(df,sc,label):
    ep=episodes(df); days=(df.t.max()-df.t.min())/86400000
    print(f"\n== {label}  AUC={roc_auc_score(df[Y],sc):.3f}  episodes={ep.ep.nunique()}  days={days:.0f}")
    for T in [40,50,60,65,70,75,80,85,90]:
        a=sim(df,sc,T)
        if len(a)==0: continue
        hit=ep.merge(df.assign(sc=sc)[["sym","t","sc"]],on=["sym","t"])
        rec=hit[hit.sc>=T].ep.nunique()/ep.ep.nunique()
        print(f"T={T:3d} alerts/day={len(a)/days:7.1f} prec(+10% до -5%, 1ч)={a[Y].mean():.3f} +20%/2ч={a.y20_8_120.mean():.3f} "
              f"mfe60 med={a.mfe60.median():5.2f}% avg trade16/8={a.r16_8_120.mean():+.2f}% recall_ep={rec:.2f}")
cut=pd.Timestamp("2026-05-01"); tr=d[d.dt<cut]; te=d[d.dt>=cut]
lr,names=fit(tr); P,sc=to_points(lr,names)
print("points(train):",json.dumps(P))
report(te,score(te,P),"NEW score, out-of-sample May-Aug 2026")
report(te,old_score(te),"OLD score v15, same data")
lr2,names2=fit(d); P2,_=to_points(lr2,names2)
print("points(full year):",json.dumps(P2))
json.dump({"bins":{f:B[f] for f in FE},"points":P2,"points_train":P},open("points10.json","w"))
s2=score(te,P2); 
print("monthly OOS precision @T=70 (train-fit)")
s=score(te,P); 
for mo,g in te.assign(s=s).groupby(te.dt.dt.to_period("M")):
    a=sim(g,g.s.values,70); print(mo,len(a),round(a[Y].mean(),3),round(a.r16_8_120.mean(),2))
