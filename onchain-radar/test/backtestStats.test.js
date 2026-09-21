import assert from "node:assert/strict";
import { pearsonCorrelation, compareBySignal } from "../src/backtestStats.js";

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

run("pearsonCorrelation: perfect positive correlation → ~1", () => {
  const r = pearsonCorrelation([1, 2, 3, 4, 5], [2, 4, 6, 8, 10]);
  assert.ok(Math.abs(r - 1) < 1e-9, `expected ~1, got ${r}`);
});

run("pearsonCorrelation: perfect negative correlation → ~-1", () => {
  const r = pearsonCorrelation([1, 2, 3, 4, 5], [10, 8, 6, 4, 2]);
  assert.ok(Math.abs(r + 1) < 1e-9, `expected ~-1, got ${r}`);
});

run("pearsonCorrelation: fewer than 3 valid pairs → null", () => {
  assert.equal(pearsonCorrelation([1, null], [2, 3]), null);
});

run("pearsonCorrelation: skips null/NaN pairs instead of poisoning the result", () => {
  const r = pearsonCorrelation([1, 2, 3, null, 5], [2, 4, 6, NaN, 10]);
  assert.ok(Math.abs(r - 1) < 1e-9, `expected ~1 after dropping the bad pair, got ${r}`);
});

run("compareBySignal: signal group shows higher win rate when it genuinely does", () => {
  const samples = [
    { hasSignal: true, forwardReturnPct: { 7: 20 } },
    { hasSignal: true, forwardReturnPct: { 7: 15 } },
    { hasSignal: true, forwardReturnPct: { 7: -5 } },
    { hasSignal: false, forwardReturnPct: { 7: -10 } },
    { hasSignal: false, forwardReturnPct: { 7: 2 } },
    { hasSignal: false, forwardReturnPct: { 7: -3 } },
  ];
  const out = compareBySignal(samples, (s) => s.hasSignal, [7]);
  assert.equal(out[7].signal.count, 3);
  assert.equal(out[7].noSignal.count, 3);
  assert.ok(out[7].signal.winRatePct > out[7].noSignal.winRatePct);
  assert.ok(out[7].signal.avgReturnPct > out[7].noSignal.avgReturnPct);
});

run("compareBySignal: null forward returns are excluded, not treated as 0% loss", () => {
  const samples = [
    { hasSignal: true, forwardReturnPct: { 7: null } },
    { hasSignal: true, forwardReturnPct: { 7: 10 } },
  ];
  const out = compareBySignal(samples, (s) => s.hasSignal, [7]);
  assert.equal(out[7].signal.count, 1, "the null sample must not be counted");
  assert.equal(out[7].signal.avgReturnPct, 10);
});
