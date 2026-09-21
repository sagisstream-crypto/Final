import fs from "fs";
import { btEval, btNewTable, BT_COLS } from "./engine.mjs";
import { loadTable } from "./loadtable.mjs";

const tb = loadTable();
const D = tb.days.length;
const money = v => v >= 1e6 ? "$" + (v / 1e6).toFixed(v >= 1e7 ? 0 : 1) + "M"
               : v >= 1000 ? "$" + Math.round(v / 1000) + "k" : "$" + Math.round(v);
const p1 = v => (v === null || isNaN(v)) ? "—" : v.toFixed(1);
const out = []; const say = s => { out.push(s); console.log(s); };

// Подтаблица по предикату. Фильтры в btEval применяются ДО кулдауна, поэтому
// отсев строк заранее даёт ровно тот же результат, что и доп. условие внутри btEval.
// Так добавляем потолки (maxTaker/maxAtsK), которых в конфиге движка нет, не трогая движок.
function subset(src, pred, dayLo = 0, dayHi = 1e9) {
  const keep = [];
  for (let i = 0; i < src.n; i++) {
    const d = src.col.dayIdx[i];
    if (d < dayLo || d >= dayHi) continue;
    if (pred(src.col, i)) keep.push(i);
  }
  const t = btNewTable();
  t.n = keep.length; t.cap = keep.length;
  t.syms = src.syms.slice(); t.syms.forEach((s, i) => t.symIdx.set(s, i));
  t.days = src.days.slice(dayLo === 0 && dayHi > src.days.length ? 0 : dayLo, Math.min(dayHi, src.days.length));
  t.meta = { ...src.meta };
  for (const c of BT_COLS) {
    const a = new Float64Array(keep.length);
    for (let j = 0; j < keep.length; j++) a[j] = src.col[c][keep[j]];
    t.col[c] = a;
  }
  if (dayLo) for (let j = 0; j < keep.length; j++) t.col.dayIdx[j] -= dayLo;
  return t;
}
const ALL = (c, i) => true;
const BASE = { horizon: 60, pump: 3, big: 10, maxPrice: 10, maxVol: 5e6, minVol: 0 };
const HDR = ["конфигурация".padEnd(42), "N".padStart(7), "сиг/дн".padStart(7), "+3%".padStart(6), "LB95".padStart(6),
             "10%+".padStart(6), "LB95".padStart(6), "avgMFE".padStart(7), "medMFE".padStart(7), "avgMAE".padStart(7),
             "вын/дн".padStart(7), "10%/дн".padStart(7), "пар".padStart(5)].join(" ");
const line = (l, r) => [l.padEnd(42), String(r.n).padStart(7), r.perDay.toFixed(1).padStart(7), p1(r.pumpRate).padStart(6),
    p1(r.pumpLB).padStart(6), p1(r.bigRate).padStart(6), p1(r.bigLB).padStart(6), p1(r.avgMfe).padStart(7),
    p1(r.medMfe).padStart(7), p1(r.avgMae).padStart(7), r.pumpsPerDay.toFixed(2).padStart(7),
    r.bigPerDay.toFixed(2).padStart(7), String(r.pairs).padStart(5)].join(" ");

// ---------- F. проверка «обратных» компонентов score ----------
say("\n## F. Доп. фильтры-потолки, которых нет в шапке (база: SCORE70 + Δ1м $30k)");
say(HDR);
const seed = { ...BASE, types: ["SIGNAL"], scoreThr: 70, t1m: 30000, minD1m: 30000 };
const variants = [
  ["без доп. фильтров", ALL],
  ["тейкер <= 0.85", (c,i) => c.taker[i] <= 0.85],
  ["тейкер <= 0.70", (c,i) => c.taker[i] <= 0.70],
  ["тейкер 0.35..0.70", (c,i) => c.taker[i] >= 0.35 && c.taker[i] <= 0.70],
  ["чек atsK <= 2", (c,i) => c.atsK[i] <= 2],
  ["чек atsK <= 1.5", (c,i) => c.atsK[i] <= 1.5],
  ["чек atsK <= 1", (c,i) => c.atsK[i] <= 1],
  ["тейкер 0.35..0.70 + чек<=2", (c,i) => c.taker[i] >= 0.35 && c.taker[i] <= 0.70 && c.atsK[i] <= 2],
  ["тейкер 0.35..0.70 + чек<=1.5", (c,i) => c.taker[i] >= 0.35 && c.taker[i] <= 0.70 && c.atsK[i] <= 1.5],
];
const fRes = [];
for (const [l, p] of variants) {
  const t = p === ALL ? tb : subset(tb, p);
  const r = btEval(t, seed);
  fRes.push({ l, r }); say(line(l, r));
}

// ---------- G. лестница финального кандидата ----------
say("\n## G. Наращивание фильтров: что каждый шаг даёт (все 6 месяцев)");
say(HDR);
const ladder = [
  ["заводские SCORE60 Δ1м$30k", ALL, { types:["SIGNAL"], scoreThr:60, t1m:30000 }],
  ["+ minΔ1м $30k", ALL, { types:["SIGNAL"], scoreThr:60, t1m:30000, minD1m:30000 }],
  ["score 70", ALL, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000 }],
  ["+ Δ%1м >= 1", ALL, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000, minPct1m:1 }],
  ["+ VWAP >= +3%", ALL, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000, minPct1m:1, minVwapDev:3 }],
  ["+ новый хай 24ч", ALL, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000, minPct1m:1, minVwapDev:3, requireNewHigh:true }],
  ["+ тейкер<=0.70, чек<=2", (c,i)=>c.taker[i]>=0.35&&c.taker[i]<=0.70&&c.atsK[i]<=2, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000, minPct1m:1, minVwapDev:3, requireNewHigh:true }],
  ["+ RVOL >= 15", (c,i)=>c.taker[i]>=0.35&&c.taker[i]<=0.70&&c.atsK[i]<=2, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000, minPct1m:1, minVwapDev:3, requireNewHigh:true, minRvol:15 }],
  ["+ объём 24ч >= $1M", (c,i)=>c.taker[i]>=0.35&&c.taker[i]<=0.70&&c.atsK[i]<=2, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000, minPct1m:1, minVwapDev:3, requireNewHigh:true, minRvol:15, minVol:1e6 }],
  ["+ цена < $1", (c,i)=>c.taker[i]>=0.35&&c.taker[i]<=0.70&&c.atsK[i]<=2, { types:["SIGNAL"], scoreThr:70, t1m:30000, minD1m:30000, minPct1m:1, minVwapDev:3, requireNewHigh:true, minRvol:15, minVol:1e6, maxPrice:1 }],
];
const gRes = [];
const subCache = new Map();
function sub(p, lo, hi) {
  const key = String(p) + "|" + lo + "|" + hi;
  if (!subCache.has(key)) subCache.set(key, p === ALL && lo === 0 && hi > 1e8 ? tb : subset(tb, p, lo, hi));
  return subCache.get(key);
}
for (const [l, p, c] of ladder) {
  const r = btEval(sub(p, 0, 1e9), { ...BASE, ...c });
  gRes.push({ l, cfg: { ...BASE, ...c }, r }); say(line(l, r));
}

// ---------- H. контроль на второй половине ----------
const HALF = Math.floor(D / 2);
say(`\n## H. Обучение (дни 0..${HALF-1}) против контроля (дни ${HALF}..${D-1}) для лестницы`);
say(["конфигурация".padEnd(42), "N_об".padStart(7), "+3%об".padStart(6), "10%об".padStart(6),
     "N_кон".padStart(7), "+3%кон".padStart(7), "10%кон".padStart(7), "сиг/дн_кон".padStart(11)].join(" "));
const hRes = [];
for (const [l, p, c] of ladder) {
  const tr = btEval(sub(p, 0, HALF), { ...BASE, ...c });
  const te = btEval(sub(p, HALF, 1e9), { ...BASE, ...c });
  hRes.push({ l, tr, te });
  say([l.padEnd(42), String(tr.n).padStart(7), p1(tr.pumpRate).padStart(6), p1(tr.bigRate).padStart(6),
       String(te.n).padStart(7), p1(te.pumpRate).padStart(7), p1(te.bigRate).padStart(7), te.perDay.toFixed(2).padStart(11)].join(" "));
}
fs.writeFileSync("report_part3.json", JSON.stringify({ fRes, gRes, hRes }, null, 1));
fs.writeFileSync("report_part3.txt", out.join("\n"));
console.log("\n[part3 saved]");
