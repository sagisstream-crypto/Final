import fs from "fs";
import { loadTbl } from "./loadtbl.mjs";
import { candidates, evalCfg } from "./sweep.mjs";
import { COMPS, PRESETS } from "./engine.mjs";
import { loadKlines, SPAN_START, SPAN_END } from "./loader.mjs";
import { ema, clamp } from "./indicators.mjs";
const MID = Date.parse("2026-03-01T00:00:00Z");
const DA = (SPAN_END-SPAN_START)/864e5, DT = (MID-SPAN_START)/864e5, DE = (SPAN_END-MID)/864e5;
const out=[]; const say=s=>{out.push(s);console.log(s);};
const btc5 = await loadKlines("BTCUSDT","5m");
const be50 = ema(btc5.c,50);
const btcT = btc5.t.map(t=>t+299999);
const btcTr = btc5.c.map((c,i)=>Number.isFinite(be50[i])?clamp((c-be50[i])/c*120,-1,1):0);
const ONES = Object.fromEntries(COMPS.map(k=>[k,1]));

const CFGS = {
  swing: [
    ["заводские (порог 50, B+, обе, ATR×1.5)", ONES, {thresh:50,minGrade:'B',side:'all',atrMult:1.5}],
    ["МАКС winrate: шорты ATR×2.5", ONES, {thresh:50,minGrade:'B',side:'short',atrMult:2.5}],
    ["МАКС winrate + объём>=$10M", ONES, {thresh:50,minGrade:'B',side:'short',atrMult:2.5,minVol:10}],
    ["подобранные веса + шорты ATR×2.5", {...ONES,cvd:0.5,structure:0.5,rsi:0}, {thresh:50,minGrade:'B',side:'short',atrMult:2.5,maxAgainst:1}],
    ["лонги ATR×1.5 (что работало во 2-м полугодии)", ONES, {thresh:40,minGrade:'B',side:'long',atrMult:1.5}],
  ],
  intraday: [
    ["заводские (порог 50, B+, обе, ATR×1.5)", ONES, {thresh:50,minGrade:'B',side:'all',atrMult:1.5}],
    ["МАКС winrate: шорты ATR×2.5 + фильтр BTC", ONES, {thresh:50,minGrade:'B',side:'short',atrMult:2.5,btcFilter:true}],
    ["подобранные веса + шорты ATR×2.5", {...ONES,trend:0,momentum:2,cvd:0,oi:2,volume:1.5,rsBtc:0.5,rsi:0.5,squeeze:0.75,vwap:0.5,basis:0}, {thresh:50,minGrade:'B',side:'short',atrMult:2.5,maxAgainst:1}],
    ["обе стороны, порог 40, ATR×2", ONES, {thresh:40,minGrade:'B',side:'all',atrMult:2,minAgree:6}],
  ],
  scalp: [
    ["заводские (порог 50, B+, обе, ATR×1.5)", ONES, {thresh:50,minGrade:'B',side:'all',atrMult:1.5}],
    ["МАКС winrate: шорты ATR×1, против<=0", ONES, {thresh:50,minGrade:'B',side:'short',atrMult:1,maxAgainst:0}],
    ["подобранные веса + шорты ATR×1", {...ONES,trend:0,momentum:2,cvd:0.75,oi:2,volume:0.5,breakout:0.75,structure:0.75,rsi:0.75,vwap:0.75}, {thresh:50,minGrade:'B',side:'short',atrMult:1,maxAgainst:1}],
    ["УСТОЙЧИВАЯ: обе стороны, порог 60, ATR×1", ONES, {thresh:60,minGrade:'B',side:'all',atrMult:1}],
  ],
};
const HD=["конфигурация".padEnd(46),"сделок".padStart(7),"в день".padStart(7),"winrate".padStart(8),"LB95".padStart(6),
  "PF".padStart(5),"сумма R".padStart(8),"TP2".padStart(6),"стоп".padStart(6),"тайм".padStart(6),
  "чистый".padStart(7),"WR_об".padStart(7),"WR_кон".padStart(7),"PF_кон".padStart(7)].join(" ");
for (const P of ["swing","intraday","scalp"]) {
  const T = loadTbl(P);
  const bt = new Float32Array(T.n);
  { let j=0; const o=Array.from({length:T.n},(_,i)=>i).sort((a,b)=>T.tA[a]-T.tA[b]);
    for(const i of o){while(j+1<btcT.length&&btcT[j+1]<=T.tA[i])j++;bt[i]=btcTr[j];} }
  say(`\n${"=".repeat(146)}\n## ${P.toUpperCase()} (${PRESETS[P].tf}/${PRESETS[P].slow})\n${"=".repeat(146)}`);
  say(HD);
  for (const [label,W,c] of CFGS[P]) {
    const cfg={...c,btcTrend:bt,btcFilter:c.btcFilter||false};
    const Ca=candidates(T,P,W,40), Ct=candidates(T,P,W,40,SPAN_START,MID), Ce=candidates(T,P,W,40,MID,SPAN_END);
    const r=evalCfg(T,Ca,cfg,DA), rt=evalCfg(T,Ct,cfg,DT), re=evalCfg(T,Ce,cfg,DE);
    const clean = (r.tp2+r.stops)? r.tp2/(r.tp2+r.stops)*100 : 0;
    say([label.padEnd(46),String(r.n).padStart(7),r.perDay.toFixed(1).padStart(7),
      (r.winrate.toFixed(1)+"%").padStart(8),r.wr_lb.toFixed(1).padStart(6),
      (r.pf===Infinity?"∞":r.pf.toFixed(2)).padStart(5),
      ((r.sumR>0?"+":"")+r.sumR.toFixed(0)).padStart(8),
      String(r.tp2).padStart(6),String(r.stops).padStart(6),String(r.timeouts).padStart(6),
      (clean.toFixed(1)+"%").padStart(7),
      (rt.winrate.toFixed(1)+"%").padStart(7),(re.winrate.toFixed(1)+"%").padStart(7),
      re.pf.toFixed(2).padStart(7)].join(" "));
  }
}
fs.writeFileSync("report_FINAL.txt",out.join("\n"));
console.log("\n[сохранено report_FINAL.txt]");
