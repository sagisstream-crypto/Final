from load2 import load2, episodes
import numpy as np, pandas as pd
d=load2(); Y="y10_5_60"; d["ep"]=episodes(d)
days=(d.t.max()-d.t.min())/86400000
def cool(m,mins=60):
    keep=[];last={}
    for s,t in zip(m.sym.values,m.t.values):
        ok=not(s in last and t-last[s]<mins*60000); keep.append(ok)
        if ok: last[s]=t
    return m[np.array(keep,bool)]
nep=d.ep.nunique()
print("эпизодов",nep,"дней",round(days))
first=d[d.ep.notna()].groupby("ep").head(1)
print("доля выносов, где перед первой минутой была полка (4ч-диапазон <= X% / отн. волатильность 4ч <= Y):")
for X in [2,3,4,6]: print(f"  range240<={X}%: {(first.range240<=X).mean():.2f}   (среди всех кандидатов {(d.range240<=X).mean():.2f})")
for Y_ in [0.8,0.9,1.0]: print(f"  sig240_rel<={Y_}: {(first.sig240_rel<=Y_).mean():.2f}   (все {(d.sig240_rel<=Y_).mean():.2f})")
print("\nсканер 'полка -> пробой хая полки': ")
rows=[]
for W,rcol in [(60,"range60"),(120,"range120"),(240,"range240")]:
  for X in [1,1.5,2,3,4,6]:
    for relcol,rel in [(None,None),("sig240_rel",0.9)]:
      for rv in [3,6,10]:
        m=(d[rcol]<=X)&(d[f"brk{W}"]>0)&(d.rvol>=rv)
        if relcol: m&=(d[relcol]<=rel)
        a=cool(d[m])
        if len(a)<30: continue
        caught=a[a[Y]].ep.nunique()
        rows.append(dict(W=W,X=X,rel=rel,rvol=rv,alerts_day=len(a)/days,prec=a[Y].mean(),y20=a.y20_8_120.mean(),mfe60=a.mfe60.median(),eps=caught/nep))
r=pd.DataFrame(rows).sort_values("prec",ascending=False)
print(r.head(25).round(3).to_string())
print("\nпробой без полки (brk240>0, rvol>=6) для сравнения:")
a=cool(d[(d.brk240>0)&(d.rvol>=6)]); print(len(a)/days, a[Y].mean(), a.mfe60.median())
