// Сверка JS-ядра (vynos-core.js) с Python-бэктестом: node research/parity.js parity.json
const V = require("../vynos-core.js");
const data = require(require("path").resolve(process.argv[2]));
let sigOk = 0, sigBad = 0, trOk = 0, trBad = 0;
for (const [name, d] of Object.entries(data)) {
  const bars = d.bars.map(([ot, o, h, l, c]) => ({ ot, o, h, l, c }));
  const first = bars[0].ot, js = [];
  for (let k = 0; k < bars.length; k++) if (V.signalAt(bars, k, first)) js.push(k);
  const same = JSON.stringify(js) === JSON.stringify(d.sig);
  same ? sigOk++ : (sigBad++, console.log("SIG MISMATCH", name, js.length, d.sig.length));
  const t = V.backtest(bars, first);
  const py = d.trades.filter(([i]) => i + V.P.HOLD < bars.length); // Python дописывает незакрытые на краю
  const ok = t.length === py.length && t.every((x, n) => bars.findIndex(b => b.ot === x.ot) === py[n][0] && Math.abs(x.ret - py[n][1]) < 1e-6);
  ok ? trOk++ : (trBad++, console.log("TRADE MISMATCH", name, t.length, py.length));
}
console.log(`signals: ${sigOk} ok / ${sigBad} bad; trades: ${trOk} ok / ${trBad} bad`);
process.exit(sigBad + trBad ? 1 : 0);
