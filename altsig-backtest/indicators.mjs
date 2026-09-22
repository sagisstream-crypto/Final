// Индикаторы ALTSIG — скопированы из index-6.html дословно.
export const last = a => a.length ? a[a.length - 1] : 0;
export const clamp = (v, a, b) => v < a ? a : v > b ? b : v;
export const chg = (a, b) => b ? (a - b) / b * 100 : 0;
export function mean(a) { if (!a.length) return 0; let s = 0; for (const v of a) s += v; return s / a.length; }
export function stdev(a) {
  if (a.length < 2) return 0;
  const m = mean(a); let s = 0;
  for (const v of a) s += (v - m) * (v - m);
  return Math.sqrt(s / (a.length - 1));
}
export function ema(src, p) {
  const out = new Array(src.length).fill(NaN);
  if (src.length < p) return out;
  const k = 2 / (p + 1);
  let acc = 0;
  for (let i = 0; i < p; i++) acc += src[i];
  out[p - 1] = acc / p;
  for (let i = p; i < src.length; i++) out[i] = src[i] * k + out[i - 1] * (1 - k);
  return out;
}
export function rma(src, p) {
  const out = new Array(src.length).fill(NaN);
  if (src.length < p) return out;
  let acc = 0;
  for (let i = 0; i < p; i++) acc += src[i];
  out[p - 1] = acc / p;
  for (let i = p; i < src.length; i++) out[i] = (out[i - 1] * (p - 1) + src[i]) / p;
  return out;
}
export function rsi(c, p = 14) {
  const g = [], l = [];
  for (let i = 1; i < c.length; i++) {
    const d = c[i] - c[i - 1];
    g.push(d > 0 ? d : 0); l.push(d < 0 ? -d : 0);
  }
  const ag = rma(g, p), al = rma(l, p);
  const out = new Array(c.length).fill(NaN);
  for (let i = 0; i < ag.length; i++) {
    if (!Number.isFinite(ag[i])) continue;
    const rs = al[i] === 0 ? 100 : ag[i] / al[i];
    out[i + 1] = al[i] === 0 ? 100 : 100 - 100 / (1 + rs);
  }
  return out;
}
export function atr(h, l, c, p = 14) {
  const tr = [];
  for (let i = 0; i < c.length; i++) {
    tr.push(i === 0 ? h[i] - l[i]
      : Math.max(h[i] - l[i], Math.abs(h[i] - c[i - 1]), Math.abs(l[i] - c[i - 1])));
  }
  return rma(tr, p);
}
export function macd(c, f = 12, s = 26, sig = 9) {
  const ef = ema(c, f), es = ema(c, s);
  const line = c.map((_, i) => (Number.isFinite(ef[i]) && Number.isFinite(es[i])) ? ef[i] - es[i] : NaN);
  const valid = line.filter(Number.isFinite);
  const sl = ema(valid, sig);
  const off = line.length - valid.length;
  const signal = new Array(line.length).fill(NaN);
  for (let i = 0; i < sl.length; i++) signal[i + off] = sl[i];
  const hist = line.map((v, i) => (Number.isFinite(v) && Number.isFinite(signal[i])) ? v - signal[i] : NaN);
  return { line, signal, hist };
}
export function bbandsWidth(c, p = 20, mult = 2) {
  // только width — остальное analyze() не использует
  const w = new Array(c.length).fill(NaN);
  let sum = 0, sum2 = 0;
  for (let i = 0; i < c.length; i++) {
    sum += c[i]; sum2 += c[i] * c[i];
    if (i >= p) { sum -= c[i - p]; sum2 -= c[i - p] * c[i - p]; }
    if (i >= p - 1) {
      const m = sum / p;
      const varr = Math.max(0, (sum2 - sum * sum / p) / (p - 1));  // как stdev(): делитель n-1
      const sd = Math.sqrt(varr);
      w[i] = m ? (2 * mult * sd) / m * 100 : 0;
    }
  }
  return w;
}
export function fundingScore(f) {
  if (!Number.isFinite(f)) return null;
  if (f > 0.1)   return -1;
  if (f > 0.05)  return -0.6;
  if (f > 0.02)  return -0.15;
  if (f >= 0)    return 0.15;
  if (f > -0.02) return 0.35;
  if (f > -0.05) return 0.7;
  return 1;
}
export function rsiScore(r) {
  if (!Number.isFinite(r)) return 0;
  if (r >= 80) return -0.55;
  if (r >= 62) return 0.85;
  if (r >= 53) return 0.6;
  if (r > 47)  return 0;
  if (r > 38)  return -0.6;
  if (r > 20)  return -0.85;
  return 0.55;
}
// slopePct на префиксе [..i] окном p — та же формула, но без пересоздания массивов
export function slopePctAt(src, i, p) {
  const s = Math.max(0, i - p + 1);
  const n = i - s + 1;
  if (n < 3) return 0;
  let sx = 0, sy = 0, sxy = 0, sxx = 0, kk = 0;
  for (let j = s; j <= i; j++, kk++) {
    const y = src[j];
    if (!Number.isFinite(y)) return 0;
    sx += kk; sy += y; sxy += kk * y; sxx += kk * kk;
  }
  const d = n * sxx - sx * sx;
  if (!d) return 0;
  const k = (n * sxy - sx * sy) / d;
  const m = sy / n;
  return m ? k / m * 100 : 0;
}
