import { config, assertConfigured } from "./config.js";
import { scanToken } from "./watcher.js";
import { maybeFireSignal } from "./alerts.js";

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function scanOnce() {
  for (const entry of config.watchlist) {
    try {
      const result = await scanToken(entry);
      if (!result.ok) {
        console.log(`[skip] ${entry.chainId}:${entry.address} — ${result.reason}`);
        continue;
      }
      const fired = await maybeFireSignal(result);
      if (!fired) {
        console.log(`[ok] ${entry.chainId}:${entry.address} score=${result.scoreObj.score} (ниже порога или кулдаун)`);
      }
    } catch (err) {
      console.error(`[error] ${entry.chainId}:${entry.address} — ${err.message}`);
    }
  }
}

async function main() {
  const problems = assertConfigured();
  if (problems.length) {
    console.error("Радар не настроен:");
    for (const p of problems) console.error("  - " + p);
    console.error("\nСкопируйте .env.example в .env и заполните, затем запустите снова.");
    process.exit(1);
  }

  console.log(`onchain-radar: слежу за ${config.watchlist.length} токеном(ами), опрос каждые ${Math.round(config.pollIntervalMs / 1000)}с`);
  // eslint-disable-next-line no-constant-condition
  while (true) {
    await scanOnce();
    await sleep(config.pollIntervalMs);
  }
}

main();
