import urllib.request, io, zipfile, os, glob, concurrent.futures as cf, pandas as pd
cols="ot o h l c v ct qv n tbv tbqv ig".split()
def get(u):
    with urllib.request.urlopen(u, timeout=60) as r: return r.read()
def one(path):
    m, s = os.path.basename(path)[:-8].split("_",1)
    out=f"data_hold/{m}_{s}.parquet"
    if os.path.exists(out): return s,"cached"
    base="futures/um" if m=="fut" else "spot"; parts=[]
    for d in range(1,29):
        try:
            z=zipfile.ZipFile(io.BytesIO(get(f"https://data.binance.vision/data/{base}/daily/klines/{s}/5m/{s}-5m-2026-09-{d:02d}.zip")))
            df=pd.read_csv(z.open(z.namelist()[0]),header=None)
            if not str(df.iloc[0,0]).isdigit(): df=df.iloc[1:]
            df.columns=cols; parts.append(df)
        except Exception: pass
    if not parts: return s,"none"
    sep=pd.concat(parts).astype(float); sep["ot"]=sep["ot"].where(sep["ot"]<1e14, sep["ot"]//1000)
    sep=sep[["ot","o","h","l","c","v","qv","n","tbqv"]]
    old=pd.read_parquet(path); old=old[old.ot>=pd.Timestamp("2026-08-01",tz="UTC").value//10**6]
    df=pd.concat([old,sep]).drop_duplicates("ot").sort_values("ot").reset_index(drop=True)
    df.to_parquet(out); return s,len(sep)
with cf.ThreadPoolExecutor(24) as ex:
    res=list(ex.map(one, sorted(glob.glob("data/*.parquet"))))
print(sum(1 for r in res if isinstance(r[1],int)), [r for r in res if not isinstance(r[1],int)][:10])
