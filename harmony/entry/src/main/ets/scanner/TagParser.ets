import fs from '@ohos.file.fs';
import util from '@ohos.util';

// 从文件 fd 中解析音频元数据（纯 ArkTS 实现，覆盖 FLAC / MP3 / WAV / M4A）
export class PictureBytes {
  bytes: Uint8Array = new Uint8Array(0);
  mime: string = '';
}

export class Meta {
  title: string = '';
  artist: string = '';
  album: string = '';
  albumartist: string = '';
  genre: string = '';
  year: string = '';
  trackNo: number = 0;
  discNo: number = 0;
  durationMs: number = 0;
  sampleRate: number = 0;
  channels: number = 0;
  bitDepth: number = 0;
  bitrateKbps: number = 0;
  healthHint: string = '';
}

class Reader {
  private raf: fs.RandomAccessFile;

  constructor(f: fs.File) {
    this.raf = fs.createRandomAccessFileSync(f);
  }

  read(size: number, pos: number): Uint8Array {
    if (size <= 0) {
      return new Uint8Array(0);
    }
    const buf = new ArrayBuffer(size);
    let n = 0;
    try {
      this.raf.setFilePointer(pos);
      n = this.raf.readSync(buf, { offset: 0, length: size });
    } catch (e) {
      return new Uint8Array(0);
    }
    return new Uint8Array(buf, 0, n);
  }
}

function u16le(a: Uint8Array, o: number): number {
  return (a[o] | (a[o + 1] << 8)) & 0xffff;
}

function u32be(a: Uint8Array, o: number): number {
  return a[o] * 16777216 + (a[o + 1] << 16) + (a[o + 2] << 8) + a[o + 3];
}

function u32le(a: Uint8Array, o: number): number {
  return a[o] + (a[o + 1] << 8) + (a[o + 2] << 16) + a[o + 3] * 16777216;
}

function u64be(a: Uint8Array, o: number): number {
  return u32be(a, o) * 4294967296 + u32be(a, o + 4);
}

function u16be(a: Uint8Array, o: number): number {
  return ((a[o] << 8) | a[o + 1]) & 0xffff;
}

// AIFF 的 80 位 IEEE 扩展精度采样率
function ext80be(a: Uint8Array, o: number): number {
  const se = ((a[o] & 0x7f) << 8) | a[o + 1];
  if (se == 0) {
    return 0;
  }
  const mant = u32be(a, o + 2) * 4294967296 + u32be(a, o + 6);
  return mant * Math.pow(2, se - 16383 - 63);
}

function syncsafe(a: Uint8Array, o: number): number {
  return ((a[o] & 0x7f) << 21) | ((a[o + 1] & 0x7f) << 14) | ((a[o + 2] & 0x7f) << 7) | (a[o + 3] & 0x7f);
}

function ascii(a: Uint8Array, o: number, len: number): string {
  let s = '';
  for (let i = 0; i < len && o + i < a.length; i++) {
    s += String.fromCharCode(a[o + i]);
  }
  return s;
}

function cc(s: string): number {
  // 4 字符 -> 无符号 32 位（用于 box type 比较）
  return s.charCodeAt(0) * 16777216 + s.charCodeAt(1) * 65536 + s.charCodeAt(2) * 256 + s.charCodeAt(3);
}

const decoders = new Map<string, util.TextDecoder>();

function dec(enc: string, a: Uint8Array, s: number, e: number): string {
  if (e <= s) {
    return '';
  }
  let d = decoders.get(enc);
  if (!d) {
    try {
      d = util.TextDecoder.create(enc);
    } catch (err) {
      return ascii(a, s, e - s);
    }
    decoders.set(enc, d);
  }
  try {
    return d.decodeWithStream(a.subarray(s, e));
  } catch (err) {
    return ascii(a, s, e - s);
  }
}

function latin1(a: Uint8Array, s: number, e: number): string {
  let out = '';
  for (let i = s; i < e && i < a.length; i++) {
    if (a[i] == 0) {
      break;
    }
    out += String.fromCharCode(a[i]);
  }
  return out;
}

function cleanTag(s: string): string {
  return s.replace(/\0+$/g, '').trim();
}

function toInt(s: string): number {
  const n = parseInt(s);
  return isNaN(n) ? 0 : n;
}

// ---------------- FLAC ----------------

function parseVorbisComment(vc: Uint8Array, m: Meta): void {
  if (vc.length < 8) {
    return;
  }
  const vendorLen = u32le(vc, 0);
  let p = 4 + vendorLen;
  if (p + 4 > vc.length) {
    return;
  }
  const count = u32le(vc, p);
  p += 4;
  for (let i = 0; i < count && p + 4 <= vc.length; i++) {
    const l = u32le(vc, p);
    p += 4;
    if (p + l > vc.length) {
      break;
    }
    const kv = dec('utf-8', vc, p, p + l);
    p += l;
    const eq = kv.indexOf('=');
    if (eq <= 0) {
      continue;
    }
    const key = kv.substring(0, eq).toUpperCase();
    const val = cleanTag(kv.substring(eq + 1));
    if (key == 'TITLE') {
      m.title = val;
    } else if (key == 'ARTIST') {
      m.artist = val;
    } else if (key == 'ALBUM') {
      m.album = val;
    } else if (key == 'ALBUMARTIST' || key == 'ALBUM ARTIST') {
      m.albumartist = val;
    } else if (key == 'GENRE') {
      m.genre = val;
    } else if (key == 'DATE' || key == 'YEAR') {
      const ym = val.match(/\d{4}/);
      m.year = ym ? ym[0] : val;
    } else if (key == 'TRACKNUMBER') {
      m.trackNo = toInt(val);
    } else if (key == 'DISCNUMBER' || key == 'DISC') {
      m.discNo = toInt(val.split('/')[0]);
    }
  }
}

function parseFlac(r: Reader, size: number, m: Meta): void {
  const magic = r.read(4, 0);
  if (ascii(magic, 0, 4) != 'fLaC') {
    return;
  }
  let pos = 4;
  while (pos + 4 <= size) {
    const h = r.read(4, pos);
    if (h.length < 4) {
      break;
    }
    const last = (h[0] & 0x80) != 0;
    const type = h[0] & 0x7f;
    const len = (h[1] << 16) | (h[2] << 8) | h[3];
    const dataPos = pos + 4;
    if (type == 0 && len >= 34) {
      const si = r.read(34, dataPos);
      if (si.length >= 34) {
        m.sampleRate = (si[10] << 12) | (si[11] << 4) | (si[12] >> 4);
        m.channels = ((si[12] >> 1) & 0x07) + 1;
        m.bitDepth = (((si[12] & 0x01) << 4) | (si[13] >> 4)) + 1;
        const total = (si[13] & 0x0f) * 4294967296 + u32be(si, 14);
        if (m.sampleRate > 0) {
          m.durationMs = Math.round(total * 1000 / m.sampleRate);
        }
      }
    } else if (type == 4 && len > 0 && len < 8388608) {
      const vcBytes = r.read(len, dataPos);
      parseVorbisComment(vcBytes, m);
    }
    pos = dataPos + len;
    if (last) {
      break;
    }
  }
}

// ---------------- MP3 ----------------

function decodeId3Text(body: Uint8Array): string {
  if (body.length < 2) {
    return '';
  }
  const encByte = body[0];
  let s = 1;
  let enc = 'utf-8';
  if (encByte == 0) {
    return cleanTag(latin1(body, s, body.length));
  } else if (encByte == 1) {
    if (body[1] == 0xff && body[2] == 0xfe) {
      enc = 'utf-16le';
      s = 3;
    } else if (body[1] == 0xfe && body[2] == 0xff) {
      enc = 'utf-16be';
      s = 3;
    } else {
      enc = 'utf-16le';
    }
  } else if (encByte == 2) {
    enc = 'utf-16be';
  } else {
    enc = 'utf-8';
  }
  return cleanTag(dec(enc, body, s, body.length));
}

const MP3_BR_1 = [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320];
const MP3_BR_2 = [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160];
const MP3_SR = [
  [44100, 48000, 32000],
  [22050, 24000, 16000],
  [11025, 12000, 8000]
];

function estimateMp3(r: Reader, audioStart: number, size: number, m: Meta): void {
  const window = Math.min(size - audioStart, 65536);
  if (window < 4) {
    return;
  }
  const buf = r.read(window, audioStart);
  let framePos = -1;
  for (let i = 0; i + 4 < buf.length; i++) {
    if (buf[i] == 0xff && (buf[i + 1] & 0xe0) == 0xe0 && (buf[i + 1] & 0x06) != 0 &&
      (buf[i + 2] & 0xf0) != 0xf0 && (buf[i + 2] & 0x0c) != 0x0c) {
      framePos = i;
      break;
    }
  }
  if (framePos < 0) {
    return;
  }
  const verBits = (buf[framePos + 1] >> 3) & 0x03;
  const layerBits = (buf[framePos + 1] >> 1) & 0x03;
  const brIdx = (buf[framePos + 2] >> 4) & 0x0f;
  const srIdx = (buf[framePos + 2] >> 2) & 0x03;
  const chMode = (buf[framePos + 3] >> 6) & 0x03;
  if (verBits == 1 || layerBits != 1) {
    // 非常见 MPEG Layer III，放弃精确解析
    return;
  }
  const isMpeg1 = verBits == 3;
  const sr = MP3_SR[verBits == 3 ? 0 : (verBits == 2 ? 1 : 2)][srIdx];
  const spf = isMpeg1 ? 1152 : 576;
  m.sampleRate = sr;
  m.channels = chMode == 3 ? 1 : 2;
  // Xing / Info 头
  const sideInfo = isMpeg1 ? (chMode == 3 ? 17 : 32) : (chMode == 3 ? 9 : 17);
  const xoff = framePos + 4 + sideInfo;
  if (xoff + 12 <= buf.length) {
    const tag4 = ascii(buf, xoff, 4);
    if (tag4 == 'Xing' || tag4 == 'Info') {
      const flags = u32be(buf, xoff + 4);
      let frames = 0;
      if ((flags & 0x01) != 0 && xoff + 12 <= buf.length) {
        frames = u32be(buf, xoff + 8);
      }
      if (frames > 0 && sr > 0) {
        m.durationMs = Math.round(frames * spf * 1000 / sr);
        if (m.durationMs > 0) {
          m.bitrateKbps = Math.round(size * 8 / m.durationMs);
        }
        return;
      }
    }
  }
  // CBR 估算
  const table = isMpeg1 ? MP3_BR_1 : MP3_BR_2;
  const kbps = table[brIdx];
  if (kbps > 0) {
    const dataBytes = Math.max(0, size - audioStart - framePos);
    m.durationMs = Math.round(dataBytes * 8 / (kbps * 1000) * 1000);
    m.bitrateKbps = kbps;
  }
}

function parseMp3(r: Reader, size: number, m: Meta): void {
  let audioStart = 0;
  const h = r.read(10, 0);
  if (h.length >= 10 && ascii(h, 0, 3) == 'ID3') {
    const major = h[3];
    // ID3v2 全系（2.2/2.3/2.4）标签头长度都是 syncsafe；此前把 2.3 当大端读，
    // 会把 tagEnd 算成天文数字从而整段跳过，导致写回后回读标题为空。
    const tagSize = syncsafe(h, 6);
    const tagEnd = 10 + tagSize;
    if (tagEnd > 10 && tagEnd < size + 1024) {
      let pos = 10;
      if (major >= 3) {
        // 跳过扩展头（如有）
        const maybeExt = r.read(8, pos);
        if (maybeExt.length >= 8 && ascii(maybeExt, 0, 4) == 'ext ') {
          const extLen = major >= 4 ? syncsafe(maybeExt, 4) : u32be(maybeExt, 4);
          pos += 4 + extLen;
        }
      }
      while (pos + 10 <= tagEnd) {
        const fh = r.read(10, pos);
        if (fh.length < 10) {
          break;
        }
        const id = ascii(fh, 0, 4);
        if (!/^[A-Z0-9]{4}$/.test(id)) {
          break;
        }
        const fsize = major >= 4 ? syncsafe(fh, 4) : u32be(fh, 4);
        if (fsize <= 0 || fsize > 16777216 || pos + 10 + fsize > tagEnd + 16) {
          break;
        }
        if (id == 'TIT2' || id == 'TPE1' || id == 'TPE2' || id == 'TALB' || id == 'TCON' ||
          id == 'TYER' || id == 'TDRC' || id == 'TRCK' || id == 'TPOS') {
          const body = r.read(fsize, pos + 10);
          const txt = decodeId3Text(body);
          if (id == 'TIT2') {
            m.title = txt;
          } else if (id == 'TPE1') {
            m.artist = txt;
          } else if (id == 'TPE2') {
            m.albumartist = txt;
          } else if (id == 'TALB') {
            m.album = txt;
          } else if (id == 'TCON') {
            m.genre = txt;
          } else if (id == 'TYER' || id == 'TDRC') {
            const ym = txt.match(/\d{4}/);
            m.year = ym ? ym[0] : txt;
          } else if (id == 'TPOS') {
            m.discNo = toInt(txt.split('/')[0]);
          } else {
            m.trackNo = toInt(txt.split('/')[0]);
          }
        }
        pos = pos + 10 + fsize;
      }
      audioStart = tagEnd;
    }
  }
  estimateMp3(r, audioStart, size, m);
  // ID3v1 兜底
  if (m.title == '' && size >= 128) {
    const t = r.read(128, size - 128);
    if (t.length >= 128 && ascii(t, 0, 3) == 'TAG') {
      m.title = cleanTag(latin1(t, 3, 33));
      m.artist = cleanTag(latin1(t, 33, 63));
      m.album = cleanTag(latin1(t, 63, 93));
      m.year = cleanTag(latin1(t, 93, 97));
    }
  }
}

// ---------------- WAV ----------------

function parseWav(r: Reader, size: number, m: Meta): void {
  const h = r.read(12, 0);
  if (h.length < 12 || ascii(h, 0, 4) != 'RIFF' || ascii(h, 8, 4) != 'WAVE') {
    return;
  }
  let pos = 12;
  let byteRate = 0;
  while (pos + 8 <= size) {
    const ch = r.read(8, pos);
    if (ch.length < 8) {
      break;
    }
    const cid = ascii(ch, 0, 4);
    const clen = u32le(ch, 4);
    if (cid == 'fmt ' && clen >= 16) {
      const f = r.read(16, pos + 8);
      if (f.length >= 16) {
        m.channels = u16le(f, 2);
        m.sampleRate = u32le(f, 4);
        m.bitDepth = u16le(f, 14);
        byteRate = u32le(f, 8);
      }
    } else if (cid == 'ID3 ' || cid == 'id3 ') {
      // WAV 的标签写成 RIFF 内的 ID3 块（桌面版同样支持）
      readId3At(r, pos + 8, size, m);
    } else if (cid == 'data') {
      if (byteRate > 0) {
        m.durationMs = Math.round(clen * 1000 / byteRate);
        m.bitrateKbps = Math.round(byteRate * 8 / 1000);
      }
      // 继续往后扫：标签可能写在 data 之后的 ID3 块里
    }
    pos += 8 + clen + (clen & 1);
  }
}

// ---------------- AIFF / AIFC ----------------

function parseAiff(r: Reader, size: number, m: Meta): void {
  const h = r.read(12, 0);
  if (h.length < 12 || ascii(h, 0, 4) != 'FORM') {
    return;
  }
  const form = ascii(h, 8, 4);
  if (form != 'AIFF' && form != 'AIFC') {
    return;
  }
  let pos = 12;
  while (pos + 8 <= size) {
    const ch = r.read(8, pos);
    if (ch.length < 8) {
      break;
    }
    const cid = ascii(ch, 0, 4);
    const clen = u32be(ch, 4);
    if (cid == 'COMM' && clen >= 18) {
      const c = r.read(Math.min(clen, 64), pos + 8);
      if (c.length >= 18) {
        m.channels = u16be(c, 0);
        const frames = u32be(c, 2);
        m.bitDepth = u16be(c, 6);
        const rate = ext80be(c, 8);
        if (rate > 0) {
          m.sampleRate = Math.round(rate);
          m.durationMs = Math.round(frames * 1000 / rate);
        }
      }
    } else if (cid == 'ID3 ' || cid == 'id3 ') {
      readId3At(r, pos + 8, size, m);
    }
    pos += 8 + clen + (clen & 1);
  }
  if (m.bitrateKbps <= 0 && m.durationMs > 0) {
    m.bitrateKbps = Math.round(size * 8 / m.durationMs);
  }
}

// ---------------- TTA ----------------

function parseTta(r: Reader, size: number, m: Meta): void {
  const h = r.read(22, 0);
  if (h.length < 22 || ascii(h, 0, 4) != 'TTA1') {
    return;
  }
  m.channels = u16le(h, 6);
  m.bitDepth = u16le(h, 8);
  const rate = u32le(h, 10);
  const samples = u32le(h, 14);
  if (rate > 0) {
    m.sampleRate = rate;
    m.durationMs = Math.round(samples * 1000 / rate);
  }
  if (m.durationMs > 0) {
    m.bitrateKbps = Math.round(size * 8 / m.durationMs);
  }
  parseApeTag(r, size, m);
}

// ---------------- WavPack ----------------

const WV_RATES: number[] = [6000, 8000, 9600, 11025, 12000, 16000, 22050, 24000, 32000, 44100,
  48000, 64000, 88200, 96000, 192000];

function parseWv(r: Reader, size: number, m: Meta): void {
  const h = r.read(32, 0);
  if (h.length < 32 || ascii(h, 0, 4) != 'wvpk') {
    return;
  }
  const totalSamples = u32le(h, 12);
  const flags = u32le(h, 24);
  const bytesPerSample = (flags & 3) + 1;
  const mono = (flags & 4) != 0;
  const rateIdx = (flags >>> 23) & 0x0f;
  const rate = rateIdx < WV_RATES.length ? WV_RATES[rateIdx] : 0;
  m.channels = mono ? 1 : 2;
  m.bitDepth = bytesPerSample * 8;
  m.sampleRate = rate;
  if (totalSamples > 0 && totalSamples != 0xffffffff && rate > 0) {
    m.durationMs = Math.round(totalSamples * 1000 / rate);
  }
  if (m.durationMs > 0) {
    m.bitrateKbps = Math.round(size * 8 / m.durationMs);
  }
  parseApeTag(r, size, m);
}

// ---------------- Ogg（Vorbis / Opus） ----------------

function oggLastGranule(r: Reader, size: number): number {
  const len = Math.min(65536, size);
  if (len < 27) {
    return 0;
  }
  const tail = r.read(len, size - len);
  for (let i = tail.length - 14; i >= 0; i--) {
    if (tail[i] == 0x4f && tail[i + 1] == 0x67 && tail[i + 2] == 0x67 && tail[i + 3] == 0x53) {
      return u64le(tail, i + 6);
    }
  }
  return 0;
}

// 顺序读出前若干个完整 Ogg 包（跨页续包按 lacing 拼接）
function oggPackets(r: Reader, size: number, maxPackets: number): Uint8Array[] {
  const out: Uint8Array[] = [];
  let pos = 0;
  let partial: number[] = [];
  let pageGuard = 0;
  while (pos + 27 <= size && out.length < maxPackets && pageGuard++ < 64) {
    const head = r.read(27, pos);
    if (head.length < 27 || ascii(head, 0, 4) != 'OggS') {
      break;
    }
    const segCount = head[26];
    const lacing = r.read(segCount, pos + 27);
    if (lacing.length < segCount) {
      break;
    }
    let dataLen = 0;
    for (let i = 0; i < segCount; i++) {
      dataLen += lacing[i];
    }
    const page = r.read(dataLen, pos + 27 + segCount);
    let off = 0;
    for (let i = 0; i < segCount && out.length < maxPackets; i++) {
      const l = lacing[i];
      for (let j = 0; j < l && off + j < page.length; j++) {
        partial.push(page[off + j]);
      }
      off += l;
      if (l < 255) {
        out.push(new Uint8Array(partial));
        partial = [];
      }
    }
    pos += 27 + segCount + dataLen;
  }
  return out;
}

function parseOgg(r: Reader, size: number, m: Meta): void {
  const head = r.read(64, 0);
  if (head.length < 28 || ascii(head, 0, 4) != 'OggS') {
    return;
  }
  const pkts = oggPackets(r, size, 3);
  if (pkts.length < 1) {
    return;
  }
  const pkt = pkts[0];
  if (pkt.length >= 30 && pkt[0] == 0x01 && ascii(pkt, 1, 6) == 'vorbis') {
    m.channels = pkt[11];
    const rate = u32le(pkt, 12);
    const nominal = u32le(pkt, 20);
    m.sampleRate = rate;
    if (nominal > 0) {
      m.bitrateKbps = Math.round(nominal / 1000);
    }
    if (pkts.length >= 2 && pkts[1].length > 7 && ascii(pkts[1], 1, 6) == 'vorbis') {
      parseVorbisComment(pkts[1].subarray(7), m);
    }
    const granule = oggLastGranule(r, size);
    if (granule > 0 && rate > 0) {
      m.durationMs = Math.round(granule * 1000 / rate);
    }
  } else if (pkt.length >= 19 && ascii(pkt, 0, 8) == 'OpusHead') {
    m.channels = pkt[9];
    const preSkip = u16le(pkt, 10);
    const inputRate = u32le(pkt, 12);
    m.sampleRate = inputRate > 0 ? inputRate : 48000;
    if (pkts.length >= 2 && pkts[1].length >= 8 && ascii(pkts[1], 0, 8) == 'OpusTags') {
      parseVorbisComment(pkts[1].subarray(8), m);
    }
    const granule = oggLastGranule(r, size);
    if (granule > preSkip) {
      m.durationMs = Math.round((granule - preSkip) * 1000 / 48000);
    }
  } else {
    return;
  }
  if (m.bitrateKbps <= 0 && m.durationMs > 0) {
    m.bitrateKbps = Math.round(size * 8 / m.durationMs);
  }
}

// ---------------- TAK ----------------

// TAK 的 STREAMINFO 是 LSB-first 位流
function bitsLE(a: Uint8Array, bitPos: number, count: number): number {
  let v = 0;
  for (let i = 0; i < count; i++) {
    const p = bitPos + i;
    const byte = p >> 3;
    if (byte >= a.length) {
      break;
    }
    v += ((a[byte] >> (p & 7)) & 1) * Math.pow(2, i);
  }
  return v;
}

function parseTak(r: Reader, size: number, m: Meta): void {
  const h = r.read(8, 0);
  if (h.length < 8 || ascii(h, 0, 4) != 'tBaK') {
    return;
  }
  let pos = 4;
  let guard = 0;
  while (pos + 4 <= size && guard++ < 64) {
    const bh = r.read(4, pos);
    if (bh.length < 4) {
      break;
    }
    const type = bh[0] & 0x7f;
    const blockSize = bh[1] + (bh[2] << 8) + (bh[3] << 16);
    if (type == 0 || blockSize <= 0) {
      break;
    }
    // STREAMINFO：blockSize 含末尾 3 字节 CRC
    if (type == 1 && blockSize > 3) {
      const data = r.read(Math.min(blockSize - 3, 64), pos + 4);
      if (data.length >= 10) {
        const samples = bitsLE(data, 14, 35);
        const rate = bitsLE(data, 52, 18) + 6000;
        const bps = bitsLE(data, 70, 5) + 8;
        const channels = bitsLE(data, 75, 4) + 1;
        m.sampleRate = rate;
        m.bitDepth = bps;
        m.channels = channels;
        if (rate > 0 && samples > 0) {
          m.durationMs = Math.round(samples * 1000 / rate);
        }
      }
    }
    pos += 4 + blockSize;
  }
  if (m.durationMs > 0) {
    m.bitrateKbps = Math.round(size * 8 / m.durationMs);
  }
  parseApeTag(r, size, m);
}

// ---------------- M4A / MP4 ----------------

const BOX_MOOV = cc('moov');
const BOX_MVHD = cc('mvhd');
const BOX_TRAK = cc('trak');
const BOX_MDIA = cc('mdia');
const BOX_MINF = cc('minf');
const BOX_STBL = cc('stbl');
const BOX_STSD = cc('stsd');
const BOX_UDTA = cc('udta');
const BOX_META = cc('meta');
const BOX_ILST = cc('ilst');
const BOX_DATA = cc('data');
const TAG_NAM = cc('©nam');
const TAG_ART = cc('©ART');
const TAG_AART = cc('aART');
const TAG_ALB = cc('©alb');
const TAG_DAY = cc('©day');
const TAG_GEN = cc('©gen');
const TAG_DISK = cc('disk');

function parseMp4TagData(r: Reader, boxStart: number, boxEnd: number, m: Meta, tagType: number): void {
  let pos = boxStart;
  while (pos + 8 <= boxEnd) {
    const h = r.read(8, pos);
    if (h.length < 8) {
      break;
    }
    const bsize = u32be(h, 0);
    const btype = u32be(h, 4);
    if (bsize < 8 || pos + bsize > boxEnd) {
      break;
    }
    if (btype == BOX_DATA && bsize >= 16) {
      const body = r.read(bsize - 16, pos + 16);
      if (tagType == TAG_DISK) {
        // disk 是二进制：reserved(2) + disc(2) + total(2)
        if (body.length >= 4) {
          m.discNo = (body[2] << 8) | body[3];
        }
        return;
      }
      const txt = cleanTag(dec('utf-8', body, 0, body.length));
      if (tagType == TAG_NAM) {
        m.title = txt;
      } else if (tagType == TAG_ART) {
        m.artist = txt;
      } else if (tagType == TAG_AART) {
        m.albumartist = txt;
      } else if (tagType == TAG_ALB) {
        m.album = txt;
      } else if (tagType == TAG_DAY) {
        const ym = txt.match(/\d{4}/);
        m.year = ym ? ym[0] : txt;
      } else if (tagType == TAG_GEN) {
        m.genre = txt;
      }
      return;
    }
    pos += bsize;
  }
}

// stsd：取第一个音频 sample entry 的声道 / 位深 / 采样率。
// ALAC 的真实位深与采样率在嵌套的 alac magic cookie 里，需要覆盖 sample entry 的字段。
function parseStsd(r: Reader, start: number, end: number, m: Meta): void {
  if (m.sampleRate > 0 || start + 44 > end) {
    return;
  }
  const entryStart = start + 8; // version/flags + entry_count
  const eh = r.read(36, entryStart);
  if (eh.length < 36) {
    return;
  }
  const entrySize = u32be(eh, 0);
  const fmt = ascii(eh, 4, 4);
  if (fmt != 'mp4a' && fmt != 'alac' && fmt != 'aac ' && fmt != 'Opus' && fmt != 'fLaC') {
    return;
  }
  const channels = u16be(eh, 24);
  const sampleBits = u16be(eh, 26);
  const rate = u32be(eh, 32) / 65536;
  if (channels > 0) {
    m.channels = channels;
  }
  if (rate > 0 && rate < 1000000) {
    m.sampleRate = Math.round(rate);
  }
  if (fmt == 'alac') {
    m.bitDepth = sampleBits > 0 && sampleBits <= 32 ? sampleBits : 0;
    let p = entryStart + 36;
    const limit = entryStart + entrySize;
    while (p + 12 <= limit) {
      const bh = r.read(8, p);
      const bs = u32be(bh, 0);
      const bt = ascii(bh, 4, 4);
      if (bs < 12) {
        break;
      }
      if (bt == 'alac') {
        const ck = r.read(24, p + 12); // 跳过 box 头 + version/flags
        if (ck.length >= 24) {
          const depth = ck[5];
          const ch = ck[9];
          const srate = u32be(ck, 20);
          if (depth > 0 && depth <= 32) {
            m.bitDepth = depth;
          }
          if (ch > 0) {
            m.channels = ch;
          }
          if (srate > 0) {
            m.sampleRate = srate;
          }
        }
        break;
      }
      p += bs;
    }
  } else {
    m.bitDepth = 0; // AAC / Opus 等有损编码不显示位深
  }
}

function walkMp4(r: Reader, start: number, end: number, depth: number, m: Meta): void {
  if (depth > 8) {
    return;
  }
  let pos = start;
  while (pos + 8 <= end) {
    const h = r.read(8, pos);
    if (h.length < 8) {
      break;
    }
    const bsize = u32be(h, 0);
    const btype = u32be(h, 4);
    if (bsize < 8 || pos + bsize > end) {
      break;
    }
    const childStart = pos + 8;
    const childEnd = pos + bsize;
    if (btype == TAG_NAM || btype == TAG_ART || btype == TAG_ALB || btype == TAG_DAY || btype == TAG_GEN) {
      parseMp4TagData(r, childStart, childEnd, m, btype);
    } else if (btype == BOX_MOOV || btype == BOX_UDTA || btype == BOX_ILST ||
      btype == BOX_TRAK || btype == BOX_MDIA || btype == BOX_MINF || btype == BOX_STBL) {
      walkMp4(r, childStart, childEnd, depth + 1, m);
    } else if (btype == BOX_STSD) {
      parseStsd(r, childStart, childEnd, m);
    } else if (btype == BOX_META) {
      walkMp4(r, childStart + 4, childEnd, depth + 1, m); // 跳过 version/flags
    } else if (btype == BOX_MVHD) {
      const body = r.read(childEnd - childStart, childStart);
      if (body.length >= 20) {
        const ver = body[0];
        let timescale = 0;
        let dur = 0;
        if (ver == 1 && body.length >= 32) {
          timescale = u32be(body, 20);
          dur = u64be(body, 24);
        } else {
          timescale = u32be(body, 12);
          dur = u32be(body, 16);
        }
        if (timescale > 0) {
          m.durationMs = Math.round(dur * 1000 / timescale);
        }
      }
    }
    pos += bsize;
  }
}

function parseMp4(r: Reader, size: number, m: Meta): void {
  walkMp4(r, 0, size, 0, m);
  if (m.bitrateKbps <= 0 && m.durationMs > 0) {
    m.bitrateKbps = Math.round(size * 8 / m.durationMs);
  }
}

// ---------------- DSF / DFF ----------------

function u64le(a: Uint8Array, o: number): number {
  if (o + 8 > a.length) {
    return 0;
  }
  const lo = u32le(a, o);
  const hi = u32le(a, o + 4);
  return hi * 4294967296 + lo;
}

function readId3At(r: Reader, tagStart: number, size: number, m: Meta): void {
  if (tagStart <= 0 || tagStart + 10 > size) {
    return;
  }
  const h = r.read(10, tagStart);
  if (h.length < 10 || ascii(h, 0, 3) != 'ID3') {
    return;
  }
  const major = h[3];
  const tagSize = syncsafe(h, 6);
  const tagEnd = tagStart + 10 + tagSize;
  let pos = tagStart + 10;
  while (pos + 10 <= tagEnd && pos + 10 <= size) {
    const fh = r.read(10, pos);
    if (fh.length < 10) {
      break;
    }
    const id = ascii(fh, 0, 4);
    if (!/^[A-Z0-9]{4}$/.test(id)) {
      break;
    }
    const fsize = major >= 4 ? syncsafe(fh, 4) : u32be(fh, 4);
    if (fsize <= 0 || fsize > 16777216 || pos + 10 + fsize > tagEnd + 16) {
      break;
    }
    if (id == 'TIT2' || id == 'TPE1' || id == 'TPE2' || id == 'TALB' || id == 'TCON' ||
      id == 'TYER' || id == 'TDRC' || id == 'TRCK' || id == 'TPOS') {
      const body = r.read(fsize, pos + 10);
      const txt = decodeId3Text(body);
      if (id == 'TIT2') {
        m.title = txt;
      } else if (id == 'TPE1') {
        m.artist = txt;
      } else if (id == 'TPE2') {
        m.albumartist = txt;
      } else if (id == 'TALB') {
        m.album = txt;
      } else if (id == 'TCON') {
        m.genre = txt;
      } else if (id == 'TYER' || id == 'TDRC') {
        const ym = txt.match(/\d{4}/);
        m.year = ym ? ym[0] : txt;
      } else if (id == 'TPOS') {
        m.discNo = toInt(txt.split('/')[0]);
      } else {
        m.trackNo = toInt(txt.split('/')[0]);
      }
    }
    pos = pos + 10 + fsize;
  }
}

function parseDsf(r: Reader, size: number, m: Meta): void {
  const h = r.read(92, 0);
  if (h.length < 92 || ascii(h, 0, 4) != 'DSD ') {
    return;
  }
  const metadataPtr = u64le(h, 20);
  if (ascii(h, 28, 4) == 'fmt ') {
    m.channels = u32le(h, 52);
    m.sampleRate = u32le(h, 56);
    const bits = u32le(h, 60);
    m.bitDepth = bits > 0 ? bits : 1;
    const sampleCount = u64le(h, 64);
    if (m.sampleRate > 0 && sampleCount > 0) {
      m.durationMs = Math.round(sampleCount * 1000 / m.sampleRate);
    }
    if (m.bitrateKbps == 0 && m.sampleRate > 0 && m.channels > 0) {
      m.bitrateKbps = Math.round(m.sampleRate * m.channels / 1000);
    }
  }
  if (metadataPtr > 0) {
    readId3At(r, metadataPtr, size, m);
  }
}

function parseDff(r: Reader, size: number, m: Meta): void {
  const h = r.read(16, 0);
  if (h.length < 16 || ascii(h, 0, 4) != 'FRM8') {
    return;
  }
  let pos = 12;
  while (pos + 12 <= size) {
    const ch = r.read(12, pos);
    if (ch.length < 12) {
      break;
    }
    const id = ascii(ch, 0, 4);
    const len = u64be(ch, 4);
    if (len <= 0 || pos + 12 + len > size + 16) {
      break;
    }
    if (id == 'PROP') {
      const prop = r.read(Math.min(len, 4096), pos + 12);
      if (prop.length >= 4 && ascii(prop, 0, 4) == 'SND ') {
        let p = 4;
        while (p + 12 <= prop.length) {
          const sid = ascii(prop, p, 4);
          const slen = u64be(prop, p + 4);
          if (slen < 0 || p + 12 + slen > prop.length) {
            break;
          }
          if (sid == 'FS  ' && slen >= 4) {
            m.sampleRate = u32be(prop, p + 12);
          } else if (sid == 'CHNL' && slen >= 2) {
            m.channels = (prop[p + 12] << 8) | prop[p + 13];
          } else if (sid == 'CMPR' && slen >= 4) {
            const comp = ascii(prop, p + 12, 4);
            if (comp != 'DSD ') {
              m.healthHint = `DFF 压缩格式 ${comp} 暂不支持解码`;
            }
          }
          p += 12 + slen + (slen % 2);
        }
      }
    } else if (id == 'DSD ') {
      if (m.sampleRate > 0 && m.channels > 0) {
        m.durationMs = Math.round(len * 8 * 1000 / (m.sampleRate * m.channels));
      }
      break;
    } else if (id == 'DST ') {
      m.healthHint = 'DFF DST 压缩暂不支持解码';
      break;
    }
    pos += 12 + len + (len % 2);
  }
  if (m.sampleRate > 0) {
    m.bitDepth = 1;
    if (m.bitrateKbps == 0 && m.channels > 0) {
      m.bitrateKbps = Math.round(m.sampleRate * m.channels / 1000);
    }
  }
}

// ---------------- 入口 ----------------

// ---------------- 内嵌封面 ----------------

// FLAC：type 6 的 PICTURE 块
function flacPicture(r: Reader, size: number): PictureBytes | undefined {
  let pos = 4;
  while (pos + 4 <= size) {
    const h = r.read(4, pos);
    if (h.length < 4) {
      break;
    }
    const last = (h[0] & 0x80) != 0;
    const type = h[0] & 0x7f;
    const len = (h[1] << 16) | (h[2] << 8) | h[3];
    const dataPos = pos + 4;
    if (type == 6 && len > 32) {
      const b = r.read(len, dataPos);
      if (b.length > 32) {
        let i = 4;
        const mimeLen = u32be(b, i);
        i += 4;
        const mime = ascii(b, i, Math.min(mimeLen, b.length - i));
        i += mimeLen;
        const descLen = u32be(b, i);
        i += 4 + descLen + 16; // 描述 + 宽/高/色深/色数
        const picLen = u32be(b, i);
        i += 4;
        if (picLen > 0 && i + picLen <= b.length) {
          const pic = new PictureBytes();
          pic.mime = mime;
          pic.bytes = b.subarray(i, i + picLen);
          return pic;
        }
      }
    }
    pos = dataPos + len;
    if (last) {
      break;
    }
  }
  return undefined;
}

// MP3：ID3 里的 APIC 帧
function id3PictureAt(r: Reader, tagStart: number, size: number): PictureBytes | undefined {
  const head = r.read(10, tagStart);
  if (head.length < 10 || ascii(head, 0, 3) != 'ID3') {
    return undefined;
  }
  const major = head[3];
  const tagEnd = Math.min(size, tagStart + 10 + syncsafe(head, 6));
  let pos = tagStart + 10;
  while (pos + 10 <= tagEnd) {
    const fh = r.read(10, pos);
    if (fh.length < 10 || !/^[A-Z0-9]{4}$/.test(ascii(fh, 0, 4))) {
      break;
    }
    const fsize = major >= 4 ? syncsafe(fh, 4) : u32be(fh, 4);
    if (fsize <= 0 || fsize > 16777216) {
      break;
    }
    if (ascii(fh, 0, 4) == 'APIC') {
      const body = r.read(fsize, pos + 10);
      if (body.length > 4) {
        let i = 1;
        let mime = '';
        while (i < body.length && body[i] != 0) {
          mime += String.fromCharCode(body[i]);
          i++;
        }
        i += 2; // 跳过 mime 结束符 + 图片类型
        if (body[0] == 1 || body[0] == 2) {
          while (i + 1 < body.length && !(body[i] == 0 && body[i + 1] == 0)) {
            i += 2;
          }
          i += 2;
        } else {
          while (i < body.length && body[i] != 0) {
            i++;
          }
          i++;
        }
        if (i < body.length) {
          const pic = new PictureBytes();
          pic.mime = mime;
          pic.bytes = body.subarray(i);
          return pic;
        }
      }
    }
    pos += 10 + fsize;
  }
  return undefined;
}

function mp3Picture(r: Reader, size: number): PictureBytes | undefined {
  return id3PictureAt(r, 0, size);
}

// WAV：RIFF 内 ID3 chunk 的 APIC
function wavPicture(r: Reader, size: number): PictureBytes | undefined {
  const h = r.read(12, 0);
  if (h.length < 12 || ascii(h, 0, 4) != 'RIFF' || ascii(h, 8, 4) != 'WAVE') {
    return undefined;
  }
  let pos = 12;
  while (pos + 8 <= size) {
    const ch = r.read(8, pos);
    if (ch.length < 8) {
      break;
    }
    const cid = ascii(ch, 0, 4);
    const clen = u32le(ch, 4);
    if (cid == 'ID3 ' || cid == 'id3 ') {
      return id3PictureAt(r, pos + 8, size);
    }
    pos += 8 + clen + (clen & 1);
  }
  return undefined;
}

// ---------------- APE（Monkey's Audio）----------------

function parseApeTag(r: Reader, size: number, m: Meta): void {
  let end = size;
  if (end >= 128) {
    const id3 = r.read(3, end - 128);
    if (ascii(id3, 0, 3) == 'TAG') {
      end -= 128;
    }
  }
  if (end < 32) {
    return;
  }
  const footer = r.read(32, end - 32);
  if (ascii(footer, 0, 8) != 'APETAGEX') {
    return;
  }
  const tagSize = u32le(footer, 12);
  const count = u32le(footer, 16);
  const flags = u32le(footer, 20);
  const hasHeader = (flags & 0x80000000) != 0;
  // APEv2：tagSize 含 footer、不含 header。带 header 时起点仍是 end - tagSize，
  // 若该处确实是 APETAGEX 头，再向后跳过 32 字节进入条目区。
  let start = end - tagSize;
  let dataLen = tagSize;
  if (hasHeader && start + 32 <= end) {
    const maybeHeader = r.read(8, start);
    if (ascii(maybeHeader, 0, 8) == 'APETAGEX') {
      start += 32;
      dataLen -= 32;
    }
  }
  if (start < 0 || dataLen <= 0 || tagSize > 8 * 1048576) {
    return;
  }
  const data = r.read(dataLen, start);
  let pos = 0;
  for (let i = 0; i < count && pos + 9 <= data.length; i++) {
    const vlen = u32le(data, pos);
    const iflags = u32le(data, pos + 4);
    pos += 8;
    let keyEnd = pos;
    while (keyEnd < data.length && data[keyEnd] != 0) {
      keyEnd++;
    }
    const key = ascii(data, pos, keyEnd - pos).toLowerCase();
    pos = keyEnd + 1;
    if (pos + vlen > data.length) {
      break;
    }
    const valueStart = pos;
    pos += vlen;
    if ((iflags & 6) != 0) {
      // 二进制项（封面等）跳过
      continue;
    }
    const text = cleanTag(dec('utf-8', data, valueStart, valueStart + vlen));
    if (text == '') {
      continue;
    }
    if (key == 'title') {
      m.title = text;
    } else if (key == 'artist') {
      m.artist = text;
    } else if (key == 'album') {
      m.album = text;
    } else if (key == 'album artist' || key == 'albumartist') {
      m.albumartist = text;
    } else if (key == 'genre') {
      m.genre = text;
    } else if (key == 'year' || key == 'date') {
      m.year = text.length > 4 ? text.substring(0, 4) : text;
    } else if (key == 'track') {
      m.trackNo = toInt(text);
    } else if (key == 'disc') {
      m.discNo = toInt(text);
    }
  }
}

function parseApe(r: Reader, size: number, m: Meta): void {
  const h = r.read(80, 0);
  if (h.length < 40 || ascii(h, 0, 4) != 'MAC ') {
    return;
  }
  const version = u16le(h, 4);
  let sampleRate = 0;
  let channels = 0;
  let bits = 16;
  let totalFrames = 0;
  let blocksPerFrame = 0;
  let finalFrameBlocks = 0;
  if (version >= 3980 && h.length >= 76) {
    blocksPerFrame = u32le(h, 56);
    finalFrameBlocks = u32le(h, 60);
    totalFrames = u32le(h, 64);
    bits = u16le(h, 68);
    channels = u16le(h, 70);
    sampleRate = u32le(h, 72);
  } else if (h.length >= 40) {
    // 旧版 APE（<3.98）：固定 9216 样本/帧，无位深字段
    channels = u16le(h, 10);
    sampleRate = u32le(h, 12);
    totalFrames = u32le(h, 24);
    finalFrameBlocks = u32le(h, 28);
    blocksPerFrame = 9216;
    bits = 16;
  }
  if (bits <= 0) {
    bits = 16;
  }
  m.sampleRate = sampleRate;
  m.channels = channels;
  m.bitDepth = bits;
  if (sampleRate > 0 && blocksPerFrame > 0 && totalFrames > 0) {
    const frames = totalFrames > 0 ? totalFrames : 1;
    const samples = (frames - 1) * blocksPerFrame + finalFrameBlocks;
    m.durationMs = Math.round(samples * 1000 / sampleRate);
    if (m.durationMs > 0) {
      m.bitrateKbps = Math.round(size * 8 / m.durationMs);
    }
  }
  parseApeTag(r, size, m);
}

export class TagParser {
  static parse(f: fs.File, ext: string, size: number): Meta {
    const m = new Meta();
    try {
      const r = new Reader(f);
      if (ext == '.flac') {
        parseFlac(r, size, m);
      } else if (ext == '.mp3') {
        parseMp3(r, size, m);
      } else if (ext == '.wav') {
        parseWav(r, size, m);
      } else if (ext == '.m4a' || ext == '.mp4' || ext == '.aac') {
        parseMp4(r, size, m);
      } else if (ext == '.dsf') {
        parseDsf(r, size, m);
      } else if (ext == '.dff') {
        parseDff(r, size, m);
      } else if (ext == '.ape') {
        parseApe(r, size, m);
      } else if (ext == '.ogg' || ext == '.opus') {
        parseOgg(r, size, m);
      } else if (ext == '.aiff' || ext == '.aif') {
        parseAiff(r, size, m);
      } else if (ext == '.tta') {
        parseTta(r, size, m);
      } else if (ext == '.wv') {
        parseWv(r, size, m);
      } else if (ext == '.tak') {
        parseTak(r, size, m);
      }
    } catch (e) {
      // 解析失败时返回已取得的 partial 结果
    }
    return m;
  }

  // 抽取内嵌封面：清空曲库/重扫后 DB 里的封面缓存会丢，靠这个从文件里补回来
  static extractCover(f: fs.File, ext: string, size: number): PictureBytes | undefined {
    try {
      const r = new Reader(f);
      if (ext == '.flac') {
        return flacPicture(r, size);
      }
      if (ext == '.mp3') {
        return mp3Picture(r, size);
      }
      if (ext == '.wav') {
        return wavPicture(r, size);
      }
    } catch (e) {
      // 忽略：当作没有内嵌封面
    }
    return undefined;
  }
}
