import zlib from "zlib";
// Минимальный читатель ZIP: архивы Binance — один CSV внутри.
export function unzipFirst(buf) {
  if (buf.length < 30 || buf.readUInt32LE(0) !== 0x04034b50) throw new Error("not a zip");
  const method = buf.readUInt16LE(8);
  const nameLen = buf.readUInt16LE(26), extraLen = buf.readUInt16LE(28);
  const start = 30 + nameLen + extraLen;
  // размер берём из central directory (в local header он может быть 0 при streaming)
  let csize = buf.readUInt32LE(18);
  if (!csize) {
    const eocd = buf.lastIndexOf(Buffer.from([0x50, 0x4b, 0x05, 0x06]));
    const cdOff = buf.readUInt32LE(eocd + 16);
    csize = buf.readUInt32LE(cdOff + 20);
  }
  const body = buf.subarray(start, start + csize);
  return method === 0 ? body : zlib.inflateRawSync(body, { maxOutputLength: 1 << 30 });
}
