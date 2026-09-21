import fs from "fs";
const daily = JSON.parse(fs.readFileSync("daily.json", "utf8"));
const DAYS = [];
for (let d = new Date("2026-03-01"); d < new Date("2026-09-01"); d.setUTCDate(d.getUTCDate() + 1))
  DAYS.push(d.toISOString().slice(0, 10));
const prevOf = d => { const x = new Date(d); x.setUTCDate(x.getUTCDate() - 1); return x.toISOString().slice(0, 10); };

function count(maxP, maxV) {
  const per = {};
  let totalSymDays = 0;
  for (const [sym, m] of Object.entries(daily)) {
    let n = 0;
    for (const day of DAYS) {
      const p = m[prevOf(day)];
      if (!p || !(p.c > 0) || !(p.qv > 0)) continue;
      if (p.c < maxP && p.qv <= maxV) n++;
    }
    if (n) { per[sym] = n; totalSymDays += n; }
  }
  return { syms: Object.keys(per).length, symDays: totalSymDays, per };
}
for (const [p, v] of [[10, 5e6], [12, 15e6], [12, 30e6]]) {
  const r = count(p, v);
  console.log(`price<$${p} vol<=$${(v/1e6)}M -> symbols=${r.syms} symbol-days=${r.symDays}`);
}
const strict = count(10, 5e6);
const dl = count(12, 20e6);
fs.writeFileSync("universe.json", JSON.stringify({ days: DAYS, strict: strict.per, download: dl.per }));
console.log("download-set symbols:", Object.keys(dl.per).length);
