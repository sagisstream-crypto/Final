import { parentPort, workerData } from "worker_threads";
import { btNewTable, btScanSeries, btFillGaps, computeScore, BT_COLS, BT_WARMUP_MIN, BT_DAY_MIN } from "./engine.mjs";
import { loadSymbolMinutes, dropCache, SPAN_START, SPAN_MIN } from "./loader.mjs";

const { symbols, testDays, maxPrice, maxVol } = workerData;
const DAY_MS = 86400000;
const WINDOW = 3000;

// накопитель: те же колонки, но растём вручную (btPush ждёт объект)
const cap0 = 1 << 20;
let cap = cap0, n = 0;
const col = {};
for (const c of BT_COLS) col[c] = new Float64Array(cap);
function grow() {
  cap *= 2;
  for (const c of BT_COLS) { const a = new Float64Array(cap); a.set(col[c]); col[c] = a; }
}
const syms = [];
const symIdx = new Map();

let scannedDays = 0, symsOk = 0, rawEvents = 0;
for (const sym of symbols) {
  let data;
  try { data = await loadSymbolMinutes(sym); } catch (e) { dropCache(sym); continue; }
  const { rows } = data;
  if (!rows || rows.length < 5000) { dropCache(sym); continue; }
  const grid = btFillGaps(rows, SPAN_START, SPAN_MIN);
  const scratch = btNewTable();
  scratch.days = testDays.slice();
  let anyDay = 0;
  testDays.forEach((day, dayIdx) => {
    const start = Math.round((day - DAY_MS - SPAN_START) / 60000);
    if (start < 0 || start + WINDOW > SPAN_MIN) return;
    const k = grid.slice(start, start + WINDOW);
    let live = 0;
    for (let i = BT_WARMUP_MIN; i < BT_WARMUP_MIN + BT_DAY_MIN; i++) if (!k[i].empty) live++;
    if (live < 200) return;
    btScanSeries(k, { table: scratch, sym, dayIdx, computeScore });
    anyDay++;
  });
  rawEvents += scratch.n;
  // фильтр вселенной приложения + отсев минут, недостижимых ни одним порогом сетки
  let mySymId = -1;
  for (let i = 0; i < scratch.n; i++) {
    if (!(scratch.col.price[i] < maxPrice)) continue;
    if (!(scratch.col.vol24[i] <= maxVol)) continue;
    const d1 = scratch.col.d1m[i], d2 = scratch.col.d2m[i];
    const s = Math.max(scratch.col.score0[i], scratch.col.score1[i]);
    const qFin = scratch.col.quietOwnT[i] < 1e14;
    if (!(d1 >= 5000 || d2 >= 5000 || s >= 40 || qFin)) continue;
    if (mySymId < 0) { mySymId = syms.length; syms.push(sym); symIdx.set(sym, mySymId); }
    if (n >= cap) grow();
    for (const c of BT_COLS) col[c][n] = scratch.col[c][i];
    col.sym[n] = mySymId;
    n++;
  }
  scannedDays += anyDay;
  if (anyDay) symsOk++;
  dropCache(sym);
  parentPort.postMessage({ type: "progress", sym, n, symsOk, scannedDays, rawEvents });
}

const payload = { type: "done", n, syms, cols: {} };
const transfer = [];
for (const c of BT_COLS) { const a = col[c].slice(0, n); payload.cols[c] = a; transfer.push(a.buffer); }
parentPort.postMessage(payload, transfer);
