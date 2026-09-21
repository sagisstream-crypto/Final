import fs from "fs";
import path from "path";
import { unzipFirst } from "./zipread.mjs";

export const MONTHS = ["2026-02","2026-03","2026-04","2026-05","2026-06","2026-07","2026-08"];
export const EXTRA_DAYS = ["2026-09-01"];          // хвост для forward-окна 31 августа
export const SPAN_START = Date.parse("2026-02-01T00:00:00Z");
export const SPAN_END   = Date.parse("2026-09-02T00:00:00Z");
export const SPAN_MIN   = (SPAN_END - SPAN_START) / 60000;
const CACHE = process.env.BT_CACHE || "./cache";

async function getZip(url, tries = 4) {
  for (let a = 1; a <= tries; a++) {
    try {
      const r = await fetch(url);
      if (r.status === 404) return null;
      if (!r.ok) throw new Error("HTTP " + r.status);
      return Buffer.from(await r.arrayBuffer());
    } catch (e) {
      if (a === tries) throw e;
      await new Promise(res => setTimeout(res, 400 * 2 ** a));
    }
  }
}

function parseCsv(txt, out) {
  for (let s = 0, e = 0; s < txt.length; s = e + 1) {
    e = txt.indexOf("\n", s);
    if (e < 0) e = txt.length;
    if (e <= s) continue;
    const line = txt.charCodeAt(e - 1) === 13 ? txt.slice(s, e - 1) : txt.slice(s, e);
    if (!line || line.charCodeAt(0) === 111) continue;          // header "open_time"
    const f = line.split(",");
    let t = +f[0];
    if (t > 1e14) t = Math.floor(t / 1000);                     // микросекунды
    out.push({ t, o: +f[1], h: +f[2], l: +f[3], c: +f[4], bv: +f[5], qv: +f[7], n: +f[8], tbqv: +f[10] });
  }
}

// Все минутки одного символа за span. Возвращает сырые строки (без сетки).
export async function loadSymbolMinutes(sym) {
  fs.mkdirSync(CACHE, { recursive: true });
  const rows = [];
  let got = 0;
  for (const mo of MONTHS) {
    const f = path.join(CACHE, `${sym}-1m-${mo}.zip`);
    let buf = fs.existsSync(f) ? fs.readFileSync(f) : null;
    if (!buf) {
      buf = await getZip(`https://data.binance.vision/data/futures/um/monthly/klines/${sym}/1m/${sym}-1m-${mo}.zip`);
      if (buf) fs.writeFileSync(f, buf);
    }
    if (!buf) continue;
    try { parseCsv(unzipFirst(buf).toString("utf8"), rows); got++; } catch (e) {}
  }
  for (const d of EXTRA_DAYS) {
    const buf = await getZip(`https://data.binance.vision/data/futures/um/daily/klines/${sym}/1m/${sym}-1m-${d}.zip`);
    if (buf) { try { parseCsv(unzipFirst(buf).toString("utf8"), rows); } catch (e) {} }
  }
  rows.sort((a, b) => a.t - b.t);
  return { rows, months: got };
}
export function dropCache(sym) {
  for (const mo of MONTHS) {
    const f = path.join(CACHE, `${sym}-1m-${mo}.zip`);
    if (fs.existsSync(f)) fs.unlinkSync(f);
  }
}
