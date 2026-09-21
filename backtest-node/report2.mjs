import fs from "fs";
import { btEval, btOptimize, btWilson, btNewTable, BT_COLS } from "./engine.mjs";
import { loadTable } from "./loadtable.mjs";

const tb = loadTable();
const D = tb.days.length;
const money = v => v >= 1e6 ? "$" + (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M"
               : v >= 1000 ? "$" + Math.round(v / 1000) + "k" : "$" + Math.round(v);
const p1 = v => (v === null || isNaN(v)) ? "—" : v.toFixed(1);
const out = []; const say = s => { out.push(s); console.log(s); };

// подтаблица по диапазону дней — для честной проверки «обучение / контроль»
function sliceDays(src, lo, hi) {
  const keep = [];
  for (let i = 0; i < src.n; i++) { const d = src.col.dayIdx[i]; if (d >= lo && d < hi) keep.push(i); }
  const t = btNewTable();
  t.n = keep.length; t.cap = keep.length;
  t.syms = src.syms.slice(); t.syms.forEach((s, i) => t.symIdx.set(s, i));
  t.days = src.days.slice(lo, hi); t.meta = { ...src.meta };
  for (const c of BT_COLS) {
    const a = new Float64Array(keep.length);
    for (let j = 0; j < keep.length; j++) a[j] = src.col[c][keep[j]];
    t.col[c] = a;
  }
  // dayIdx перенумеровать, иначе слоты кулдауна уедут
  for (let j = 0; j < keep.length; j++) t.col.dayIdx[j] -= lo;
  return t;
}
const HALF = Math.floor(D / 2);
console.log("building train/test halves…");
const trainTb = sliceDays(tb, 0, HALF);          // 2026-03-01 .. ~2026-05-31
const testTb  = sliceDays(tb, HALF, D);          // ~2026-06-01 .. 2026-08-31
console.log(`train n=${trainTb.n} days=${trainTb.days.length}  test n=${testTb.n} days=${testTb.days.length}`);

const BASE = { horizon: 60, pump: 3, big: 10, maxPrice: 10, maxVol: 5e6, minVol: 0 };
const SPEC = {
  scoreThr: [40, 50, 55, 60, 65, 70, 75, 80, 85],
  t1m: [5000, 10000, 15000, 20000, 30000, 40000, 50000, 75000, 100000, 150000],
  t2m: [10000, 20000, 30000, 50000, 75000, 100000, 150000, 250000],
  minD1m: [null, 5000, 10000, 20000, 30000, 50000, 75000, 100000],
  minRvol: [null, 2, 4, 6, 10, 15, 25, 40],
  minPct1m: [null, 0.2, 0.5, 1, 1.5, 2, 3],
  minTaker: [null, 0.5, 0.58, 0.65, 0.7, 0.75],
  minAtsK: [null, 1.5, 2, 3, 4],
  minVwapDev: [null, 0, 3, 10],
  minRng: [null, 0.35, 0.7, 0.9],
  maxVol: [5e6, 3e6, 1.5e6, 7.5e5, 3e5],
  maxPrice: [10, 3, 1, 0.1],
  requireQuiet: [false, true],
  requireNewHigh: [false, true],
};

function hdr() {
  return ["конфигурация".padEnd(30), "N".padStart(7), "сиг/дн".padStart(7), "+3%".padStart(6), "LB95".padStart(6),
          "10%+".padStart(6), "LB95".padStart(6), "avgMFE".padStart(7), "medMFE".padStart(7), "avgMAE".padStart(7),
          "вын/дн".padStart(7), "10%/дн".padStart(7), "пар".padStart(5)].join(" ");
}
function line(label, r) {
  return [label.padEnd(30), String(r.n).padStart(7), r.perDay.toFixed(1).padStart(7), p1(r.pumpRate).padStart(6),
          p1(r.pumpLB).padStart(6), p1(r.bigRate).padStart(6), p1(r.bigLB).padStart(6), p1(r.avgMfe).padStart(7),
          p1(r.medMfe).padStart(7), p1(r.avgMae).padStart(7), r.pumpsPerDay.toFixed(2).padStart(7),
          r.bigPerDay.toFixed(2).padStart(7), String(r.pairs).padStart(5)].join(" ");
}
function describe(c) {
  const b = [];
  b.push(`типы=${c.types.join("+")}`);
  if (c.types.includes("SIGNAL")) b.push(`score>=${c.scoreThr}`);
  if (c.types.includes("VEL1M") || c.types.includes("ACCEL") || c.types.includes("QUIET")) b.push(`Δ1м>=${money(c.t1m)}`);
  if (c.types.includes("VEL2M")) b.push(`Δ2м>=${money(c.t2m)}`);
  if (c.minD1m) b.push(`minΔ1м=${money(c.minD1m)}`);
  if (c.minRvol) b.push(`RVOL>=${c.minRvol}`);
  if (c.minPct1m) b.push(`Δ%1м>=${c.minPct1m}`);
  if (c.minTaker) b.push(`тейкер>=${c.minTaker}`);
  if (c.minAtsK) b.push(`чек×>=${c.minAtsK}`);
  if (c.minVwapDev !== null && c.minVwapDev !== undefined) b.push(`VWAP>=${c.minVwapDev}%`);
  if (c.minRng) b.push(`rng>=${c.minRng}`);
  if (c.requireQuiet) b.push("из тихой полки");
  if (c.requireNewHigh) b.push("новый хай 24ч");
  b.push(`цена<$${c.maxPrice}`); b.push(`об<=${money(c.maxVol)}`);
  return b.join(", ");
}

say(`\n## D. Поиск идеальных порогов (обучение = первые ${trainTb.days.length} дней, контроль = последние ${testTb.days.length})`);
const runs = [];
for (const [name, objective, minPerDay, minN, seed] of [
  ["макс. доля выносов +3%", "pump", 2, 400, { types: ["SIGNAL"], scoreThr: 60, t1m: 30000 }],
  ["макс. доля выносов, поток >=8/дн", "pump", 8, 800, { types: ["SIGNAL"], scoreThr: 60, t1m: 30000 }],
  ["макс. доля 10%+", "big", 2, 400, { types: ["SIGNAL"], scoreThr: 70, t1m: 30000 }],
  ["макс. доля 10%+, поток >=5/дн", "big", 5, 600, { types: ["SIGNAL"], scoreThr: 70, t1m: 30000 }],
  ["макс. доля выносов (VEL1M)", "pump", 2, 400, { types: ["VEL1M"], t1m: 30000 }],
  ["макс. доля 10%+ (VEL1M)", "big", 2, 400, { types: ["VEL1M"], t1m: 30000 }],
]) {
  console.log(`optimizing: ${name} …`);
  const o = btOptimize(trainTb, { ...BASE, ...seed }, SPEC, objective, minPerDay, minN);
  const onTest = btEval(testTb, o.cfg);
  const onAll = btEval(tb, o.cfg);
  runs.push({ name, objective, cfg: o.cfg, train: o.best, test: onTest, all: onAll, tried: o.tried });
  say(`\n### ${name}   (перебрано ${o.tried} конфигураций)`);
  say(`    ${describe(o.cfg)}`);
  say(hdr());
  say(line("обучение (1-я половина)", o.best));
  say(line("КОНТРОЛЬ (2-я половина)", onTest));
  say(line("все 6 месяцев", onAll));
}

// ---------- сравнение кандидатов «руками» ----------
say("\n## E. Итоговые кандидаты против заводских, все 6 месяцев");
say(hdr());
const cands = [
  ["заводские: SCORE60, Δ1м $30k", { types: ["SIGNAL"], scoreThr: 60, t1m: 30000, t2m: 50000 }],
  ["SCORE70 + Δ1м $30k", { types: ["SIGNAL"], scoreThr: 70, t1m: 30000, minD1m: 30000 }],
  ["SCORE75 + Δ1м $50k", { types: ["SIGNAL"], scoreThr: 75, t1m: 50000, minD1m: 50000 }],
  ["SCORE70 + RVOL10 + тейкер .65", { types: ["SIGNAL"], scoreThr: 70, t1m: 30000, minD1m: 30000, minRvol: 10, minTaker: 0.65 }],
  ["SCORE70 + RVOL15 + Δ%1м>=1", { types: ["SIGNAL"], scoreThr: 70, t1m: 30000, minD1m: 30000, minRvol: 15, minPct1m: 1 }],
  ["SCORE70+RVOL15+тейк.65+Δ%>=1", { types: ["SIGNAL"], scoreThr: 70, t1m: 30000, minD1m: 30000, minRvol: 15, minTaker: 0.65, minPct1m: 1 }],
  ["то же + объём<=$1.5M", { types: ["SIGNAL"], scoreThr: 70, t1m: 30000, minD1m: 30000, minRvol: 15, minTaker: 0.65, minPct1m: 1, maxVol: 1.5e6 }],
  ["то же + объём<=$750k", { types: ["SIGNAL"], scoreThr: 70, t1m: 30000, minD1m: 30000, minRvol: 15, minTaker: 0.65, minPct1m: 1, maxVol: 7.5e5 }],
];
const candRes = cands.map(([l, c]) => ({ l, c: { ...BASE, ...c }, r: btEval(tb, { ...BASE, ...c }) }));
candRes.forEach(({ l, r }) => say(line(l, r)));

fs.writeFileSync("report_part2.json", JSON.stringify({ runs, cands: candRes }, null, 1));
fs.writeFileSync("report_part2.txt", out.join("\n"));
console.log("\n[part2 saved]");
