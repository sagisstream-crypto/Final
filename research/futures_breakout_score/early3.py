from load2 import load2, episodes
import numpy as np, pandas as pd, json
d=load2(); Y="y10_5_60"; d["ep"]=episodes(d)
def sc(df,J,key):
    s=np.zeros(len(df))
    for f,e in J["bins"].items():
        col={"rvol_pre20":"rvol_pre20"}.get(f,f)
        x=df[col].values; v=np.array(J[key][f])[np.digitize(x,e)]; v[np.isnan(x)]=0; s+=v
    return s
E=json.load(open("early_points.json")); M=json.load(open("points10.json"))
d["early"]=np.where((d.pct60m<=3)&(d.pct15m<=2),sc(d,E,"points_train"),0)
d["main"]=sc(d,M,"points_train")
te=d[d.dt>=pd.Timestamp("2026-05-01")].copy(); days=(te.t.max()-te.t.min())/86400000
def cool(m,mins=15):
    keep=[];last={}
    for s,t in zip(m.sym.values,m.t.values):
        ok=not(s in last and t-last[s]<mins*60000); keep.append(ok)
        if ok: last[s]=t
    return m[np.array(keep,bool)]
# episode table
g=te[te.ep.notna()].groupby("ep")
EP=g.agg(sym=("sym","first"),t0=("t","min"),t1=("t","max"))
def lateness(col,T):
    out=[]
    for e,r in EP.iterrows():
        w=te[(te.sym==r.sym)&(te.t>=r.t0-90*60000)&(te.t<=r.t1+60*60000)]
        peak=(w.price*(1+w.mfe120/100)).max()
        f0=w[w.t==r.t0].iloc[0]; start=f0.price/(1+np.nan_to_num(f0.pct60m)/100)
        al=w[(w[col]>=T)&(w.t<=r.t1)]
        if not len(al): continue
        a=al.iloc[0]
        out.append(dict(done=(a.price/start-1)*100,left=(peak/a.price-1)*100,frac=(peak/a.price-1)/max(peak/start-1,1e-9),lead=(r.t0-a.t)/60000))
    return pd.DataFrame(out)
print("эпизодов в проверке:",len(EP),"дней",round(days))
for col,Ts in [("early",[50,55,60,65,70,75]),("main",[70,80,85,90])]:
    for T in Ts:
        a=cool(te[te[col]>=T]); L=lateness(col,T)
        print(f"{col:5s} T={T}: алертов/день {len(a)/days:6.1f}  вынос {a[Y].mean()*100:4.1f}%  +20% {a.y20_8_120.mean()*100:4.1f}%  мед.макс.ход1ч {a.mfe60.median():4.2f}%  "
              f"эпизодов поймано {len(L)/len(EP)*100:3.0f}%  пройдено к алерту {L.done.median():4.1f}%  осталось {L.left.median():4.1f}%  доля хода впереди {L.frac.median():.2f}  раньше первой выносной минуты {(L.lead>=0).mean()*100:3.0f}%")
