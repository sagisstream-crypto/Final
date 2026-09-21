// DexScreener — публичный бесплатный API, ключ не нужен.
// https://docs.dexscreener.com/api/reference
const DEXSCREENER_BASE = "https://api.dexscreener.com/latest/dex/tokens";

// Возвращает самую ликвидную пару токена (по всем DEX/сетям, где он торгуется) —
// оттуда берём цену, ликвидность и объём/соотношение покупок-продаж за 24ч.
export async function fetchBestPair(tokenAddress) {
  const res = await fetch(`${DEXSCREENER_BASE}/${tokenAddress}`);
  if (!res.ok) throw new Error(`DexScreener HTTP ${res.status}`);
  const body = await res.json();
  const pairs = Array.isArray(body.pairs) ? body.pairs : [];
  if (!pairs.length) return null;
  pairs.sort((a, b) => (b.liquidity?.usd || 0) - (a.liquidity?.usd || 0));
  const p = pairs[0];
  return {
    priceUsd: p.priceUsd ? Number(p.priceUsd) : null,
    liquidityUsd: p.liquidity?.usd ?? null,
    fdvUsd: p.fdv ?? null,
    volume24hUsd: p.volume?.h24 ?? null,
    buys24h: p.txns?.h24?.buys ?? null,
    sells24h: p.txns?.h24?.sells ?? null,
    priceChange24hPct: p.priceChange?.h24 ?? null,
    dexId: p.dexId,
    pairUrl: p.url,
    pairAddress: p.pairAddress ? p.pairAddress.toLowerCase() : null,
  };
}
