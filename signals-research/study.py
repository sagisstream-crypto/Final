"""Threshold studies over the scanned candidate tables.

All event counts are de-duplicated: one big move produces hundreds of consecutive
qualifying minutes, and counting those as hundreds of independent "hits" is the
classic way to make a mediocre signal look spectacular.
"""
import json, os, hashlib
import numpy as np

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
COOLDOWN_MS = 120 * 60 * 1000          # same cluster cooldown the scanner uses
SPLIT_TS = 1777939200000               # 2026-05-05 -> last ~4 months held out


def basket():
    return [x["symbol"] for x in json.load(open(os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "cache", "basket.json")))]


def sym_group(sym):
    """Deterministic 50/50 symbol split for walk-forward across coins."""
    return int(hashlib.md5(sym.encode()).hexdigest(), 16) % 2


_cache = {}


def load(sym, table="spikes"):
    k = (sym, table)
    if k not in _cache:
        z = np.load(os.path.join(DATA, sym + ".npz"), allow_pickle=True)
        cols = list(z["cols"])
        arr = z[table]
        _cache[k] = (arr, {c: i for i, c in enumerate(cols)})
    return _cache[k]


def col(arr, ix, name):
    return arr[:, ix[name]].astype(np.float64)


def dedup(ts, mask, cooldown=COOLDOWN_MS):
    """Keep the FIRST qualifying minute of each cluster (rows must be time-sorted)."""
    out = np.zeros(len(ts), dtype=bool)
    last = -1e18
    for i in np.flatnonzero(mask):
        if ts[i] - last >= cooldown:
            out[i] = True
            last = ts[i]
    return out


def evaluate(rule, table="spikes", syms=None, target=10.0, horizon="g240",
             cooldown=COOLDOWN_MS):
    """rule(arr, ix) -> bool mask. Returns per-split stats."""
    syms = syms or basket()
    buckets = {}
    for s in syms:
        arr, ix = load(s, table)
        if not len(arr):
            continue
        ts = col(arr, ix, "ts")
        m = rule(arr, ix) & np.isfinite(col(arr, ix, horizon))
        if not m.any():
            continue
        fired = dedup(ts, m, cooldown)
        g = col(arr, ix, horizon)[fired]
        d = col(arr, ix, "d240")[fired]
        cl = col(arr, ix, "c240")[fired]
        t = ts[fired]
        grp = ("coinA" if sym_group(s) == 0 else "coinB")
        for name, sel in (("all", np.ones(len(t), bool)),
                          (grp, np.ones(len(t), bool)),
                          ("t_in", t < SPLIT_TS), ("t_out", t >= SPLIT_TS)):
            if not sel.any():
                continue
            b = buckets.setdefault(name, {"n": 0, "hit": 0, "g": [], "d": [], "c": [], "syms": set()})
            b["n"] += int(sel.sum())
            b["hit"] += int((g[sel] >= target).sum())
            b["g"].append(g[sel]); b["d"].append(d[sel]); b["c"].append(cl[sel])
            b["syms"].add(s)
    out = {}
    for k, b in buckets.items():
        if not b["n"]:
            continue
        g = np.concatenate(b["g"]); d = np.concatenate(b["d"]); c = np.concatenate(b["c"])
        out[k] = dict(n=b["n"], hit=b["hit"], prec=b["hit"] / b["n"],
                      med_g=float(np.nanmedian(g)), p90_g=float(np.nanpercentile(g, 90)),
                      med_dd=float(np.nanmedian(d)), med_close=float(np.nanmedian(c)),
                      pos_close=float(np.nanmean(c > 0)), nsym=len(b["syms"]))
    return out


def fmt(name, r, keys=("all", "coinA", "coinB", "t_in", "t_out")):
    line = [f"{name:<46}"]
    for k in keys:
        if k in r:
            line.append(f"{k}: n={r[k]['n']:>5} hit={100*r[k]['prec']:5.1f}%")
        else:
            line.append(f"{k}: —")
    return "  ".join(line)
