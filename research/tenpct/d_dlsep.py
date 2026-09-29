"""Sep-2026 funding (monthly archive not yet published) from www.binance.com/fapi/v1/fundingRate -> data_extra_d/fundsep_{SYM}.parquet"""
import requests, pandas as pd, numpy as np, os, lab10, time
from concurrent.futures import ThreadPoolExecutor
D = f"{lab10.HERE}/data_extra_d"
T0 = pd.Timestamp("2026-08-25").value // 10**6
def one(s):
    out = f"{D}/fundsep_{s}.parquet"
    if os.path.exists(out): return 0
    for _ in range(4):
        try:
            r = requests.get("https://www.binance.com/fapi/v1/fundingRate", params=dict(symbol=s, startTime=T0, limit=1000), timeout=20)
            if r.status_code == 200:
                j = r.json(); f = pd.DataFrame(j)
                if len(f): f = pd.DataFrame({"t": f.fundingTime.astype(np.int64), "fr": f.fundingRate.astype(float)})
                else: f = pd.DataFrame({"t": [], "fr": []})
                f.to_parquet(out); return len(f)
            time.sleep(2)
        except Exception: time.sleep(2)
    return -1
L = pd.read_csv(f"{D}/syms_by_qv.csv").iloc[:, 0].tolist()
with ThreadPoolExecutor(6) as ex: r = list(ex.map(one, L))
print(sum(x > 0 for x in r), sum(x == 0 for x in r), sum(x < 0 for x in r))
