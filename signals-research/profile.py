"""Event study: what the tape actually looks like in the 30 minutes around a
candidate trigger, winners (+10% inside 4h) vs everything else."""
import json, numpy as np, features as F
from scan2 import START, END

W = 30

def final_mask(f):
    t = f["taker"]
    return ((f["rvol"] >= 20) & (f["pct5m"] >= 2.0) & (f["range60_rel"] >= 2.5)
            & (f["dist_hi24"] >= -1.0) & (f["pos240"] >= 0.90)
            & ((t >= 0.60) | ~np.isfinite(t)))

def wake_mask(f):
    """'Compression waking up' as the scanner would see it: the pair was quiet
    relative to itself over the last hour, and the current minute breaks out of
    the 15m shelf on real volume."""
    return ((f["range60_rel"] <= 0.8) & (f["rvol"] >= 15) & (f["pct1m"] >= 0.4)
            & (f["break15"] > 0))

def main():
    basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
    acc = {k: {"win": [], "los": []} for k in ("final", "wake")}
    keys = ["rvol", "pct1m", "range15", "range15_rel", "taker", "vol_z", "net_flow_x", "cum"]
    n = {"final": [0, 0], "wake": [0, 0]}
    for s in basket:
        d = F.load_symbol(s, START, END)
        if d is None:
            continue
        f = F.build(d); F.build_extra(d, f)
        g = F.forward_max_gain(f["close"], d["high"], 240)
        c = f["close"]
        for name, mk in (("final", final_mask), ("wake", wake_mask)):
            m = mk(f) & np.isfinite(g)
            m[: F.DAY + 300] = False
            m[len(m) - 300:] = False
            last = -10**9
            idxs = []
            for i in np.flatnonzero(m):
                if i - last >= 120:
                    idxs.append(i); last = i
            for i in idxs:
                win = g[i] >= 10
                n[name][0 if win else 1] += 1
                sl = slice(i - W, i + W + 1)
                row = {k: f[k][sl] for k in keys if k != "cum"}
                row["cum"] = (c[sl] / c[i] - 1) * 100
                acc[name]["win" if win else "los"].append(row)
    out = {}
    for name in acc:
        out[name] = {"n_win": n[name][0], "n_los": n[name][1]}
        for grp in ("win", "los"):
            rows = acc[name][grp]
            if not rows:
                continue
            out[name][grp] = {k: np.nanmedian(np.vstack([r[k] for r in rows]), axis=0).round(3).tolist()
                              for k in keys}
    json.dump(out, open("cache/profiles.json", "w"))
    for name in out:
        print(f"\n=== {name}: winners={out[name]['n_win']} losers={out[name]['n_los']} "
              f"precision={100*out[name]['n_win']/max(1,out[name]['n_win']+out[name]['n_los']):.1f}%")
        for k in keys:
            for grp in ("win", "los"):
                if grp not in out[name]:
                    continue
                v = out[name][grp][k]
                pts = [v[0], v[W - 10], v[W - 5], v[W - 1], v[W], v[W + 5], v[W + 15], v[W + 30]]
                print(f"  {k:<12}{grp}  t-30 {pts[0]:8.2f} | t-10 {pts[1]:8.2f} | t-5 {pts[2]:8.2f} | "
                      f"t-1 {pts[3]:8.2f} | t0 {pts[4]:8.2f} | t+5 {pts[5]:8.2f} | t+15 {pts[6]:8.2f} | t+30 {pts[7]:8.2f}")

if __name__ == "__main__":
    main()
