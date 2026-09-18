"""Warm the cache with USDT-M perp 1m archives for the launch study."""
import json, time
from concurrent.futures import ThreadPoolExecutor
from binance_data import fetch_zip, monthly_key, months

START, END = "2026-01", "2026-08"


def job(a):
    sym, ym = a
    try:
        b = fetch_zip(monthly_key(sym, "1m", ym, market="futures/um"))
        return 0 if b is None else len(b)
    except Exception:
        return -1


def main():
    basket = [x["symbol"] for x in json.load(open("cache/futures_basket.json"))]
    tasks = [(s, m) for s in basket for m in months(START, END)]
    t0 = time.time(); ok = miss = fail = 0; tot = 0
    with ThreadPoolExecutor(max_workers=12) as ex:
        for i, n in enumerate(ex.map(job, tasks), 1):
            if n < 0: fail += 1
            elif n == 0: miss += 1
            else: ok += 1; tot += n
            if i % 100 == 0:
                print(f"{i}/{len(tasks)} ok={ok} miss={miss} fail={fail} "
                      f"{tot/1e6:.0f}MB {time.time()-t0:.0f}s", flush=True)
    print(f"DONE ok={ok} miss={miss} fail={fail} {tot/1e6:.0f}MB in {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
