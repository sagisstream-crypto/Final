import pandas as pd, glob, numpy as np
def load(cols=None):
    fs=[f for f in glob.glob("ev/*.parquet")]
    ds=[]
    for f in fs:
        d=pd.read_parquet(f,columns=cols)
        if len(d): ds.append(d)
    d=pd.concat(ds,ignore_index=True)
    d["sym"]=d["sym"].astype("category")
    d["dt"]=pd.to_datetime(d.t,unit="ms")
    return d
