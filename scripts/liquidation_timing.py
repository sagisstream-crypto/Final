#!/usr/bin/env python3
"""
Определяет, в какой час суток чаще всего случается "вынос" (стоп-хант /
ликвидационный фитиль) на низколиквидных парах Binance (spot + USDT-M
фьючерсы, 24ч объём <= порога).

Запускать НЕ из этой песочницы (тут заблокирован выход на binance.com) —
запускайте локально, там, где api.binance.com/fapi.binance.com доступны:

    python3 scripts/liquidation_timing.py
    python3 scripts/liquidation_timing.py --max-volume 3000000 --days 90

Как определяется "вынос" (для каждой 1h-свечи символа):
  - диапазон range = high - low
  - фитиль = max(верхний, нижний) относительно тела свечи
  - свеча считается выносом, если:
      1) фитиль занимает большую часть диапазона (wick_ratio >= --wick-ratio)
      2) диапазон необычно большой для этого инструмента
         (range >= --spike-mult * медианный range этого символа)
  То есть: резкий импульсный выброс цены, который "откусил" куда больше,
  чем обычная волатильность пары, и в основном фитилём, а не телом (характерно
  для стоп-ханта / принудительных ликвидаций на тонком стакане).

Результат — распределение частоты таких свечей по часу открытия (UTC),
с переводом в Asia/Almaty (UTC+5, круглый год, без перехода на летнее время).

Только стандартная библиотека Python (urllib) — ничего ставить не нужно.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from collections import defaultdict

ALMATY_UTC_OFFSET_HOURS = 5  # Asia/Almaty = UTC+5 круглый год, без DST

SPOT_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"
SPOT_TICKER_24H = "https://api.binance.com/api/v3/ticker/24hr"
SPOT_KLINES = "https://api.binance.com/api/v3/klines"

FUT_EXCHANGE_INFO = "https://fapi.binance.com/fapi/v1/exchangeInfo"
FUT_TICKER_24H = "https://fapi.binance.com/fapi/v1/ticker/24hr"
FUT_KLINES = "https://fapi.binance.com/fapi/v1/klines"

LEVERAGED_SUFFIXES = ("UPUSDT", "DOWNUSDT", "BULLUSDT", "BEARUSDT")

UA = {"User-Agent": "liquidation-timing-scanner/1.0"}


def http_get_json(url: str, params: dict | None = None, retries: int = 5):
    if params:
        from urllib.parse import urlencode
        url = f"{url}?{urlencode(params)}"
    delay = 1.0
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=UA)
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code in (418, 429) and attempt < retries - 1:
                retry_after = e.headers.get("Retry-After")
                wait = float(retry_after) if retry_after else delay
                print(f"  rate limited ({e.code}) on {url[:80]}..., sleeping {wait:.1f}s", file=sys.stderr)
                time.sleep(wait)
                delay *= 2
                continue
            raise
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < retries - 1:
                time.sleep(delay)
                delay *= 2
                continue
            raise
    raise RuntimeError(f"failed after {retries} retries: {url}")


def is_leveraged_token(symbol: str) -> bool:
    return any(symbol.endswith(suf) for suf in LEVERAGED_SUFFIXES)


def pick_symbols(market: str, max_volume: float, quote_asset: str) -> list[str]:
    """Возвращает список символов с 24ч quoteVolume <= max_volume."""
    if market == "spot":
        info = http_get_json(SPOT_EXCHANGE_INFO)
        tradeable = {
            s["symbol"]
            for s in info["symbols"]
            if s["status"] == "TRADING"
            and s["quoteAsset"] == quote_asset
            and not is_leveraged_token(s["symbol"])
        }
        tickers = http_get_json(SPOT_TICKER_24H)
    else:
        info = http_get_json(FUT_EXCHANGE_INFO)
        tradeable = {
            s["symbol"]
            for s in info["symbols"]
            if s["status"] == "TRADING"
            and s["quoteAsset"] == quote_asset
            and s.get("contractType") == "PERPETUAL"
        }
        tickers = http_get_json(FUT_TICKER_24H)

    picked = []
    for t in tickers:
        sym = t["symbol"]
        if sym not in tradeable:
            continue
        try:
            vol = float(t["quoteVolume"])
        except (KeyError, ValueError):
            continue
        if vol <= max_volume:
            picked.append(sym)
    return sorted(picked)


def fetch_klines(market: str, symbol: str, interval: str, days: int) -> list[list]:
    url = SPOT_KLINES if market == "spot" else FUT_KLINES
    end_ms = int(time.time() * 1000)
    start_ms = end_ms - days * 86400 * 1000
    out = []
    cursor = start_ms
    while cursor < end_ms:
        batch = http_get_json(
            url,
            {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "limit": 1000,
            },
        )
        if not batch:
            break
        out.extend(batch)
        last_open = batch[-1][0]
        if last_open <= cursor:
            break
        cursor = last_open + 1
        if len(batch) < 1000:
            break
    return out


def analyze_symbol(candles: list[list], wick_ratio_thr: float, spike_mult: float):
    """Возвращает (hour_utc -> flagged_count, hour_utc -> total_count)."""
    flagged = defaultdict(int)
    total = defaultdict(int)

    ranges = []
    parsed = []
    for k in candles:
        open_ms, o, h, l, c = k[0], float(k[1]), float(k[2]), float(k[3]), float(k[4])
        rng = h - l
        parsed.append((open_ms, o, h, l, c, rng))
        if rng > 0:
            ranges.append(rng)

    if len(ranges) < 20:
        return flagged, total

    median_rng = statistics.median(ranges)
    if median_rng <= 0:
        return flagged, total

    for open_ms, o, h, l, c, rng in parsed:
        hour_utc = time.gmtime(open_ms / 1000).tm_hour
        total[hour_utc] += 1
        if rng <= 0:
            continue
        upper_wick = h - max(o, c)
        lower_wick = min(o, c) - l
        wick = max(upper_wick, lower_wick)
        wick_ratio = wick / rng
        if wick_ratio >= wick_ratio_thr and rng >= spike_mult * median_rng:
            flagged[hour_utc] += 1

    return flagged, total


def utc_hour_to_almaty(hour_utc: int) -> int:
    return (hour_utc + ALMATY_UTC_OFFSET_HOURS) % 24


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--markets", default="spot,futures", help="какие рынки сканировать (spot,futures)")
    ap.add_argument("--quote-asset", default="USDT", help="котируемый актив (по умолчанию USDT)")
    ap.add_argument("--max-volume", type=float, default=3_000_000, help="макс. 24ч quoteVolume в долларах")
    ap.add_argument("--days", type=int, default=90, help="сколько дней истории анализировать")
    ap.add_argument("--interval", default="1h", help="таймфрейм свечей (1h рекомендуется)")
    ap.add_argument("--wick-ratio", type=float, default=0.65, help="мин. доля фитиля в диапазоне свечи")
    ap.add_argument("--spike-mult", type=float, default=1.8, help="во сколько раз range должен превышать медианный")
    ap.add_argument("--workers", type=int, default=6, help="кол-во параллельных потоков для скачивания")
    ap.add_argument("--limit-symbols", type=int, default=0, help="ограничить число символов (0 = без лимита, для теста)")
    ap.add_argument("--out", default="", help="сохранить сырые результаты в JSON-файл")
    args = ap.parse_args()

    markets = [m.strip() for m in args.markets.split(",") if m.strip()]

    all_symbols: list[tuple[str, str]] = []  # (market, symbol)
    for market in markets:
        print(f"[{market}] ищу символы с 24ч объёмом <= {args.max_volume:,.0f} {args.quote_asset}...", file=sys.stderr)
        syms = pick_symbols(market, args.max_volume, args.quote_asset)
        if args.limit_symbols:
            syms = syms[: args.limit_symbols]
        print(f"[{market}] найдено {len(syms)} символов", file=sys.stderr)
        all_symbols.extend((market, s) for s in syms)

    if not all_symbols:
        print("Не найдено ни одного символа под условия фильтра.", file=sys.stderr)
        return

    total_flagged = defaultdict(int)
    total_count = defaultdict(int)
    per_symbol_summary = {}

    def worker(item):
        market, symbol = item
        try:
            candles = fetch_klines(market, symbol, args.interval, args.days)
            flagged, total = analyze_symbol(candles, args.wick_ratio, args.spike_mult)
            return market, symbol, flagged, total, None
        except Exception as e:  # noqa: BLE001 - repot and continue scanning other symbols
            return market, symbol, None, None, str(e)

    done = 0
    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        for market, symbol, flagged, total, err in ex.map(worker, all_symbols):
            done += 1
            if done % 25 == 0 or done == len(all_symbols):
                print(f"  прогресс: {done}/{len(all_symbols)}", file=sys.stderr)
            if err:
                print(f"  {market}:{symbol} пропущен ({err})", file=sys.stderr)
                continue
            sym_flagged_total = sum(flagged.values())
            sym_candles_total = sum(total.values())
            per_symbol_summary[f"{market}:{symbol}"] = {
                "flagged": sym_flagged_total,
                "candles": sym_candles_total,
            }
            for h, v in flagged.items():
                total_flagged[h] += v
            for h, v in total.items():
                total_count[h] += v

    print()
    print("=" * 72)
    print("Частота 'выносов' по часу открытия свечи (UTC -> Asia/Almaty, UTC+5)")
    print("=" * 72)
    rows = []
    for hour_utc in range(24):
        flagged = total_flagged.get(hour_utc, 0)
        candles = total_count.get(hour_utc, 0)
        rate = flagged / candles if candles else 0.0
        rows.append((hour_utc, flagged, candles, rate))

    print(f"{'UTC':>5} {'Алматы':>7} {'выносов':>9} {'свечей':>9} {'частота':>9}")
    for hour_utc, flagged, candles, rate in sorted(rows, key=lambda r: r[0]):
        almaty = utc_hour_to_almaty(hour_utc)
        print(f"{hour_utc:>02d}:00 {almaty:>02d}:00 {flagged:>9} {candles:>9} {rate*100:>8.2f}%")

    print()
    print("Топ-5 часов по частоте выносов:")
    top = sorted(rows, key=lambda r: r[3], reverse=True)[:5]
    for hour_utc, flagged, candles, rate in top:
        almaty = utc_hour_to_almaty(hour_utc)
        print(
            f"  {hour_utc:02d}:00-{(hour_utc+1)%24:02d}:00 UTC  "
            f"= {almaty:02d}:00-{(almaty+1)%24:02d}:00 Алматы  "
            f"-> {rate*100:.2f}% свечей ({flagged}/{candles})"
        )

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "params": vars(args),
                    "symbols_scanned": len(all_symbols),
                    "by_hour_utc": {str(h): {"flagged": total_flagged.get(h, 0), "candles": total_count.get(h, 0)} for h in range(24)},
                    "per_symbol": per_symbol_summary,
                },
                f,
                ensure_ascii=False,
                indent=2,
            )
        print(f"\nСырые данные сохранены в {args.out}")


if __name__ == "__main__":
    main()
