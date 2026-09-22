import fs from "fs";
import { loadTbl } from "./loadtbl.mjs";
import { candidates, evalCfg } from "./sweep.mjs";
import { COMPS, PRESETS } from "./engine.mjs";
import { loadKlines, SPAN_START, SPAN_END } from "./loader.mjs";
import { ema, clamp } from "./indicators.mjs";

const P = process.env.PRESET || "intraday";
const MIN_N = Number(process.env.MIN_N || 400);
const MID = Date.parse("2026-03-01T00:00:00Z");
const DAYS_ALL = (SPAN_END - SPAN_START) / 86400000;
const DAYS_TR = (MID - SPAN_START) / 86400000, DAYS_TE = (SPAN_END - MID) / 86400000;
const out = []; const say = s => { out.push(s); console.log(s); };

const btc5 = await loadKlines("BTCUSDT", "5m");
const be50 = ema(btc5.c, 50);
const btcT = btc5.t.map(t => t + 299999);
const btcTr = btc5.c.map((c, i) => Number.isFinite(be50[i]) ? clamp((c - be50[i]) / c * 120, -1, 1) : 0);

const T = loadTbl(P);
const bt = new Float32Array(T.n);
{
  let j = 0;
  const order = Array.from({ length: T.n }, (_, i) => i).sort((a, b) => T.tA[a] - T.tA[b]);
  for (const i of order) { while (j + 1 < btcT.length && btcT[j + 1] <= T.tA[i]) j++; bt[i] = btcTr[j]; }
}
const ONES = Object.fromEntries(COMPS.map(k => [k, 1]));
const H = (l) => [l.padEnd(44), "сделок".padStart(7), "в день".padStart(7), "winrate".padStart(8),
  "LB95".padStart(6), "PF".padStart(6), "сумма R".padStart(8), "ср. R".padStart(7), "пар".padStart(5)].join(" ");
const L = (l, r) => [l.padEnd(44), String(r.n).padStart(7), r.perDay.toFixed(2).padStart(7),
  (r.winrate.toFixed(1) + "%").padStart(8), r.wr_lb.toFixed(1).padStart(6),
  (r.pf === Infinity ? "∞" : r.pf.toFixed(2)).padStart(6),
  ((r.sumR > 0 ? "+" : "") + r.sumR.toFixed(0)).padStart(8), r.avgR.toFixed(3).padStart(7),
  String(r.pairs).padStart(5)].join(" ");

say(`\n${"=".repeat(122)}\n## ${P.toUpperCase()} (${PRESETS[P].tf}/${PRESETS[P].slow}) · строк ${T.n} · отсечка N>=${MIN_N}\n${"=".repeat(122)}`);

const Call = candidates(T, P, ONES, 40);
// ---- C. дополнительные фильтры поверх лучшей базы ----
say(`\n### C. Фильтры поверх базы (порог 50, грейд B+, шорты, ATR×2.5)`);
say(H("фильтр"));
const base = { thresh: 50, minGrade: 'B', side: 'short', atrMult: 2.5, btcFilter: false, btcTrend: bt };
const filters = [
  ["без доп. фильтров", {}],
  ["фильтр BTC вкл.", { btcFilter: true }],
  ["согласных >= 5", { minAgree: 5 }],
  ["согласных >= 6", { minAgree: 6 }],
  ["против <= 2", { maxAgainst: 2 }],
  ["против <= 1", { maxAgainst: 1 }],
  ["против <= 0", { maxAgainst: 0 }],
  ["согласных >= 5, против <= 1", { minAgree: 5, maxAgainst: 1 }],
  ["согласных >= 6, против <= 1", { minAgree: 6, maxAgainst: 1 }],
  ["объём 24ч >= $10M", { minVol: 10 }],
  ["объём 24ч >= $50M", { minVol: 50 }],
  ["объём 24ч <= $30M", { maxVol: 30 }],
  ["объём 24ч <= $10M", { maxVol: 10 }],
];
const fres = filters.map(([l, f]) => ({ l, f, r: evalCfg(T, Call, { ...base, ...f }, DAYS_ALL) }));
fres.forEach(x => say(L(x.l, x.r)));

// ---- D. координатный спуск по весам ----
say(`\n### D. Подбор весов координатным спуском (цель — нижняя граница winrate, N>=${MIN_N})`);
const MULS = [0, 0.5, 0.75, 1, 1.5, 2];
function score(r) {
  if (r.n < MIN_N) return -Infinity;
  return r.wr_lb;
}
let curW = { ...ONES };
let bestCfg = { ...base, minAgree: 5, maxAgainst: 1 };
let curC = candidates(T, P, curW, 40);
let curR = evalCfg(T, curC, bestCfg, DAYS_ALL);
let bestVal = score(curR), tried = 1;
say(`старт: ` + L("веса по умолчанию", curR).trim());
for (let pass = 0; pass < 2; pass++) {
  let improved = false;
  for (const k of COMPS) {
    for (const m of MULS) {
      if (curW[k] === m) continue;
      const cand = { ...curW, [k]: m };
      const C2 = candidates(T, P, cand, 40);
      const r = evalCfg(T, C2, bestCfg, DAYS_ALL);
      tried++;
      if (score(r) > bestVal) { bestVal = score(r); curW = cand; curC = C2; curR = r; improved = true; }
    }
  }
  say(`  проход ${pass + 1}: winrate ${curR.winrate.toFixed(1)}% (LB ${curR.wr_lb.toFixed(1)}), сделок ${curR.n}, перебрано ${tried}`);
  if (!improved) break;
}
say(`\nнайденные множители весов: ` + COMPS.map(k => `${k}×${curW[k]}`).filter(s => !s.endsWith("×1")).join(", "));
say(H("итог"));
say(L("подобранные веса", curR));

// ---- E. проверка на отложенном периоде ----
say(`\n### E. Обучение (сен'25–фев'26) против контроля (мар'26–авг'26)`);
say([" ".padEnd(44), "сделок".padStart(7), "в день".padStart(7), "winrate".padStart(8), "LB95".padStart(6),
     "PF".padStart(6), "сумма R".padStart(8), "ср. R".padStart(7), "пар".padStart(5)].join(" "));
for (const [label, W, cfg] of [
  ["заводские веса, заводские настройки", ONES, { thresh: 50, minGrade: 'B', side: 'all', atrMult: 1.5, btcFilter: false, btcTrend: bt }],
  ["заводские веса, лучшая база", ONES, bestCfg],
  ["подобранные веса, лучшая база", curW, bestCfg],
]) {
  const ctr = candidates(T, P, W, 40, SPAN_START, MID);
  const cte = candidates(T, P, W, 40, MID, SPAN_END);
  const rtr = evalCfg(T, ctr, cfg, DAYS_TR), rte = evalCfg(T, cte, cfg, DAYS_TE);
  say(`  ${label}`);
  say(L("    обучение", rtr));
  say(L("    КОНТРОЛЬ", rte));
}
fs.writeFileSync(`report_C_${P}.txt`, out.join("\n"));
console.log(`\n[сохранено report_C_${P}.txt]`);
