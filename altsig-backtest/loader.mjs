import fs from "fs";
import path from "path";
import { unzipFirst } from "./zipread.mjs";

export const MONTHS = ["2025-09","2025-10","2025-11","2025-12","2026-01","2026-02",
                       "2026-03","2026-04","2026-05","2026-06","2026-07","2026-08"];
export const SPAN_START = Date.parse("2025-09-01T00:00:00Z");
export const SPAN_END   = Date.parse("2026-09-01T00:00:00Z");
const B = "https://data.binance.vision/data/futures/um";
const CACHE = process.env.BT_CACHE || "./cache";

async function get(url, tries = 4) {
  for (let a = 1; a <= tries; a++) {
    try {
      const r = await fetch(url);
      if (r.status === 404) return null;
      if (!r.ok) throw new Error("HTTP " + r.status);
      return Buffer.from(await r.arrayBuffer());
    } catch (e) {
      if (a === tries) return null;
      await new Promise(z => setTimeout(z, 300 * 2 ** a));
    }
  }
}
function parseK(txt, K) {
  for (let s = 0, e = 0; s < txt.length; s = e + 1) {
    e = txt.indexOf("\n", s); if (e < 0) e = txt.length;
    if (e <= s) continue;
    const line = txt.charCodeAt(e - 1) === 13 ? txt.slice(s, e - 1) : txt.slice(s, e);
    if (!line || line.charCodeAt(0) === 111) continue;
    const f = line.split(",");
    let t = +f[0]; if (t > 1e14) t = Math.floor(t / 1000);
    K.t.push(t); K.o.push(+f[1]); K.h.push(+f[2]); K.l.push(+f[3]); K.c.push(+f[4]);
    K.v.push(+f[5]); K.qv.push(+f[7]); K.tq.push(+f[10]);
  }
}
const emptyK = () => ({ t: [], o: [], h: [], l: [], c: [], v: [], qv: [], tq: [] });

// klines одного интервала за весь год
export async function loadKlines(sym, interval) {
  const K = emptyK();
  for (const mo of MONTHS) {
    const buf = await get(`${B}/monthly/klines/${sym}/${interval}/${sym}-${interval}-${mo}.zip`);
    if (!buf) continue;
    try { parseK(unzipFirst(buf).toString("utf8"), K); } catch (e) {}
  }
  return K;
}
export async function loadPremium(sym) {
  const K = emptyK();
  for (const mo of MONTHS) {
    const buf = await get(`${B}/monthly/premiumIndexKlines/${sym}/5m/${sym}-5m-${mo}.zip`);
    if (!buf) continue;
    try { parseK(unzipFirst(buf).toString("utf8"), K); } catch (e) {}
  }
  // basis в процентах — как (mark-index)/index*100 в приложении
  return K.t.map((t, i) => ({ t: t + 299999, b: K.c[i] * 100 }));
}
export async function loadFunding(sym) {
  const out = [];
  for (const mo of MONTHS) {
    const buf = await get(`${B}/monthly/fundingRate/${sym}/${sym}-fundingRate-${mo}.zip`);
    if (!buf) continue;
    let txt; try { txt = unzipFirst(buf).toString("utf8"); } catch (e) { continue; }
    for (const line of txt.split("\n")) {
      if (!line || line.startsWith("calc_time")) continue;
      const f = line.split(",");
      let t = +f[0]; if (t > 1e14) t = Math.floor(t / 1000);
      out.push({ t, r: +f[2] * 100 });          // fund: +r*100 -> проценты
    }
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}
// OI: суточные metrics, шаг 5 минут
export async function loadOI(sym, days, conc = 16) {
  const rows = [];
  let idx = 0;
  async function w() {
    while (idx < days.length) {
      const d = days[idx++];
      const buf = await get(`${B}/daily/metrics/${sym}/${sym}-metrics-${d}.zip`);
      if (!buf) continue;
      let txt; try { txt = unzipFirst(buf).toString("utf8"); } catch (e) { continue; }
      for (const line of txt.split("\n")) {
        if (!line || line.startsWith("create_time")) continue;
        const f = line.split(",");
        const t = Date.parse(f[0].replace(" ", "T") + "Z");
        const v = +f[3];
        if (Number.isFinite(t) && v > 0) rows.push({ t, v });
      }
    }
  }
  await Promise.all(Array.from({ length: conc }, w));
  rows.sort((a, b) => a.t - b.t);
  return rows;
}
export function dayList() {
  const out = [];
  for (let t = SPAN_START; t < SPAN_END; t += 86400000) out.push(new Date(t).toISOString().slice(0, 10));
  return out;
}
