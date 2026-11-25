import fs from '@ohos.file.fs';
import util from '@ohos.util';
import { BusinessError } from '@ohos.base';
import { Db } from '../db/Db';
import { Meta, TagParser } from '../scanner/TagParser';

const MAX_WRITE_BYTES: number = 500 * 1024 * 1024;
const COPY_BUF_BYTES: number = 1024 * 1024;

export class TagPatch {
  title: string = '';
  artist: string = '';
  album: string = '';
  genre: string = '';
  year: string = '';
  trackNo: number = 0;
}

export class WriteResult {
  ok: boolean = false;
  message: string = '';
  backupPath: string = '';
}

interface BytePatch {
  pos: number;
  bytes: Uint8Array;
}

class RAF {
  private raf: fs.RandomAccessFile;

  constructor(f: fs.File) {
    this.raf = fs.createRandomAccessFileSync(f);
  }

  read(pos: number, len: number): Uint8Array {
    if (len <= 0) {
      return new Uint8Array(0);
    }
    const buf = new ArrayBuffer(len);
    const n = this.readInto(buf, pos, len);
    return new Uint8Array(buf, 0, n);
  }

  readInto(buf: ArrayBuffer, pos: number, len: number): number {
    if (len <= 0) {
      return 0;
    }
    try {
      this.raf.setFilePointer(pos);
      return this.raf.readSync(buf, { offset: 0, length: len });
    } catch (e) {
      return 0;
    }
  }
}

function concat(parts: Uint8Array[]): Uint8Array {
  let len = 0;
  for (const p of parts) {
    len += p.length;
  }
  const out = new Uint8Array(len);
  let pos = 0;
  for (const p of parts) {
    out.set(p, pos);
    pos += p.length;
  }
  return out;
}

function bytesOf(s: string): Uint8Array {
  const enc = new util.TextEncoder();
  return enc.encode(s);
}

function utf8(a: Uint8Array): string {
  const d = new util.TextDecoder();
  return d.decodeWithStream(a);
}

function ascii(a: Uint8Array, o: number, len: number): string {
  let s = '';
  for (let i = 0; i < len && o + i < a.length; i++) {
    s += String.fromCharCode(a[o + i]);
  }
  return s;
}

function u32be(v: number): Uint8Array {
  const b = new Uint8Array(4);
  b[0] = (v >>> 24) & 0xff;
  b[1] = (v >>> 16) & 0xff;
  b[2] = (v >>> 8) & 0xff;
  b[3] = v & 0xff;
  return b;
}

function u32le(v: number): Uint8Array {
  const b = new Uint8Array(4);
  b[0] = v & 0xff;
  b[1] = (v >>> 8) & 0xff;
  b[2] = (v >>> 16) & 0xff;
  b[3] = (v >>> 24) & 0xff;
  return b;
}

function syncsafe(v: number): Uint8Array {
  const b = new Uint8Array(4);
  b[0] = (v >>> 21) & 0x7f;
  b[1] = (v >>> 14) & 0x7f;
  b[2] = (v >>> 7) & 0x7f;
  b[3] = v & 0x7f;
  return b;
}

function readSyncsafe(a: Uint8Array, o: number): number {
  return ((a[o] & 0x7f) << 21) | ((a[o + 1] & 0x7f) << 14) | ((a[o + 2] & 0x7f) << 7) | (a[o + 3] & 0x7f);
}

function readBe32(a: Uint8Array, o: number): number {
  return a[o] * 16777216 + (a[o + 1] << 16) + (a[o + 2] << 8) + a[o + 3];
}

function readLe32(a: Uint8Array, o: number): number {
  return a[o] + (a[o + 1] << 8) + (a[o + 2] << 16) + a[o + 3] * 16777216;
}

function writeBytes(fd: number, data: Uint8Array): void {
  if (data.length == 0) {
    return;
  }
  const ab = new ArrayBuffer(data.length);
  new Uint8Array(ab).set(data);
  fs.writeSync(fd, ab, { offset: 0, length: data.length });
}

function applyPatches(buf: Uint8Array, absPos: number, patches: BytePatch[]): void {
  for (const p of patches) {
    const start = p.pos - absPos;
    if (start >= 0 && start + p.bytes.length <= buf.length) {
      buf.set(p.bytes, start);
    }
  }
}

function copyRange(r: RAF, dstFd: number, from: number, to: number,
  patches: BytePatch[]): void {
  if (to <= from) {
    return;
  }
  const buf = new ArrayBuffer(COPY_BUF_BYTES);
  let pos = from;
  while (pos < to) {
    const len = Math.min(COPY_BUF_BYTES, to - pos);
    const n = r.readInto(buf, pos, len);
    if (n <= 0) {
      break;
    }
    const part = new Uint8Array(buf, 0, n);
    applyPatches(part, pos, patches);
    writeBytes(dstFd, part);
    pos += n;
  }
}

function patchBe32(pos: number, value: number): BytePatch {
  return { pos: pos, bytes: u32be(value) };
}

// ---------------- FLAC ----------------

class FlacBlock {
  headerPos: number = 0;
  type: number = 0;
  dataStart: number = 0;
  dataEnd: number = 0;
  last: boolean = false;
}

function buildVorbisComment(vendor: Uint8Array, desired: Map<string, string>): Uint8Array {
  const entries: string[] = [];
  let p = 0;
  while (p + 4 <= vendor.length) {
    const l = readLe32(vendor, p);
    p += 4;
    if (l < 0 || p + l > vendor.length) {
      break;
    }
    const kv = utf8(vendor.subarray(p, p + l));
    p += l;
    const eq = kv.indexOf('=');
    if (eq <= 0) {
      continue;
    }
    const key = kv.substring(0, eq).toUpperCase();
    const val = desired.has(key) ? desired.get(key)! : kv.substring(eq + 1);
    entries.push(`${key}=${val}`);
  }
  desired.forEach((v: string, k: string) => {
    let found = false;
    for (const e of entries) {
      if (e.startsWith(`${k}=`)) {
        found = true;
        break;
      }
    }
    if (!found) {
      entries.push(`${k}=${v}`);
    }
  });
  const parts: Uint8Array[] = [u32le(vendor.length), vendor, u32le(entries.length)];
  for (const e of entries) {
    const b = bytesOf(e);
    parts.push(u32le(b.length));
    parts.push(b);
  }
  return concat(parts);
}

class RangeEdit {
  start: number = 0;
  end: number = 0;
  bytes: Uint8Array = new Uint8Array(0);
}

// FLAC PICTURE block（type 6）：front cover / MIME / 描述 / 尺寸 / 颜色 / 图像长度 / 图像
function buildPictureBlock(cover: Uint8Array, mime: string, last: boolean): Uint8Array {
  const mimeBytes = bytesOf(mime);
  const data = concat([
    u32be(3), u32be(mimeBytes.length), mimeBytes,
    u32be(0), u32be(0), u32be(0), u32be(0), u32be(0),
    u32be(cover.length), cover
  ]);
  const h = new Uint8Array(4);
  h[0] = (last ? 0x80 : 0) | 0x06;
  h[1] = (data.length >>> 16) & 0xff;
  h[2] = (data.length >>> 8) & 0xff;
  h[3] = data.length & 0xff;
  return concat([h, data]);
}

function writeFlac(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const head = r.read(0, 4);
  if (ascii(head, 0, 4) != 'fLaC') {
    throw new Error('FLAC 头校验失败');
  }
  const blocks: FlacBlock[] = [];
  let pos = 4;
  while (pos + 4 <= size) {
    const h = r.read(pos, 4);
    if (h.length < 4) {
      break;
    }
    const last = (h[0] & 0x80) != 0;
    const type = h[0] & 0x7f;
    const len = (h[1] << 16) | (h[2] << 8) | h[3];
    const dataStart = pos + 4;
    if (dataStart + len > size) {
      break;
    }
    const block = new FlacBlock();
    block.headerPos = pos;
    block.type = type;
    block.dataStart = dataStart;
    block.dataEnd = dataStart + len;
    block.last = last;
    blocks.push(block);
    pos = dataStart + len;
    if (last) {
      break;
    }
  }
  const desired = new Map<string, string>();
  if (p.title != '') desired.set('TITLE', p.title);
  if (p.artist != '') desired.set('ARTIST', p.artist);
  if (p.album != '') desired.set('ALBUM', p.album);
  if (p.genre != '') desired.set('GENRE', p.genre);
  if (p.year != '') desired.set('DATE', p.year);
  if (p.trackNo > 0) desired.set('TRACKNUMBER', `${p.trackNo}`);

  const vc = blocks.find((b: FlacBlock) => b.type == 4);
  const pic = blocks.find((b: FlacBlock) => b.type == 6);
  const patches: BytePatch[] = [];
  const edits: RangeEdit[] = [];
  let insertAt: number = -1;
  let insertBytes = new Uint8Array(0);

  if (vc) {
    const old = r.read(vc.dataStart, vc.dataEnd - vc.dataStart);
    const vl = readLe32(old, 0);
    const vendor = old.subarray(4, 4 + vl);
    const vcData = buildVorbisComment(vendor, desired);
    const header = new Uint8Array(4);
    header[0] = (vc.last ? 0x80 : 0) | 0x04;
    header[1] = (vcData.length >>> 16) & 0xff;
    header[2] = (vcData.length >>> 8) & 0xff;
    header[3] = vcData.length & 0xff;
    const edit = new RangeEdit();
    edit.start = vc.headerPos;
    edit.end = vc.dataEnd;
    edit.bytes = concat([header, vcData]);
    edits.push(edit);
  } else {
    const first = blocks.length > 0 ? blocks[0] : undefined;
    insertAt = first ? first.dataEnd : 4;
    const lastBlock = blocks.length > 0 ? blocks[blocks.length - 1] : undefined;
    if (insertAt >= size && lastBlock) {
      const h = r.read(lastBlock.headerPos, 1);
      patches.push({ pos: lastBlock.headerPos, bytes: new Uint8Array([h[0] & 0x7f]) });
    }
    const vcData = buildVorbisComment(new Uint8Array(0), desired);
    const header = new Uint8Array(4);
    header[0] = (insertAt >= size ? 0x80 : 0) | 0x04;
    header[1] = (vcData.length >>> 16) & 0xff;
    header[2] = (vcData.length >>> 8) & 0xff;
    header[3] = vcData.length & 0xff;
    insertBytes = concat([insertBytes, header, vcData]);
  }

  if (cover) {
    if (pic) {
      const edit = new RangeEdit();
      edit.start = pic.headerPos;
      edit.end = pic.dataEnd;
      edit.bytes = buildPictureBlock(cover, mime, pic.last);
      edits.push(edit);
    } else {
      // 插在 Vorbis Comment 之前；若无 VC 则与 VC 一起插在 STREAMINFO 之后
      if (insertAt < 0) {
        insertAt = vc ? vc.headerPos : (blocks.length > 0 ? blocks[0].dataEnd : 4);
      }
      insertBytes = concat([insertBytes, buildPictureBlock(cover, mime, false)]);
    }
  }
  if (insertBytes.length > 0 && insertAt >= 0) {
    const edit = new RangeEdit();
    edit.start = insertAt;
    edit.end = insertAt;
    edit.bytes = insertBytes;
    edits.push(edit);
  }
  edits.sort((a: RangeEdit, b: RangeEdit) => a.start - b.start);
  let cursor = 0;
  for (const edit of edits) {
    copyRange(r, dstFd, cursor, edit.start, patches);
    writeBytes(dstFd, edit.bytes);
    cursor = edit.end;
  }
  copyRange(r, dstFd, cursor, size, patches);
}

// ---------------- MP3 ----------------

interface Id3Frame {
  id: string;
  body: Uint8Array;
}

function decodeId3(body: Uint8Array): string {
  if (body.length < 1) {
    return '';
  }
  const enc = body[0];
  if (enc == 0 || enc == 3) {
    const d = new util.TextDecoder();
    return d.decodeWithStream(body.subarray(1));
  }
  let cs = '';
  for (let i = 1; i + 1 < body.length; i += 2) {
    cs += String.fromCharCode(body[i] | (body[i + 1] << 8));
  }
  return cs.replace(/\0+$/, '');
}

function encodeId3(s: string): Uint8Array {
  // UTF-8（encoding=3），兼容 ID3v2.3 / v2.4
  return concat([new Uint8Array([3]), bytesOf(s)]);
}

function buildId3v1(p: TagPatch): Uint8Array {
  const b = new Uint8Array(128);
  b.set(bytesOf('TAG'), 0);
  const put = (s: string, off: number, len: number): void => {
    const src = bytesOf(s);
    const n = Math.min(len - 1, src.length);
    for (let i = 0; i < n; i++) {
      b[off + i] = src[i];
    }
  };
  put(p.title, 3, 30);
  put(p.artist, 33, 30);
  put(p.album, 63, 30);
  put(p.year, 93, 4);
  if (p.trackNo > 0 && p.trackNo < 256) {
    b[125] = p.trackNo;
  }
  return b;
}

function writeMp3(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  let audioStart = 0;
  let major = 3;
  const frames: Id3Frame[] = [];
  const head = r.read(0, 10);
  if (ascii(head, 0, 3) == 'ID3') {
    major = head[3];
    const tagSize = major >= 3 ? readSyncsafe(head, 6) : readBe32(head, 6);
    audioStart = Math.min(size, 10 + tagSize);
    let pos = 10;
    while (pos + 10 <= audioStart) {
      const fh = r.read(pos, 10);
      if (!/^[A-Z0-9]{4}$/.test(ascii(fh, 0, 4))) {
        break;
      }
      const fsize = major >= 4 ? readSyncsafe(fh, 4) : readBe32(fh, 4);
      if (fsize <= 0 || fsize > 16777216 || pos + 10 + fsize > audioStart + 8) {
        break;
      }
      frames.push({ id: ascii(fh, 0, 4), body: r.read(pos + 10, fsize) });
      pos += 10 + fsize;
    }
  }
  const desired = new Map<string, string>();
  if (p.title != '') desired.set('TIT2', p.title);
  if (p.artist != '') desired.set('TPE1', p.artist);
  if (p.album != '') desired.set('TALB', p.album);
  if (p.genre != '') desired.set('TCON', p.genre);
  if (p.year != '') desired.set('TYER', p.year);
  if (p.trackNo > 0) desired.set('TRCK', `${p.trackNo}`);
  const idMap = ['TIT2', 'TPE1', 'TALB', 'TCON', 'TYER', 'TRCK'];
  for (const id of idMap) {
    const text = desired.get(id);
    if (text == undefined) {
      continue;
    }
    let found = false;
    for (const f of frames) {
      if (f.id == id) {
        f.body = encodeId3(text);
        found = true;
        break;
      }
    }
    if (!found) {
      frames.push({ id: id, body: encodeId3(text) });
    }
  }
  if (cover) {
    const apic = concat([
      new Uint8Array([3]), bytesOf(mime), new Uint8Array([0, 3, 0]), cover
    ]);
    let foundApic = false;
    for (const f of frames) {
      if (f.id == 'APIC') {
        f.body = apic;
        foundApic = true;
        break;
      }
    }
    if (!foundApic) {
      frames.push({ id: 'APIC', body: apic });
    }
  }
  const frameParts: Uint8Array[] = [];
  let tagBodyLen = 0;
  for (const f of frames) {
    const h = concat([bytesOf(f.id), u32be(f.body.length), new Uint8Array([0, 0])]);
    frameParts.push(h);
    frameParts.push(f.body);
    tagBodyLen += h.length + f.body.length;
  }
  const id3 = concat([bytesOf('ID3'), new Uint8Array([3, 0]), syncsafe(tagBodyLen), concat(frameParts)]);
  const tail = r.read(Math.max(0, size - 128), 128);
  const hasId3v1 = ascii(tail, 0, 3) == 'TAG';
  const audioEnd = hasId3v1 ? size - 128 : size;
  writeBytes(dstFd, id3);
  copyRange(r, dstFd, audioStart, audioEnd, []);
  if (hasId3v1) {
    const v1 = buildId3v1(p);
    // 仅在提供了对应字段时覆盖，否则保留原 ID3v1 值
    const old = tail;
    if (p.title == '') v1.set(old.subarray(3, 33), 3);
    if (p.artist == '') v1.set(old.subarray(33, 63), 33);
    if (p.album == '') v1.set(old.subarray(63, 93), 63);
    if (p.year == '') v1.set(old.subarray(93, 97), 93);
    if (p.trackNo == 0) v1[125] = old[125];
    v1.set(old.subarray(97, 125), 97);
    v1[125] = p.trackNo > 0 && p.trackNo < 256 ? p.trackNo : old[125];
    v1.set(old.subarray(126, 128), 126);
    writeBytes(dstFd, v1);
  }
}

// ---------------- M4A / MP4 ----------------

interface Mp4Atom {
  start: number;
  size: number;
  type: number;
  headerLen: number;
}

function atomType(s: string): number {
  return s.charCodeAt(0) * 16777216 + s.charCodeAt(1) * 65536 + s.charCodeAt(2) * 256 + s.charCodeAt(3);
}

function findAtom(r: RAF, start: number, end: number, type: number): Mp4Atom | undefined {
  let pos = start;
  while (pos + 8 <= end) {
    const h = r.read(pos, 8);
    if (h.length < 8) {
      break;
    }
    const size = readBe32(h, 0);
    const t = readBe32(h, 4);
    const headerLen = size == 1 ? 16 : 8;
    if (size < headerLen || pos + size > end) {
      break;
    }
    if (t == type) {
      return { start: pos, size: size, type: t, headerLen: headerLen };
    }
    pos += size;
  }
  return undefined;
}

function buildIlstChild(name: string, text: string): Uint8Array {
  const textBytes = bytesOf(text);
  const data = concat([new Uint8Array(16), textBytes]);
  const inner = concat([u32be(data.length), bytesOf('data'), data]);
  return concat([u32be(inner.length + 8), bytesOf(name), inner]);
}

function writeM4a(dstFd: number, r: RAF, size: number, p: TagPatch,
  cover?: Uint8Array, mime: string = 'image/jpeg'): void {
  const moov = findAtom(r, 0, size, atomType('moov'));
  if (!moov) {
    throw new Error('M4A 缺少 moov');
  }
  const moovEnd = moov.start + moov.size;
  const udta = findAtom(r, moov.start + moov.headerLen, moovEnd, atomType('udta'));
  if (!udta) {
    throw new Error('M4A 缺少 udta');
  }
  const udtaEnd = udta.start + udta.size;
  const meta = findAtom(r, udta.start + udta.headerLen, udtaEnd, atomType('meta'));
  if (!meta) {
    throw new Error('M4A 缺少 meta');
  }
  const ilst = findAtom(r, meta.start + meta.headerLen + 4, meta.start + meta.size, atomType('ilst'));
  if (!ilst) {
    throw new Error('M4A 缺少 ilst');
  }
  const ilstEnd = ilst.start + ilst.size;

  // 保留非文本原子（封面等），替换/追加文本原子
  const keep: Uint8Array[] = [];
  let pos = ilst.start + 8;
  const names = ['©nam', '©ART', '©alb', '©day', '©gen', 'covr'];
  while (pos + 8 <= ilstEnd) {
    const h = r.read(pos, 8);
    const childSize = readBe32(h, 0);
    if (childSize < 8 || pos + childSize > ilstEnd) {
      break;
    }
    const name = ascii(h, 4, 4);
    if (names.indexOf(name) < 0) {
      keep.push(r.read(pos, childSize));
    }
    pos += childSize;
  }
  const values: string[] = [p.title, p.artist, p.album, p.year, p.genre];
  const children: Uint8Array[] = [];
  for (let i = 0; i < names.length; i++) {
    if (values[i] != '') {
      children.push(buildIlstChild(names[i], values[i]));
    }
  }
  for (const b of keep) {
    children.push(b);
  }
  if (cover) {
    const dataType: number = mime == 'image/png' ? 14 : 13;
    const data = concat([new Uint8Array([0, 0, 0, dataType, 0, 0, 0, 0]), cover]);
    const inner = concat([u32be(data.length + 8), bytesOf('data'), data]);
    children.push(concat([u32be(inner.length + 8), bytesOf('covr'), inner]));
  }
  const body = concat(children);
  const newIlst = concat([u32be(body.length + 8), bytesOf('ilst'), body]);
  const delta = newIlst.length - ilst.size;
  const patches: BytePatch[] = [];
  if (delta != 0) {
    patches.push(patchBe32(moov.start, moov.size + delta));
    patches.push(patchBe32(udta.start, udta.size + delta));
    patches.push(patchBe32(meta.start, meta.size + delta));
  }
  copyRange(r, dstFd, 0, ilst.start, patches);
  writeBytes(dstFd, newIlst);
  copyRange(r, dstFd, ilstEnd, size, []);
}

// ---------------- 入口 ----------------

function extOf(path: string): string {
  const dot = path.lastIndexOf('.');
  return dot >= 0 ? path.substring(dot).toLowerCase() : '';
}

export class TagWriter {
  static async write(path: string, patch: TagPatch): Promise<WriteResult> {
    const res = new WriteResult();
    const ext = extOf(path);
    if (ext != '.flac' && ext != '.mp3' && ext != '.m4a') {
      res.message = `不支持写入的格式：${ext}`;
      return res;
    }
    const st = fs.statSync(path);
    if (st.size > MAX_WRITE_BYTES) {
      res.message = '文件超过 500MB，按策略拒绝写入';
      return res;
    }
    const tmp = `${path}.seqaudio.tmp`;
    const old = `${path}.seqaudio.old`;
    const keepBackup = (await Db.getSetting('tag_write_backup', '1')) != '0';
    const bak = `${path}.bak`;
    const src = fs.openSync(path, fs.OpenMode.READ_ONLY);
    let dstFile: fs.File | undefined = undefined;
    try {
      const before = TagParser.parse(src, ext, st.size);
      dstFile = fs.openSync(tmp, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      const r = new RAF(src);
      if (ext == '.flac') {
        writeFlac(dstFile.fd, r, st.size, patch);
      } else if (ext == '.mp3') {
        writeMp3(dstFile.fd, r, st.size, patch);
      } else {
        writeM4a(dstFile.fd, r, st.size, patch);
      }
      fs.closeSync(dstFile);
      dstFile = undefined;
      // 写后校验：可解析 + 时长差异 ≤1s + 目标字段回读一致
      const tmpFile = fs.openSync(tmp, fs.OpenMode.READ_ONLY);
      try {
        const after = TagParser.parse(tmpFile, ext, st.size);
        if (Math.abs(after.durationMs - before.durationMs) > 1000) {
          throw new Error('写后时长偏差超过 1s，已回滚');
        }
        const check = (want: string, got: string): void => {
          if (want != '' && got != want) {
            throw new Error(`写后回读不一致：期望 ${want}，实际 ${got}`);
          }
        };
        check(patch.title, after.title);
        check(patch.artist, after.artist);
        check(patch.album, after.album);
        if (patch.year != '') {
          const wantYear = patch.year.match(/\d{4}/);
          const gotYear = after.year.match(/\d{4}/);
          if (wantYear && (!gotYear || wantYear[0] != gotYear[0])) {
            throw new Error(`写后年份回读不一致：期望 ${patch.year}，实际 ${after.year}`);
          }
        }
      } finally {
        fs.closeSync(tmpFile);
      }
      // 原子替换：原文件改名 → tmp 改名；失败则从备份/旧文件恢复
      const removeIfExists = (p: string): void => {
        try {
          fs.unlinkSync(p);
        } catch (e) {
          // ignore
        }
      };
      removeIfExists(keepBackup ? bak : old);
      fs.renameSync(path, keepBackup ? bak : old);
      try {
        fs.renameSync(tmp, path);
      } catch (e) {
        fs.renameSync(keepBackup ? bak : old, path);
        const err = e as BusinessError;
        throw new Error(err.message ? err.message : '原子替换失败');
      }
      if (!keepBackup) {
        removeIfExists(old);
      } else {
        res.backupPath = bak;
      }
      res.ok = true;
      res.message = keepBackup ? '写入成功（已保留 .bak）' : '写入成功';
    } catch (e) {
      const err = e as BusinessError;
      res.ok = false;
      res.message = err.message ? err.message : `${e}`;
      // 清理可能残留的 tmp（原文件仍在或已由 rename 恢复）
      try {
        fs.unlinkSync(tmp);
      } catch (e2) {
        // ignore
      }
    } finally {
      if (dstFile != undefined) {
        try {
          fs.closeSync(dstFile);
        } catch (e) {
          // ignore
        }
      }
      try {
        fs.closeSync(src);
      } catch (e) {
        // ignore
      }
    }
    return res;
  }

  // 封面嵌入：复用同一套全量重写 + 原子替换 + .bak 策略
  static async embedCover(path: string, coverPath: string): Promise<WriteResult> {
    const res = new WriteResult();
    const ext = extOf(path);
    if (ext != '.flac' && ext != '.mp3' && ext != '.m4a') {
      res.message = `不支持写入的格式：${ext}`;
      return res;
    }
    const mime = extOf(coverPath) == '.png' ? 'image/png' : 'image/jpeg';
    const st = fs.statSync(path);
    if (st.size > MAX_WRITE_BYTES) {
      res.message = '文件超过 500MB，按策略拒绝写入';
      return res;
    }
    const cf = fs.openSync(coverPath, fs.OpenMode.READ_ONLY);
    let cover: Uint8Array = new Uint8Array(0);
    try {
      const cs = fs.statSync(coverPath);
      const cab = new ArrayBuffer(cs.size);
      const n = fs.readSync(cf.fd, cab, { offset: 0, length: cs.size });
      cover = new Uint8Array(cab, 0, n);
    } finally {
      fs.closeSync(cf);
    }
    if (cover.length == 0) {
      res.message = '封面文件为空';
      return res;
    }
    const tmp = `${path}.seqaudio.tmp`;
    const old = `${path}.seqaudio.old`;
    const bak = `${path}.bak`;
    const keepBackup = (await Db.getSetting('tag_write_backup', '1')) != '0';
    const src = fs.openSync(path, fs.OpenMode.READ_ONLY);
    let dstFile: fs.File | undefined = undefined;
    try {
      const before = TagParser.parse(src, ext, st.size);
      dstFile = fs.openSync(tmp, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
      const r = new RAF(src);
      const patch = new TagPatch();
      if (ext == '.flac') {
        writeFlac(dstFile.fd, r, st.size, patch, cover, mime);
      } else if (ext == '.mp3') {
        writeMp3(dstFile.fd, r, st.size, patch, cover, mime);
      } else {
        writeM4a(dstFile.fd, r, st.size, patch, cover, mime);
      }
      fs.closeSync(dstFile);
      dstFile = undefined;
      const tmpFile = fs.openSync(tmp, fs.OpenMode.READ_ONLY);
      try {
        const after = TagParser.parse(tmpFile, ext, st.size);
        if (Math.abs(after.durationMs - before.durationMs) > 1000) {
          throw new Error('写后时长偏差超过 1s，已回滚');
        }
      } finally {
        fs.closeSync(tmpFile);
      }
      const removeIfExists = (p: string): void => {
        try {
          fs.unlinkSync(p);
        } catch (e) {
          // ignore
        }
      };
      removeIfExists(keepBackup ? bak : old);
      fs.renameSync(path, keepBackup ? bak : old);
      try {
        fs.renameSync(tmp, path);
      } catch (e) {
        fs.renameSync(keepBackup ? bak : old, path);
        const err = e as BusinessError;
        throw new Error(err.message ? err.message : '原子替换失败');
      }
      if (!keepBackup) {
        removeIfExists(old);
      } else {
        res.backupPath = bak;
      }
      res.ok = true;
      res.message = keepBackup ? '封面已嵌入（保留 .bak）' : '封面已嵌入';
    } catch (e) {
      const err = e as BusinessError;
      res.ok = false;
      res.message = err.message ? err.message : `${e}`;
      try {
        fs.unlinkSync(tmp);
      } catch (e2) {
        // ignore
      }
    } finally {
      if (dstFile != undefined) {
        try {
          fs.closeSync(dstFile);
        } catch (e) {
          // ignore
        }
      }
      try {
        fs.closeSync(src);
      } catch (e) {
        // ignore
      }
    }
    return res;
  }
}
