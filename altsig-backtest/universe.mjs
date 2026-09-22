import fs from "fs";
import { unzipFirst } from "./zipread.mjs";

// Фильтр вселенной — дословно как buildUniverse()/excluded()/volOk() в ALTSIG,
// только источник списка пар — S3-листинг архивов вместо exchangeInfo (451).
const DEFAULT_EXCLUDE =
  'BTC,ETH,BNB,SOL,XRP,DOGE,USDC,FDUSD,TUSD,BUSD,DAI,EUR,TRY,BRL,ARS,AEUR,BTCDOM,USDP,XUSD,PAXG,WBTC,WBETH,BETH,BTCST,USDE,USD1';
const JUNK_RE = /(UP|DOWN|BULL|BEAR)$|^\d+L$|^\d+S$/;
const MIN_VOL = 3;                    // CFG.minVol — $3M за 24ч, нижняя граница

function excluded(base) {
  const list = DEFAULT_EXCLUDE.toUpperCase().split(/[,\s]+/).filter(Boolean);
  const stripped = base.replace(/^(1000000|100000|10000|1000|1M|1B)/, '');
  return list.includes(base) || list.includes(stripped);
}

const BASE = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision";
let marker = "", all = [];
while (true) {
  const url = `${BASE}?delimiter=/&prefix=${encodeURIComponent("data/futures/um/monthly/klines/")}&max-keys=1000` +
              (marker ? `&marker=${encodeURIComponent(marker)}` : "");
  const xml = await (await fetch(url)).text();
  all.push(...[...xml.matchAll(/<Prefix>data\/futures\/um\/monthly\/klines\/([^<\/]+)\/<\/Prefix>/g)].map(m => m[1]));
  if (!/<IsTruncated>true<\/IsTruncated>/.test(xml)) break;
  const nm = xml.match(/<NextMarker>([^<]*)<\/NextMarker>/);
  if (!nm) break;
  marker = nm[1];
}
const cands = all.filter(s => s.endsWith("USDT"))
                 .filter(s => { const b = s.slice(0, -4); return !excluded(b) && !JUNK_RE.test(b); });
console.error(`пар в архиве: ${all.length}, кандидатов USDT после exclude/junk: ${cands.length}`);

// суточные свечи за год -> средний объём, чтобы применить volOk
const MONTHS = [];
for (let y = 2025, m = 9; MONTHS.length < 12; m++) { if (m > 12) { m = 1; y++; } MONTHS.push(`${y}-${String(m).padStart(2,"0")}`); }
console.error("месяцы:", MONTHS.join(" "));

async function getZip(url, tries = 3) {
  for (let a = 1; a <= tries; a++) {
    try { const r = await fetch(url); if (r.status === 404) return null; if (!r.ok) throw new Error(r.status);
          return Buffer.from(await r.arrayBuffer()); }
    catch (e) { if (a === tries) return null; await new Promise(z => setTimeout(z, 400 * 2 ** a)); }
  }
}
const daily = {};
let done = 0;
async function worker(q) {
  while (q.length) {
    const sym = q.pop();
    const rows = [];
    for (const mo of MONTHS) {
      const buf = await getZip(`https://data.binance.vision/data/futures/um/monthly/klines/${sym}/1d/${sym}-1d-${mo}.zip`);
      if (!buf) continue;
      let txt; try { txt = unzipFirst(buf).toString("utf8"); } catch (e) { continue; }
      for (const line of txt.split("\n")) {
        if (!line || line.startsWith("open_time")) continue;
        const f = line.split(",");
        let t = +f[0]; if (t > 1e14) t = Math.floor(t / 1000);
        rows.push({ d: new Date(t).toISOString().slice(0, 10), c: +f[4], qv: +f[7] });
      }
    }
    if (rows.length) daily[sym] = rows;
    if (++done % 50 === 0) console.error(`суточные ${done}/${cands.length}`);
  }
}
await Promise.all(Array.from({ length: 24 }, () => worker(cands.slice())
  .catch(e => console.error(e.message))).slice(0, 0).concat(
  (() => { const q = cands.slice(); return Array.from({ length: 24 }, () => worker(q)); })()));

// пара входит во вселенную, если в году есть дни с объёмом >= $3M
const uni = {};
for (const [sym, rows] of Object.entries(daily)) {
  const ok = rows.filter(r => r.qv >= MIN_VOL * 1e6);
  if (ok.length >= 30) uni[sym] = { days: rows.length, okDays: ok.length };
}
fs.writeFileSync("daily.json", JSON.stringify(daily));
fs.writeFileSync("universe.json", JSON.stringify({ months: MONTHS, uni }));
console.error(`вселенная: ${Object.keys(uni).length} пар (>=30 дней с объёмом >= $${MIN_VOL}M)`);
