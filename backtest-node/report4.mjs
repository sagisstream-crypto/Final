import fs from "fs";
import { btEval, btNewTable, BT_COLS } from "./engine.mjs";
import { loadTable } from "./loadtable.mjs";
const tb = loadTable();
const money = v => v >= 1e6 ? "$" + (v/1e6).toFixed(v>=1e7?0:1) + "M" : v >= 1000 ? "$" + Math.round(v/1000) + "k" : "$" + Math.round(v);
const p1 = v => (v===null||isNaN(v)) ? "—" : v.toFixed(1);
const out=[]; const say=s=>{out.push(s);console.log(s);};
function subset(src, pred) {
  const keep=[]; for (let i=0;i<src.n;i++) if (pred(src.col,i)) keep.push(i);
  const t=btNewTable(); t.n=keep.length; t.cap=keep.length;
  t.syms=src.syms.slice(); t.syms.forEach((s,i)=>t.symIdx.set(s,i));
  t.days=src.days.slice(); t.meta={...src.meta};
  for (const c of BT_COLS){const a=new Float64Array(keep.length);for(let j=0;j<keep.length;j++)a[j]=src.col[c][keep[j]];t.col[c]=a;}
  return t;
}
const CEIL = (c,i) => c.taker[i] <= 0.70 && c.atsK[i] <= 2;
const sub = subset(tb, CEIL);
const BASE={horizon:60,pump:3,big:10,maxPrice:10,maxVol:5e6,minVol:0};
const CORE={types:["SIGNAL"],t1m:30000,minD1m:30000,minPct1m:1,minVwapDev:3,requireNewHigh:true};
const HDR=["вариант".padEnd(34),"N".padStart(7),"сиг/дн".padStart(7),"+3%".padStart(6),"LB95".padStart(6),"10%+".padStart(6),"LB95".padStart(6),"avgMFE".padStart(7),"avgMAE".padStart(7),"вын/дн".padStart(7),"10%/дн".padStart(7)].join(" ");
const line=(l,r)=>[l.padEnd(34),String(r.n).padStart(7),r.perDay.toFixed(1).padStart(7),p1(r.pumpRate).padStart(6),p1(r.pumpLB).padStart(6),p1(r.bigRate).padStart(6),p1(r.bigLB).padStart(6),p1(r.avgMfe).padStart(7),p1(r.avgMae).padStart(7),r.pumpsPerDay.toFixed(2).padStart(7),r.bigPerDay.toFixed(2).padStart(7)].join(" ");

say("## I. Чувствительность к score внутри финальной связки");
say(HDR);
const I=[];
for (const s of [50,55,60,65,70,75,80,85]) { const r=btEval(sub,{...BASE,...CORE,scoreThr:s}); I.push({s,r}); say(line(`score >= ${s}`,r)); }
say("\n## J. Чувствительность к Δ1м (score 70)");
say(HDR);
const J=[];
for (const t of [10000,20000,30000,50000,75000,100000,150000]) { const r=btEval(sub,{...BASE,...CORE,scoreThr:70,t1m:t,minD1m:t}); J.push({t,r}); say(line(`Δ1м >= ${money(t)}`,r)); }
say("\n## K. Чувствительность к Δ%1м (score 70, Δ1м $30k)");
say(HDR);
const K=[];
for (const p of [0,0.5,1,1.5,2,3]) { const r=btEval(sub,{...BASE,...CORE,scoreThr:70,minPct1m:p||null}); K.push({p,r}); say(line(`Δ%1м >= ${p}%`,r)); }
say("\n## L. Чувствительность к макс. объёму 24ч (score 70)");
say(HDR);
const L=[];
for (const v of [5e6,3e6,1.5e6,7.5e5]) { const r=btEval(sub,{...BASE,...CORE,scoreThr:70,maxVol:v}); L.push({v,r}); say(line(`объём <= ${money(v)}`,r)); }
say("\n## M. Чувствительность к макс. цене (score 70)");
say(HDR);
const M=[];
for (const v of [10,3,1,0.1]) { const r=btEval(sub,{...BASE,...CORE,scoreThr:70,maxPrice:v}); M.push({v,r}); say(line(`цена < $${v}`,r)); }
say("\n## N. Горизонты 15 / 60 / 120 мин для финальной связки (score 70)");
say(HDR);
const N=[];
for (const h of [15,60,120]) { const r=btEval(sub,{...BASE,...CORE,scoreThr:70,horizon:h}); N.push({h,r}); say(line(`горизонт ${h} мин`,r)); }
fs.writeFileSync("report_part4.json",JSON.stringify({I,J,K,L,M,N},null,1));
fs.writeFileSync("report_part4.txt",out.join("\n"));
console.log("\n[part4 saved]");
