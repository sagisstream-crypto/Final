import { fetchRecentTokenTransfers } from "./providers/etherscanClient.js";
import { fetchBestPair } from "./providers/dexscreenerClient.js";
import { analyzeTransfers, computeAccumulationScore } from "./score.js";
import { KNOWN_EXCHANGE_WALLETS } from "./knownExchangeWallets.js";
import { chainLabel } from "./chains.js";

const TRANSFER_WINDOW_LIMIT = 200; // сколько последних Transfer-событий по контракту разбирать за проход

export async function scanToken({ chainId, address }) {
  const pair = await fetchBestPair(address);
  if (!pair || !pair.priceUsd) {
    return { chainId, address, ok: false, reason: "нет активной DEX-пары / цены на DexScreener" };
  }

  const transfers = await fetchRecentTokenTransfers(chainId, address, { limit: TRANSFER_WINDOW_LIMIT });

  const addressTags = new Map();
  for (const w of (KNOWN_EXCHANGE_WALLETS[chainId] || [])) addressTags.set(w.address, "exchange");
  // сама DEX-пара — это не "кошелёк, который накапливает", её сделки уже посчитаны
  // в pair.buys24h/sells24h, поэтому исключаем её из подсчёта крупных кошельков.
  if (pair.pairAddress) addressTags.set(pair.pairAddress, "pool");

  const stats = analyzeTransfers(transfers, { addressTags, priceUsd: pair.priceUsd });
  const scoreObj = computeAccumulationScore({
    ...stats,
    liquidityUsd: pair.liquidityUsd,
    volume24hUsd: pair.volume24hUsd,
    buys24h: pair.buys24h,
    sells24h: pair.sells24h,
  });

  return {
    ok: true,
    chainId,
    chainLabel: chainLabel(chainId),
    address,
    pair,
    stats,
    scoreObj,
    scannedTransfers: transfers.length,
    scannedAt: Date.now(),
  };
}
