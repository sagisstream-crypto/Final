import fs from "fs";
import { Worker } from "worker_threads";
import { loadKlines } from "./loader.mjs";
import { COMPS } from "./engine.mjs";
import { ema, chg, clamp } from "./indicators.mjs";

// fs.writeSync не принимает больше 2 ГБ за раз — пишем кусками
const CHUNK = 1 << 30;
function writeBig(fd, b) {
  for (let o = 0; o < b.length; o += CHUNK) fs.writeSync(fd, b, o, Math.min(CHUNK, b.length - o));
}

const PRESETS_RUN = (process.env.PRESETS || "scalp,intraday,swing").split(",");
const A_MULTS = [1.0, 1.5, 2.0, 2.5];
const SCORE_FLOOR = 30;
const NW = Number(process.env.NW || 6);

console.log("грузим BTC…");
const btc5 = await loadKlines("BTCUSDT", "5m");
const be50 = ema(btc5.c, 50);
const btc = {
  t: btc5.t.map(t => t + 299999),
  ch1h: btc5.c.map((c, i) => chg(c, btc5.c[Math.max(0, i - 12)])),
  trend: btc5.c.map((c, i) => Number.isFinite(be50[i]) ? clamp((c - be50[i]) / c * 120, -1, 1) : 0),
};
console.log(`BTC: ${btc5.c.length} баров 5m`);

const u = JSON.parse(fs.readFileSync("universe.json", "utf8"));
const symbols = Object.keys(u.uni).sort();
console.log(`пар: ${symbols.length} · пресеты: ${PRESETS_RUN.join(",")} · воркеров: ${NW}`);

const chunks = Array.from({ length: NW }, () => []);
symbols.forEach((s, i) => chunks[i % NW].push(s));
const t0 = Date.now();
const stat = Array.from({ length: NW }, () => ({ doneSyms: 0, totalRows: 0 }));
let lastLog = 0;

const results = await Promise.all(chunks.map((list, wi) => new Promise((res, rej) => {
  const w = new Worker(new URL("./worker.mjs", import.meta.url), {
    workerData: { symbols: list, btc, presets: PRESETS_RUN, aMults: A_MULTS, scoreFloor: SCORE_FLOOR },
    resourceLimits: { maxOldGenerationSizeMb: 2600 },
    env: { ...process.env, BT_CACHE: `./cache${wi}` },
  });
  w.on("message", m => {
    if (m.type === "progress") {
      stat[wi] = m;
      const now = Date.now();
      if (now - lastLog > 15000) {
        lastLog = now;
        const d = stat.reduce((a, s) => a + s.doneSyms, 0);
        const r = stat.reduce((a, s) => a + s.totalRows, 0);
        const el = (now - t0) / 1000;
        console.log(`[${(el/60).toFixed(1)}м] пар ${d}/${symbols.length} · строк ${(r/1e6).toFixed(2)}M · ETA ~${d ? ((el/d*(symbols.length-d))/60).toFixed(0) : "?"}м`);
      }
    } else if (m.type === "done") res(m);
  });
  w.on("error", rej);
})));

const NC = COMPS.length, NOUT = 2 * A_MULTS.length * 2, STRIDE = NC + 3 + NOUT;
const allSyms = [];
const symMap = results.map(r => r.syms.map(s => { const id = allSyms.length; allSyms.push(s); return id; }));
for (const p of PRESETS_RUN) {
  const total = results.reduce((a, r) => a + r.presets[p].n, 0);
  console.log(`${p}: ${total} строк, сохраняем…`);
  const buf = new Float32Array(total * STRIDE), tA = new Float64Array(total), sA = new Int32Array(total);
  let off = 0;
  results.forEach((r, wi) => {
    const P = r.presets[p];
    buf.set(P.buf, off * STRIDE); tA.set(P.tA, off);
    for (let i = 0; i < P.n; i++) sA[off + i] = symMap[wi][P.sA[i]];
    off += P.n;
  });
  const fd = fs.openSync(`tbl_${p}.bin`, "w");
  writeBig(fd, Buffer.from(buf.buffer, 0, total * STRIDE * 4));
  writeBig(fd, Buffer.from(tA.buffer, 0, total * 8));
  writeBig(fd, Buffer.from(sA.buffer, 0, total * 4));
  fs.closeSync(fd);
  fs.writeFileSync(`tbl_${p}.json`, JSON.stringify({ n: total, stride: STRIDE, comps: COMPS, aMults: A_MULTS, syms: allSyms, scoreFloor: SCORE_FLOOR }));
}
console.log(`ГОТОВО за ${((Date.now()-t0)/60000).toFixed(1)}м · пар с данными ${allSyms.length}`);
