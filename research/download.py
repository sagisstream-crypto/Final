"""Качаем 5m свечи USDT-M (окт 2025 – сен 2026) из data.binance.vision → parquet на символ."""
import io, os, sys, zipfile, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

ROOT = "https://data.binance.vision/data/futures/um"
OUT = os.environ.get("KLINE_DIR", "data/k5m")
MONTHS = [f"2025-{m:02d}" for m in (10, 11, 12)] + [f"2026-{m:02d}" for m in range(1, 9)]
DAYS = [f"2026-09-{d:02d}" for d in range(1, 31)]
COLS = ["open_time", "o", "h", "l", "c", "vol", "close_time", "qv", "trades", "tb_vol", "tb_qv", "ignore"]

def fetch(url):
    try:
        raw = urllib.request.urlopen(urllib.parse.quote(url, safe=":/"), timeout=60).read()
    except urllib.error.HTTPError as e:
        if e.code == 404: return None
        raise
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        txt = z.read(z.namelist()[0]).decode()
    lines = [l for l in txt.splitlines() if l and l[0].isdigit()]  # отрезаем заголовок
    if not lines: return None
    df = pd.read_csv(io.StringIO("\n".join(lines)), header=None, names=COLS)
    return df[["open_time", "o", "h", "l", "c", "qv", "trades", "tb_qv"]]

def get(url):
    for i in range(4):
        try: return fetch(url)
        except Exception as e:
            err = e
    print("FAIL", url, err, file=sys.stderr); return None

def one(sym):
    path = f"{OUT}/{sym}.parquet"
    if os.path.exists(path): return sym, "cached"
    parts = []
    for m in MONTHS:
        d = get(f"{ROOT}/monthly/klines/{sym}/5m/{sym}-5m-{m}.zip")
        if d is not None: parts.append(d)
    if not parts and get(f"{ROOT}/daily/klines/{sym}/5m/{sym}-5m-2026-09-15.zip") is None:
        open(path + ".none", "w").close(); return sym, "empty"
    for d in DAYS:
        x = get(f"{ROOT}/daily/klines/{sym}/5m/{sym}-5m-{d}.zip")
        if x is not None: parts.append(x)
    if not parts:
        open(path + ".none", "w").close(); return sym, "empty"
    df = pd.concat(parts).drop_duplicates("open_time").sort_values("open_time")
    for c in ["o", "h", "l", "c", "qv", "tb_qv"]: df[c] = df[c].astype("float32")
    df["trades"] = df["trades"].astype("int32")
    df.to_parquet(path, index=False)
    return sym, len(df)

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    syms = [s for s in open("symbols.txt").read().split() if not os.path.exists(f"{OUT}/{s}.parquet.none")]
    done = 0
    with ThreadPoolExecutor(24) as ex:
        for sym, r in ex.map(one, syms):
            done += 1
            if done % 50 == 0: print(done, sym, r, flush=True)
    print("DONE")
