#!/usr/bin/env python3
"""
Downloader for Binance official public historical klines.

Data source: Binance Vision public archive, accessed through its raw S3 REST
endpoint (https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/).
This is the same archive the official `binance-public-data` repo uses; only the
hostname differs (S3 host instead of the data.binance.vision CNAME).

Monthly archives are used (not daily) to keep the number of HTTP requests sane.

Usage:
    python3 backtest/download_data.py                # default basket, 1d
    python3 backtest/download_data.py --interval 4h
"""

import argparse
import csv
import io
import os
import sys
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
import zipfile
from concurrent.futures import ThreadPoolExecutor

BUCKET = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
NS = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Liquid, well-established USDT spot pairs spanning several market regimes.
SYMBOLS = [
    "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT", "ADAUSDT",
    "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT", "LTCUSDT",
    "TRXUSDT", "ATOMUSDT", "NEARUSDT", "APTUSDT", "ARBUSDT",
    "OPUSDT", "INJUSDT", "SUIUSDT", "UNIUSDT", "AAVEUSDT",
    "FILUSDT", "ETCUSDT", "BCHUSDT", "ALGOUSDT", "SANDUSDT",
]

KLINE_COLUMNS = [
    "open_time", "open", "high", "low", "close", "volume", "close_time",
    "quote_volume", "trades", "taker_buy_base", "taker_buy_quote", "ignore",
]


def _get(url, retries=4):
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "binance-backtest/1.0"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                return resp.read()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


def list_keys(prefix):
    """List every object key under a prefix, following continuation tokens."""
    keys = []
    token = None
    while True:
        params = {"list-type": "2", "prefix": prefix, "max-keys": "1000"}
        if token:
            params["continuation-token"] = token
        url = f"{BUCKET}/?{urllib.parse.urlencode(params)}"
        root = ET.fromstring(_get(url))
        for contents in root.findall("s3:Contents", NS):
            key = contents.find("s3:Key", NS).text
            if key.endswith(".zip"):
                keys.append(key)
        truncated = root.findtext("s3:IsTruncated", default="false", namespaces=NS)
        if truncated != "true":
            break
        token = root.findtext("s3:NextContinuationToken", namespaces=NS)
        if not token:
            break
    return sorted(keys)


def fetch_month(key):
    """Download one monthly zip and return its rows as lists of strings."""
    raw = _get(f"{BUCKET}/{key}")
    with zipfile.ZipFile(io.BytesIO(raw)) as zf:
        name = zf.namelist()[0]
        text = zf.read(name).decode("utf-8", errors="replace")
    rows = []
    for row in csv.reader(io.StringIO(text)):
        if not row or not row[0]:
            continue
        # Some archives ship a header row; skip it.
        if not row[0].replace(".", "").isdigit():
            continue
        rows.append(row[:12])
    return rows


def download_symbol(symbol, interval, out_dir, since=None):
    out_path = os.path.join(out_dir, f"{symbol}-{interval}.csv")
    prefix = f"data/spot/monthly/klines/{symbol}/{interval}/"
    keys = list_keys(prefix)
    if since:
        # key tail is <SYM>-<interval>-YYYY-MM.zip
        keys = [k for k in keys if k.rsplit("-", 2)[-2] + "-" + k.rsplit("-", 2)[-1][:2] >= since]
    if not keys:
        print(f"  {symbol:10s} NO DATA")
        return None

    with ThreadPoolExecutor(max_workers=12) as pool:
        chunks = list(pool.map(fetch_month, keys))

    rows = [r for chunk in chunks for r in chunk]
    # Binance switched open_time from ms to us in some 2025 archives; normalise.
    norm = []
    seen = set()
    for r in rows:
        ts = int(r[0])
        if ts > 10_000_000_000_000:  # microseconds
            ts //= 1000
        if ts in seen:
            continue
        seen.add(ts)
        norm.append([ts] + r[1:])
    norm.sort(key=lambda r: r[0])

    os.makedirs(out_dir, exist_ok=True)
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(KLINE_COLUMNS)
        w.writerows(norm)

    first = time.strftime("%Y-%m-%d", time.gmtime(norm[0][0] / 1000))
    last = time.strftime("%Y-%m-%d", time.gmtime(norm[-1][0] / 1000))
    print(f"  {symbol:10s} {len(norm):6d} bars  {first} -> {last}  ({len(keys)} months)")
    return out_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", default="1d")
    ap.add_argument("--symbols", nargs="*", default=SYMBOLS)
    ap.add_argument("--out", default=None)
    ap.add_argument("--since", default=None, help="YYYY-MM lower bound on monthly archives")
    args = ap.parse_args()

    out_dir = args.out or os.path.join(DATA_DIR, args.interval)
    os.makedirs(out_dir, exist_ok=True)
    print(f"Downloading {len(args.symbols)} symbols @ {args.interval} -> {out_dir}")
    for sym in args.symbols:
        path = os.path.join(out_dir, f"{sym}-{args.interval}.csv")
        if os.path.exists(path):
            print(f"  {sym:10s} cached")
            continue
        try:
            download_symbol(sym, args.interval, out_dir, args.since)
        except Exception as exc:  # noqa: BLE001
            print(f"  {sym:10s} ERROR {exc}", file=sys.stderr)


if __name__ == "__main__":
    main()
