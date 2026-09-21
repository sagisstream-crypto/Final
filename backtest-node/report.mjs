import fs from "fs";
import { btEval, btMarginal, btOptimize, btWilson } from "./engine.mjs";
import { loadTable } from "./loadtable.mjs";

const tb = loadTable();
const D = tb.days.length;
console.log(`# events=${tb.n} symbols=${tb.syms.length} days=${D} (${tb.meta.source})`);

const BASE = { horizon: 60, pump: 3, big: 10, maxPrice: 10, maxVol: 5e6, minVol: 0 };
const money = v => v >= 1e6 ? "$" + (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M"
               : v >= 1000 ? "$" + Math.round(v / 1000) + "k" : "$" + Math.round(v);
const p1 = v => (v === null || isNaN(v)) ? "—" : v.toFixed(1);
const MIN_N = Number(process.env.MIN_N || 200);   // порог «это не находка на десятке наблюдений»

const out = [];
const say = s => { out.push(s); console.log(s); };

// ---------- 1. SCORE × Δ1м : доля выносов (MFE>=3% за 60 мин) ----------
const SCORES = [40, 45, 50, 55, 60, 65, 70, 75, 80, 85];
const T1 = [5000, 10000, 15000, 20000, 30000, 40000, 50000, 75000, 100000, 150000];
say("\n## A. SCORE-сигнал: доля выносов +3%/60мин  [ячейка: % (сигналов/день)]");
say("score | " + T1.map(money).join(" | "));
const gridA = [];
for (const s of SCORES) {
  const row = [];
  for (const t of T1) {
    const r = btEval(tb, { ...BASE, types: ["SIGNAL"], scoreThr: s, t1m: t, minD1m: t });
    row.push(r);
  }
  gridA.push({ s, row });
  say(String(s).padStart(5) + " | " + row.map(r => r.n < MIN_N ? `·(${(r.perDay).toFixed(1)})` : `${p1(r.pumpRate)} (${r.perDay.toFixed(1)})`).join(" | "));
}
say("\n## A2. те же ячейки: доля 10%+");
say("score | " + T1.map(money).join(" | "));
gridA.forEach(({ s, row }) => say(String(s).padStart(5) + " | " + row.map(r => r.n < MIN_N ? `·(${r.perDay.toFixed(1)})` : `${p1(r.bigRate)} (${r.perDay.toFixed(1)})`).join(" | ")));

// ---------- 2. Пороговые типы ----------
function line(label, r) {
  return [label.padEnd(26), String(r.n).padStart(7), r.perDay.toFixed(1).padStart(7),
          p1(r.pumpRate).padStart(6), p1(r.pumpLB).padStart(6), p1(r.bigRate).padStart(6), p1(r.bigLB).padStart(6),
          p1(r.hit5).padStart(6), p1(r.hit20).padStart(6), p1(r.avgMfe).padStart(6), p1(r.medMfe).padStart(6),
          p1(r.avgMae).padStart(6), p1(r.deepRate).padStart(6), r.pumpsPerDay.toFixed(1).padStart(7),
          r.bigPerDay.toFixed(2).padStart(7), String(r.pairs).padStart(5)].join(" ");
}
const HDR = ["конфигурация".padEnd(26), "N".padStart(7), "сиг/дн".padStart(7), "+3%".padStart(6), "LB95".padStart(6),
             "10%+".padStart(6), "LB95".padStart(6), "+5%".padStart(6), "20%+".padStart(6), "avgMFE".padStart(6),
             "medMFE".padStart(6), "avgMAE".padStart(6), "MAE<-3".padStart(6), "вын/дн".padStart(7), "10%/дн".padStart(7), "пар".padStart(5)].join(" ");

say("\n## B. Типы сигналов и пороги (горизонт 60 мин)");
say(HDR);
const byType = [];
for (const t of T1) byType.push([`VEL1M ${money(t)}`, { types: ["VEL1M"], t1m: t }]);
for (const t of [10000, 20000, 30000, 50000, 75000, 100000, 150000, 250000]) byType.push([`VEL2M ${money(t)}`, { types: ["VEL2M"], t2m: t }]);
for (const t of [10000, 20000, 30000, 50000, 75000, 100000]) byType.push([`ACCEL ${money(t)}`, { types: ["ACCEL"], t1m: t }]);
for (const t of [10000, 30000, 50000]) byType.push([`QUIET при Δ1м ${money(t)}`, { types: ["QUIET"], t1m: t }]);
for (const s of [50, 60, 70, 80]) byType.push([`SCORE ${s} (без Δ)`, { types: ["SIGNAL"], scoreThr: s, t1m: 30000 }]);
byType.push(["SCORE60+VEL1M $30k", { types: ["SIGNAL", "VEL1M"], scoreThr: 60, t1m: 30000 }]);
byType.push(["заводские (SCORE60 $30k/$50k)", { types: ["SIGNAL"], scoreThr: 60, t1m: 30000, t2m: 50000 }]);
const byTypeRes = byType.map(([l, c]) => ({ l, r: btEval(tb, { ...BASE, ...c }) }));
byTypeRes.forEach(({ l, r }) => say(line(l, r)));

// ---------- 3. Срезы по признакам ----------
say("\n## C. Срезы по признакам (население: VEL1M $10k, цена<$10, объём<=$5M)");
const wide = { ...BASE, types: ["VEL1M"], t1m: 10000 };
const bands = {
  score: [[0,30],[30,40],[40,50],[50,60],[60,70],[70,80],[80,101]],
  rvol: [[0,2],[2,4],[4,8],[8,15],[15,30],[30,60],[60,1e9]],
  pct1m: [[-100,0],[0,0.4],[0.4,1],[1,2],[2,4],[4,8],[8,1e9]],
  taker: [[0,0.35],[0.35,0.5],[0.5,0.58],[0.58,0.7],[0.7,0.85],[0.85,1.01]],
  atsK: [[0,1],[1,2],[2,4],[4,8],[8,1e9]],
  d1m: [[5000,10000],[10000,25000],[25000,50000],[50000,100000],[100000,250000],[250000,1e12]],
  vol24: [[0,1e5],[1e5,3e5],[3e5,1e6],[1e6,3e6],[3e6,5e6]],
  price: [[0,0.01],[0.01,0.1],[0.1,1],[1,3],[3,10]],
  rng: [[0,0.35],[0.35,0.7],[0.7,0.9],[0.9,1.01]],
  vwapDev: [[-100,-3],[-3,0],[0,3],[3,10],[10,1e9]],
  quiet: [[0,1],[1,2]],
  newHigh: [[0,1],[1,2]],
  hour: [[0,4],[4,8],[8,12],[12,16],[16,20],[20,24]],
};
const marg = {};
for (const [f, b] of Object.entries(bands)) {
  marg[f] = btMarginal(tb, wide, f, b);
  say(`-- ${f}`);
  marg[f].forEach(a => say(`   [${a.lo}, ${a.hi})`.padEnd(22) + `N=${String(a.n).padStart(8)}  +3%=${p1(a.pumpRate).padStart(5)}  10%+=${p1(a.bigRate).padStart(5)}  avgMFE=${p1(a.avgMfe).padStart(5)}  medMFE=${p1(a.medMfe).padStart(5)}  avgMAE=${p1(a.avgMae).padStart(6)}`));
}
fs.writeFileSync("report_part1.json", JSON.stringify({ meta: tb.meta, gridA: gridA.map(g=>({s:g.s,row:g.row})), byType: byTypeRes, marg }, null, 1));
fs.writeFileSync("report_part1.txt", out.join("\n"));
console.log("\n[part1 saved]");

// ---------- база сравнения: а что даёт вообще любая минута из вселенной ----------
const baseAll = btMarginal(tb, { ...BASE, types: [], t1m: 0 }, "price", [[0, 10]])[0];
const baseline = `BASELINE (все минуты-кандидаты вселенной, без сигнала): N=${baseAll.n} +3%=${p1(baseAll.pumpRate)}% 10%+=${p1(baseAll.bigRate)}% avgMFE=${p1(baseAll.avgMfe)}% medMFE=${p1(baseAll.medMfe)}%`;
console.log("\n" + baseline);
fs.appendFileSync("report_part1.txt", "\n\n" + baseline + "\n");
fs.writeFileSync("baseline.json", JSON.stringify(baseAll));
