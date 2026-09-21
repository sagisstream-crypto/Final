import assert from "node:assert/strict";
import { analyzeTransfers, computeAccumulationScore } from "../src/score.js";

function run(name, fn) {
  try {
    fn();
    console.log(`ok - ${name}`);
  } catch (err) {
    console.error(`FAIL - ${name}`);
    console.error(err);
    process.exitCode = 1;
  }
}

run("net exchange outflow → positive netExchangeFlowUsd", () => {
  const addressTags = new Map([["0xexch", "exchange"]]);
  const transfers = [
    { from: "0xexch", to: "0xwhale1", amount: 1000 }, // withdrawal from exchange
    { from: "0xwhale2", to: "0xexch", amount: 200 },  // deposit to exchange
  ];
  const stats = analyzeTransfers(transfers, { addressTags, priceUsd: 2, largeTransferUsdMin: 100 });
  assert.equal(stats.exchangeOutflowUsd, 2000);
  assert.equal(stats.exchangeInflowUsd, 400);
  assert.equal(stats.netExchangeFlowUsd, 1600);
});

run("pool address excluded from distinct large wallet count", () => {
  const addressTags = new Map([["0xpool", "pool"]]);
  const transfers = [
    { from: "0xpool", to: "0xtrader1", amount: 10000 },
    { from: "0xtrader2", to: "0xpool", amount: 5000 },
  ];
  const stats = analyzeTransfers(transfers, { addressTags, priceUsd: 1, largeTransferUsdMin: 100 });
  assert.equal(stats.distinctLargeWallets, 0, "pool-facing transfers should not count as wallet accumulation");
});

run("repeat buyer detected across two qualifying transfers", () => {
  const transfers = [
    { from: "0xa", to: "0xwhale", amount: 500 },
    { from: "0xb", to: "0xwhale", amount: 600 },
  ];
  const stats = analyzeTransfers(transfers, { priceUsd: 1, largeTransferUsdMin: 100 });
  assert.equal(stats.distinctLargeWallets, 1);
  assert.equal(stats.repeatBuyers, 1);
});

run("new recipient excludes wallets that also sent in-window", () => {
  const transfers = [
    { from: "0xrouter", to: "0xnew", amount: 1000 },
    { from: "0xnew", to: "0xother", amount: 100 }, // 0xnew also sent → not "new" anymore, but 0xother still is
  ];
  const stats = analyzeTransfers(transfers, { priceUsd: 1 });
  assert.equal(stats.newRecipientsUsd, 100, "only 0xother's receipt should count; 0xnew is disqualified by sending");
});

run("score clamps to 0..100 and reflects strong accumulation", () => {
  const { score, parts } = computeAccumulationScore({
    netExchangeFlowUsd: 100000,
    distinctLargeWallets: 8,
    repeatBuyers: 4,
    newRecipientsUsd: 50000,
    liquidityUsd: 500000,
    volume24hUsd: 300000,
    buys24h: 80,
    sells24h: 20,
  });
  assert.ok(score >= 80, `expected strong score, got ${score}`);
  assert.ok(score <= 100);
  assert.ok(parts.length > 0);
});

run("score penalizes exchange inflow and low activity", () => {
  const { score } = computeAccumulationScore({
    netExchangeFlowUsd: -50000,
    distinctLargeWallets: 0,
    repeatBuyers: 0,
    newRecipientsUsd: 0,
    liquidityUsd: 100000,
    volume24hUsd: 1000,
    buys24h: 5,
    sells24h: 20,
  });
  assert.ok(score < 20, `expected low score, got ${score}`);
});
