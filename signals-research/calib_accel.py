"""Walk-forward calibration of the acceleration detector + noise-rejection proof."""
import hashlib, json, sys
import numpy as np
import features as F
import accel as A

START, END = "2025-09", "2026-08"
SPLIT_TS = 1777939200000
COOL = 120           # bars (=minutes) of dedup between fires of the same pair


def sym_group(s):
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % 2


def dedup_idx(idx, cool=COOL):
    out, last = [], -10 ** 9
    for i in idx:
        if i - last >= cool:
            out.append(i); last = i
    return np.array(out, dtype=int)


def run_lengths(mask):
    """For each index, the length of the True-run it belongs to (0 if False)."""
    n = len(mask)
    out = np.zeros(n, dtype=int)
    i = 0
    while i < n:
        if not mask[i]:
            i += 1; continue
        j = i
        while j < n and mask[j]:
            j += 1
        out[i:j] = j - i
        i = j
    return out


VARIANTS = {
    "A w=10 p=1": dict(w=10, persist=1),
    "A w=10 p=2": dict(w=10, persist=2),
    "A w=10 p=3": dict(w=10, persist=3),
    "B w=15 p=2": dict(w=15, persist=2),
    "C w=10 p=2 r2p=.45": dict(w=10, persist=2, min_r2_p=0.45),
    "D w=10 p=2 gain=2": dict(w=10, persist=2, min_gain=2.0),
    "E w=10 p=2 rvol=15": dict(w=10, persist=2, min_rvol=15.0),
    "F w=8  p=2": dict(w=8, persist=2),
}


def main():
    basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
    res = {k: {g: [0, 0] for g in ("all", "coinA", "coinB", "t_in", "t_out")} for k in VARIANTS}
    res["NAIVE rvol>=30 & pct1m>=0.6"] = {g: [0, 0] for g in ("all", "coinA", "coinB", "t_in", "t_out")}
    blips = {k: [0, 0] for k in list(VARIANTS) + ["NAIVE rvol>=30 & pct1m>=0.6"]}
    gains = {k: [] for k in blips}
    for s in basket:
        d = F.load_symbol(s, START, END)
        if d is None:
            continue
        f = F.build(d)
        g240 = F.forward_max_gain(f["close"], d["high"], 240)
        ts = d["ts"]
        hot = np.nan_to_num(f["rvol"], nan=0) >= 10
        rl = run_lengths(hot)
        warm = np.arange(len(ts)) >= F.DAY + 60
        ok = warm & np.isfinite(g240)
        grp = "coinA" if sym_group(s) == 0 else "coinB"
        cand = {}
        for name, kw in VARIANTS.items():
            p = kw.pop("persist")
            st = A.accel_state(f["close"], d["qv"], f["avg_min"], **kw)
            kw["persist"] = p
            cand[name] = A.persist(st["state"], p) & ok
        cand["NAIVE rvol>=30 & pct1m>=0.6"] = ((np.nan_to_num(f["rvol"], nan=0) >= 30)
                                               & (np.nan_to_num(f["pct1m"], nan=-9) >= 0.6) & ok)
        for name, m in cand.items():
            idx = dedup_idx(np.flatnonzero(m))
            if not len(idx):
                continue
            hit = g240[idx] >= 10
            gains[name].append(g240[idx])
            blips[name][0] += len(idx)
            blips[name][1] += int((rl[idx] <= 2).sum())
            for bk, sel in (("all", np.ones(len(idx), bool)), (grp, np.ones(len(idx), bool)),
                            ("t_in", ts[idx] < SPLIT_TS), ("t_out", ts[idx] >= SPLIT_TS)):
                res[name][bk][0] += int(sel.sum())
                res[name][bk][1] += int(hit[sel].sum())
    print("base rate +10%/4h for a random minute = 0.83%\n")
    print(f"{'variant':<30}" + "".join(f"{k:>18}" for k in ("all", "coinA", "coinB", "t_in", "t_out")))
    for name, b in res.items():
        row = f"{name:<30}"
        for k in ("all", "coinA", "coinB", "t_in", "t_out"):
            n, h = b[k]
            row += f"{n:>7}/{100*h/n if n else 0:5.1f}%".rjust(18)
        print(row)
    print("\nnoise rejection — share of fires landing on an isolated 1-2 minute blip:")
    for name, (n, bl) in blips.items():
        gg = np.concatenate(gains[name]) if gains[name] else np.array([0.0])
        print(f"  {name:<30} fires={n:>6}  blip-fires={bl:>6} ({100*bl/max(n,1):5.1f}%)  "
              f"median fwd max gain {np.nanmedian(gg):5.2f}%  fires/coin/month {n/82/12:5.2f}")
    json.dump({k: {g: v for g, v in b.items()} for k, b in res.items()},
              open("cache/accel_calib.json", "w"), indent=1)


if __name__ == "__main__":
    main()
