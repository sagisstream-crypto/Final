"""Walk-forward calibration of the explosive-launch (BLAST) rule.

Honest framing baked into the measurement: the entry is at the CLOSE of the
qualifying candle, i.e. after the move has already printed. Forward max gain,
forward drawdown and forward close are all measured from that close.
"""
import hashlib, json, sys
import numpy as np
import features as F
import blast as B

SPLIT_TS = 1777939200000
COOL = 120                      # bars between fires of the same pair


def grp(s):
    return "coinA" if int(hashlib.md5(s.encode()).hexdigest(), 16) % 2 == 0 else "coinB"


def dedup(idx, cool=COOL):
    out, last = [], -10 ** 9
    for i in idx:
        if i - last >= cool:
            out.append(i); last = i
    return np.array(out, dtype=int)


VARIANTS = {
    "L1 tx>=15 rng>=1.0 rvol>=15": dict(trades_x=15, bar_range=1.0, rvol=15, range_x=3),
    "L2 tx>=25 rng>=1.5 rvol>=25": dict(trades_x=25, bar_range=1.5, rvol=25, range_x=4),
    "L3 tx>=40 rng>=2.0 rvol>=40": dict(trades_x=40, bar_range=2.0, rvol=40, range_x=5),
    "L4 tx>=60 rng>=2.5 rvol>=60": dict(trades_x=60, bar_range=2.5, rvol=60, range_x=6),
    "L2 no trade-count term":      dict(trades_x=1,  bar_range=1.5, rvol=25, range_x=4),
    "L2 no range-expansion term":  dict(trades_x=25, bar_range=1.5, rvol=25, range_x=0),
    "$-volume twin (no txn)":      dict(trades_x=1,  bar_range=1.5, rvol=60, range_x=4),
    # clean A/B: identical severity on every other axis, trade count on/off
    "AB base  rvol>=60 rng>=2.5":  dict(trades_x=1,  bar_range=2.5, rvol=60, range_x=6),
    "AB +txn>=25":                 dict(trades_x=25, bar_range=2.5, rvol=60, range_x=6),
    "AB +txn>=60":                 dict(trades_x=60, bar_range=2.5, rvol=60, range_x=6),
    "AB +txn>=120":                dict(trades_x=120, bar_range=2.5, rvol=60, range_x=6),
    # and the reverse: lean on trade count, relax the dollar term
    "txn-led tx>=60 rvol>=15":     dict(trades_x=60, bar_range=2.5, rvol=15, range_x=6),
}


def run(basket, loader, start, end, label, bar_min=1):
    res = {k: {g: [0, 0] for g in ("all", "coinA", "coinB", "t_in", "t_out")} for k in VARIANTS}
    stats = {k: {"g": [], "d": [], "c": [], "tx": [], "rng": [], "sym": set()} for k in VARIANTS}
    hz = max(240 // bar_min, 1)
    for s in basket:
        d = loader(s, start, end)
        if d is None:
            continue
        if bar_min > 1:
            d = B.resample(d, bar_min)
        f = F.build(d, quiet_n=max(20 // bar_min, 4))
        b = B.build_blast(d, f, base_n=max(60 // bar_min, 12))
        g = F.forward_max_gain(f["close"], d["high"], hz)
        dd = F.forward_min_draw(f["close"], d["low"], hz)
        cc = F.forward_close(f["close"], hz)
        ts = d["ts"]
        ok = (np.arange(len(ts)) >= max(F.DAY // bar_min, 120) + 60) & np.isfinite(g)
        gg = grp(s)
        for name, kw in VARIANTS.items():
            m = B.blast_mask(f, b, **kw) & ok
            idx = dedup(np.flatnonzero(m), max(COOL // bar_min, 12))
            if not len(idx):
                continue
            hit = g[idx] >= 10
            st = stats[name]
            st["g"].append(g[idx]); st["d"].append(dd[idx]); st["c"].append(cc[idx])
            st["tx"].append(b["trades_x"][idx]); st["rng"].append(b["bar_range"][idx])
            st["sym"].add(s)
            for bk, sel in (("all", np.ones(len(idx), bool)), (gg, np.ones(len(idx), bool)),
                            ("t_in", ts[idx] < SPLIT_TS), ("t_out", ts[idx] >= SPLIT_TS)):
                res[name][bk][0] += int(sel.sum())
                res[name][bk][1] += int(hit[sel].sum())
    print(f"\n===== {label} =====")
    print(f"{'variant':<30}" + "".join(f"{k:>15}" for k in ("all", "coinA", "coinB", "t_in", "t_out")))
    for name, bb in res.items():
        row = f"{name:<30}"
        for k in ("all", "coinA", "coinB", "t_in", "t_out"):
            n, h = bb[k]
            row += f"{n:>5}/{100*h/n if n else 0:5.1f}%".rjust(15)
        print(row)
    print()
    for name, st in stats.items():
        if not st["g"]:
            print(f"  {name:<30} no fires"); continue
        g = np.concatenate(st["g"]); dd = np.concatenate(st["d"]); cc = np.concatenate(st["c"])
        tx = np.concatenate(st["tx"]); rg = np.concatenate(st["rng"])
        print(f"  {name:<30} n={len(g):>4} med_max={np.nanmedian(g):6.2f}% "
              f"med_dd={np.nanmedian(dd):6.2f}% med_close={np.nanmedian(cc):6.2f}% "
              f"p(close>0)={100*np.nanmean(cc>0):4.0f}% med_tx=×{np.nanmedian(tx):6.0f} "
              f"med_rng={np.nanmedian(rg):5.2f}% pairs={len(st['sym'])}")
    return res


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "spot"
    barmin = int(sys.argv[2]) if len(sys.argv) > 2 else 1
    if which == "spot":
        basket = [x["symbol"] for x in json.load(open("cache/basket.json"))]
        run(basket, lambda s, a, z: F.load_symbol(s, a, z), "2025-09", "2026-08",
            f"SPOT basket, {barmin}m bars, target +10% within 4h", barmin)
    else:
        basket = [x["symbol"] for x in json.load(open("cache/futures_basket.json"))]
        run(basket, lambda s, a, z: F.load_symbol(s, a, z, market="futures/um"),
            "2026-01", "2026-08", f"USDT-M PERP basket, {barmin}m bars, target +10% within 4h", barmin)
