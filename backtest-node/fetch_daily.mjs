import fs from "fs";
import { unzipFirst } from "./zipread.mjs";

const MONTHS = ["2026-02","2026-03","2026-04","2026-05","2026-06","2026-07","2026-08"];
const syms = JSON.parse(fs.readFileSync("symbols_all.json", "utf8"));
const CONC = 24;
const daily = {};   // sym -> { "YYYY-MM-DD": {c, qv} }
let done = 0, miss = 0;

async function getZip(url, tries = 4) {
  for (let a = 1; a <= tries; a++) {
    try {
      const r = await fetch(url);
      if (r.status === 404) return null;
      if (!r.ok) throw new Error("HTTP " + r.status);
      return Buffer.from(await r.arrayBuffer());
    } catch (e) {
      if (a === tries) return null;
      await new Promise(r => setTimeout(r, 500 * 2 ** a));
    }
  }
}

async function worker(queue) {
  while (queue.length) {
    const sym = queue.pop();
    const m = {};
    for (const mo of MONTHS) {
      const url = `https://data.binance.vision/data/futures/um/monthly/klines/${sym}/1d/${sym}-1d-${mo}.zip`;
      const buf = await getZip(url);
      if (!buf) { miss++; continue; }
      let txt;
      try { txt = unzipFirst(buf).toString("utf8"); } catch (e) { continue; }
      for (const line of txt.split("\n")) {
        if (!line || line.startsWith("open_time")) continue;
        const f = line.split(",");
        let t = +f[0];
        if (t > 1e14) t = Math.floor(t / 1000);       // микросекунды в новых архивах
        const day = new Date(t).toISOString().slice(0, 10);
        m[day] = { c: +f[4], qv: +f[7] };
      }
    }
    if (Object.keys(m).length) daily[sym] = m;
    done++;
    if (done % 50 === 0) console.error(`daily ${done}/${syms.length} miss=${miss}`);
  }
}
const q = syms.slice();
await Promise.all(Array.from({ length: CONC }, () => worker(q)));
fs.writeFileSync("daily.json", JSON.stringify(daily));
console.error(`saved daily for ${Object.keys(daily).length} symbols`);
