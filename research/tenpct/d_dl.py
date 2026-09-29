"""Download funding, premium index 1h, metrics (5m->hourly) from data.binance.vision into data_extra_d/.
usage: python d_dl.py fund|prem|metrics [N_top]"""
import sys, os, io, zipfile, requests, pandas as pd, numpy as np, lab10, threading
from concurrent.futures import ThreadPoolExecutor
D = os.path.join(lab10.HERE, "data_extra_d"); B = "https://data.binance.vision/data/futures/um"
S = threading.local()
def get(u):
    if not hasattr(S, "s"): S.s = requests.Session()
    for _ in range(4):
        try:
            r = S.s.get(u, timeout=30)
            if r.status_code == 404: return None
            if r.status_code == 200:
                z = zipfile.ZipFile(io.BytesIO(r.content)); return z.read(z.namelist()[0])
        except Exception: pass
    return None
def rd(b):
    if b is None: return None
    t = pd.read_csv(io.BytesIO(b), header=None)
    if not str(t.iloc[0, 0])[:1].isdigit(): t.columns = t.iloc[0]; t = t.iloc[1:]
    return t
MONTHS = [f"{y}-{m:02d}" for y, m in [(2025, 10), (2025, 11), (2025, 12)] + [(2026, i) for i in range(1, 9)]]
SEPDAYS = [f"2026-09-{d:02d}" for d in range(1, 29)]
DAYS = [d.strftime("%Y-%m-%d") for d in pd.date_range("2025-10-01", "2026-09-28")]
def syms():
    r = []
    for m, s in lab10.all_syms():
        if m != "fut": continue
        H = pd.read_parquet(f"{lab10.DST}/fut_{s}.parquet", columns=["qv"]); r.append((H.qv.sum(), s))
    return [s for _, s in sorted(r, reverse=True)]
def fund(s):
    out = f"{D}/fund_{s}.parquet"
    if os.path.exists(out): return
    fs = [rd(get(f"{B}/monthly/fundingRate/{s}/{s}-fundingRate-{m}.zip")) for m in MONTHS]
    fs = [f for f in fs if f is not None]
    if not fs: pd.DataFrame({"t": [], "fr": []}).to_parquet(out); return
    f = pd.concat(fs); f = pd.DataFrame({"t": f.iloc[:, 0].astype(np.int64).values, "ih": f.iloc[:, 1].astype(float).values,
                                        "fr": f.iloc[:, 2].astype(float).values}); f.to_parquet(out)
def prem(s):
    out = f"{D}/prem_{s}.parquet"
    if os.path.exists(out): return
    fs = [rd(get(f"{B}/monthly/premiumIndexKlines/{s}/1h/{s}-1h-{m}.zip")) for m in MONTHS]
    fs += [rd(get(f"{B}/daily/premiumIndexKlines/{s}/1h/{s}-1h-{d}.zip")) for d in SEPDAYS]
    fs = [f for f in fs if f is not None]
    if not fs: pd.DataFrame({"ot": []}).to_parquet(out); return
    f = pd.concat(fs).iloc[:, :5].astype(float); f.columns = ["ot", "po", "ph", "pl", "pc"]; f["ot"] = f.ot.astype(np.int64)
    f.drop_duplicates("ot").sort_values("ot").to_parquet(out)
def metrics(s):
    out = f"{D}/met_{s}.parquet"
    if os.path.exists(out): return
    H = pd.read_parquet(f"{lab10.DST}/fut_{s}.parquet", columns=["ot"])
    t0, t1 = pd.to_datetime(H.ot.min(), unit="ms").normalize(), pd.to_datetime(H.ot.max(), unit="ms")
    ds = [d for d in DAYS if t0 <= pd.Timestamp(d) <= t1]
    with ThreadPoolExecutor(24) as ex: fs = list(ex.map(lambda d: rd(get(f"{B}/daily/metrics/{s}/{s}-metrics-{d}.zip")), ds))
    fs = [f for f in fs if f is not None]
    if not fs: pd.DataFrame({"ot": []}).to_parquet(out); return
    f = pd.concat(fs); f.columns = ["ts", "sym", "oi", "oiv", "tt_acc", "tt_pos", "gl_acc", "taker"][:f.shape[1]]
    f["t"] = pd.to_datetime(f.ts).values.astype("datetime64[ms]").astype("int64")
    for c in ["oi", "oiv", "tt_acc", "tt_pos", "gl_acc", "taker"]: f[c] = pd.to_numeric(f[c], errors="coerce")
    # value at the last 5m stamp within hour -> known at hour close. stamp t = snapshot time; hour ot contains t in [ot, ot+1h)
    f["ot"] = (f.t - 1) // 3600000 * 3600000  # snapshot at ot+1h (=close) belongs to hour ot
    g = f.sort_values("t").groupby("ot")
    h = g[["oi", "oiv", "tt_acc", "tt_pos", "gl_acc"]].last(); h["taker"] = g.taker.mean()
    h.reset_index().to_parquet(out)
if __name__ == "__main__":
    os.makedirs(D, exist_ok=True)
    L = syms(); pd.Series(L).to_csv(f"{D}/syms_by_qv.csv", index=False)
    n = int(sys.argv[2]) if len(sys.argv) > 2 else len(L)
    fn = {"fund": fund, "prem": prem, "metrics": metrics}[sys.argv[1]]
    with ThreadPoolExecutor(24 if sys.argv[1] != "metrics" else 1) as ex:
        for i, _ in enumerate(ex.map(fn, L[:n])):
            if i % 50 == 0: print(i, flush=True)
