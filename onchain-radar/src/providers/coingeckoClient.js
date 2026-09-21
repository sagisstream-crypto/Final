// CoinGecko public API — бесплатно, без ключа, но с жёстким троттлингом
// (публичный free-тир ~10-30 запросов/мин на IP, точная цифра не документирована
// и плавает, поэтому держим большой запас).
// https://docs.coingecko.com/reference/contract-address
const COINGECKO_BASE = "https://api.coingecko.com/api/v3";
const MIN_REQUEST_GAP_MS = 2500;
let lastRequestAt = 0;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function throttle() {
  const wait = MIN_REQUEST_GAP_MS - (Date.now() - lastRequestAt);
  if (wait > 0) await sleep(wait);
  lastRequestAt = Date.now();
}

async function callCoingecko(pathAndQuery) {
  await throttle();
  const res = await fetch(`${COINGECKO_BASE}${pathAndQuery}`);
  if (res.status === 429) {
    throw new Error("CoinGecko 429 — превышен лимит бесплатного тира, подождите и запустите бэктест заново (он идемпотентный, прогресс не теряется в рамках одного запуска)");
  }
  if (!res.ok) throw new Error(`CoinGecko HTTP ${res.status} (${pathAndQuery})`);
  return res.json();
}

// Резолвит адрес контракта в CoinGecko coin id — id нужен для истории цены.
// Возвращает null, если токен не индексирован CoinGecko (частый случай для
// совсем свежих/мелких токенов — тогда бэктест по нему просто пропускается).
export async function resolveCoinId(coingeckoPlatform, contractAddress) {
  try {
    const body = await callCoingecko(`/coins/${coingeckoPlatform}/contract/${contractAddress.toLowerCase()}`);
    return body?.id || null;
  } catch (err) {
    if (String(err.message).includes("HTTP 404")) return null;
    throw err;
  }
}

// Вся дневная история цены между fromUnix и toUnix (сек) одним запросом —
// экономим лимит вместо отдельного вызова на каждую точку сэмплирования.
// Возвращает [{ ts (мс), priceUsd }, ...] по возрастанию времени.
export async function fetchHistoricalPriceRange(coinId, fromUnixSec, toUnixSec) {
  const body = await callCoingecko(
    `/coins/${coinId}/market_chart/range?vs_currency=usd&from=${Math.round(fromUnixSec)}&to=${Math.round(toUnixSec)}`
  );
  const prices = Array.isArray(body?.prices) ? body.prices : [];
  return prices.map(([ts, price]) => ({ ts, priceUsd: price })).sort((a, b) => a.ts - b.ts);
}

// Берёт цену, ближайшую к targetTs (мс), из уже загруженной серии.
// Возвращает null, если ближайшая точка дальше maxGapMs — не додумываем,
// чего в данных нет (актуально на краях серии, где CoinGecko мог обрезать день).
export function nearestPrice(series, targetTs, maxGapMs = 36 * 60 * 60 * 1000) {
  if (!series.length) return null;
  let best = series[0];
  let bestGap = Math.abs(series[0].ts - targetTs);
  for (const p of series) {
    const gap = Math.abs(p.ts - targetTs);
    if (gap < bestGap) { best = p; bestGap = gap; }
  }
  return bestGap <= maxGapMs ? best.priceUsd : null;
}
