import pandas as pd, glob, numpy as np
def load2(cols=None):
    ds=[pd.read_parquet(f,columns=cols) for f in glob.glob("ev2/*.parquet")]
    d=pd.concat([x for x in ds if len(x)],ignore_index=True)
    d["sym"]=d["sym"].astype("category"); d["dt"]=pd.to_datetime(d.t,unit="ms")
    d=d.replace([np.inf,-np.inf],np.nan)
    return d.sort_values(["sym","t"]).reset_index(drop=True)
def episodes(d,Y="y10_5_60",gap=120):
    p=d[d[Y]][["sym","t"]]
    ep=((p.sym!=p.sym.shift())|(p.t-p.t.shift()>gap*60000)).cumsum()
    e=pd.Series(np.nan,index=d.index); e[p.index]=ep.values
    return e
