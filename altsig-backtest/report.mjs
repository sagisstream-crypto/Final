import fs from "fs";
import { loadTbl } from "./loadtbl.mjs";
import { candidates, evalCfg, wilson } from "./sweep.mjs";
import { DEFAULT_W, COMPS, PRESETS } from "./engine.mjs";
import { loadKlines, SPAN_START, SPAN_END } from "./loader.mjs";
import { ema, clamp } from "./indicators.mjs";

const DAYS = (SPAN_END - SPAN_START) / 86400000;
const PRESETS_RUN = (process.env.PRESETS || "scalp,intraday,swing").split(",");
const MIN_N = Number(process.env.MIN_N || 300);
const out = []; const say = s => { out.push(s); console.log(s); };

// btcTrend по времени бара (в таблицу не писали — восстанавливаем из ряда BTC)
console.log("грузим BTC для фильтра…");
const btc5 = await loadKlines("BTCUSDT", "5m");
const be50 = ema(btc5.c, 50);
const btcT = btc5.t.map(t => t + 299999);
const btcTr = btc5.c.map((c, i) => Number.isFinite(be50[i]) ? clamp((c - be50[i]) / c * 120, -1, 1) : 0);
function btcTrendFor(tA, n) {
  const arr = new Float32Array(n);
  let j = 0;
  const order = Array.from({ length: n }, (_, i) => i).sort((a, b) => tA[a] - tA[b]);
  for (const i of order) {
    while (j + 1 < btcT.length && btcT[j + 1] <= tA[i]) j++;
    arr[i] = btcTr[j];
  }
  return arr;
}

const H = (l) => [l.padEnd(38), "сделок".padStart(7), "в день".padStart(7), "winrate".padStart(8),
  "LB95".padStart(6), "PF".padStart(6), "сумма R".padStart(8), "ср. R".padStart(7),
  "TP2".padStart(6), "стоп".padStart(6), "тайм".padStart(6), "часов".padStart(6), "пар".padStart(5)].join(" ");
const L = (l, r) => [l.padEnd(38), String(r.n).padStart(7), r.perDay.toFixed(2).padStart(7),
  (r.winrate.toFixed(1) + "%").padStart(8), r.wr_lb.toFixed(1).padStart(6),
  (r.pf === Infinity ? "∞" : r.pf.toFixed(2)).padStart(6),
  ((r.sumR > 0 ? "+" : "") + r.sumR.toFixed(0)).padStart(8), r.avgR.toFixed(3).padStart(7),
  String(r.tp2).padStart(6), String(r.stops).padStart(6), String(r.timeouts).padStart(6),
  r.avgHoldH.toFixed(1).padStart(6), String(r.pairs).padStart(5)].join(" ");

const ONES = Object.fromEntries(COMPS.map(k => [k, 1]));
const best = {};

for (const P of PRESETS_RUN) {
  if (!fs.existsSync(`tbl_${P}.bin`)) { console.log(`нет таблицы для ${P}`); continue; }
  const T = loadTbl(P);
  const bt = btcTrendFor(T.tA, T.n);
  say(`\n${"=".repeat(120)}\n## ПРЕСЕТ ${P.toUpperCase()} (${PRESETS[P].tf}/${PRESETS[P].slow}) · строк в таблице ${T.n}\n${"=".repeat(120)}`);
  const C = candidates(T, P, ONES, 40);
  say(`кандидатов с |score|>=40: ${C.idx.length}`);

  // ---- A. базовая сетка: порог × грейд × сторона × atrMult ----
  say(`\n### A. Пороги, грейды, сторона, множитель стопа (веса по умолчанию)`);
  say(H("конфигурация"));
  const rows = [];
  for (const thresh of [40, 45, 50, 55, 60, 65]) {
    for (const minGrade of ['B', 'A', 'A+']) {
      for (const side of ['all', 'long', 'short']) {
        for (const atrMult of [1.0, 1.5, 2.0, 2.5]) {
          const cfg = { thresh, minGrade, side, atrMult, btcFilter: false, btcTrend: bt };
          const r = evalCfg(T, C, cfg, DAYS);
          rows.push({ cfg, r, label: `thr${thresh} ${minGrade} ${side} ATR×${atrMult}` });
        }
      }
    }
  }
  rows.filter(x => x.r.n >= MIN_N).sort((a, b) => b.r.wr_lb - a.r.wr_lb).slice(0, 15)
      .forEach(x => say(L(x.label, x.r)));
  say(`  (показаны 15 лучших по нижней границе winrate из ${rows.length} комбинаций, отсечка N>=${MIN_N})`);

  // ---- B. эталон: заводские настройки ----
  const stock = evalCfg(T, C, { thresh: 50, minGrade: 'B', side: 'all', atrMult: 1.5, btcFilter: false, btcTrend: bt }, DAYS);
  say(`\n### B. Заводские (порог 50, грейд B+, обе стороны, ATR×1.5)`);
  say(H("конфигурация")); say(L("заводские", stock));

  best[P] = { rows, stock, T, C, bt };
}
fs.writeFileSync("report_A.txt", out.join("\n"));
console.log("\n[A сохранён]");
