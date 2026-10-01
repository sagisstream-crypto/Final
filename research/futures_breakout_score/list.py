import urllib.request, re
B="https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
syms=[];marker=""
while True:
    u=f"{B}?prefix=data/futures/um/monthly/klines/&delimiter=/&marker={marker}"
    x=urllib.request.urlopen(u).read().decode()
    p=re.findall(r"<Prefix>data/futures/um/monthly/klines/([^/<]+)/</Prefix>",x)
    syms+=p
    m=re.search(r"<NextMarker>([^<]+)</NextMarker>",x)
    if "<IsTruncated>true" in x and m: marker=m.group(1)
    else: break
syms=sorted(set(s for s in syms if s.endswith("USDT")))
open("syms.txt","w").write("\n".join(syms))
print(len(syms))
