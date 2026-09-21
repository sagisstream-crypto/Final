import fs from "fs";
import { Worker } from "worker_threads";
import { BT_COLS } from "./engine.mjs";

const u = JSON.parse(fs.readFileSync("universe.json", "utf8"));
const TEST_DAYS = u.days.map(d => Date.parse(d + "T00:00:00Z"));
const MAX_PRICE = 10, MAX_VOL = 5e6;
// качаем всё, что хоть раз подходило под фильтр шапки; точный отбор — поминутно в движке
const symbols = Object.keys(u.download).sort();
const NW = Number(process.env.NW || 4);
console.log(`symbols=${symbols.length} days=${TEST_DAYS.length} workers=${NW}`);

const chunks = Array.from({ length: NW }, () => []);
symbols.forEach((s, i) => chunks[i % NW].push(s));

const t0 = Date.now();
const stats = Array.from({ length: NW }, () => ({ n: 0, symsOk: 0, scannedDays: 0, rawEvents: 0, sym: "" }));
let lastLog = 0, doneCount = 0;

const results = await Promise.all(chunks.map((list, wi) => new Promise((resolve, reject) => {
  const w = new Worker(new URL("./worker.mjs", import.meta.url), {
    workerData: { symbols: list, testDays: TEST_DAYS, maxPrice: MAX_PRICE, maxVol: MAX_VOL },
    resourceLimits: { maxOldGenerationSizeMb: 3000 },
    env: { ...process.env, BT_MAX_EVENTS: "5000000", BT_CACHE: `./cache${wi}` },
  });
  w.on("message", m => {
    if (m.type === "progress") {
      stats[wi] = m;
      const now = Date.now();
      if (now - lastLog > 15000) {
        lastLog = now;
        const tot = stats.reduce((a, s) => a + s.n, 0);
        const sd = stats.reduce((a, s) => a + s.symsOk, 0);
        const raw = stats.reduce((a, s) => a + s.rawEvents, 0);
        const el = (now - t0) / 1000;
        const eta = sd ? el / sd * (symbols.length - sd) : 0;
        console.log(`[${(el/60).toFixed(1)}m] symbols ${sd}/${symbols.length} · events ${(tot/1e6).toFixed(2)}M (raw ${(raw/1e6).toFixed(1)}M) · ETA ~${(eta/60).toFixed(0)}m`);
      }
    } else if (m.type === "done") { doneCount++; resolve(m); }
  });
  w.on("error", reject);
})));

// слияние: перенумеровываем символы
const total = results.reduce((a, r) => a + r.n, 0);
console.log(`merging ${total} events from ${NW} workers…`);
const col = {};
for (const c of BT_COLS) col[c] = new Float64Array(total);
const allSyms = [];
let off = 0;
for (const r of results) {
  const map = r.syms.map(s => { const id = allSyms.length; allSyms.push(s); return id; });
  for (const c of BT_COLS) col[c].set(r.cols[c], off);
  for (let i = 0; i < r.n; i++) col.sym[off + i] = map[r.cols.sym[i]];
  off += r.n;
}
const meta = {
  market: "USDT-M", generatedAt: Date.now(), days: TEST_DAYS.length,
  maxPrice: MAX_PRICE, maxVol: MAX_VOL, symbolsTotal: symbols.length,
  symbolsWithEvents: allSyms.length, source: "data.binance.vision monthly 1m klines 2026-03..2026-08",
  elapsedSec: (Date.now() - t0) / 1000,
};
fs.writeFileSync("table_meta.json", JSON.stringify({ n: total, syms: allSyms, days: TEST_DAYS, meta }));
const fd = fs.openSync("table_cols.bin", "w");
for (const c of BT_COLS) fs.writeSync(fd, Buffer.from(col[c].buffer, 0, total * 8));
fs.closeSync(fd);
console.log(`DONE events=${total} symbols=${allSyms.length} elapsed=${((Date.now()-t0)/60000).toFixed(1)}m`);
