"""Final parameter pick + a hard synthetic proof that 1-2 bar blips cannot fire."""
import hashlib, json
import numpy as np
import features as F, accel as A
from calib_accel import sym_group, dedup_idx, run_lengths, SPLIT_TS, START, END

FINAL = dict(w=10, min_gain=2.0, min_rvol=12.0)
VARIANTS = {
    "D  gain=2":                 dict(w=10, min_gain=2.0, persist=2),
    "D+ gain=2 rvol=12":         dict(w=10, min_gain=2.0, min_rvol=12.0, persist=2),
    "D+ gain=2 rvol=12 p=3":     dict(w=10, min_gain=2.0, min_rvol=12.0, persist=3),
    "D+ gain=2.5 rvol=12":       dict(w=10, min_gain=2.5, min_rvol=12.0, persist=2),
    "D+ gain=2 rvol=12 r2v=.45": dict(w=10, min_gain=2.0, min_rvol=12.0, min_r2_v=0.45, persist=2),
}


def main():
    basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
    res = {k: {g: [0, 0] for g in ("all", "coinA", "coinB", "t_in", "t_out")} for k in VARIANTS}
    blips = {k: [0, 0] for k in VARIANTS}
    dd = {k: [] for k in VARIANTS}
    # --- synthetic blip injection ---
    inj = {k: [0, 0, 0] for k in VARIANTS}   # [1-bar spikes, 2-bar spikes, 10-bar ramps]
    for si, s in enumerate(basket):
        d = F.load_symbol(s, START, END)
        if d is None:
            continue
        f = F.build(d)
        g240 = F.forward_max_gain(f["close"], d["high"], 240)
        dmin = F.forward_min_draw(f["close"], d["low"], 240)
        ts = d["ts"]
        hot = np.nan_to_num(f["rvol"], nan=0) >= 10
        rl = run_lengths(hot)
        ok = (np.arange(len(ts)) >= F.DAY + 60) & np.isfinite(g240)
        grp = "coinA" if sym_group(s) == 0 else "coinB"
        for name, kw in VARIANTS.items():
            kw = dict(kw); p = kw.pop("persist")
            st = A.accel_state(f["close"], d["qv"], f["avg_min"], **kw)
            m = A.persist(st["state"], p) & ok
            idx = dedup_idx(np.flatnonzero(m))
            if not len(idx):
                continue
            hit = g240[idx] >= 10
            dd[name].append(dmin[idx])
            blips[name][0] += len(idx); blips[name][1] += int((rl[idx] <= 2).sum())
            for bk, sel in (("all", np.ones(len(idx), bool)), (grp, np.ones(len(idx), bool)),
                            ("t_in", ts[idx] < SPLIT_TS), ("t_out", ts[idx] >= SPLIT_TS)):
                res[name][bk][0] += int(sel.sum()); res[name][bk][1] += int(hit[sel].sum())
        if si < 20:      # synthetic injection on the first 20 coins is plenty
            _inject(d, f, VARIANTS, inj)
    print("base rate +10%/4h = 0.83%   (deduped events, 82 coins x 12 months)\n")
    hdr = f"{'variant':<28}" + "".join(f"{k:>16}" for k in ("all", "coinA", "coinB", "t_in", "t_out"))
    print(hdr)
    for name, b in res.items():
        row = f"{name:<28}"
        for k in ("all", "coinA", "coinB", "t_in", "t_out"):
            n, h = b[k]
            row += f"{n:>6}/{100*h/n if n else 0:5.1f}%".rjust(16)
        print(row)
    print()
    for name in VARIANTS:
        n, bl = blips[name]
        dv = np.concatenate(dd[name]) if dd[name] else np.array([0.0])
        print(f"  {name:<28} fires={n:>5} blip={100*bl/max(n,1):5.1f}%  "
              f"fires/coin/month={n/82/12:4.2f}  median drawdown/4h={np.nanmedian(dv):5.2f}%")
    print("\nSYNTHETIC INJECTION (20 coins, 2000 flat segments each):")
    print("  a flat stretch of real tape with an artificial spike glued on top")
    for name in VARIANTS:
        a, b, c = inj[name]
        print(f"  {name:<28} 1-bar spike fires: {a:>4}/2000   2-bar spike fires: {b:>4}/2000   "
              f"10-bar ramp fires: {c:>4}/2000")


def _inject(d, f, variants, inj, n_seg=2000, seed=11):
    rng = np.random.default_rng(seed)
    n = len(d["close"])
    starts = rng.integers(F.DAY + 100, n - 100, size=n_seg)
    W = 14
    for name, kw in variants.items():
        kw = dict(kw); p = kw.pop("persist")
        for mode in range(3):
            fires = 0
            for s0 in starts:
                c = d["close"][s0 - W:s0].astype(float).copy()
                q = d["qv"][s0 - W:s0].astype(float).copy()
                am = float(np.nan_to_num(f["avg_min"][s0], nan=1.0)) or 1.0
                # flatten the segment so only the injected shape is left
                c[:] = c[0]
                q[:] = am * 0.5
                if mode == 0:            # single-bar blip
                    c[-1] = c[0] * 1.03
                    q[-1] = am * 60
                elif mode == 1:          # two-bar blip
                    c[-2] = c[0] * 1.015; c[-1] = c[0] * 1.03
                    q[-2] = am * 40; q[-1] = am * 60
                else:                    # genuine 10-bar ramp
                    k = 10
                    c[-k:] = c[0] * (1 + np.linspace(0.003, 0.03, k))
                    q[-k:] = am * np.linspace(3, 40, k)
                st = A.accel_state(c, q, np.full(W, am), **kw)
                if A.persist(st["state"], p)[-1]:
                    fires += 1
            inj[name][mode] += fires


if __name__ == "__main__":
    main()
