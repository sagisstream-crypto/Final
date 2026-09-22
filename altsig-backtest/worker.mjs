import { parentPort, workerData } from "worker_threads";
import { loadKlines, loadPremium, loadFunding, loadOI, dayList } from "./loader.mjs";
import { scanPair, PRESETS, DEFAULT_W, COMPS } from "./engine.mjs";
import { buildPlan, simulate } from "./simulate.mjs";
import { clamp } from "./indicators.mjs";

const { symbols, btc, presets, aMults, scoreFloor } = workerData;
const DAYS = dayList();
const NC = COMPS.length;
const NOUT = 2 * aMults.length * 2;             // 2 стороны × сетка atrMult × {result, blockMin}
const STRIDE = NC + 3 + NOUT;                   // компоненты + s0,extATR,qv24 + исходы

const acc = {};                                 // preset -> {cap,n,buf,tArr,symArr}
for (const p of presets) acc[p] = { cap: 1 << 18, n: 0, buf: new Float32Array((1 << 18) * STRIDE),
                                    tArr: new Float64Array(1 << 18), symArr: new Int32Array(1 << 18) };
function grow(A) {
  A.cap *= 2;
  const b = new Float32Array(A.cap * STRIDE); b.set(A.buf); A.buf = b;
  const t = new Float64Array(A.cap); t.set(A.tArr); A.tArr = t;
  const s = new Int32Array(A.cap); s.set(A.symArr); A.symArr = s;
}
const symList = [];
function rollSum(a, win) {
  const n = a.length, out = new Float64Array(n);
  let s = 0;
  for (let i = 0; i < n; i++) { s += a[i]; if (i >= win) s -= a[i - win]; out[i] = s; }
  return out;
}

let doneSyms = 0, totalRows = 0;
for (const sym of symbols) {
  let k5, k15, k1h, k4h, prem, fund, oi;
  try {
    [k5, k15, k1h, k4h, prem, fund, oi] = await Promise.all([
      loadKlines(sym, "5m"), loadKlines(sym, "15m"), loadKlines(sym, "1h"), loadKlines(sym, "4h"),
      loadPremium(sym), loadFunding(sym), loadOI(sym, DAYS, 20),
    ]);
  } catch (e) { parentPort.postMessage({ type: "progress", sym, err: 1, doneSyms: ++doneSyms, totalRows }); continue; }
  if (!k5 || k5.c.length < 3000) { parentPort.postMessage({ type: "progress", sym, doneSyms: ++doneSyms, totalRows }); continue; }
  const qv24 = rollSum(k5.qv, 288);
  const series = { "5m": k5, "15m": k15, "1h": k1h, "4h": k4h };
  const symId = symList.length; symList.push(sym);

  for (const pn of presets) {
    const PR = PRESETS[pn], pmul = PR.w || {};
    let bars;
    try {
      bars = scanPair({ main: series[PR.tf], slow: series[PR.slow], k5, funding: fund, oi, basis: prem, qv24 }, pn, btc);
    } catch (e) { continue; }
    if (!bars) continue;
    const A = acc[pn];
    for (const b of bars) {
      let num = 0, den = 0;
      for (const k of COMPS) {
        const v = b.C[k]; if (v === undefined) continue;
        const w = (DEFAULT_W[k] || 0) * (pmul[k] || 1); if (!w) continue;
        num += w * v; den += w;
      }
      if (!den) continue;
      let s = num / den * 100;
      if (b.extATR > 2.6) s *= 0.75;
      s = clamp(s, -100, 100);
      if (Math.abs(s) < scoreFloor) continue;      // недостижимо ни одним порогом перебора

      if (A.n >= A.cap) grow(A);
      const off = A.n * STRIDE;
      for (let ci = 0; ci < NC; ci++) {
        const v = b.C[COMPS[ci]];
        A.buf[off + ci] = v === undefined ? NaN : v;
      }
      A.buf[off + NC] = s;
      A.buf[off + NC + 1] = b.extATR;
      A.buf[off + NC + 2] = b.qv24;
      // исходы: обе стороны × сетка atrMult (план и симуляция — как в приложении)
      let oi2 = off + NC + 3;
      for (const side of ["long", "short"]) {
        for (const am of aMults) {
          const aMult = PR.atrMult * (am / 1.5);
          const pl = buildPlan(b, side, aMult);
          let r = NaN, bm = 0;
          if (pl.R > 0) {
            const sim = simulate(k5, b.i5, side, pl.entry, pl.stop, pl.tp2, pl.entryType, pl.R);
            if (sim.truncated) { r = NaN; bm = 0; }
            else if (sim.cancelled) { r = NaN; bm = sim.blockMin; }
            else { r = sim.result; bm = sim.blockMin; }
          }
          A.buf[oi2++] = r; A.buf[oi2++] = bm;
        }
      }
      A.tArr[A.n] = b.t; A.symArr[A.n] = symId;
      A.n++; totalRows++;
    }
  }
  parentPort.postMessage({ type: "progress", sym, doneSyms: ++doneSyms, totalRows });
}

const payload = { type: "done", syms: symList, presets: {} };
const transfer = [];
for (const p of presets) {
  const A = acc[p];
  const buf = A.buf.slice(0, A.n * STRIDE), tA = A.tArr.slice(0, A.n), sA = A.symArr.slice(0, A.n);
  payload.presets[p] = { n: A.n, buf, tA, sA };
  transfer.push(buf.buffer, tA.buffer, sA.buffer);
}
parentPort.postMessage(payload, transfer);
