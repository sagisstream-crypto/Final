"""Download helpers for the Binance Vision archive via its raw S3 endpoint.

data.binance.vision (the CNAME) is blocked in some environments; the underlying
bucket is reachable directly at s3-ap-northeast-1.amazonaws.com.
"""
import io, os, time, zipfile, urllib.request, urllib.error

HOST = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "cache")

COLS = ["open_time","open","high","low","close","volume","close_time",
        "quote_volume","trades","taker_buy_base","taker_buy_quote","ignore"]


def monthly_key(symbol, interval, ym, market="spot"):
    return f"data/{market}/monthly/klines/{symbol}/{interval}/{symbol}-{interval}-{ym}.zip"


def fetch_zip(key, retries=4):
    """Return raw zip bytes for an archive key, cached on disk. None if 404."""
    path = os.path.join(CACHE, key.replace("/", "_"))
    if os.path.exists(path):
        if os.path.getsize(path) == 0:
            return None
        with open(path, "rb") as f:
            return f.read()
    last = None
    for i in range(retries):
        try:
            with urllib.request.urlopen(HOST + key, timeout=180) as r:
                blob = r.read()
            os.makedirs(CACHE, exist_ok=True)
            with open(path, "wb") as f:
                f.write(blob)
            return blob
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                os.makedirs(CACHE, exist_ok=True)
                open(path, "wb").close()   # negative cache
                return None
            last = e
        except Exception as e:
            last = e
        time.sleep(1.5 * (i + 1))
    raise last


def read_klines(symbol, interval, ym, market="spot"):
    """Yield rows (list of str) of one monthly kline archive, or None if missing."""
    blob = fetch_zip(monthly_key(symbol, interval, ym, market))
    if blob is None:
        return None
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        name = z.namelist()[0]
        raw = z.read(name).decode()
    lines = raw.strip().split("\n")
    if lines and lines[0].startswith("open_time"):
        lines = lines[1:]
    return [ln.split(",") for ln in lines if ln]


def months(start, end):
    """months('2025-09','2026-08') -> list of 'YYYY-MM'."""
    sy, sm = map(int, start.split("-"))
    ey, em = map(int, end.split("-"))
    out = []
    while (sy, sm) <= (ey, em):
        out.append(f"{sy:04d}-{sm:02d}")
        sm += 1
        if sm == 13:
            sm = 1; sy += 1
    return out
