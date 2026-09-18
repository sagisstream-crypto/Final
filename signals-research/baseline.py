"""Unconditional base rates: how often does ANY random minute of a basket coin
run +N% within a given horizon. Everything a signal produces is judged against
these numbers."""
import json, os, numpy as np, features as F
from scan import START, END

def main():
    basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
    out = {}
    for s in basket:
        d = F.load_symbol(s, START, END)
        if d is None:
            continue
        c, h, l = d["close"], d["high"], d["low"]
        rec = {}
        for hz in (60, 120, 240, 360):
            g = F.forward_max_gain(c, h, hz)[F.DAY::60]
            g = g[np.isfinite(g)]
            rec[f"g{hz}"] = [float(np.mean(g >= t)) for t in (3, 5, 10, 15)]
            rec[f"n{hz}"] = int(len(g))
        out[s] = rec
        print(s, {k: [round(x, 4) for x in v] for k, v in rec.items() if k == "g240"}, flush=True)
    json.dump(out, open("cache/baserates.json", "w"))
    agg = {}
    for hz in (60, 120, 240, 360):
        tot = sum(out[s][f"n{hz}"] for s in out)
        for i, t in enumerate((3, 5, 10, 15)):
            agg[f"h{hz}_ge{t}"] = sum(out[s][f"g{hz}"][i] * out[s][f"n{hz}"] for s in out) / tot
    print("AGG", json.dumps({k: round(v, 5) for k, v in agg.items()}, indent=1))

if __name__ == "__main__":
    main()
