import fs from '@ohos.file.fs';
import util from '@ohos.util';

// 从文件 fd 中解析音频元数据（纯 ArkTS 实现，覆盖 FLAC / MP3 / WAV / M4A）
export class Meta {
  title: string = '';
  artist: string = '';
  album: string = '';
  genre: string = '';
  year: string = '';
  trackNo: number = 0;
  durationMs: number = 0;
  sampleRate: number = 0;
  channels: number = 0;
  bitDepth: number = 0;
  bitrateKbps: number = 0;
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
    } else if (key == 'GENRE') {
      m.genre = val;
    } else if (key == 'DATE' || key == 'YEAR') {
      const ym = val.match(/\d{4}/);
      m.year = ym ? ym[0] : val;
    } else if (key == 'TRACKNUMBER') {
      m.trackNo = toInt(val);
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
    const tagSize = major >= 4 ? syncsafe(h, 6) : u32be(h, 6);
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
        if (id == 'TIT2' || id == 'TPE1' || id == 'TALB' || id == 'TCON' ||
          id == 'TYER' || id == 'TDRC' || id == 'TRCK') {
          const body = r.read(fsize, pos + 10);
          const txt = decodeId3Text(body);
          if (id == 'TIT2') {
            m.title = txt;
          } else if (id == 'TPE1') {
            m.artist = txt;
          } else if (id == 'TALB') {
            m.album = txt;
          } else if (id == 'TCON') {
            m.genre = txt;
          } else if (id == 'TYER' || id == 'TDRC') {
            const ym = txt.match(/\d{4}/);
            m.year = ym ? ym[0] : txt;
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
    } else if (cid == 'data') {
      if (byteRate > 0) {
        m.durationMs = Math.round(clen * 1000 / byteRate);
        m.bitrateKbps = Math.round(byteRate * 8 / 1000);
      }
      break;
    }
    pos += 8 + clen + (clen & 1);
  }
}

// ---------------- M4A / MP4 ----------------

const BOX_MOOV = cc('moov');
const BOX_MVHD = cc('mvhd');
const BOX_UDTA = cc('udta');
const BOX_META = cc('meta');
const BOX_ILST = cc('ilst');
const BOX_DATA = cc('data');
const TAG_NAM = cc('©nam');
const TAG_ART = cc('©ART');
const TAG_ALB = cc('©alb');
const TAG_DAY = cc('©day');
const TAG_GEN = cc('©gen');

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
      const txt = cleanTag(dec('utf-8', body, 0, body.length));
      if (tagType == TAG_NAM) {
        m.title = txt;
      } else if (tagType == TAG_ART) {
        m.artist = txt;
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

function walkMp4(r: Reader, start: number, end: number, depth: number, m: Meta): void {
  if (depth > 6) {
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
    } else if (btype == BOX_MOOV || btype == BOX_UDTA || btype == BOX_ILST) {
      walkMp4(r, childStart, childEnd, depth + 1, m);
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
}

// ---------------- 入口 ----------------

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
      }
    } catch (e) {
      // 解析失败时返回已取得的 partial 结果
    }
    return m;
  }
}
