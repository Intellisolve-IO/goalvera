/** Pure helpers for the iPhone Record page. No microphone and no network. */

const MD5_K = new Uint32Array([
  0xd76aa478, 0xe8c7b756, 0x242070db, 0xc1bdceee, 0xf57c0faf, 0x4787c62a, 0xa8304613, 0xfd469501,
  0x698098d8, 0x8b44f7af, 0xffff5bb1, 0x895cd7be, 0x6b901122, 0xfd987193, 0xa679438e, 0x49b40821,
  0xf61e2562, 0xc040b340, 0x265e5a51, 0xe9b6c7aa, 0xd62f105d, 0x02441453, 0xd8a1e681, 0xe7d3fbc8,
  0x21e1cde6, 0xc33707d6, 0xf4d50d87, 0x455a14ed, 0xa9e3e905, 0xfcefa3f8, 0x676f02d9, 0x8d2a4c8a,
  0xfffa3942, 0x8771f681, 0x6d9d6122, 0xfde5380c, 0xa4beea44, 0x4bdecfa9, 0xf6bb4b60, 0xbebfbc70,
  0x289b7ec6, 0xeaa127fa, 0xd4ef3085, 0x04881d05, 0xd9d4d039, 0xe6db99e5, 0x1fa27cf8, 0xc4ac5665,
  0xf4292244, 0x432aff97, 0xab9423a7, 0xfc93a039, 0x655b59c3, 0x8f0ccc92, 0xffeff47d, 0x85845dd1,
  0x6fa87e4f, 0xfe2ce6e0, 0xa3014314, 0x4e0811a1, 0xf7537e82, 0xbd3af235, 0x2ad7d2bb, 0xeb86d391,
]);
const MD5_S = [
  7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21,
];

export const SEGMENT_MS = 60_000;
export const PREFERRED_MIME = ['audio/mp4', 'audio/mp4;codecs=mp4a.40.2', 'audio/aac', 'audio/webm;codecs=opus', 'audio/webm'];

/** Teacher and admin (and the laptop demo, which has no signed-in role). RBT stays read-only. */
export function canRecord(user) {
  if (!user) return true;
  if (user.read_only || user.role === 'rbt') return false;
  return user.role === 'admin' || user.role === 'teacher' || user.role === 'reviewer';
}

/** Same filename rule as the Hosyond board: spaces become '-', 16 characters. */
export function cleanClassCode(src) {
  const out = [];
  for (const ch of String(src || '')) {
    if (/[A-Za-z0-9-]/.test(ch)) out.push(ch);
    else if ((ch === ' ' || ch === '_') && out.length && out[out.length - 1] !== '-') out.push('-');
  }
  while (out.length && out[out.length - 1] === '-') out.pop();
  let text = out.join('');
  if (text.length > 16) {
    text = text.slice(0, 16);
    while (text.endsWith('-')) text = text.slice(0, -1);
  }
  return text;
}

export function pickMime(isTypeSupported) {
  if (typeof isTypeSupported !== 'function') return null;
  return PREFERRED_MIME.find((t) => isTypeSupported(t)) || null;
}

export function extensionFor(mime) {
  return String(mime || '').toLowerCase().includes('webm') ? 'webm' : 'm4a';
}

export function partName(index, mime) {
  return `part_${String(index).padStart(4, '0')}.${extensionFor(mime)}`;
}

export function localStamp(date) {
  const p = (n) => String(n).padStart(2, '0');
  return `${date.getFullYear()}${p(date.getMonth() + 1)}${p(date.getDate())}_${p(date.getHours())}${p(date.getMinutes())}${p(date.getSeconds())}`;
}

export function offsetMinutes(date) {
  return -date.getTimezoneOffset();
}

export function newSessionId(randomBytes) {
  const bytes = randomBytes || crypto.getRandomValues(new Uint8Array(16));
  return `s${[...bytes].map((b) => b.toString(16).padStart(2, '0')).join('')}`;
}

export function bytesToB64(bytes) {
  if (typeof Buffer !== 'undefined') return Buffer.from(bytes).toString('base64');
  let s = '';
  for (const b of bytes) s += String.fromCharCode(b);
  return btoa(s);
}

export function md5(bytes) {
  const msg = bytes instanceof Uint8Array ? bytes : new Uint8Array(bytes);
  const bitLen = msg.length * 8;
  const withPad = new Uint8Array(((msg.length + 9 + 63) >> 6) << 6);
  withPad.set(msg);
  withPad[msg.length] = 0x80;
  const view = new DataView(withPad.buffer);
  view.setUint32(withPad.length - 8, bitLen >>> 0, true);
  view.setUint32(withPad.length - 4, Math.floor(bitLen / 0x100000000), true);
  let a0 = 0x67452301;
  let b0 = 0xefcdab89;
  let c0 = 0x98badcfe;
  let d0 = 0x10325476;
  const rotl = (x, c) => ((x << c) | (x >>> (32 - c))) >>> 0;
  for (let off = 0; off < withPad.length; off += 64) {
    const M = new Uint32Array(16);
    for (let i = 0; i < 16; i += 1) M[i] = view.getUint32(off + i * 4, true);
    let A = a0;
    let B = b0;
    let C = c0;
    let D = d0;
    for (let i = 0; i < 64; i += 1) {
      let F;
      let g;
      if (i < 16) { F = (B & C) | (~B & D); g = i; }
      else if (i < 32) { F = (D & B) | (~D & C); g = (5 * i + 1) % 16; }
      else if (i < 48) { F = B ^ C ^ D; g = (3 * i + 5) % 16; }
      else { F = C ^ (B | ~D); g = (7 * i) % 16; }
      F = (F + A + MD5_K[i] + M[g]) >>> 0;
      A = D;
      D = C;
      C = B;
      B = (B + rotl(F, MD5_S[i])) >>> 0;
    }
    a0 = (a0 + A) >>> 0;
    b0 = (b0 + B) >>> 0;
    c0 = (c0 + C) >>> 0;
    d0 = (d0 + D) >>> 0;
  }
  const out = new Uint8Array(16);
  const ov = new DataView(out.buffer);
  ov.setUint32(0, a0, true);
  ov.setUint32(4, b0, true);
  ov.setUint32(8, c0, true);
  ov.setUint32(12, d0, true);
  return out;
}

/** Castagnoli CRC, the one Cloud Storage calls crc32c. */
export function crc32c(bytes) {
  let crc = 0xffffffff;
  for (const b of bytes) {
    crc ^= b;
    for (let i = 0; i < 8; i += 1) crc = (crc >>> 1) ^ (0x82f63b78 & -(crc & 1));
  }
  return (crc ^ 0xffffffff) >>> 0;
}

export function checksums(bytes) {
  const raw = crc32c(bytes);
  const be = new Uint8Array(4);
  new DataView(be.buffer).setUint32(0, raw, false);
  return { md5: bytesToB64(md5(bytes)), crc32c: bytesToB64(be), size: bytes.length };
}

export async function checksumBlob(blob) {
  const bytes = new Uint8Array(await blob.arrayBuffer());
  return checksums(bytes);
}

export function round1(n) {
  return Math.round(n * 10) / 10;
}
