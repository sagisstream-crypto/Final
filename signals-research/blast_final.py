"""Final BLAST calibration: is the trade-count axis actually earning its place,
and what are the honest numbers for an entry at the qualifying candle's close."""
import hashlib, json
import numpy as np
import features as F
import blast as B

SPLIT_TS = 1777939200000
BAR = 5                      # the screenshots are 5-minute candles
HZ = 240 // BAR              # 4 hours forward


def grp(s):
    return "A" if int(hashlib.md5(s.encode()).hexdigest(), 16) % 2 == 0 else "B"


def dedup(idx, cool=24):
    out, last = [], -10 ** 9
    for i in idx:
        if i - last >= cool:
            out.append(i); last = i
    return np.array(out, dtype=int)


RULES = {
    "БЕЗ числа сделок (rvol>=60)":  dict(trades_x=1,   bar_range=2.5, rvol=60, range_x=6),
    "+ сделки ×60":                 dict(trades_x=60,  bar_range=2.5, rvol=60, range_x=6),
    "+ сделки ×120":                dict(trades_x=120, bar_range=2.5, rvol=60, range_x=6),
    "ПРОД: сделки ×40, rvol>=40":   dict(trades_x=40,  bar_range=2.5, rvol=40, range_x=5),
}


def main():
    basket = json.load(open("cache/futures_basket.json"))
    vol = {x["symbol"]: x["med_daily_quote_vol"] for x in basket}
    thin_cut = np.percentile(list(vol.values()), 50)
    acc = {k: [] for k in RULES}
    base_hit = base_n = 0
    for x in basket:
        s = x["symbol"]
        d = F.load_symbol(s, "2026-01", "2026-08", market="futures/um")
        if d is None:
            continue
        d = B.resample(d, BAR)
        f = F.build(d, quiet_n=4)
        b = B.build_blast(d, f, base_n=12)
        g = F.forward_max_gain(f["close"], d["high"], HZ)
        dd = F.forward_min_draw(f["close"], d["low"], HZ)
        cc = F.forward_close(f["close"], HZ)
        ts = d["ts"]
        ok = (np.arange(len(ts)) >= 12 * 24 + 24) & np.isfinite(g)
        # unconditional base rate on the same population, sampled every 2h
        samp = ok & (np.arange(len(ts)) % 24 == 0)
        base_hit += int((g[samp] >= 10).sum()); base_n += int(samp.sum())
        for name, kw in RULES.items():
            idx = dedup(np.flatnonzero(B.blast_mask(f, b, **kw) & ok))
            for i in idx:
                acc[name].append((g[i], dd[i], cc[i], ts[i], grp(s),
                                  vol[s] < thin_cut, b["trades_x"][i],
                                  b["bar_range"][i], s))
    print(f"База: случайная точка той же корзины даёт +10% за 4ч в "
          f"{100*base_hit/base_n:.2f}% случаев (n={base_n})\n")
    hdr = f"{'правило':<30}{'n':>6}{'все':>8}{'coinA':>8}{'coinB':>8}{'t_in':>8}{'t_out':>8}" \
          f"{'тонкие':>9}{'толстые':>9}"
    print(hdr)
    for name, rows in acc.items():
        if not rows:
            print(f"{name:<30}  нет срабатываний"); continue
        a = np.array([(r[0], r[3], r[5]) for r in rows], dtype=float)
        g, ts, thin = a[:, 0], a[:, 1], a[:, 2].astype(bool)
        gr = np.array([r[4] for r in rows])
        def p(sel):
            return f"{100*np.mean(g[sel] >= 10):6.1f}%" if sel.any() else "     —"
        print(f"{name:<30}{len(g):>6}{p(np.ones(len(g), bool)):>8}{p(gr=='A'):>8}"
              f"{p(gr=='B'):>8}{p(ts < SPLIT_TS):>8}{p(ts >= SPLIT_TS):>8}"
              f"{p(thin):>9}{p(~thin):>9}")
    print()
    for name, rows in acc.items():
        if not rows:
            continue
        g = np.array([r[0] for r in rows]); dd = np.array([r[1] for r in rows])
        cc = np.array([r[2] for r in rows]); tx = np.array([r[6] for r in rows])
        rg = np.array([r[7] for r in rows]); syms = {r[8] for r in rows}
        print(f"  {name:<30} n={len(g):>4} пар={len(syms):>3} "
              f"мед.макс={np.nanmedian(g):5.2f}% мед.просадка={np.nanmedian(dd):6.2f}% "
              f"мед.через4ч={np.nanmedian(cc):6.2f}% плюс.через4ч={100*np.nanmean(cc>0):3.0f}% "
              f"P(+5%)={100*np.mean(g>=5):4.1f}% P(+20%)={100*np.mean(g>=20):4.1f}% "
              f"мед.сделок=×{np.nanmedian(tx):5.0f} мед.свеча={np.nanmedian(rg):5.2f}%")


if __name__ == "__main__":
    main()
