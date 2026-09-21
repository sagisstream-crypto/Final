// Бэктест: проверяет, действительно ли всплеск score/метрик накопления в
// прошлом предшествовал росту цены — а не просто "звучит логично".
//
// Для каждого токена из WATCHLIST берёт несколько точек в прошлом (раз в
// BACKTEST_SAMPLE_INTERVAL_DAYS дней за последние BACKTEST_PERIOD_DAYS),
// в каждой точке честно воссоздаёт метрики накопления ТОЛЬКО по данным,
// доступным до этой даты (Etherscan tokentx за BACKTEST_LOOKBACK_DAYS дней
// до точки), и сравнивает с тем, что реально произошло с ценой (CoinGecko)
// через 7/14/30 дней.
//
// Важное ограничение: DexScreener не отдаёт историю, поэтому здесь нет
// DEX buy/sell ratio и нормировки на ликвидность из живого score.js —
// бэктестятся только компоненты, которые можно честно восстановить из
// истории: net exchange flow, число крупных кошельков, повторные покупки,
// новые крупные держатели. См. README "Что бэктестится, а что нет".
import { writeFileSync, mkdirSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config, assertConfigured } from "./config.js";
import { CHAINS, chainLabel } from "./chains.js";
import { fetchTokenTransfersInRange, fetchBlockNumberByTimestamp } from "./providers/etherscanClient.js";
import { resolveCoinId, fetchHistoricalPriceRange, nearestPrice } from "./providers/coingeckoClient.js";
import { analyzeTransfers } from "./score.js";
import { KNOWN_EXCHANGE_WALLETS } from "./knownExchangeWallets.js";
import { pearsonCorrelation, compareBySignal } from "./backtestStats.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.join(__dirname, "..", "data");
const DAY_MS = 24 * 60 * 60 * 1000;

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

async function withRetry(fn, label, tries = 3) {
  for (let i = 0; i < tries; i++) {
    try {
      return await fn();
    } catch (err) {
      const isLast = i === tries - 1;
      const rateLimited = /rate limit|429/i.test(err.message);
      if (isLast || !rateLimited) throw err;
      console.warn(`[retry] ${label}: ${err.message} — жду 2с и пробую снова (${i + 1}/${tries})`);
      await sleep(2000);
    }
  }
}

function pct(a, b) {
  return a === null || b === null || a === 0 ? null : ((b / a) - 1) * 100;
}

async function backtestToken(entry) {
  const { chainId, address } = entry;
  const chainCfg = CHAINS[chainId];
  if (!chainCfg?.coingeckoPlatform) {
    console.warn(`[skip] ${chainId}:${address} — сеть не размечена для CoinGecko в chains.js`);
    return [];
  }

  const coinId = await withRetry(() => resolveCoinId(chainCfg.coingeckoPlatform, address), `resolveCoinId(${address})`);
  if (!coinId) {
    console.warn(`[skip] ${chainId}:${address} — CoinGecko не знает этот контракт (слишком новый/мелкий токен)`);
    return [];
  }

  const now = Date.now();
  const periodStart = now - config.backtestPeriodDays * DAY_MS;
  const priceSeries = await withRetry(
    () => fetchHistoricalPriceRange(coinId, periodStart / 1000, now / 1000),
    `fetchHistoricalPriceRange(${coinId})`
  );
  if (priceSeries.length < 5) {
    console.warn(`[skip] ${chainId}:${address} (${coinId}) — CoinGecko вернул слишком мало точек цены (${priceSeries.length})`);
    return [];
  }

  const addressTags = new Map();
  for (const w of (KNOWN_EXCHANGE_WALLETS[chainId] || [])) addressTags.set(w.address, "exchange");

  const maxForward = Math.max(...config.backtestForwardDays);
  const sampleDates = [];
  for (let d = config.backtestPeriodDays; d >= maxForward; d -= config.backtestSampleIntervalDays) {
    sampleDates.push(now - d * DAY_MS);
  }

  const samples = [];
  for (const sampleDate of sampleDates) {
    const priceAtSample = nearestPrice(priceSeries, sampleDate);
    if (priceAtSample === null) {
      console.warn(`[skip-sample] ${address} @ ${new Date(sampleDate).toISOString().slice(0, 10)} — нет цены рядом с этой датой`);
      continue;
    }

    let blockEnd, blockStart;
    try {
      blockEnd = await withRetry(() => fetchBlockNumberByTimestamp(chainId, sampleDate / 1000, "before"), "getblocknobytime(end)");
      blockStart = await withRetry(
        () => fetchBlockNumberByTimestamp(chainId, (sampleDate - config.backtestLookbackDays * DAY_MS) / 1000, "before"),
        "getblocknobytime(start)"
      );
    } catch (err) {
      console.warn(`[skip-sample] ${address} @ ${new Date(sampleDate).toISOString().slice(0, 10)} — ${err.message}`);
      continue;
    }

    const transfers = await withRetry(
      () => fetchTokenTransfersInRange(chainId, address, { startBlock: blockStart, endBlock: blockEnd, limit: 1000 }),
      "fetchTokenTransfersInRange"
    );
    const truncated = transfers.length >= 1000;
    if (truncated) {
      console.warn(`[warn] ${address} @ ${new Date(sampleDate).toISOString().slice(0, 10)} — окно перевода упёрлось в лимит 1000, метрики занижены`);
    }

    const stats = analyzeTransfers(transfers, {
      addressTags,
      priceUsd: priceAtSample,
      largeTransferUsdMin: config.backtestLargeTransferUsdMin,
    });
    const totalVolumeUsd = transfers.reduce((a, t) => a + (t.amount || 0) * priceAtSample, 0);

    const forwardReturnPct = {};
    for (const h of config.backtestForwardDays) {
      const priceForward = nearestPrice(priceSeries, sampleDate + h * DAY_MS);
      forwardReturnPct[h] = pct(priceAtSample, priceForward);
    }

    samples.push({
      chainId,
      chainLabel: chainLabel(chainId),
      address,
      coinId,
      sampleDate,
      sampleDateIso: new Date(sampleDate).toISOString().slice(0, 10),
      priceAtSample,
      transfersScanned: transfers.length,
      truncated,
      netExchangeFlowUsd: stats.netExchangeFlowUsd,
      netExchangeFlowRatio: totalVolumeUsd > 0 ? stats.netExchangeFlowUsd / totalVolumeUsd : null,
      distinctLargeWallets: stats.distinctLargeWallets,
      repeatBuyers: stats.repeatBuyers,
      newRecipientsUsd: stats.newRecipientsUsd,
      newRecipientsRatio: totalVolumeUsd > 0 ? stats.newRecipientsUsd / totalVolumeUsd : null,
      forwardReturnPct,
    });
  }
  return samples;
}

function printSummary(allSamples) {
  console.log(`\n=== Итог: ${allSamples.length} валидных точек сэмплирования ===\n`);

  console.log("Корреляция метрики (в момент сэмплирования) с forward-доходностью:");
  for (const h of config.backtestForwardDays) {
    const rets = allSamples.map((s) => s.forwardReturnPct[h]);
    const corrFlow = pearsonCorrelation(allSamples.map((s) => s.netExchangeFlowRatio), rets);
    const corrWallets = pearsonCorrelation(allSamples.map((s) => s.distinctLargeWallets), rets);
    const corrRepeat = pearsonCorrelation(allSamples.map((s) => s.repeatBuyers), rets);
    const corrNew = pearsonCorrelation(allSamples.map((s) => s.newRecipientsRatio), rets);
    console.log(`  +${h}д: net-flow ratio r=${fmtR(corrFlow)} · крупных кошельков r=${fmtR(corrWallets)} · повторных покупок r=${fmtR(corrRepeat)} · новых держателей r=${fmtR(corrNew)}`);
  }

  console.log(`\nГруппа "похоже на накопление" (net-flow > 0 И крупных кошельков >= ${config.backtestSignalMinWallets}) vs остальное:`);
  const predicate = (s) => s.netExchangeFlowUsd > 0 && s.distinctLargeWallets >= config.backtestSignalMinWallets;
  const cmp = compareBySignal(allSamples, predicate, config.backtestForwardDays);
  for (const h of config.backtestForwardDays) {
    const sig = cmp[h].signal, no = cmp[h].noSignal;
    console.log(`  +${h}д — сигнал (n=${sig.count}): win rate ${fmtPct(sig.winRatePct)}, средняя доходность ${fmtPct(sig.avgReturnPct)}, медиана ${fmtPct(sig.medianReturnPct)}`);
    console.log(`         без сигнала (n=${no.count}): win rate ${fmtPct(no.winRatePct)}, средняя доходность ${fmtPct(no.avgReturnPct)}, медиана ${fmtPct(no.medianReturnPct)}`);
  }

  console.log(`\nr близко к 0 или "сигнал" не лучше "без сигнала" по выборке из ${allSamples.length} точек = метрика на этих токенах не предсказывает движение,`);
  console.log(`сколько бы красиво это ни звучало в рекламных постах. Мало точек (< ~30-50) — выводам доверять рано, это только прикидка.`);
}

function fmtR(r) { return r === null ? "н/д" : r.toFixed(2); }
function fmtPct(v) { return v === null ? "н/д" : (v >= 0 ? "+" : "") + v.toFixed(1) + "%"; }

async function main() {
  const problems = assertConfigured();
  if (problems.length) {
    console.error("Бэктест не настроен:");
    for (const p of problems) console.error("  - " + p);
    process.exit(1);
  }

  console.log(`onchain-radar backtest: ${config.watchlist.length} токен(ов), период ${config.backtestPeriodDays}д, шаг ${config.backtestSampleIntervalDays}д, форвард [${config.backtestForwardDays.join(",")}]д`);

  const allSamples = [];
  for (const entry of config.watchlist) {
    console.log(`\n--- ${entry.chainId}:${entry.address} ---`);
    const samples = await backtestToken(entry);
    console.log(`  собрано точек: ${samples.length}`);
    allSamples.push(...samples);
  }

  if (!existsSync(dataDir)) mkdirSync(dataDir, { recursive: true });
  const reportPath = path.join(dataDir, "backtest-samples.json");
  writeFileSync(reportPath, JSON.stringify(allSamples, null, 2));
  console.log(`\nСырые точки сохранены в ${reportPath}`);

  if (!allSamples.length) {
    console.log("\nНи одной валидной точки не собрано — нечего анализировать. Смотрите [skip]/[skip-sample] выше, почему.");
    return;
  }
  printSummary(allSamples);
}

main().catch((err) => {
  console.error("Бэктест упал:", err);
  process.exit(1);
});
