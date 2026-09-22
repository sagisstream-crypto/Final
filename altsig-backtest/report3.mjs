import fs from "fs";
import { loadTbl } from "./loadtbl.mjs";
import { candidates, evalCfg } from "./sweep.mjs";
import { COMPS, PRESETS } from "./engine.mjs";
import { loadKlines, SPAN_START, SPAN_END } from "./loader.mjs";
import { ema, clamp } from "./indicators.mjs";

const P = process.env.PRESET || "intraday";
const MIN_N = Number(process.env.MIN_N || 400);
const MID = Date.parse("2026-03-01T00:00:00Z");
const DAYS_TR = (MID - SPAN_START) / 86400000, DAYS_TE = (SPAN_END - MID) / 86400000;
const DAYS_ALL = (SPAN_END - SPAN_START) / 86400000;
const out = []; const say = s => { out.push(s); console.log(s); };

const btc5 = await loadKlines("BTCUSDT", "5m");
const be50 = ema(btc5.c, 50);
const btcT = btc5.t.map(t => t + 299999);
const btcTr = btc5.c.map((c, i) => Number.isFinite(be50[i]) ? clamp((c - be50[i]) / c * 120, -1, 1) : 0);
const T = loadTbl(P);
const bt = new Float32Array(T.n);
{ let j = 0;
  const order = Array.from({ length: T.n }, (_, i) => i).sort((a, b) => T.tA[a] - T.tA[b]);
  for (const i of order) { while (j + 1 < btcT.length && btcT[j + 1] <= T.tA[i]) j++; bt[i] = btcTr[j]; } }

const ONES = Object.fromEntries(COMPS.map(k => [k, 1]));
const Ctr = candidates(T, P, ONES, 40, SPAN_START, MID);
const Cte = candidates(T, P, ONES, 40, MID, SPAN_END);
const Call = candidates(T, P, ONES, 40);
say(`\n${"=".repeat(132)}\n## ${P.toUpperCase()} (${PRESETS[P].tf}/${PRESETS[P].slow}) · кандидатов: обучение ${Ctr.idx.length}, контроль ${Cte.idx.length}\n${"=".repeat(132)}`);

const grid = [];
for (const thresh of [40, 50, 60])
  for (const minGrade of ['B', 'A'])
    for (const side of ['all', 'long', 'short'])
      for (const atrMult of [1.0, 1.5, 2.0, 2.5])
        for (const btcFilter of [false, true])
          for (const minAgree of [0, 5, 6])
            for (const maxAgainst of [9, 1, 0])
              grid.push({ thresh, minGrade, side, atrMult, btcFilter, minAgree, maxAgainst, btcTrend: bt });
say(`перебираем ${grid.length} конфигураций на ОБУЧЕНИИ…`);
const lab = c => `thr${c.thresh} ${c.minGrade} ${c.side} ATR×${c.atrMult}${c.btcFilter ? " BTC" : ""}${c.minAgree ? " ag>=" + c.minAgree : ""}${c.maxAgainst < 9 ? " vs<=" + c.maxAgainst : ""}`;

const res = grid.map(c => ({ c, tr: evalCfg(T, Ctr, c, DAYS_TR) }))
                .filter(x => x.tr.n >= MIN_N);
res.forEach(x => { x.te = evalCfg(T, Cte, x.c, DAYS_TE); });
res.sort((a, b) => b.tr.wr_lb - a.tr.wr_lb);

const HD = ["конфигурация".padEnd(40), "N_об".padStart(7), "WR_об".padStart(7), "PF_об".padStart(6),
            "N_кон".padStart(7), "WR_кон".padStart(7), "LB_кон".padStart(7), "PF_кон".padStart(6),
            "R_кон".padStart(7), "в день".padStart(7)].join(" ");
const LN = x => [lab(x.c).padEnd(40), String(x.tr.n).padStart(7), (x.tr.winrate.toFixed(1)+"%").padStart(7),
  x.tr.pf.toFixed(2).padStart(6), String(x.te.n).padStart(7), (x.te.winrate.toFixed(1)+"%").padStart(7),
  x.te.wr_lb.toFixed(1).padStart(7), x.te.pf.toFixed(2).padStart(6),
  ((x.te.sumR>0?"+":"")+x.te.sumR.toFixed(0)).padStart(7), x.te.perDay.toFixed(2).padStart(7)].join(" ");

say(`\n### F. Топ-12 по winrate НА ОБУЧЕНИИ — и что из этого вышло на контроле`);
say(HD);
res.slice(0, 12).forEach(x => say(LN(x)));
const dTop = res.slice(0, 12).reduce((a, x) => a + (x.tr.winrate - x.te.winrate), 0) / 12;
say(`  средняя просадка winrate обучение -> контроль: ${dTop.toFixed(1)} п.п.`);

say(`\n### G. Устойчивые: лучшие по КОНТРОЛЮ среди тех, кто и на обучении был прибыльным (PF_об > 1)`);
say(HD);
const stable = res.filter(x => x.tr.pf > 1 && x.te.n >= MIN_N).sort((a, b) => b.te.wr_lb - a.te.wr_lb);
stable.slice(0, 12).forEach(x => say(LN(x)));

say(`\n### H. Лучшие по сумме R на контроле (прибыль, а не доля побед)`);
say(HD);
[...res].filter(x => x.te.n >= MIN_N).sort((a, b) => b.te.sumR - a.te.sumR).slice(0, 10).forEach(x => say(LN(x)));

// сводка: сколько конфигураций вообще прибыльны на контроле
const prof = res.filter(x => x.te.n >= MIN_N && x.te.pf > 1).length;
const tot = res.filter(x => x.te.n >= MIN_N).length;
say(`\nприбыльных на контроле: ${prof} из ${tot} (${(100*prof/tot).toFixed(0)}%)`);
const allBest = [...res].sort((a,b)=>b.tr.winrate-a.tr.winrate)[0];
say(`максимальный winrate на обучении: ${allBest.tr.winrate.toFixed(1)}% (${allBest.tr.n} сделок) -> на контроле ${allBest.te.winrate.toFixed(1)}%`);
fs.writeFileSync(`report_F_${P}.txt`, out.join("\n"));
console.log(`\n[сохранено report_F_${P}.txt]`);
