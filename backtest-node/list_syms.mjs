// Полный список символов из S3-листинга data.binance.vision (включая делистнутые).
const BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision";
const PREFIX = "data/futures/um/monthly/klines/";
let marker = "", out = [], pages = 0;
while (true) {
  const url = `${BASE}?delimiter=/&prefix=${encodeURIComponent(PREFIX)}&max-keys=1000` +
              (marker ? `&marker=${encodeURIComponent(marker)}` : "");
  const xml = await (await fetch(url)).text();
  const names = [...xml.matchAll(/<Prefix>data\/futures\/um\/monthly\/klines\/([^<\/]+)\/<\/Prefix>/g)].map(m => m[1]);
  out.push(...names);
  pages++;
  const trunc = /<IsTruncated>true<\/IsTruncated>/.test(xml);
  const nm = xml.match(/<NextMarker>([^<]*)<\/NextMarker>/);
  if (!trunc || !nm) break;
  marker = nm[1];
}
console.error(`pages=${pages} total=${out.length}`);
const usdt = out.filter(s => s.endsWith("USDT") && !s.toUpperCase().includes("ALPHA"));
console.error(`USDT=${usdt.length}`);
await import("fs").then(fs => fs.writeFileSync("symbols_all.json", JSON.stringify(usdt, null, 0)));
