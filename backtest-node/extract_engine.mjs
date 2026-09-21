import fs from "fs";
const src = fs.readFileSync("/home/user/Final/index.html", "utf8");

function block(startMark, endMark) {
  const a = src.indexOf(startMark), b = src.indexOf(endMark);
  if (a < 0 || b < 0) throw new Error("marker not found: " + startMark);
  return src.slice(a, b + endMark.length);
}
// computeScore целиком: от заголовка секции v15 до следующей секции
const csA = src.indexOf("// ---------- v15: composite score ----------");
const csB = src.indexOf("// ---------- v15: kline_1m");
if (csA < 0 || csB < 0) throw new Error("computeScore bounds not found");
const computeScore = src.slice(csA, csB);

const core = block("// ================== BACKTEST ENGINE v1 (BT-CORE-START) ==================",
                   "// ================== BT-CORE-END ==================");
const sweep = block("// ================== BT-SWEEP-START ==================",
                    "// ================== BT-SWEEP-END ==================");

// Единственная правка — потолок таблицы: 300k рассчитан на телефон,
// здесь таблица живёт в 12 ГБ heap. Правило сигналов не трогаем.
const corePatched = core.replace(/const BT_MAX_EVENTS = \d+;/,
  "const BT_MAX_EVENTS = Number(process.env.BT_MAX_EVENTS || 300000);");
if (corePatched === core) throw new Error("BT_MAX_EVENTS patch failed");

const out = `// АВТОГЕНЕРАЦИЯ из index.html — не редактировать вручную.
// Блоки computeScore / BT-CORE / BT-SWEEP скопированы дословно.
${computeScore}
${corePatched}
${sweep}
export { computeScore, btNewTable, btScanSeries, btParseKlines, btFillGaps, btPush, btGrow,
         btEval, btCfg, btMarginal, btOptimize, btWilson, btSymId,
         BT_COLS, BT_WARMUP_MIN, BT_DAY_MIN, BT_FORWARD_MIN, BT_WINDOW_MIN,
         BT_EVENT_FLOOR, BT_EVENT_SCORE_FLOOR, BT_TYPES, BT_COOLDOWN, BT_DEFAULT_CFG };
`;
fs.writeFileSync("engine.mjs", out);
const sha = (s) => import("crypto").then(c => c.createHash("sha256").update(s).digest("hex").slice(0, 12));
console.log("computeScore bytes:", computeScore.length);
console.log("BT-CORE bytes:", core.length);
console.log("BT-SWEEP bytes:", sweep.length);
console.log("engine.mjs written:", out.length, "bytes");
