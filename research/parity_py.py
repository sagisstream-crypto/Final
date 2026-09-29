"""Dump a sample of symbols with Python signals/trades (backtest_vynos.py) for research/parity.js.
python parity_py.py DATA_DIR  (dir of {mkt}_{SYM}.parquet with ot o h l c qv)"""
import glob, json, sys, numpy as np, pandas as pd
import backtest_vynos as B
out = {}
for f in sorted(glob.glob(sys.argv[1] + "/*.parquet"))[::25]:
    df = pd.read_parquet(f)[["ot", "o", "h", "l", "c", "qv"]].reset_index(drop=True)
    sig, w, _ = B.signals(df)
    tr = B.trades_for(df, "x", "x", 0.0)
    idx = {t: i for i, t in enumerate(df.ot.values)}
    out[f.split("/")[-1][:-8]] = {"bars": df.values.tolist(), "sig": np.flatnonzero(sig).tolist(),
                                  "trades": [[idx[t["entry_time"]] - 1, round(t["ret"], 8)] for t in tr]}
json.dump(out, open("parity.json", "w"))
print(len(out), "symbols,", sum(len(v["sig"]) for v in out.values()), "signals,", sum(len(v["trades"]) for v in out.values()), "trades")
