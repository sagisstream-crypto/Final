import { appendFileSync, mkdirSync, existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config } from "./config.js";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const dataDir = path.join(__dirname, "..", "data");
const journalPath = path.join(dataDir, "signals.jsonl");

export const SCORE_ALERT_THRESHOLD = 55;
const COOLDOWN_MS = 6 * 60 * 60 * 1000; // 6ч на токен — это ончейн-накопление, не минутный скальп
const lastFiredAt = new Map(); // `${chainId}:${address}` -> ts

function fmtUsd(n) {
  if (n === null || n === undefined) return "—";
  return "$" + Math.round(n).toLocaleString("en-US");
}

export function formatSignalCard(result) {
  const { chainLabel, address, pair, stats, scoreObj } = result;
  const lines = [
    `🐋 НАКОПЛЕНИЕ · score ${scoreObj.score} · ${chainLabel}`,
    `Токен: ${address}`,
    `Цена: $${pair.priceUsd} · ликвидность ${fmtUsd(pair.liquidityUsd)} · FDV ${fmtUsd(pair.fdvUsd)}`,
    `Net-flow с бирж: ${stats.netExchangeFlowUsd >= 0 ? "+" : ""}${fmtUsd(stats.netExchangeFlowUsd)}`,
    `DEX 24ч: ${pair.buys24h ?? "—"} покупок / ${pair.sells24h ?? "—"} продаж · объём ${fmtUsd(pair.volume24hUsd)}`,
    `Крупных кошельков в деле: ${stats.distinctLargeWallets} (повторных: ${stats.repeatBuyers})`,
    `Новые крупные держатели: ${fmtUsd(stats.newRecipientsUsd)}`,
    scoreObj.parts.length ? `Причины: ${scoreObj.parts.join(" · ")}` : "",
    pair.pairUrl ? `Пара: ${pair.pairUrl}` : "",
    ``,
    `Это не финсовет — ончейн-скан по публичным данным, дальше решение и риск ваши.`,
  ];
  return lines.filter(Boolean).join("\n");
}

function journalEntry(result) {
  return JSON.stringify({
    ts: result.scannedAt,
    chainId: result.chainId,
    address: result.address,
    score: result.scoreObj.score,
    parts: result.scoreObj.parts,
    priceUsd: result.pair.priceUsd,
    liquidityUsd: result.pair.liquidityUsd,
    netExchangeFlowUsd: result.stats.netExchangeFlowUsd,
    distinctLargeWallets: result.stats.distinctLargeWallets,
    repeatBuyers: result.stats.repeatBuyers,
  }) + "\n";
}

function writeJournal(result) {
  if (!existsSync(dataDir)) mkdirSync(dataDir, { recursive: true });
  appendFileSync(journalPath, journalEntry(result));
}

async function pushToTelegram(text) {
  if (!config.telegramBotToken || !config.telegramChatId) return;
  const url = `https://api.telegram.org/bot${config.telegramBotToken}/sendMessage`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id: config.telegramChatId, text, disable_web_page_preview: true }),
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    console.error(`Telegram push failed: HTTP ${res.status} ${body.slice(0, 200)}`);
  }
}

// Возвращает true, если карточка сигнала была отправлена/записана (т.е. score прошёл
// порог и не попал в кулдаун этого токена).
export async function maybeFireSignal(result) {
  if (!result.ok || result.scoreObj.score < SCORE_ALERT_THRESHOLD) return false;
  const key = `${result.chainId}:${result.address}`;
  const now = Date.now();
  if (now - (lastFiredAt.get(key) || 0) < COOLDOWN_MS) return false;
  lastFiredAt.set(key, now);

  const card = formatSignalCard(result);
  console.log("\n" + card + "\n" + "-".repeat(40));
  writeJournal(result);
  await pushToTelegram(card);
  return true;
}
