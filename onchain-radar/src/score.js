// Композитный score накопления — та же идея, что computeScore() в VolScan
// (index.html): каждый компонент даёт баллы, сумма зажимается в 0..100.
// Всё нормируется на ликвидность пары, а не на голые $ — иначе микрокап и
// токен с $50M ликвидности сравнивались бы по одной шкале.
const LARGE_TRANSFER_USD_MIN_DEFAULT = 5000;

// addressTags: Map<address, "exchange" | "pool"> — адреса CEX-кошельков и DEX-пула/роутера
// этого токена. Оба исключаются из "кошелёк накапливает" — это разные сигналы
// (net-flow считается только для "exchange", DEX buy/sell уже даёт DexScreener отдельно).
export function analyzeTransfers(transfers, { addressTags = new Map(), priceUsd, largeTransferUsdMin = LARGE_TRANSFER_USD_MIN_DEFAULT }) {
  let exchangeOutflowUsd = 0;
  let exchangeInflowUsd = 0;
  const recvCount = new Map(); // address -> qualifying large-transfer receive count
  const everSent = new Set();
  const recvUsdIfNew = new Map(); // address -> summed usd received, only for addresses never seen as sender

  for (const t of transfers) {
    if (!priceUsd || !t.amount) continue;
    const usd = t.amount * priceUsd;
    const fromTag = addressTags.get(t.from) || null;
    const toTag = addressTags.get(t.to) || null;

    if (toTag === "exchange" && fromTag !== "exchange") exchangeInflowUsd += usd;
    if (fromTag === "exchange" && toTag !== "exchange") exchangeOutflowUsd += usd;

    if (t.from) everSent.add(t.from);

    const qualifiesAsWalletMove = usd >= largeTransferUsdMin && !toTag && !fromTag;
    if (qualifiesAsWalletMove && t.to) {
      recvCount.set(t.to, (recvCount.get(t.to) || 0) + 1);
    }
  }

  for (const t of transfers) {
    if (!priceUsd || !t.amount || !t.to) continue;
    if (addressTags.get(t.to)) continue;
    if (everSent.has(t.to)) continue; // не "новый" — сам когда-то отправлял в этом окне
    const usd = t.amount * priceUsd;
    recvUsdIfNew.set(t.to, (recvUsdIfNew.get(t.to) || 0) + usd);
  }

  const distinctLargeWallets = [...recvCount.keys()].length;
  const repeatBuyers = [...recvCount.values()].filter((n) => n >= 2).length;
  const newRecipientsUsd = [...recvUsdIfNew.values()].reduce((a, b) => a + b, 0);

  return {
    exchangeOutflowUsd,
    exchangeInflowUsd,
    netExchangeFlowUsd: exchangeOutflowUsd - exchangeInflowUsd,
    distinctLargeWallets,
    repeatBuyers,
    newRecipientsUsd,
  };
}

export function computeAccumulationScore({ netExchangeFlowUsd, distinctLargeWallets, repeatBuyers, newRecipientsUsd, liquidityUsd, volume24hUsd, buys24h, sells24h }) {
  const parts = [];
  let s = 0;
  const liq = Math.max(liquidityUsd || 0, 1);

  // 1) Net exchange flow относительно ликвидности пары.
  const netFlowRatio = netExchangeFlowUsd / liq;
  if (netFlowRatio >= 0.05) { s += 25; parts.push(`вывод с бирж $${Math.round(netExchangeFlowUsd).toLocaleString()}`); }
  else if (netFlowRatio >= 0.02) { s += 15; parts.push("вывод с бирж"); }
  else if (netFlowRatio >= 0.005) { s += 7; }
  else if (netFlowRatio <= -0.02) { s -= 20; parts.push("занос на биржи"); }

  // 2) DEX buy/sell (по числу сделок за 24ч — DexScreener free API не отдаёт объём отдельно по стороне).
  if (buys24h !== null && sells24h !== null && (buys24h + sells24h) > 0) {
    const buyRatio = buys24h / (buys24h + sells24h);
    if (buyRatio >= 0.65) { s += 20; parts.push(`DEX покупки ${Math.round(buyRatio * 100)}%`); }
    else if (buyRatio >= 0.55) { s += 10; }
    else if (buyRatio <= 0.35) { s -= 15; parts.push("DEX продажи доминируют"); }
  }

  // 3) Сколько независимых крупных кошельков участвовало — совпадение важнее размера одной сделки.
  if (distinctLargeWallets >= 6) { s += 20; parts.push(`${distinctLargeWallets} крупных кошельков`); }
  else if (distinctLargeWallets >= 3) { s += 12; parts.push(`${distinctLargeWallets} крупных кошельков`); }
  else if (distinctLargeWallets >= 1) { s += 5; }

  // 4) Повторные покупки — конкретный кошелёк докупает не один раз в окне.
  if (repeatBuyers >= 3) { s += 15; parts.push("повторные покупки"); }
  else if (repeatBuyers >= 1) { s += 7; }

  // 5) Новые крупные получатели относительно ликвидности — "новые руки" входят в актив.
  const newRatio = newRecipientsUsd / liq;
  if (newRatio >= 0.03) { s += 10; parts.push("новые крупные держатели"); }
  else if (newRatio >= 0.01) { s += 5; }

  // штраф: активность ниже 5% ликвидности в объёме за 24ч — почти мёртвая пара, сигнал ненадёжен
  if (volume24hUsd !== null && volume24hUsd / liq < 0.05) { s -= 10; parts.push("низкая активность"); }

  return { score: Math.max(0, Math.min(100, Math.round(s))), parts };
}
