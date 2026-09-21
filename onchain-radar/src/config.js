import { readFileSync, existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const envPath = path.join(__dirname, "..", ".env");

// Крошечный .env-загрузчик, чтобы не тянуть npm-зависимость только ради этого.
function loadDotEnv(file) {
  if (!existsSync(file)) return;
  const text = readFileSync(file, "utf8");
  for (const rawLine of text.split("\n")) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const eq = line.indexOf("=");
    if (eq === -1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    if (process.env[key] === undefined) process.env[key] = val;
  }
}
loadDotEnv(envPath);

function parseWatchlist(raw) {
  if (!raw) return [];
  return raw.split(",").map((s) => s.trim()).filter(Boolean).map((entry) => {
    const [chainIdStr, address] = entry.split(":");
    return { chainId: Number(chainIdStr), address: address?.toLowerCase() };
  }).filter((e) => e.chainId && e.address && e.address.startsWith("0x"));
}

export const config = {
  etherscanApiKey: process.env.ETHERSCAN_API_KEY || "",
  watchlist: parseWatchlist(process.env.WATCHLIST),
  telegramBotToken: process.env.TELEGRAM_BOT_TOKEN || "",
  telegramChatId: process.env.TELEGRAM_CHAT_ID || "",
  pollIntervalMs: Number(process.env.POLL_INTERVAL_MS) || 300000,
};

export function assertConfigured() {
  const problems = [];
  if (!config.etherscanApiKey) {
    problems.push("ETHERSCAN_API_KEY не задан — получите бесплатный на https://etherscan.io/apis и впишите в .env");
  }
  if (!config.watchlist.length) {
    problems.push("WATCHLIST пуст — впишите хотя бы один токен как chainId:0xАдрес в .env");
  }
  return problems;
}
