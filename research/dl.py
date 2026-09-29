import json, urllib.request, io, zipfile, os, sys, concurrent.futures as cf
import pandas as pd, numpy as np
MAJORS = set("BTC ETH BNB SOL XRP DOGE ADA TRX TON LINK AVAX LTC BCH DOT XLM SHIB HBAR SUI UNI NEAR APT ICP ETC FIL ATOM".split())
STABLE = set("USDC FDUSD TUSD USD1 RLUSD USDP DAI EUR XAUT PAXG U USDE BFUSD AEUR".split())
MONTHS = ["2026-03","2026-04","2026-05","2026-06","2026-07","2026-08"]
TF = "5m"
def get(url):
    with urllib.request.urlopen(url, timeout=60) as r: return r.read()
def ok_sym(s):
    if not s.endswith("USDT"): return False
    b = s[:-4]
    return b.isascii() and b.isalnum() and b not in MAJORS and b not in STABLE and not b.endswith(("UP","DOWN","BULL","BEAR")) and not s.endswith("BUSDT") or False
t = json.loads(get("https://data-api.binance.vision/api/v3/ticker/24hr"))
spot = [x for x in t if ok_sym(x["symbol"]) and float(x["quoteVolume"])>2e6]
spot.sort(key=lambda x:-float(x["quoteVolume"]))
spot_syms = [x["symbol"] for x in spot][:90]
import re
lst = get("https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/um/monthly/klines/&delimiter=/").decode()
fut_all = set(re.findall(r"klines/([A-Z0-9]+)/", lst))
# futures: take alts; rank by spot volume when available, else include list below
fut_syms = [s for s in spot_syms if s in fut_all][:70]
extra = [s for s in sorted(fut_all) if ok_sym(s) and s not in fut_syms]
print(len(spot_syms), len(fut_syms), len(extra))
cols="ot o h l c v ct qv n tbv tbqv ig".split()
def fetch(mkt, sym):
    out = f"data/{mkt}_{sym}.parquet"
    if os.path.exists(out): return sym, "cached"
    base = "futures/um" if mkt=="fut" else "spot"
    parts=[]
    for m in MONTHS:
        try:
            z = zipfile.ZipFile(io.BytesIO(get(f"https://data.binance.vision/data/{base}/monthly/klines/{sym}/{TF}/{sym}-{TF}-{m}.zip")))
            df = pd.read_csv(z.open(z.namelist()[0]), header=None)
            if not str(df.iloc[0,0]).isdigit(): df = df.iloc[1:]
            df.columns = cols; parts.append(df)
        except Exception as e: pass
    if not parts: return sym, "none"
    df = pd.concat(parts).astype(float)
    df["ot"] = df["ot"].where(df["ot"]<1e14, df["ot"]//1000)
    df = df[["ot","o","h","l","c","v","qv","n","tbqv"]].drop_duplicates("ot").sort_values("ot").reset_index(drop=True)
    if len(df) < 20000: return sym, f"short {len(df)}"
    df.to_parquet(out); return sym, len(df)
jobs=[("spot",s) for s in spot_syms]+[("fut",s) for s in fut_syms]
with cf.ThreadPoolExecutor(16) as ex:
    for r in ex.map(lambda a: fetch(*a), jobs): print(r, flush=True)
