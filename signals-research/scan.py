"""Sweep the whole basket, emitting per-symbol candidate tables for the study.

Two tables per symbol, both stored as float32 .npz in data/ (gitignored):
  spikes - every minute whose volume is at least mildly anomalous (transient
           events: compression wake-ups, volume explosions)
  grid   - a stride-5 sample of every minute whose 20-minute taker pressure is
           tilted to the buy side (persistent states: accumulation)
Forward outcomes are attached here, once, so all downstream threshold sweeps are
pure post-processing.
"""
import json, os, sys, time
import numpy as np
import features as F

START, END = "2025-09", "2026-08"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

COLS = ["ts", "close", "day_qv", "rvol", "vol_z", "vol_mult", "shelf_cv",
        "range15", "range15_rel", "range60", "range60_rel",
        "pct1m", "pct2m", "pct5m", "taker", "taker_w", "taker_frac",
        "net_flow_x", "ats_x", "rng", "dist_hi24", "break15",
        "g60", "g120", "g240", "g360", "d240", "c240"]


def process(symbol):
    d = F.load_symbol(symbol, START, END)
    if d is None:
        return None
    f = F.build(d)
    f["g60"] = F.forward_max_gain(f["close"], d["high"], 60)
    f["g120"] = F.forward_max_gain(f["close"], d["high"], 120)
    f["g240"] = F.forward_max_gain(f["close"], d["high"], 240)
    f["g360"] = F.forward_max_gain(f["close"], d["high"], 360)
    f["d240"] = F.forward_min_draw(f["close"], d["low"], 240)
    f["c240"] = F.forward_close(f["close"], 240)
    f["ts"] = d["ts"].astype(float)

    n = len(f["close"])
    idx = np.arange(n)
    warm = idx >= F.DAY + 60          # need a full trailing day of baseline
    have = np.isfinite(f["g240"]) & np.isfinite(f["rvol"]) & np.isfinite(f["vol_z"])

    spike_m = warm & have & (np.nan_to_num(f["vol_z"], nan=-9) >= 2.0)
    grid_m = (warm & have & (idx % 5 == 0)
              & (np.nan_to_num(f["taker_w"], nan=0) >= 0.55))

    def pack(mask):
        return np.column_stack([np.nan_to_num(f[c], nan=np.nan)[mask] for c in COLS]).astype(np.float32)

    np.savez_compressed(os.path.join(OUT, symbol + ".npz"),
                        spikes=pack(spike_m), grid=pack(grid_m),
                        n_bars=np.array([n]), cols=np.array(COLS))
    return spike_m.sum(), grid_m.sum(), n


def main():
    os.makedirs(OUT, exist_ok=True)
    basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
    t0 = time.time()
    for i, s in enumerate(basket):
        p = os.path.join(OUT, s + ".npz")
        if os.path.exists(p):
            continue
        try:
            r = process(s)
        except Exception as e:
            print("ERR", s, repr(e), flush=True); continue
        if r is None:
            print("skip", s, flush=True); continue
        print(f"[{i+1}/{len(basket)}] {s:<14} bars={r[2]:>7} spikes={r[0]:>6} grid={r[1]:>6} "
              f"{time.time()-t0:.0f}s", flush=True)
    print("DONE", time.time() - t0)


if __name__ == "__main__":
    main()
