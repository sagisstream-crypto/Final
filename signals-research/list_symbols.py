import urllib.request, re, json, sys
H = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/"
def get(url):
    for _ in range(5):
        try:
            with urllib.request.urlopen(url, timeout=90) as r:
                return r.read().decode()
        except Exception as e:
            err = e
    raise err
prefixes = []
token = None
while True:
    u = H + "?list-type=2&prefix=data/spot/monthly/klines/&delimiter=/&max-keys=1000"
    if token: u += "&continuation-token=" + urllib.parse.quote(token, safe="")
    x = get(u)
    prefixes += re.findall(r"<Prefix>data/spot/monthly/klines/([^/<]+)/</Prefix>", x)
    m = re.search(r"<NextContinuationToken>([^<]+)</NextContinuationToken>", x)
    if not m or "<IsTruncated>false" in x: break
    token = m.group(1)
syms = sorted(set(s for s in prefixes if s.endswith("USDT")))
json.dump(syms, open("cache/all_usdt_symbols.json","w"))
print(len(syms))
