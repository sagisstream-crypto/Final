"""Study driver for the second-pass tables (data2/), which carry exact int64
timestamps, longer accumulation windows and 12h/24h forward horizons."""
import json, os, hashlib
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data2")
COOLDOWN_MS = 120 * 60 * 1000
SPLIT_TS = 1777939200000        # ~2026-05-05: last 4 months held out


def basket():
    return [x["symbol"] for x in json.load(open(os.path.join(HERE, "cache", "basket.json")))]


def sym_group(s):
    return int(hashlib.md5(s.encode()).hexdigest(), 16) % 2


_cache = {}


def load(sym, table="spikes"):
    k = (sym, table)
    if k not in _cache:
        z = np.load(os.path.join(DATA, sym + ".npz"), allow_pickle=True)
        cols = [str(c) for c in z["cols"]]
        _cache[k] = (z[table], z[table + "_ts"], {c: i for i, c in enumerate(cols)})
    return _cache[k]


def col(a, ix, n):
    return a[:, ix[n]].astype(np.float64)


def dedup(ts, mask, cooldown=COOLDOWN_MS):
    out = np.zeros(len(ts), dtype=bool)
    last = np.int64(-1) * np.int64(10**15)
    for i in np.flatnonzero(mask):
        if ts[i] - last >= cooldown:
            out[i] = True
            last = ts[i]
    return out


def evaluate(rule, table="spikes", syms=None, target=10.0, horizon="g240",
             cooldown=COOLDOWN_MS, extra_cols=()):
    syms = syms or basket()
    buckets = {}
    for s in syms:
        a, ts, ix = load(s, table)
        if not len(a):
            continue
        m = rule(a, ix) & np.isfinite(col(a, ix, horizon))
        if not m.any():
            continue
        fired = dedup(ts, m, cooldown)
        if not fired.any():
            continue
        g = col(a, ix, horizon)[fired]
        t = ts[fired]
        d = col(a, ix, "d240")[fired]
        c = col(a, ix, "c240")[fired]
        grp = "coinA" if sym_group(s) == 0 else "coinB"
        for name, sel in (("all", np.ones(len(t), bool)), (grp, np.ones(len(t), bool)),
                          ("t_in", t < SPLIT_TS), ("t_out", t >= SPLIT_TS)):
            if not sel.any():
                continue
            b = buckets.setdefault(name, {"n": 0, "hit": 0, "g": [], "d": [], "c": [], "sym": {}})
            b["n"] += int(sel.sum()); b["hit"] += int((g[sel] >= target).sum())
            b["g"].append(g[sel]); b["d"].append(d[sel]); b["c"].append(c[sel])
            if name == "all":
                b["sym"][s] = (int(sel.sum()), int((g[sel] >= target).sum()))
    out = {}
    for k, b in buckets.items():
        g = np.concatenate(b["g"]); d = np.concatenate(b["d"]); c = np.concatenate(b["c"])
        out[k] = dict(n=b["n"], hit=b["hit"], prec=b["hit"] / b["n"],
                      med_g=float(np.nanmedian(g)), p75_g=float(np.nanpercentile(g, 75)),
                      med_dd=float(np.nanmedian(d)), med_close=float(np.nanmedian(c)),
                      pos_close=float(np.nanmean(c > 0)), per_sym=b.get("sym", {}))
    return out


def fmt(name, r, keys=("all", "coinA", "coinB", "t_in", "t_out")):
    p = [f"{name:<52}"]
    for k in keys:
        p.append(f"{k} n={r[k]['n']:>5} {100*r[k]['prec']:5.1f}%" if k in r else f"{k} —")
    return " | ".join(p)
