"""Barrier-label lab: '+10% before -10%' for every Binance USDT alt pair (spot + USDT-M, incl. delisted), Oct 2025 – Sep 28 2026.
build():  per symbol -> h10/{mkt}_{SYM}.parquet : HOURLY bars (o h l c qv n tbqv, ot = hour open ms) + labels for an
          entry at the CLOSE of hour t (entry price = open of the next 5m bar):
          up_t / dn_t = minutes until +B / -B is first touched (5m highs/lows, NaN if not within CAP days),
          lab = +1 (up first), -1 (down first), 0 (neither within CAP), same 5m bar touching both = -1 for long (conservative)
          and counted as -1 too for short => use lab_long / lab_short below.
          B = 0.10, CAP = 14 days. Also rows for B=0.05 (up5_t, dn5_t) for exploration.
load(mkt, sym) -> hourly DataFrame.  all_syms() -> list of (mkt, sym).
Win rule: LONG wins iff up_t < dn_t (strict) ; SHORT wins iff dn_t < up_t (strict); ties (same 5m bar) = loss for both.
TRAIN = ot < 2026-05-01 UTC ; TEST = ot >= 2026-05-01.
NOTE: labels near the end of data (last 14 days) can be censored (0 because data ended) — treat lab==0 rows with ot > 2026-09-14 as unknown.
"""
import glob, os, numpy as np, pandas as pd, multiprocessing as mp
HERE = os.path.dirname(os.path.abspath(__file__))
SRC = f"{HERE}/klines_5m"; DST = f"{HERE}/h10"
SPLIT = pd.Timestamp("2026-05-01", tz="UTC").value // 10**6
CENSOR = pd.Timestamp("2026-09-14", tz="UTC").value // 10**6
CAP = 14 * 288
def _sparse(a, op):
    lv = [a]; k = 1
    while (1 << k) <= len(a):
        p = lv[-1]; s = 1 << (k - 1); lv.append(op(p[:-s], p[s:])); k += 1
    return lv
def first_hit(lv, start, thr, ge, n):
    """first index j >= start with a[j] >= thr (ge) / a[j] <= thr (not ge); n if none. vectorised binary lifting."""
    pos = start.copy()
    for k in range(len(lv) - 1, -1, -1):
        L = lv[k]; step = 1 << k
        ok = pos + step <= n
        idx = np.minimum(pos, len(L) - 1)
        v = L[idx]
        adv = ok & ((v < thr) if ge else (v > thr))
        pos = np.where(adv, pos + step, pos)
    return pos
def one(path):
    mkt, sym = os.path.basename(path)[:-8].split("_", 1)
    out = f"{DST}/{mkt}_{sym}.parquet"
    if os.path.exists(out): return
    d = pd.read_parquet(path); n = len(d)
    if n < 2000: return
    h, l, o, ot = d.h.values, d.l.values, d.o.values, d.ot.values.astype(np.int64)
    lh, ll = _sparse(h, np.maximum), _sparse(l, np.minimum)
    d["hr"] = ot // 3600000 * 3600000
    g = d.groupby("hr", sort=True)
    H = pd.DataFrame({"o": g.o.first(), "h": g.h.max(), "l": g.l.min(), "c": g.c.last(), "qv": g.qv.sum(),
                      "n": g.n.sum(), "tbqv": g.tbqv.sum(), "last": g.apply(lambda x: x.index[-1], include_groups=False)})
    H.index.name = "ot"; H = H.reset_index()
    s = H["last"].values + 1; valid = s < n
    s = np.minimum(s, n - 1); e = o[s]
    for B, tag in ((0.10, ""), (0.05, "5")):
        ju = first_hit(lh, s, e * (1 + B), True, n); jd = first_hit(ll, s, e * (1 - B), False, n)
        ju = np.where((ju - s) < CAP, ju, n); jd = np.where((jd - s) < CAP, jd, n)
        H[f"up{tag}_t"] = np.where(ju < n, (ju - s) * 5.0, np.nan); H[f"dn{tag}_t"] = np.where(jd < n, (jd - s) * 5.0, np.nan)
    H.loc[~valid, ["up_t", "dn_t", "up5_t", "dn5_t"]] = np.nan
    H["entry"] = e
    H.drop(columns="last").to_parquet(out)
def build():
    os.makedirs(DST, exist_ok=True)
    fs = sorted(glob.glob(f"{SRC}/*.parquet"))
    with mp.Pool(4) as p: list(p.imap_unordered(one, fs, chunksize=4))
def all_syms():
    return [tuple(os.path.basename(f)[:-8].split("_", 1)) for f in sorted(glob.glob(f"{DST}/*.parquet"))]
def load(mkt, sym):
    H = pd.read_parquet(f"{DST}/{mkt}_{sym}.parquet")
    u, dn = H.up_t.fillna(np.inf).values, H.dn_t.fillna(np.inf).values
    H["lab_long"] = np.where(u < dn, 1, np.where(dn < u, -1, np.where(np.isfinite(u), -1, 0)))  # tie -> loss
    H["lab_short"] = np.where(dn < u, 1, np.where(u < dn, -1, np.where(np.isfinite(u), -1, 0)))
    H.loc[(H.lab_long == 0) & (H.ot > CENSOR), ["lab_long", "lab_short"]] = np.nan
    return H
if __name__ == "__main__":
    build(); print(len(all_syms()))
