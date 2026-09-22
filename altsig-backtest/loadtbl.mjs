import fs from "fs";
const CHUNK = 1 << 30;
function readBig(fd, b, pos) {
  for (let o = 0; o < b.length; o += CHUNK) {
    const len = Math.min(CHUNK, b.length - o);
    fs.readSync(fd, b, o, len, pos + o);
  }
}
export function loadTbl(preset) {
  const meta = JSON.parse(fs.readFileSync(`tbl_${preset}.json`, "utf8"));
  const { n, stride } = meta;
  const fd = fs.openSync(`tbl_${preset}.bin`, "r");
  const buf = new Float32Array(n * stride);
  readBig(fd, Buffer.from(buf.buffer), 0);
  const tA = new Float64Array(n);
  readBig(fd, Buffer.from(tA.buffer), n * stride * 4);
  const sA = new Int32Array(n);
  readBig(fd, Buffer.from(sA.buffer), n * stride * 4 + n * 8);
  fs.closeSync(fd);
  return { meta, n, stride, buf, tA, sA };
}
