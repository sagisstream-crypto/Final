// Симуляция сделки по правилам openJournal()/updateJournal() из ALTSIG.
// Исход: стоп -> -1R, TP2 -> +2R, иначе через 24ч от входа -> текущий R.
// Лимит, не исполнившийся за 90 минут -> cancelled: в winrate не идёт,
// но пару всё это время блокирует (в приложении запись висит с open=true).
// blockMin — сколько минут от сигнала пара занята.
export function simulate(k5, i5, side, entry, stop, tp2, entryType, R) {
  const dir = side === 'long' ? 1 : -1;
  const n = k5.c.length;
  const createdT = k5.t[i5] + 299999;
  let j = i5 + 1;
  let pending = entryType === 'limit';
  let entryT = createdT;
  if (pending) {
    for (; j < n; j++) {
      const hit = dir === 1 ? k5.l[j] <= entry : k5.h[j] >= entry;
      if (hit) { pending = false; entryT = k5.t[j] + 299999; j++; break; }
      if (k5.t[j] - createdT > 90 * 60000) return { cancelled: true, blockMin: 90 };
    }
    if (pending) return { cancelled: true, blockMin: 90 };
  }
  let mfe = 0, mae = 0;
  for (; j < n; j++) {
    const hi = k5.h[j], lo = k5.l[j];
    const rHi = ((hi - entry) / R) * dir, rLo = ((lo - entry) / R) * dir;
    if (Math.max(rHi, rLo) > mfe) mfe = Math.max(rHi, rLo);
    if (Math.min(rHi, rLo) < mae) mae = Math.min(rHi, rLo);
    const hitStop = dir === 1 ? lo <= stop : hi >= stop;
    const hitTp2  = dir === 1 ? hi >= tp2  : lo <= tp2;
    const closeT = k5.t[j] + 299999;
    // в приложении стоп проверяется первым: если бар задел обе границы, берём стоп
    if (hitStop) return { result: -1, mfe, mae, blockMin: (closeT - createdT) / 60000 };
    if (hitTp2)  return { result: 2,  mfe, mae, blockMin: (closeT - createdT) / 60000 };
    if (k5.t[j] - entryT > 24 * 3600e3) {
      const r = ((k5.c[j] - entry) / R) * dir;
      return { result: +r.toFixed(2), mfe, mae, blockMin: (closeT - createdT) / 60000 };
    }
  }
  return { truncated: true };
}
export function buildPlan(bar, side, aMult) {
  const { price, atrV, e21, vw, lo12, hi12, extATR } = bar;
  const anchor = side === 'long' ? Math.max(e21, vw) : Math.min(e21, vw);
  let entryType = 'market', entry = price;
  if (extATR > 1.3) {
    if (side === 'long' && anchor < price * 0.999) { entryType = 'limit'; entry = anchor; }
    else if (side === 'short' && anchor > price * 1.001) { entryType = 'limit'; entry = anchor; }
  }
  let stop;
  if (side === 'long') {
    stop = Math.min(lo12 - atrV * 0.15, entry - atrV * aMult);
    stop = Math.max(stop, entry - atrV * 4);
    stop = Math.min(stop, entry - atrV * 0.35);
  } else {
    stop = Math.max(hi12 + atrV * 0.15, entry + atrV * aMult);
    stop = Math.min(stop, entry + atrV * 4);
    stop = Math.max(stop, entry + atrV * 0.35);
  }
  const R = Math.abs(entry - stop);
  const tp2 = side === 'long' ? entry + 2 * R : entry - 2 * R;
  return { entry, stop, tp2, R, entryType, slPct: entry ? R / entry * 100 : 0 };
}
