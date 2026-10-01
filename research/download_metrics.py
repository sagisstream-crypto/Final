"""Открытый интерес и long/short (5 мин) с data.binance.vision — только дни вокруг событий."""
import io, os, zipfile, urllib.parse, urllib.request, urllib.error
from concurrent.futures import ThreadPoolExecutor
import pandas as pd

ROOT = "https://data.binance.vision/data/futures/um/daily/metrics"
OUT = "data/metrics"

def fetch(sym, day):
    url = urllib.parse.quote(f"{ROOT}/{sym}/{sym}-metrics-{day}.zip", safe=":/")
    for _ in range(4):
        try:
            raw = urllib.request.urlopen(url, timeout=60).read()
            with zipfile.ZipFile(io.BytesIO(raw)) as z:
                return pd.read_csv(z.open(z.namelist()[0]))
        except urllib.error.HTTPError as e:
            if e.code == 404: return None
        except Exception:
            pass
    return None

if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    E = pd.read_parquet("data/events_r.parquet")
    need = {}
    for s, t in zip(E.sym, E.t):
        for k in (0, 1):
            need.setdefault(s, set()).add((t - pd.Timedelta(days=k)).strftime("%Y-%m-%d"))
    jobs = [(s, d) for s, ds in need.items() if not os.path.exists(f"{OUT}/{s}.parquet") for d in sorted(ds)]
    print("files", len(jobs), flush=True)
    res = {}
    with ThreadPoolExecutor(48) as ex:
        for n, ((s, d), df) in enumerate(zip(jobs, ex.map(lambda j: fetch(*j), jobs))):
            if df is not None: res.setdefault(s, []).append(df)
            if n % 5000 == 0: print(n, flush=True)
    for s, parts in res.items():
        df = pd.concat(parts)
        df["t"] = pd.to_datetime(df.create_time)
        df = df.drop(columns=["create_time", "symbol"]).drop_duplicates("t").sort_values("t")
        df.astype({c: "float32" for c in df.columns if c != "t"}).to_parquet(f"{OUT}/{s}.parquet", index=False)
    print("DONE", len(res))
