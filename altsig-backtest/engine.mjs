// Порт analyze() из index-6.html на исторические ряды.
// Формулы компонентов скопированы построчно; отличается только то, что
// значения берутся по индексу бара, а не с хвоста массива.
import { ema, rma, rsi, atr, macd, bbandsWidth, fundingScore, rsiScore,
         slopePctAt, clamp, chg, mean, stdev } from "./indicators.mjs";

export const WEIGHT_DEFS = [
  ['trend',14],['momentum',12],['cvd',14],['oi',13],['volume',12],['breakout',12],
  ['structure',10],['book',8],['rsBtc',8],['rsi',8],['squeeze',7],['funding',7],
  ['vwap',6],['liq',6],['basis',5],
];
export const DEFAULT_W = Object.fromEntries(WEIGHT_DEFS);
export const PRESETS = {
  scalp:    { tf:'5m',  slow:'15m', mainMin:5,  slowMin:15, atrMult:1.2,
              w:{momentum:1.35, volume:1.3, book:1.3, cvd:1.2, trend:.75, structure:.8} },
  intraday: { tf:'15m', slow:'1h',  mainMin:15, slowMin:60, atrMult:1.5, w:{} },
  swing:    { tf:'1h',  slow:'4h',  mainMin:60, slowMin:240, atrMult:2.0,
              w:{trend:1.35, structure:1.3, oi:1.15, momentum:.7, book:.5, volume:.85} },
};
// компоненты, восстановимые из архивов (book и liq недоступны — см. README)
export const COMPS = ['trend','momentum','cvd','oi','volume','breakout','structure',
                      'rsBtc','rsi','squeeze','funding','vwap','basis'];

// ---- скользящие помощники ----
function rollMax(a, win, endExclusive) {
  // максимум по окну [i-win, i-1] если endExclusive, иначе [i-win+1, i]
  const n = a.length, out = new Float64Array(n).fill(NaN), dq = new Int32Array(n);
  let head = 0, tail = 0;
  for (let i = 0; i < n; i++) {
    const cut = endExclusive ? i - win : i - win + 1;
    while (tail > head && dq[head] < cut) head++;
    if (endExclusive) { if (i > 0) out[i] = tail > head ? a[dq[head]] : NaN; }
    while (tail > head && a[dq[tail - 1]] <= a[i]) tail--;
    dq[tail++] = i;
    if (!endExclusive) out[i] = a[dq[head]];
  }
  return out;
}
function rollMin(a, win, endExclusive) {
  const n = a.length, out = new Float64Array(n).fill(NaN), dq = new Int32Array(n);
  let head = 0, tail = 0;
  for (let i = 0; i < n; i++) {
    const cut = endExclusive ? i - win : i - win + 1;
    while (tail > head && dq[head] < cut) head++;
    if (endExclusive) { if (i > 0) out[i] = tail > head ? a[dq[head]] : NaN; }
    while (tail > head && a[dq[tail - 1]] >= a[i]) tail--;
    dq[tail++] = i;
    if (!endExclusive) out[i] = a[dq[head]];
  }
  return out;
}
function rollSum(a, win) {
  const n = a.length, out = new Float64Array(n);
  let s = 0;
  for (let i = 0; i < n; i++) { s += a[i]; if (i >= win) s -= a[i - win]; out[i] = s; }
  return out;
}
// перцентиль последнего значения в окне win — как pctRank()
function rollPctRank(a, win) {
  const n = a.length, out = new Float64Array(n).fill(0.5);
  const buf = [];
  for (let i = 0; i < n; i++) {
    if (Number.isFinite(a[i])) buf.push(a[i]);
    if (buf.length > win) buf.shift();
    if (buf.length < 8) { out[i] = 0.5; continue; }
    const v = buf[buf.length - 1];
    let below = 0;
    for (const x of buf) if (x < v) below++;
    out[i] = below / buf.length;
  }
  return out;
}
// пивоты: точка j подтверждается на баре j+w
function pivotStream(h, l, w) {
  const n = h.length, hiAt = new Array(n), loAt = new Array(n);
  for (let i = w; i < n - w; i++) {
    let ph = true, pl = true;
    for (let j = i - w; j <= i + w; j++) {
      if (j === i) continue;
      if (h[j] >= h[i]) ph = false;
      if (l[j] <= l[i]) pl = false;
    }
    if (ph) hiAt[i] = h[i];
    if (pl) loAt[i] = l[i];
  }
  return { hiAt, loAt };
}
// последние два подтверждённых пивота на бар i внутри окна look
function lastTwo(at, i, w, look) {
  const out = [];
  for (let j = i - w; j >= Math.max(0, i - look + 1) && out.length < 2; j--) {
    if (at[j] !== undefined) out.push({ i: j, p: at[j] });
  }
  return out.reverse();     // [предыдущий, последний]
}

// --------------------------------------------------------------------------
// Один проход по паре: на каждом закрытом баре main TF считаем 13 компонентов
// и всё, что нужно торговому плану.
// --------------------------------------------------------------------------
export function scanPair(P, presetName, btc, opt = {}) {
  const PR = PRESETS[presetName];
  const kM = P.main, kS = P.slow, k5 = P.k5;
  const n = kM.c.length;
  const LOOK = 200;                          // limit=200 в getKlines
  if (n < 120) return null;

  const e9 = ema(kM.c, 9), e21 = ema(kM.c, 21), e50m = ema(kM.c, 50);
  const mac = macd(kM.c);
  const atrA = atr(kM.h, kM.l, kM.c, 14);
  const rArr = rsi(kM.c, 14);
  const bw = bbandsWidth(kM.c, 20, 2);
  const wRankA = rollPctRank(bw, 110);
  const dHiA = rollMax(kM.h, 20, true);      // slice(-21,-1)
  const dLoA = rollMin(kM.l, 20, true);
  const lo12 = rollMin(kM.l, 12, false);     // slice(-12) включая текущий
  const hi12 = rollMax(kM.h, 12, false);
  const qvSum50 = rollSum(kM.qv, 50);        // для среднего по slice(-51,-1)
  const qv2Sum50 = rollSum(kM.qv.map(v => v * v), 50);
  const tqSum8 = rollSum(kM.tq, 8), qvSum8 = rollSum(kM.qv, 8);
  const tqSum60 = rollSum(kM.tq, 60), qvSum60 = rollSum(kM.qv, 60);
  // VWAP(48) по типичной цене и базовому объёму
  const tpv = kM.c.map((c, i) => (kM.h[i] + kM.l[i] + c) / 3 * kM.v[i]);
  const tpvS = rollSum(tpv, 48), vS = rollSum(kM.v, 48);
  const piv3 = pivotStream(kM.h, kM.l, 3);
  const piv2 = pivotStream(kM.h, kM.l, 2);

  // медленный ТФ
  const sE20 = kS ? ema(kS.c, 20) : null, sE50 = kS ? ema(kS.c, 50) : null;
  // 5m: CVD за 1 час и часовое изменение
  const cvdBar = k5.qv.map((qv, i) => 2 * k5.tq[i] - qv);
  const cvdS = rollSum(cvdBar, 12);

  const out = [];
  let si = -1, fi = -1, oiI = -1, mkI = -1, f5 = -1, bi = -1;
  const closeT = i => kM.t[i] + PR.mainMin * 60000 - 1;

  for (let i = 60; i < n; i++) {
    const T = closeT(i);
    // двигаем указатели по закрытым барам других рядов (без заглядывания вперёд)
    while (kS && si + 1 < kS.c.length && kS.t[si + 1] + PR.slowMin * 60000 - 1 <= T) si++;
    while (f5 + 1 < k5.c.length && k5.t[f5 + 1] + 300000 - 1 <= T) f5++;
    while (fi + 1 < P.funding.length && P.funding[fi + 1].t <= T) fi++;
    while (oiI + 1 < P.oi.length && P.oi[oiI + 1].t <= T) oiI++;
    while (mkI + 1 < P.basis.length && P.basis[mkI + 1].t <= T) mkI++;
    while (bi + 1 < btc.t.length && btc.t[bi + 1] <= T) bi++;
    if (f5 < 13 || bi < 0) continue;

    const price = k5.c[f5];                   // refC: tick недоступен -> fresh=false
    if (!(price > 0)) continue;
    const atrV = Number.isFinite(atrA[i]) ? atrA[i] : price * 0.005;
    const atrPct = price ? atrV / price * 100 : 0;
    const C = {};
    const put = (k, v) => { if (v !== null && Number.isFinite(v)) C[k] = clamp(v, -1, 1); };

    /* 1. тренд старшего ТФ */
    if (kS && si > 55) {
      const c = kS.c[si], a = sE20[si], b = sE50[si];
      if (Number.isFinite(a) && Number.isFinite(b)) {
        const sl = slopePctAt(sE20, si, 12);
        put('trend', (c > a ? .32 : -.32) + (a > b ? .33 : -.33) + clamp(sl * 9, -1, 1) * .35);
      }
    }
    /* 2. импульс */
    const hist = mac.hist[i];
    if (Number.isFinite(hist) && Number.isFinite(e9[i]) && Number.isFinite(e21[i])) {
      put('momentum', clamp((hist / atrV) * 2.2, -1, 1) * .45
        + (e9[i] > e21[i] ? .28 : -.28)
        + clamp(slopePctAt(kM.c, i, 6) * 4, -1, 1) * .27);
    }
    /* 3. дельта тейкеров */
    const tdNow = qvSum8[i] ? (2 * tqSum8[i] - qvSum8[i]) / qvSum8[i] : 0;
    const tdBase = qvSum60[i] ? (2 * tqSum60[i] - qvSum60[i]) / qvSum60[i] : 0;
    let cvdV = clamp((tdNow - tdBase * 0.6) * 2.6, -1, 1);
    const pxMove = chg(price, kM.c[i - 8] || price);
    if (tdNow > 0.06 && pxMove < -0.2) cvdV = clamp(cvdV + 0.35, -1, 1);
    if (tdNow < -0.06 && pxMove > 0.2) cvdV = clamp(cvdV - 0.35, -1, 1);
    put('cvd', cvdV);
    /* 4. открытый интерес */
    let oi15 = null, oi1h = null;
    if (oiI >= 12) {
      const cur = P.oi[oiI].v, v3 = P.oi[oiI - 3].v, v12 = P.oi[oiI - 12].v;
      oi15 = chg(cur, v3); oi1h = chg(cur, v12);
      const p15 = chg(price, k5.c[f5 - 3] || price);
      const mag = clamp(Math.abs(oi15) / 1.4, 0, 1);
      let v15 = 0;
      if (oi15 > 0.05 && p15 > 0.05)        v15 = mag;
      else if (oi15 > 0.05 && p15 < -0.05)  v15 = -mag;
      else if (oi15 < -0.05 && p15 > 0.05)  v15 = mag * 0.45;
      else if (oi15 < -0.05 && p15 < -0.05) v15 = -mag * 0.45;
      if (oi1h > 0 === oi15 > 0) v15 = clamp(v15 * 1.2, -1, 1);
      put('oi', v15);
    }
    /* 5. всплеск объёма (burst из live-сокета недоступен -> 0) */
    const vMean = qvSum50[i] / 50;
    const vVar = Math.max(0, (qv2Sum50[i] - qvSum50[i] * qvSum50[i] / 50) / 49);
    const vSd = Math.sqrt(vVar);
    const vRec = (kM.qv[i] + kM.qv[i - 1] + kM.qv[i - 2]) / 3;
    const z = vSd ? (vRec - vMean) / vSd : 0;
    const dir3 = Math.sign(kM.c[i] - kM.o[i - 2] || 0) || 0;
    put('volume', clamp(Math.max(clamp(z / 2.6, 0, 1), 0) * (dir3 || 0.15), -1, 1));
    /* 6. пробой диапазона */
    const dHi = dHiA[i], dLo = dLoA[i];
    let brk = 0;
    if (Number.isFinite(dHi) && Number.isFinite(dLo) && dHi - dLo > 0) {
      const rng = dHi - dLo;
      const pos = clamp((price - dLo) / rng, -0.6, 1.6);
      brk = (pos - 0.5) * 2;
      if (price > dHi) brk = 1; else if (price < dLo) brk = -1;
      const ext = Math.abs(price - (brk > 0 ? dHi : dLo)) / atrV;
      if (Math.abs(brk) === 1 && ext > 1.8) brk *= 0.5;
      put('breakout', clamp(brk, -1, 1));
    }
    /* 7. структура свингов */
    {
      const H = lastTwo(piv3.hiAt, i, 3, LOOK), L = lastTwo(piv3.loAt, i, 3, LOOK);
      let sv;
      if (H.length < 2 || L.length < 2) {
        let a = 0, b = 0;
        for (let j = i - 7; j <= i; j++) a += kM.c[j];
        for (let j = i - 19; j <= i - 8; j++) b += kM.c[j];
        a /= 8; b /= 12;
        sv = (!a || !b) ? 0 : clamp((a - b) / b * 45, -0.8, 0.8);
      } else {
        const hh = H[1].p > H[0].p, hl = L[1].p > L[0].p;
        sv = (hh && hl) ? 1 : (!hh && !hl) ? -1 : (hh ? 0.3 : -0.3);
      }
      put('structure', sv);
    }
    /* 9. сила против BTC */
    const ch1h = chg(price, k5.c[Math.max(0, f5 - 12)] || price);
    put('rsBtc', clamp((ch1h - btc.ch1h[bi]) / Math.max(1.2, atrPct * 1.4), -1, 1));
    /* 10. RSI + дивергенция */
    {
      const rV = rArr[i];
      let div = 0;
      const H = lastTwo(piv2.hiAt, i, 2, LOOK), L = lastTwo(piv2.loAt, i, 2, LOOK);
      if (H.length === 2) { const a = H[0], b = H[1];
        if (b.p > a.p && Number.isFinite(rArr[b.i]) && Number.isFinite(rArr[a.i]) && rArr[b.i] < rArr[a.i] - 2) div -= 1; }
      if (L.length === 2) { const a = L[0], b = L[1];
        if (b.p < a.p && Number.isFinite(rArr[b.i]) && Number.isFinite(rArr[a.i]) && rArr[b.i] > rArr[a.i] + 2) div += 1; }
      div = clamp(div, -1, 1);
      put('rsi', clamp(rsiScore(rV) * 0.75 + div * 0.4, -1, 1));
    }
    /* 11. сжатие волатильности */
    const wRank = wRankA[i];
    const momV = C.momentum ?? 0;
    const dirHint = Math.sign(momV) || Math.sign(brk) || 1;
    let sqV = 0;
    if (wRank < 0.25) sqV = (1 - wRank / 0.25) * 0.9 * dirHint;
    else if (wRank > 0.9) sqV = -0.4 * dirHint;
    put('squeeze', sqV);
    /* 12. фандинг */
    const fund = fi >= 0 ? P.funding[fi].r : null;
    if (fund !== null) put('funding', fundingScore(fund));
    /* 13. VWAP */
    const vw = vS[i] ? tpvS[i] / vS[i] : kM.c[i];
    put('vwap', clamp((price - vw) / (atrV * 1.6), -1, 1) * 0.85);
    /* 15. базис перп/индекс */
    if (mkI >= 0) put('basis', -clamp(P.basis[mkI].b / 0.16, -1, 1) * 0.8);

    const e21v = Number.isFinite(e21[i]) ? e21[i] : price;
    const extATR = Math.abs(price - e21v) / atrV;
    out.push({
      t: T, price, atrV, atrPct, e21: e21v, vw, dHi, dLo,
      lo12: lo12[i], hi12: hi12[i], extATR,
      qv24: P.qv24 ? P.qv24[f5] : 0,
      btcTrend: btc.trend[bi],
      C,
      i5: f5,
    });
  }
  return out;
}
