from load2 import load2, episodes
import numpy as np, pandas as pd
d=load2(); Y="y10_5_60"
d["ep"]=episodes(d)
print("rows",len(d),"base",d[Y].mean().round(4),"episodes",d.ep.nunique())
first=d[d.ep.notna()].groupby("ep").head(1)   # первая «выносная» минута каждого эпизода
print("\n--- первая минута выноса (самый ранний вход, который ещё дал +10%) ---")
F=["pct1m","pct5m","pct15m","pct60m","pct24h","rvol","rvol5","rvol_pre20","rvol_pre60","rvol_pre240","range60","range240","range60_rel","sig60_rel","sig240_rel","brk60","brk240","qcv60","taker","closePos","atsK","sig1440"]
q=[.1,.25,.5,.75,.9]
tab=pd.DataFrame({f:first[f].quantile(q).values for f in F},index=q).T
tab2=pd.DataFrame({f:d[f].quantile(q).values for f in F},index=q).T
print(pd.concat([tab.round(2),tab2.round(2)],axis=1,keys=["first_vynos","all_cand"]).to_string())
early=d[(d.pct60m<=3)&(d.pct15m<=2)]
print("\nранние кандидаты (pct60m<=3, pct15m<=2):",len(early),"base",early[Y].mean().round(4))
for f in ["range60_rel","range240","range60","sig60_rel","sig240_rel","rvol_pre60","rvol_pre240","qcv60","brk60","brk240","rvol","pct1m","rvol5","taker","closePos","atsK","sig1440","pct24h","rng","vwapDev","tradesK","body"]:
    b=pd.qcut(early[f],10,duplicates="drop")
    g=early.groupby(b,observed=True)[Y].agg(["mean","size"])
    print(f, " | ".join(f"{iv.right:.3g}:{m*100:.2f}" for iv,m in zip(g.index,g["mean"])))
