"""Second pass: longer accumulation/compression windows, longer forward horizons,
and an UNCONDITIONAL stride-10 grid so the accumulation study isn't confined to
bars that already have a buy tilt."""
import json, os, time
import numpy as np
import features as F

START, END = "2025-09", "2026-08"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data2")

COLS = ["close", "day_qv", "rvol", "vol_z", "vol_mult", "shelf_cv",
        "range15", "range15_rel", "range60", "range60_rel", "range240", "range240_rel",
        "pos60", "pos240", "pct1m", "pct2m", "pct5m",
        "taker", "taker_w", "taker_frac", "net_flow_x",
        "taker_w60", "net_flow_x60", "taker_w240", "net_flow_x240",
        "ats_x", "rng", "dist_hi24",
        "g60", "g120", "g240", "g360", "g720", "g1440", "d240", "d1440", "c240", "c1440"]


def process(symbol):
    d = F.load_symbol(symbol, START, END)
    if d is None:
        return None
    f = F.build(d)
    F.build_extra(d, f)
    for hz in (60, 120, 240, 360, 720, 1440):
        f[f"g{hz}"] = F.forward_max_gain(f["close"], d["high"], hz)
    f["d240"] = F.forward_min_draw(f["close"], d["low"], 240)
    f["d1440"] = F.forward_min_draw(f["close"], d["low"], 1440)
    f["c240"] = F.forward_close(f["close"], 240)
    f["c1440"] = F.forward_close(f["close"], 1440)

    n = len(f["close"])
    idx = np.arange(n)
    warm = idx >= F.DAY + 240
    have = np.isfinite(f["g1440"]) & np.isfinite(f["rvol"])
    spike_m = warm & have & (np.nan_to_num(f["rvol"], nan=-9) >= 10)
    grid_m = warm & have & (idx % 10 == 0)

    def pack(mask):
        return np.column_stack([f[c][mask] for c in COLS]).astype(np.float32)

    np.savez_compressed(os.path.join(OUT, symbol + ".npz"),
                        spikes=pack(spike_m), grid=pack(grid_m),
                        spikes_ts=d["ts"][spike_m], grid_ts=d["ts"][grid_m],
                        cols=np.array(COLS), n_bars=np.array([n]))
    return int(spike_m.sum()), int(grid_m.sum())


def main():
    os.makedirs(OUT, exist_ok=True)
    basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
    t0 = time.time()
    for i, s in enumerate(basket):
        if os.path.exists(os.path.join(OUT, s + ".npz")):
            continue
        try:
            r = process(s)
        except Exception as e:
            print("ERR", s, repr(e), flush=True); continue
        print(f"[{i+1}/{len(basket)}] {s:<14} spikes={r[0]:>6} grid={r[1]:>6} {time.time()-t0:.0f}s", flush=True)
    print("DONE", time.time() - t0)


if __name__ == "__main__":
    main()
