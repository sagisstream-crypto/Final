import { ETHERSCAN_V2_BASE } from "../chains.js";
import { config } from "../config.js";

const MIN_REQUEST_GAP_MS = 220; // free tier ~5 req/s — держим запас
let lastRequestAt = 0;

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function throttle() {
  const wait = MIN_REQUEST_GAP_MS - (Date.now() - lastRequestAt);
  if (wait > 0) await sleep(wait);
  lastRequestAt = Date.now();
}

async function callEtherscan(chainId, params) {
  await throttle();
  const url = new URL(ETHERSCAN_V2_BASE);
  url.searchParams.set("chainid", String(chainId));
  for (const [k, v] of Object.entries(params)) url.searchParams.set(k, v);
  url.searchParams.set("apikey", config.etherscanApiKey);

  const res = await fetch(url);
  if (!res.ok) throw new Error(`Etherscan HTTP ${res.status} (${params.module}.${params.action})`);
  const body = await res.json();
  // Etherscan возвращает status "0" и для "нет результатов", и для реальных ошибок —
  // "No transactions found" не должно валить весь опрос, остальное — должно.
  if (body.status === "0" && body.message !== "No transactions found") {
    throw new Error(`Etherscan API error (${params.module}.${params.action}): ${body.message} — ${JSON.stringify(body.result).slice(0, 200)}`);
  }
  return body.result;
}

// Последние N ERC-20 Transfer-событий по контракту (не по конкретному кошельку —
// это глобальная лента переводов токена, из неё вытаскиваем крупные и повторные адреса).
export async function fetchRecentTokenTransfers(chainId, contractAddress, { limit = 100 } = {}) {
  const result = await callEtherscan(chainId, {
    module: "account",
    action: "tokentx",
    contractaddress: contractAddress,
    page: 1,
    offset: limit,
    sort: "desc",
  });
  if (!Array.isArray(result)) return [];
  return result.map((t) => ({
    hash: t.hash,
    timestamp: Number(t.timeStamp) * 1000,
    from: t.from?.toLowerCase(),
    to: t.to?.toLowerCase(),
    rawValue: t.value,
    decimals: Number(t.tokenDecimal),
    amount: Number(t.value) / 10 ** Number(t.tokenDecimal),
    symbol: t.tokenSymbol,
  }));
}

export async function fetchTokenBalance(chainId, contractAddress, walletAddress, decimals) {
  const result = await callEtherscan(chainId, {
    module: "account",
    action: "tokenbalance",
    contractaddress: contractAddress,
    address: walletAddress,
    tag: "latest",
  });
  return Number(result) / 10 ** decimals;
}

export async function fetchTokenSupply(chainId, contractAddress) {
  const result = await callEtherscan(chainId, {
    module: "stats",
    action: "tokensupply",
    contractaddress: contractAddress,
  });
  return Number(result);
}
