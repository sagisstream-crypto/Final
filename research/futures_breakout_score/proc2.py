import urllib.request, zipfile, io, os, sys, numpy as np, pandas as pd, concurrent.futures as cf
from numpy.lib.stride_tricks import sliding_window_view as swv
MONTHS=[f"2025-{m:02d}" for m in range(9,13)]+[f"2026-{m:02d}" for m in range(1,9)]
os.makedirs("ev2",exist_ok=True)
def load(s):
    parts=[]
    for mo in MONTHS:
        u=f"https://data.binance.vision/data/futures/um/monthly/klines/{s}/1m/{s}-1m-{mo}.zip"
        for att in range(3):
            try:
                z=zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u,timeout=60).read()))
                d=pd.read_csv(z.open(z.namelist()[0]),header=None)
                if not str(d.iloc[0,0]).isdigit(): d=d.iloc[1:]
                parts.append(d.iloc[:,:11].astype(float)); break
            except urllib.error.HTTPError: break
            except Exception: pass
    if not parts: return None
    d=pd.concat(parts); d.columns=["t","o","h","l","c","v","ct","q","n","tbv","tbq"]
    d=d.drop_duplicates("t").sort_values("t").set_index("t")
    # reindex to continuous minutes
    idx=np.arange(d.index[0],d.index[-1]+1,60000)
    d=d.reindex(idx)
    d["c"]=d["c"].ffill(); 
    for k in "ohl": d[k]=d[k].fillna(d["c"])
    for k in ["v","q","n","tbv","tbq"]: d[k]=d[k].fillna(0)
    return d
def roll_med(x,w):
    return pd.Series(x).rolling(w,min_periods=max(3,w//3)).median().values
def proc(s):
    out=f"ev2/{s}.parquet"
    if os.path.exists(out): return s,-1
    d=load(s)
    if d is None or len(d)<3000: 
        pd.DataFrame().to_parquet(out); return s,0
    o,h,l,c,v,q,n,tbv=[d[k].values for k in ["o","h","l","c","v","q","n","tbv"]]
    N=len(c)
    Q=pd.Series(q); V=pd.Series(v)
    vol24=Q.rolling(1440,min_periods=720).sum().values*(1440/np.minimum(1440,np.maximum(1,pd.Series(np.ones(N)).rolling(1440,min_periods=1).sum().values)))
    vol24_prev=np.r_[np.nan,vol24[:-1]]
    exp=vol24/1440
    rvol=q/exp
    def lag(x,k): return np.r_[np.full(k,np.nan),x[:-k]]
    pct=lambda k: (c/lag(c,k)-1)*100
    hi24=pd.Series(h).rolling(1440,min_periods=720).max().values
    lo24=pd.Series(l).rolling(1440,min_periods=720).min().values
    hi24p=lag(hi24,1)
    vwap=Q.rolling(1440,min_periods=720).sum().values/np.maximum(V.rolling(1440,min_periods=720).sum().values,1e-12)
    ats=np.where(n>=3,q/np.maximum(n,1),np.nan)
    atsmed=lag(roll_med(ats,22),1)
    rng_=np.where(h>l,h-l,np.nan)
    closepos=(c-l)/rng_; wick=(h-np.maximum(o,c))/rng_; body=np.abs(c-o)/rng_
    taker=np.where(v>0,tbv/np.maximum(v,1e-12),np.nan)
    # prior 20m quiet: q std / median over t-21..t-1
    qp=lag(q,1)
    qmed20=roll_med(qp,20); qstd20=pd.Series(qp).rolling(20,min_periods=10).std().values
    rvol_pre20=pd.Series(qp).rolling(20,min_periods=10).mean().values/exp
    rvol5=Q.rolling(5).sum().values/(exp*5)
    nmed=lag(roll_med(n.astype(float),60),1)
    tradesK=n/np.maximum(nmed,1)
    r1=np.r_[np.nan,np.diff(np.log(c))]
    sig60=lag(pd.Series(r1).rolling(60,min_periods=30).std().values,1)*100
    newhi=(h>=hi24p).astype(float)
    newhi3=pd.Series(newhi).rolling(3,min_periods=1).max().values
    greens=pd.Series((c>o).astype(int)); grp=(greens!=greens.shift()).cumsum()
    green_run=greens.groupby(grp).cumsum().values*greens.values
    # ---- полка: окна до сигнальной минуты ----
    shelf={}
    for W in (60,120,240):
        hiW=lag(pd.Series(h).rolling(W,min_periods=W//2).max().values,1)
        loW=lag(pd.Series(l).rolling(W,min_periods=W//2).min().values,1)
        shelf[f"range{W}"]=(hiW/loW-1)*100
        shelf[f"brk{W}"]=(c/hiW-1)*100
        shelf[f"rvol_pre{W}"]=pd.Series(qp).rolling(W,min_periods=W//2).mean().values/exp
    r60=pd.Series(shelf["range60"])
    shelf["range60_rel"]=shelf["range60"]/r60.rolling(1440,min_periods=360).median().values
    sig240=lag(pd.Series(r1).rolling(240,min_periods=120).std().values,1)*100
    sig1440=lag(pd.Series(r1).rolling(1440,min_periods=720).std().values,1)*100
    shelf["sig60_rel"]=sig60/sig1440; shelf["sig240_rel"]=sig240/sig1440; shelf["sig1440"]=sig1440
    qcv60=pd.Series(qp).rolling(60,min_periods=30).std().values/np.maximum(pd.Series(qp).rolling(60,min_periods=30).mean().values,1e-9)
    shelf["qcv60"]=qcv60
    p1=pct(1)
    cand=(rvol>=3)&(p1>0)&(q>=1500)&(vol24_prev<=3e6)&np.isfinite(vol24_prev)
    cand[:1440]=False; cand[N-121:]=False
    ix=np.where(cand)[0]
    if len(ix)==0:
        pd.DataFrame().to_parquet(out); return s,0
    # labels: forward windows
    H=120
    fh=swv(h,H)[ix+1]; fl=swv(l,H)[ix+1]; fc=swv(c,H)[ix+1]
    e=c[ix][:,None]
    gh=(fh/e-1)*100; gl=(fl/e-1)*100
    def first(mask):
        a=mask.argmax(1).astype(float); a[~mask.any(1)]=np.inf; return a
    res={}
    for tp,sl,hz in [(10,5,60),(5,3,30),(16,8,120),(20,8,120)]:
        th=first(gh[:,:hz]>=tp); tl=first(gl[:,:hz]<=-sl)
        res[f"y{tp}_{sl}_{hz}"]=(th<tl)&np.isfinite(th)   # tie -> stop first (conservative)
        # trade return
        lastc=(fc[:,hz-1]/e[:,0]-1)*100
        res[f"r{tp}_{sl}_{hz}"]=np.where(np.isfinite(tl)&(tl<=th),-sl,np.where(np.isfinite(th),tp,lastc))
    df=pd.DataFrame(dict(sym=s,t=d.index.values[ix],price=c[ix],q=q[ix],vol24=vol24_prev[ix],
        rvol=rvol[ix],rvol5=rvol5[ix],rvol_pre20=rvol_pre20[ix],
        pct1m=p1[ix],pct2m=pct(2)[ix],pct5m=pct(5)[ix],pct15m=pct(15)[ix],pct60m=pct(60)[ix],pct24h=pct(1440)[ix],
        rng=((c-lo24)/np.where(hi24>lo24,hi24-lo24,np.nan))[ix],vwapDev=((c/vwap-1)*100)[ix],
        atsK=(ats/atsmed)[ix],taker=taker[ix],closePos=closepos[ix],wick=wick[ix],body=body[ix],
        quietCv=(qstd20/np.maximum(qmed20,1e-9))[ix],tradesK=tradesK[ix],sig60=sig60[ix],
        newhi=newhi[ix],newhi3=newhi3[ix],greens=green_run[ix],**{k:v[ix] for k,v in shelf.items()},
        mfe60=gh[:,:60].max(1),mae60=gl[:,:60].min(1),mfe120=gh.max(1),**res))
    for k in df.columns:
        if df[k].dtype==np.float64 and k not in ('t',): df[k]=df[k].astype(np.float32)
    df.to_parquet(out)
    return s,len(df)
if __name__=="__main__":
    syms=open(sys.argv[1]).read().split()
    with cf.ProcessPoolExecutor(int(sys.argv[2]) if len(sys.argv)>2 else 6) as ex:
        for i,(s,k) in enumerate(ex.map(proc,syms)):
            print(i,s,k,flush=True)
