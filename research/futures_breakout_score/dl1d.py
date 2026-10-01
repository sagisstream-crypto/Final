import urllib.request, zipfile, io, os, concurrent.futures as cf
MONTHS=[f"2025-{m:02d}" for m in range(9,13)]+[f"2026-{m:02d}" for m in range(1,9)]
syms=open("syms.txt").read().split()
os.makedirs("d1",exist_ok=True)
def get(s):
    rows=[]
    for mo in MONTHS:
        u=f"https://data.binance.vision/data/futures/um/monthly/klines/{s}/1d/{s}-1d-{mo}.zip"
        try:
            z=zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u,timeout=30).read()))
            t=z.read(z.namelist()[0]).decode().splitlines()
            rows+=[l for l in t if l and l[0].isdigit()]
        except Exception as e: pass
    if rows: open(f"d1/{s}.csv","w").write("\n".join(rows))
    return s,len(rows)
with cf.ThreadPoolExecutor(32) as ex:
    r=list(ex.map(get,syms))
print(sum(1 for s,n in r if n))

# отбор: пары, у которых за год был хотя бы 3 дня с оборотом <= $3M.
# Это только чтобы не качать заведомо крупные пары; сам поминутный фильтр <= $3M — в proc.py.
import pandas as pd, glob
out=[]
for f in glob.glob("d1/*.csv"):
    d=pd.read_csv(f,header=None)
    out.append((os.path.basename(f)[:-4],(d[7]<=3e6).sum()))
el=[s for s,n in out if n>=3]
open("eligible.txt","w").write("\n".join(sorted(el)))
print("eligible",len(el))
