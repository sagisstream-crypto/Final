// Вынос-детектор: ядро сигнала. Один и тот же код работает в браузере (vynos.html)
// и в Node (research/parity.js сверяет его с Python-бэктестом свеча в свечу).
//
// Сигнал (5m, только LONG), проверен на 155 альт-парах Binance spot + USDT-M:
//   бар i   (вынос):        low[i] < min(low[i-48..i-1])  — снесён 4-часовой минимум,
//                           close[i] > этого минимума      — закрылись обратно выше,
//                           (close[i]/close[i-12]-1)/ATR% > -2 — перед выносом не было обвала.
//   бар i+1 (подтверждение): отскок w = close[i+1]/low[i] - 1 в диапазоне [1.5%, 10%),
//                           час открытия бара i+1 по UTC >= 12.
//   вход — открытие бара i+2, TP = +0.5·w, SL = −2·w, выход по времени через 48 баров (4ч).
//   у пары должно быть >= 7 дней истории (свежие листинги отсекаются).
(function (root) {
  const P = {
    N: 48,            // окно минимума, баров (48 × 5m = 4ч)
    ATR_N: 48,
    RET12_MIN: -2.0,  // «не было обвала» в ATR
    W_LO: 0.015, W_HI: 0.10,
    H_FROM: 12,       // UTC-час бара подтверждения
    TPK: 0.5, SLK: 2.0,
    HOLD: 48,
    MIN_AGE_MS: 7 * 24 * 3600 * 1000,
    FEE: 0.001,
  };
  const NEED = P.N + P.ATR_N + 3; // баров истории, нужных для одной проверки

  // bars: [{ot,o,h,l,c}], только ЗАКРЫТЫЕ свечи, по возрастанию времени.
  // Проверяет, дал ли сигнал бар подтверждения k (сигнал на его закрытии).
  function signalAt(bars, k, firstBarMs) {
    const i = k - 1; // бар выноса
    if (i < P.N + P.ATR_N + 1 || k >= bars.length) return null;
    const b = bars[i], cb = bars[k];
    if (firstBarMs != null && cb.ot - firstBarMs < P.MIN_AGE_MS) return null;
    if (new Date(cb.ot).getUTCHours() < P.H_FROM) return null;
    let lowN = Infinity;
    for (let j = i - P.N; j < i; j++) lowN = Math.min(lowN, bars[j].l);
    if (!(b.l < lowN && b.c > lowN)) return null;
    let tr = 0; // ATR по 48 барам ДО бара выноса
    for (let j = i - P.ATR_N; j < i; j++) {
      const x = bars[j], pc = bars[j - 1].c;
      tr += Math.max(x.h - x.l, Math.abs(x.h - pc), Math.abs(x.l - pc));
    }
    const atrp = tr / P.ATR_N / b.c;
    if (!(atrp > 0)) return null;
    const ret12 = (b.c / bars[i - 12].c - 1) / atrp;
    if (!(ret12 > P.RET12_MIN)) return null;
    const w = cb.c / b.l - 1;
    if (!(w >= P.W_LO && w < P.W_HI)) return null;
    return {
      sweepOt: b.ot, confirmOt: cb.ot, sweepLow: b.l, lowN, w,
      tpPct: P.TPK * w, slPct: P.SLK * w, ret12, atrp,
    };
  }

  // Прогон сделки от бара входа e (entry = open[e]); как в research/lib.py:
  // оба уровня в одной свече = стоп (консервативно), таймаут = close через HOLD баров.
  function runTrade(bars, e, tpPct, slPct) {
    const entry = bars[e].o, tp = entry * (1 + tpPct), sl = entry * (1 - slPct);
    const end = Math.min(bars.length - 1, e - 1 + P.HOLD);
    for (let j = e; j <= end; j++) {
      if (bars[j].l <= sl) return { exitIdx: j, ret: -slPct - P.FEE, result: "SL" };
      if (bars[j].h >= tp) return { exitIdx: j, ret: tpPct - P.FEE, result: "TP" };
    }
    if (e - 1 + P.HOLD > bars.length - 1) return null; // ещё открыта
    return { exitIdx: end, ret: bars[end].c / entry - 1 - P.FEE, result: "TIME" };
  }

  // Бэктест на загруженной истории: сделки не перекрываются в рамках одной пары.
  function backtest(bars, firstBarMs) {
    const trades = []; let busy = -1;
    for (let k = 0; k + 1 < bars.length; k++) {
      if (k <= busy) continue;
      const s = signalAt(bars, k, firstBarMs);
      if (!s) continue;
      const r = runTrade(bars, k + 1, s.tpPct, s.slPct);
      if (!r) break;
      trades.push(Object.assign({ ot: bars[k].ot, entry: bars[k + 1].o }, s, r));
      busy = r.exitIdx;
    }
    return trades;
  }

  const api = { P, NEED, signalAt, runTrade, backtest };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.VynosCore = api;
})(typeof self !== "undefined" ? self : this);
