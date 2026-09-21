// Чистая статистика бэктеста — без сети, поэтому полностью покрывается тестами
// на синтетических данных (test/backtestStats.test.js).

export function pearsonCorrelation(xs, ys) {
  const pairs = [];
  for (let i = 0; i < xs.length; i++) {
    const x = xs[i], y = ys[i];
    if (x === null || x === undefined || Number.isNaN(x)) continue;
    if (y === null || y === undefined || Number.isNaN(y)) continue;
    pairs.push([x, y]);
  }
  if (pairs.length < 3) return null; // меньше 3 точек — коэффициент не значит ничего
  const n = pairs.length;
  const mx = pairs.reduce((a, [x]) => a + x, 0) / n;
  const my = pairs.reduce((a, [, y]) => a + y, 0) / n;
  let cov = 0, vx = 0, vy = 0;
  for (const [x, y] of pairs) {
    cov += (x - mx) * (y - my);
    vx += (x - mx) ** 2;
    vy += (y - my) ** 2;
  }
  if (vx === 0 || vy === 0) return null; // константа — корреляция не определена
  return cov / Math.sqrt(vx * vy);
}

function summarizeReturns(values) {
  const clean = values.filter((v) => v !== null && v !== undefined && !Number.isNaN(v));
  if (!clean.length) return { count: 0, winRatePct: null, avgReturnPct: null, medianReturnPct: null };
  const sorted = [...clean].sort((a, b) => a - b);
  const mid = Math.floor(sorted.length / 2);
  const median = sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  return {
    count: clean.length,
    winRatePct: (clean.filter((v) => v > 0).length / clean.length) * 100,
    avgReturnPct: clean.reduce((a, b) => a + b, 0) / clean.length,
    medianReturnPct: median,
  };
}

// Делит samples на "сигнал" (predicate(sample) === true) и "нет сигнала" и
// сравнивает forward-доходность между группами на каждом горизонте — это и
// есть ответ на вопрос "работает ли это лучше, чем ничего не делать".
// samples[i].forwardReturnPct = { "7": number|null, "14": number|null, ... }
export function compareBySignal(samples, predicate, forwardHorizons) {
  const signal = samples.filter(predicate);
  const noSignal = samples.filter((s) => !predicate(s));
  const out = {};
  for (const h of forwardHorizons) {
    out[h] = {
      signal: summarizeReturns(signal.map((s) => s.forwardReturnPct[h])),
      noSignal: summarizeReturns(noSignal.map((s) => s.forwardReturnPct[h])),
    };
  }
  return out;
}
