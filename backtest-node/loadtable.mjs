import fs from "fs";
import { btNewTable, BT_COLS } from "./engine.mjs";
export function loadTable() {
  const m = JSON.parse(fs.readFileSync("table_meta.json", "utf8"));
  const tb = btNewTable();
  tb.n = m.n; tb.cap = m.n; tb.syms = m.syms; tb.days = m.days; tb.meta = m.meta;
  tb.syms.forEach((s, i) => tb.symIdx.set(s, i));
  const fd = fs.openSync("table_cols.bin", "r");
  let off = 0;
  for (const c of BT_COLS) {
    const a = new Float64Array(m.n);
    fs.readSync(fd, Buffer.from(a.buffer), 0, m.n * 8, off);
    tb.col[c] = a; off += m.n * 8;
  }
  fs.closeSync(fd);
  return tb;
}
