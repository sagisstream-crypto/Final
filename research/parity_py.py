import lib, vynos_signal as a_best, json, pandas as pd, numpy as np, sys
out = {}
for m, s in lib.symbols()[::7]:
    df = lib.load(m, s); sig, _, tp, sl = a_best.signal(df)
    t = lib.simulate(df, sig, 1, tp, sl, a_best.HOLD)
    out[f"{m}_{s}"] = {"bars": df[["ot","o","h","l","c"]].values.tolist(),
                       "sig": np.flatnonzero(sig).tolist(), "trades": [[int(a), round(b, 8)] for a, b in zip(t.i, t.ret)]}
json.dump(out, open("parity.json", "w"))
print(len(out), sum(len(v["sig"]) for v in out.values()), sum(len(v["trades"]) for v in out.values()))
