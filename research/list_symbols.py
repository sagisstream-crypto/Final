"""Список USDT-M символов из архива data.binance.vision."""
import re, urllib.request
BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
def list_prefixes(prefix):
    out, marker = [], ""
    while True:
        url = f"{BASE}?prefix={prefix}&delimiter=/&marker={marker}"
        xml = urllib.request.urlopen(url, timeout=60).read().decode()
        out += re.findall(r"<Prefix>([^<]+)</Prefix></CommonPrefixes>", xml)
        if "<IsTruncated>true" not in xml: return out
        marker = re.search(r"<NextMarker>([^<]+)</NextMarker>", xml).group(1)
if __name__ == "__main__":
    syms = [p.rstrip("/").split("/")[-1] for p in list_prefixes("data/futures/um/monthly/klines/")]
    syms = [s for s in syms if s.endswith("USDT")]
    open("symbols.txt", "w").write("\n".join(syms))
    print(len(syms))
