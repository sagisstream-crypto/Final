import fs from "fs";
import { loadTbl } from "./loadtbl.mjs";
import { DEFAULT_W, PRESETS, COMPS } from "./engine.mjs";
import { clamp } from "./indicators.mjs";

const NC = COMPS.length;
const A_MULTS = [1.0, 1.5, 2.0, 2.5];
const GRADE_RANK = { 'B': 1, 'A': 2, 'A+': 3 };

// Wilson 95% нижняя граница — чтобы конфигурация на 12 сделках не побеждала
export function wilson(k, n) {
  if (!n) return 0;
  const z = 1.96, p = k / n;
  const d = 1 + z * z / n, c = p + z * z / (2 * n);
  const m = z * Math.sqrt(p * (1 - p) / n + z * z / (4 * n * n));
  return Math.max(0, (c - m) / d) * 100;
}

// Шаг 1: при заданных весах посчитать score/side/agree/against для всех строк
// и оставить только те, что могут стать сигналом при минимальном пороге.
export function candidates(T, preset, wMul, minThresh, tFrom = 0, tTo = Infinity) {
  const PR = PRESETS[preset], pmul = PR.w || {};
  const w = new Float64Array(NC);
  for (let i = 0; i < NC; i++) {
    const k = COMPS[i];
    w[i] = (DEFAULT_W[k] || 0) * (wMul[k] ?? 1) * (pmul[k] || 1);
  }
  const { n, stride, buf, tA, sA } = T;
  const idx = [], sc = [], ag = [], ag2 = [];
  for (let r = 0; r < n; r++) {
    const tt = tA[r];
    if (tt < tFrom || tt >= tTo) continue;
    const off = r * stride;
    let num = 0, den = 0, up = 0, dn = 0;
    for (let c = 0; c < NC; c++) {
      const v = buf[off + c];
      if (Number.isNaN(v)) continue;
      const ww = w[c];
      if (!ww) continue;
      num += ww * v; den += ww;
      if (v > 0.32) up++; else if (v < -0.32) dn++;
    }
    if (!den) continue;
    let s = num / den * 100;
    if (buf[off + NC + 1] > 2.6) s *= 0.75;          // перегрета от EMA21
    s = clamp(s, -100, 100);
    const a = s < 0 ? -s : s;
    if (a < minThresh) continue;
    idx.push(r); sc.push(s); ag.push(up); ag2.push(dn);
  }
  return { idx: Int32Array.from(idx), sc: Float32Array.from(sc),
           up: Int8Array.from(ag), dn: Int8Array.from(ag2) };
}

// Шаг 2: по кандидатам прогнать конкретную конфигурацию с правилами приложения:
// порог -> грейд -> фильтры -> кулдаун 30 мин на пару -> одна открытая сделка.
export function evalCfg(T, C, cfg, days) {
  const { stride, buf, tA, sA } = T;
  const amIdx = A_MULTS.indexOf(cfg.atrMult);
  const minRank = GRADE_RANK[cfg.minGrade] || 1;
  const seenT = new Map(), seenSide = new Map(), openUntil = new Map();
  let n = 0, wins = 0, sumR = 0, gp = 0, gl = 0, cancelled = 0;
  let sumHold = 0, tp2 = 0, stops = 0, timeouts = 0;
  const pairs = new Set();

  for (let q = 0; q < C.idx.length; q++) {
    const r = C.idx[q], off = r * stride;
    const s = C.sc[q], a = s < 0 ? -s : s;
    if (a < cfg.thresh) continue;
    const side = s >= 0 ? 0 : 1;                      // 0=long 1=short
    if (cfg.side !== 'all' && (cfg.side === 'long') !== (side === 0)) continue;
    const agree = side === 0 ? C.up[q] : C.dn[q];
    const against = side === 0 ? C.dn[q] : C.up[q];
    let g = 0;
    if (a >= 70 && agree >= 6 && against <= 2) g = 3;
    else if (a >= 58 && agree >= 5 && against <= 3) g = 2;
    else if (agree >= 4) g = 1;
    if (!g || g < minRank) continue;
    if (cfg.minAgree && agree < cfg.minAgree) continue;
    if (cfg.maxAgainst !== undefined && against > cfg.maxAgainst) continue;
    const qv24 = buf[off + NC + 2];
    if (cfg.minVol && qv24 < cfg.minVol * 1e6) continue;
    if (cfg.maxVol && qv24 > cfg.maxVol * 1e6) continue;
    // фильтр BTC — как blocked в analyze()
    if (cfg.btcFilter) {
      const bt = cfg.btcTrend ? cfg.btcTrend[r] : 0;
      if ((side === 0 && bt < -0.35) || (side === 1 && bt > 0.35)) continue;
    }
    const sym = sA[r], t = tA[r];
    // S.seen: тот же сигнал по паре не чаще 30 мин, если сторона не сменилась
    const pt = seenT.get(sym), ps = seenSide.get(sym);
    const fresh = pt === undefined || ps !== side || (t - pt > 30 * 60000);
    if (!fresh) continue;
    seenT.set(sym, t); seenSide.set(sym, side);
    // openJournal: по паре не больше одной открытой записи
    const ou = openUntil.get(sym);
    if (ou !== undefined && t < ou) continue;

    const base = off + NC + 3 + (side * A_MULTS.length + amIdx) * 2;
    const res = buf[base], bm = buf[base + 1];
    if (bm > 0) openUntil.set(sym, t + bm * 60000);
    if (Number.isNaN(res)) { cancelled++; continue; }   // лимит не исполнился
    n++; sumR += res; sumHold += bm;
    if (res > 0) { wins++; gp += res; } else gl += -res;
    if (res === 2) tp2++; else if (res === -1) stops++; else timeouts++;
    pairs.add(sym);
  }
  return {
    n, cancelled, winrate: n ? wins / n * 100 : 0, wr_lb: wilson(wins, n),
    sumR, avgR: n ? sumR / n : 0, pf: gl ? gp / gl : (gp ? Infinity : 0),
    perDay: n / days, tp2, stops, timeouts, pairs: pairs.size,
    avgHoldH: n ? sumHold / n / 60 : 0,
  };
}
