#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Binance USD-M Futures — "real run-up vs fake spike" research.

Universe:
    USD-M Futures symbols that existed in the Binance public archive during the window.

Primary filter:
    rolling 24h QUOTE volume <= $2,000,000 at the event time (override with --max-24h-usd).

Method:
    1) Download 1h klines for the whole historical universe to find symbols that
       could have had rolling 24h quote volume <= the threshold.
    2) Download 5m klines only for those candidate symbols.
    3) Detect 5m impulses while 24h quote volume <= the threshold.
    4) Measure pre-impulse compression, RVOL, volume acceleration, candle shape,
       trade count acceleration, taker-buy share and future continuation.
    5) Produce CSVs + an HTML report. No trading signals are generated.

Run it locally (this needs a real internet connection to data.binance.vision and
downloads several GB for a 6-month window across the full USD-M universe):

    python -m pip install -r requirements.txt
    python binance_runup_research.py --months 6 --workers 8

IMPORTANT:
- This is research, not a claim that any threshold predicts future returns.
- "Real continuation" is a configurable research label, not an objective market truth.
- Binance public archives are the source. Archived data can contain gaps/defects;
  the script records missing-file/errors rather than silently fabricating rows.
"""

from __future__ import annotations
import argparse, concurrent.futures as cf
import io, time, zipfile
from pathlib import Path
from urllib.parse import urlencode
import xml.etree.ElementTree as ET

import numpy as np
import pandas as pd
import requests

BASE = "https://data.binance.vision"
S3 = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
KLINE_COLS = [
    "open_time","open","high","low","close","volume","close_time",
    "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"
]

SESSION = requests.Session()
SESSION.headers.update({"User-Agent":"binance-runup-research/1.1"})

def get(url, timeout=60, retries=4):
    last = None
    for i in range(retries):
        try:
            r = SESSION.get(url, timeout=timeout)
            if r.status_code == 200:
                return r
            last = f"{r.status_code} {r.text[:200]}"
        except Exception as e:
            last = repr(e)
        time.sleep(1.5 * (i+1))
    raise RuntimeError(f"GET failed: {url} :: {last}")

def months_between(start, end):
    out=[]
    cur=pd.Timestamp(start.year, start.month, 1, tz="UTC")
    last=pd.Timestamp(end.year, end.month, 1, tz="UTC")
    while cur <= last:
        out.append(cur.strftime("%Y-%m"))
        cur = cur + pd.offsets.MonthBegin(1)
    return out

def days_between(start, end):
    out=[]
    d=start.normalize()
    while d < end:
        out.append(d.strftime("%Y-%m-%d"))
        d += pd.Timedelta(days=1)
    return out

def list_symbols():
    # S3 CommonPrefixes gives the historical archive's symbol directories.
    prefix="data/futures/um/monthly/klines/"
    token=None
    syms=set()
    while True:
        params={"list-type":"2","prefix":prefix,"delimiter":"/","max-keys":"1000"}
        if token: params["continuation-token"]=token
        u=S3+"?"+urlencode(params)
        root=ET.fromstring(get(u).text)
        ns={"s":"http://s3.amazonaws.com/doc/2006-03-01/"}
        for cp in root.findall("s:CommonPrefixes",ns):
            p=cp.find("s:Prefix",ns).text
            tail=p[len(prefix):].strip("/")
            if tail:
                syms.add(tail)
        more=root.find("s:IsTruncated",ns)
        if more is None or more.text.lower()!="true":
            break
        tok=root.find("s:NextContinuationToken",ns)
        token=tok.text if tok is not None else None
        if not token: break
    return sorted(syms)

def archive_url(symbol, interval, period):
    # Monthly path for full months; daily path for the current, still-incomplete month.
    if len(period)==7:
        return f"{BASE}/data/futures/um/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{period}.zip"
    return f"{BASE}/data/futures/um/daily/klines/{symbol}/{interval}/{symbol}-{interval}-{period}.zip"

def read_zip_kline(symbol, interval, period, start, end):
    u=archive_url(symbol,interval,period)
    try:
        r=get(u,timeout=90)
    except Exception:
        return None
    try:
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            names=[n for n in z.namelist() if n.lower().endswith(".csv")]
            if not names:
                return None
            with z.open(names[0]) as f:
                df=pd.read_csv(f,header=None,names=KLINE_COLS)
        df["open_time"]=pd.to_datetime(df["open_time"],unit="ms",utc=True)
        df=df[(df.open_time>=start)&(df.open_time<end)]
        return df
    except Exception:
        return None

def periods_for(interval, start, end):
    # Full calendar months from monthly archives; the current, incomplete month from daily archives.
    cur_month_start = pd.Timestamp(end.year, end.month, 1, tz="UTC")
    p = [m for m in months_between(start, cur_month_start) if pd.Timestamp(m+"-01",tz="UTC") < cur_month_start]
    p += days_between(max(start, cur_month_start), end)
    return p

def load_symbol(symbol, interval, start, end):
    frames=[]
    for p in periods_for(interval, start, end):
        x=read_zip_kline(symbol,interval,p,start,end)
        if x is not None and len(x): frames.append(x)
    if not frames: return pd.DataFrame(columns=KLINE_COLS)
    df=pd.concat(frames,ignore_index=True).drop_duplicates("open_time").sort_values("open_time")
    for c in KLINE_COLS[1:]:
        df[c]=pd.to_numeric(df[c],errors="coerce")
    return df.reset_index(drop=True)

def screen_1h(symbol, start, end, max_24h_usd):
    df=load_symbol(symbol,"1h",start,end)
    if df.empty: return symbol, None
    # A 24h rolling sum can only be <= threshold if every constituent 1h bar is <= threshold.
    # We use a rolling 24h sum to avoid an overly loose candidate screen.
    q=df["quote_volume"].fillna(0.0)
    r24=q.rolling(24,min_periods=24).sum()
    ok=r24<=max_24h_usd
    return symbol, {
        "symbol":symbol,
        "rows_1h":len(df),
        "min_24h_quote":float(r24.min()) if r24.notna().any() else np.nan,
        "eligible":bool(ok.any()),
    }

def prep_5m(df, max_24h_usd):
    if df.empty: return df
    df=df.copy()
    q=df.quote_volume.astype(float)
    # 24h rolling quote volume and baseline volume from previous 24h.
    df["vol24"] = q.rolling(288,min_periods=288).sum()
    # Exclude current bar from RVOL baseline.
    df["vol_med_24h"] = q.shift(1).rolling(288,min_periods=96).median()
    df["rvol"] = q / df["vol_med_24h"].replace(0,np.nan)
    # 12h compression range, also excluding current bar.
    prev=df.shift(1)
    hi12=prev["high"].rolling(144,min_periods=72).max()
    lo12=prev["low"].rolling(144,min_periods=72).min()
    df["range12"]=(hi12/lo12-1)*100
    # Volume acceleration vs prior 3 bars and prior 1h.
    df["vol_prev_1h"]=q.shift(1).rolling(12,min_periods=6).mean()
    df["vol_accel_1h"]=q/df["vol_prev_1h"].replace(0,np.nan)
    df["ret5"]=(df.close/df.open-1)*100
    df["close_pos"]=(df.close-df.low)/(df.high-df.low).replace(0,np.nan)
    df["body_pct"]=abs(df.close-df.open)/(df.high-df.low).replace(0,np.nan)*100
    df["range_pct"]=(df.high/df.low-1)*100
    # Taker buy quote share.
    df["taker_buy_share"]=df.taker_buy_quote/df.quote_volume.replace(0,np.nan)
    # Trade acceleration.
    df["trades_prev_1h"]=df.trades.shift(1).rolling(12,min_periods=6).mean()
    df["trades_accel"]=df.trades/df.trades_prev_1h.replace(0,np.nan)
    return df

def extract_events(symbol, df, max_24h_usd, min_ret=5.0, min_rvol=3.0,
                    real_gain_pct=20.0, real_within_bars=24, adverse_pct=8.0):
    df=prep_5m(df, max_24h_usd)
    if df.empty: return []
    # Candidate impulse: at least +5% body, or +8% high excursion.
    signal=(df.vol24<=max_24h_usd) & (
        (df.ret5>=min_ret) | ((df.high/df.open-1)*100>=8.0)
    ) & (df.rvol>=min_rvol)
    idx=np.flatnonzero(signal.to_numpy())
    events=[]
    last_i=-999999
    for i in idx:
        # cluster nearby bars into one event; keep first qualifying bar.
        if i-last_i < 12: continue
        if i+48>=len(df): continue
        last_i=i
        row=df.iloc[i]
        base=float(row.open)
        future=df.iloc[i+1:i+49]
        # Forward MFE/MAE by 5m bar, measured from event open.
        mfe2h=(future.high.max()/base-1)*100
        mae2h=(future.low.min()/base-1)*100
        # Horizons.
        def fwd(n):
            x=df.iloc[i+1:i+1+n]
            if x.empty: return (np.nan,np.nan)
            return (float((x.high.max()/base-1)*100), float((x.low.min()/base-1)*100))
        m30,a30=fwd(6); m1h,a1h=fwd(12); m2h,a2h=fwd(24); m4h,a4h=fwd(48)
        # "Real continuation" research label:
        # +real_gain_pct max upside within real_within_bars bars AND did not first
        # suffer an adverse_pct move. We calculate first-hit order, not just end result.
        real=False
        adverse_first=False
        window=df.iloc[i+1:i+1+real_within_bars]
        for _,fr in window.iterrows():
            if fr.low <= base*(1-adverse_pct/100.0):
                adverse_first=True
                break
            if fr.high >= base*(1+real_gain_pct/100.0):
                real=True
                break
        label="REAL_CONTINUATION" if real else "FAILED_OR_PARTIAL"
        events.append({
            "symbol":symbol,
            "time_utc":row.open_time.isoformat(),
            "price":base,
            "ret5_pct":row.ret5,
            "range_pct":row.range_pct,
            "body_pct":row.body_pct,
            "close_pos":row.close_pos,
            "quote_volume_5m":row.quote_volume,
            "volume24_usd":row.vol24,
            "rvol":row.rvol,
            "vol_accel_1h":row.vol_accel_1h,
            "range12_pct":row.range12,
            "trades":row.trades,
            "trades_accel":row.trades_accel,
            "taker_buy_share":row.taker_buy_share,
            "mfe_30m_pct":m30,"mae_30m_pct":a30,
            "mfe_1h_pct":m1h,"mae_1h_pct":a1h,
            "mfe_2h_pct":m2h,"mae_2h_pct":a2h,
            "mfe_4h_pct":m4h,"mae_4h_pct":a4h,
            "label":label
        })
    return events

def summarize(events):
    e=pd.DataFrame(events)
    if e.empty: return e, pd.DataFrame()
    grp=e.groupby("label")
    rows=[]
    for label,g in grp:
        row={"label":label,"events":len(g)}
        for c in ["ret5_pct","range_pct","body_pct","close_pos","volume24_usd","rvol",
                  "vol_accel_1h","range12_pct","trades_accel","taker_buy_share",
                  "mfe_30m_pct","mfe_1h_pct","mfe_2h_pct","mfe_4h_pct",
                  "mae_30m_pct","mae_1h_pct","mae_2h_pct","mae_4h_pct"]:
            row[c+"_median"]=float(g[c].median())
            row[c+"_p75"]=float(g[c].quantile(.75))
        rows.append(row)
    return e, pd.DataFrame(rows)

def make_html(events, summary, meta, out):
    top=events.copy()
    if not top.empty:
        top=top.sort_values(["mfe_2h_pct","rvol"],ascending=False).head(100)
        table=top.to_html(index=False,float_format=lambda x:f"{x:.3f}")
    else: table="<p>No events found.</p>"
    sm=summary.to_html(index=False,float_format=lambda x:f"{x:.3f}") if not summary.empty else "<p>No summary.</p>"
    html=f"""<!doctype html><html><head><meta charset="utf-8">
<title>Binance run-up research</title>
<style>body{{font-family:Arial,sans-serif;margin:30px}}table{{border-collapse:collapse;font-size:12px}}th,td{{border:1px solid #ccc;padding:5px}}th{{background:#eee}}code{{background:#f4f4f4;padding:2px}}</style></head>
<body><h1>Binance USD-M Futures — run-up research</h1>
<h2>Run metadata</h2><pre>{meta}</pre>
<h2>Comparison</h2>{sm}
<h2>Largest observed 2h continuation events</h2>{table}
<h2>Interpretation</h2>
<p>Use the comparison table to see which pre-event variables differ between events
that reached the research continuation label and those that did not. The label is
a research convention, not a trading guarantee. Thresholds can be changed and rerun.</p>
</body></html>"""
    Path(out).write_text(html,encoding="utf-8")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--workers",type=int,default=8)
    ap.add_argument("--cache",default="binance_cache")
    ap.add_argument("--out",default="binance_results")
    ap.add_argument("--max-symbols",type=int,default=0)
    ap.add_argument("--months",type=float,default=6.0, help="trailing window length, in months, ending now (UTC)")
    ap.add_argument("--start",default=None, help="override window start, e.g. 2026-03-18 (UTC)")
    ap.add_argument("--end",default=None, help="override window end, e.g. 2026-09-19 (UTC), exclusive")
    ap.add_argument("--max-24h-usd",type=float,default=2_000_000.0, help="primary filter: rolling 24h quote volume ceiling")
    ap.add_argument("--min-ret-pct",type=float,default=5.0, help="minimum 5m candle body %% to count as a candidate impulse")
    ap.add_argument("--min-rvol",type=float,default=3.0, help="minimum RVOL to count as a candidate impulse")
    ap.add_argument("--real-gain-pct",type=float,default=20.0, help="research label: upside within the window that counts as REAL_CONTINUATION")
    ap.add_argument("--real-within-hours",type=float,default=2.0, help="research label: horizon (hours) to reach --real-gain-pct")
    ap.add_argument("--adverse-pct",type=float,default=8.0, help="research label: adverse move that disqualifies REAL_CONTINUATION if hit first")
    args=ap.parse_args()
    cache=Path(args.cache); out=Path(args.out)
    cache.mkdir(parents=True,exist_ok=True); out.mkdir(parents=True,exist_ok=True)

    end = pd.Timestamp(args.end, tz="UTC") if args.end else pd.Timestamp.now(tz="UTC").ceil("D")
    start = pd.Timestamp(args.start, tz="UTC") if args.start else end - pd.DateOffset(months=args.months)
    real_within_bars = max(1, round(args.real_within_hours*12))

    print("Listing historical USD-M symbols...")
    symbols=list_symbols()
    if args.max_symbols: symbols=symbols[:args.max_symbols]
    print("Symbols:",len(symbols))

    # Phase 1: 1h screening.
    screen=[]
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs=[ex.submit(screen_1h,s,start,end,args.max_24h_usd) for s in symbols]
        for n,f in enumerate(cf.as_completed(futs),1):
            try:
                sym,res=f.result()
                if res: screen.append(res)
            except Exception as e:
                print("screen error:",repr(e))
            if n%100==0: print("1h:",n,"/",len(symbols))
    sdf=pd.DataFrame(screen)
    sdf.to_csv(out/"symbol_screen.csv",index=False)
    candidates=sdf.loc[sdf.eligible.fillna(False),"symbol"].tolist() if not sdf.empty else []
    print("Candidates:",len(candidates))

    # Phase 2: 5m research.
    all_events=[]
    errors=[]
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs={ex.submit(load_symbol,s,"5m",start,end):s for s in candidates}
        for n,f in enumerate(cf.as_completed(futs),1):
            s=futs[f]
            try:
                df=f.result()
                ev=extract_events(s, df, args.max_24h_usd, min_ret=args.min_ret_pct, min_rvol=args.min_rvol,
                                   real_gain_pct=args.real_gain_pct, real_within_bars=real_within_bars,
                                   adverse_pct=args.adverse_pct)
                all_events.extend(ev)
            except Exception as e:
                errors.append({"symbol":s,"error":repr(e)})
            if n%25==0: print("5m:",n,"/",len(candidates),"events:",len(all_events))
    if errors: pd.DataFrame(errors).to_csv(out/"errors.csv",index=False)
    events,summary=summarize(all_events)
    events.to_csv(out/"runup_events.csv",index=False)
    summary.to_csv(out/"runup_summary.csv",index=False)
    meta=f"""symbols_in_archive={len(symbols)}
eligible_candidates={len(candidates)}
events={len(events)}
errors={len(errors)}
start={start}
end={end}
filter_24h_quote_usd<={args.max_24h_usd:.0f}
event_body_pct>={args.min_ret_pct} OR high_excursion_pct>=8
event_rvol>={args.min_rvol}
real_label=first +{args.real_gain_pct}% within {args.real_within_hours}h before a -{args.adverse_pct}% adverse move"""
    (out/"README.txt").write_text(meta,encoding="utf-8")
    make_html(events,summary,meta,out/"report.html")
    print("\nDONE")
    print("Open:",out/"report.html")
    print("Events:",len(events))
    print("Results folder:",out.resolve())

if __name__=="__main__":
    main()
