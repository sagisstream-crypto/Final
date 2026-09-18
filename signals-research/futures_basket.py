"""USDT-M perpetual basket for the explosive-launch study.

The spot basket was deliberately liquid (>= $300k/day). The launch shape shows
up on thin, recently listed perps, so this basket deliberately reaches DOWN the
liquidity ladder instead of up.
"""
import json, re, statistics, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from binance_data import HOST, read_klines

PROBE = ["2026-03", "2026-08"]
STABLE = {"USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "EURI", "EUR", "USD1"}


def list_symbols():
    out, token = [], None
    while True:
        u = (HOST + "?list-type=2&prefix=data/futures/um/monthly/klines/"
             "&delimiter=/&max-keys=1000")
        if token:
            u += "&continuation-token=" + urllib.parse.quote(token, safe="")
        with urllib.request.urlopen(u, timeout=120) as r:
            x = r.read().decode()
        out += re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>", x)
        m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", x)
        if not m or "<IsTruncated>false" in x:
            break
        token = m.group(1)
    return sorted(s for s in set(out)
                  if s.endswith("USDT") and re.fullmatch(r"[A-Z0-9]+", s)
                  and s[:-4] not in STABLE)


def probe(sym):
    vols = []
    for ym in PROBE:
        rows = read_klines(sym, "1d", ym, market="futures/um")
        if not rows or len(rows) < 20:
            return None
        vols.append(statistics.median(float(r[7]) for r in rows))
    return sym, statistics.median(vols)


def main():
    syms = list_symbols()
    print("futures USDT symbols in archive:", len(syms))
    alive = {}
    with ThreadPoolExecutor(max_workers=24) as ex:
        for r in ex.map(probe, syms):
            if r:
                alive[r[0]] = r[1]
    print("alive across the probe months:", len(alive))
    rows = sorted(((v, s) for s, v in alive.items()), reverse=True)
    bands = [(2e7, 1e18, 25), (3e6, 2e7, 30), (5e5, 3e6, 30), (0, 5e5, 25)]
    picked = []
    import random
    random.seed(11)
    for lo, hi, n in bands:
        pool = [r for r in rows if lo <= r[0] < hi]
        picked += pool if len(pool) <= n else random.sample(pool, n)
        print(f"  band ${lo:,.0f}-${hi:,.0f}: pool {len(pool)} -> taking {min(n, len(pool))}")
    picked.sort(reverse=True)
    json.dump([{"symbol": s, "med_daily_quote_vol": round(v)} for v, s in picked],
              open("cache/futures_basket.json", "w"), indent=1)
    print("picked", len(picked))


if __name__ == "__main__":
    main()
