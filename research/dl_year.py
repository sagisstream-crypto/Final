import json, urllib.request, urllib.error, io, zipfile, os, concurrent.futures as cf, pandas as pd, numpy as np, time
MONTHS = [f"2025-{m:02d}" for m in (10,11,12)] + [f"2026-{m:02d}" for m in range(1,9)]
DAYS = [f"2026-09-{d:02d}" for d in range(1,29)]
cols = "ot o h l c v ct qv n tbv tbqv ig".split()
def get(u):
    for a in range(4):
        try:
            with urllib.request.urlopen(u, timeout=60) as r: return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception: pass
        time.sleep(2 ** a)
    return None
def rd(b):
    z = zipfile.ZipFile(io.BytesIO(b)); df = pd.read_csv(z.open(z.namelist()[0]), header=None)
    if not str(df.iloc[0, 0]).isdigit(): df = df.iloc[1:]
    df.columns = cols; return df
def one(ms):
    mkt, sym = ms; out = f"data_year/{mkt}_{sym}.parquet"
    if os.path.exists(out) or os.path.exists(out + ".none"): return 0
    base = "futures/um" if mkt == "fut" else "spot"; parts = []
    for m in MONTHS:
        b = get(f"https://data.binance.vision/data/{base}/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip")
        if b: parts.append(rd(b))
    if parts and MONTHS[-1] in "".join([]) or parts:
        last = pd.to_numeric(parts[-1]["ot"]).max()
        if last > 1.785e12 or last > 1.785e15:  # has August 2026 data -> fetch September days
            for d in DAYS:
                b = get(f"https://data.binance.vision/data/{base}/daily/klines/{sym}/5m/{sym}-5m-{d}.zip")
                if b: parts.append(rd(b))
    if not parts:
        open(out + ".none", "w").close(); return 0
    df = pd.concat(parts).astype(float)
    df["ot"] = df["ot"].where(df["ot"] < 1e14, df["ot"] // 1000)
    df = df[["ot","o","h","l","c","qv","n","tbqv"]].drop_duplicates("ot").sort_values("ot").reset_index(drop=True)
    if len(df) < 2100:
        open(out + ".none", "w").close(); return 0
    df.to_parquet(out); return 1
uni = json.load(open("uni_all.json"))  # [[mkt, SYM], ...] from backtest_vynos.list_all()
done = 0
with cf.ThreadPoolExecutor(32) as ex:
    for i, r in enumerate(ex.map(one, uni), 1):
        done += r
        if i % 100 == 0: print(i, len(uni), "saved", done, flush=True)
print("DONE saved", done)
