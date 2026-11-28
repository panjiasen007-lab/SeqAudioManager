// 曲目数据模型（与桌面版字段对齐的鸿蒙子集；v1.1 扩展 CUE/健康/歌词/封面/元数据字段）
export interface Track {
  id: number;
  path: string;
  fileName: string;
  ext: string;
  title: string;
  artist: string;
  album: string;
  albumartist: string;
  genre: string;
  year: string;
  trackNo: number;
  discNo: number;
  durationMs: number;
  sampleRate: number;
  channels: number;
  bitDepth: number;
  bitrateKbps: number;
  sizeBytes: number;
  mtimeMs: number;
  // v1.1 起新增
  cuePath: string;
  isCueChild: number;
  cueTrackNo: number;
  cueStartMs: number;
  cueEndMs: number;
  cuePregapMs: number;
  replayGain: number;
  healthStatus: string;
  healthDetail: string;
  searchBlob: string;
  lyrics: string;
  coverBlobPath: string;
  metaSource: string;
  metaCheckedAt: number;
  // v1.3 起新增：真假无损 / 频谱 / 联网匹配
  verdict: string;
  verdictConfidence: number;
  verdictReason: string;
  analyzedAt: number;
  matchConfidence: number;
}

export const AUDIO_EXTS: string[] = ['.flac', '.mp3', '.wav', '.m4a', '.ogg', '.opus', '.aac',
  '.aiff', '.aif', '.tta', '.wv', '.tak', '.dsf', '.dff', '.ape', '.iso'];
export const CUE_EXTS: string[] = ['.cue'];

export function emptyTrack(): Track {
  return {
    id: 0,
    path: '',
    fileName: '',
    ext: '',
    title: '',
    artist: '',
    album: '',
    albumartist: '',
    genre: '',
    year: '',
    trackNo: 0,
    discNo: 0,
    durationMs: 0,
    sampleRate: 0,
    channels: 0,
    bitDepth: 0,
    bitrateKbps: 0,
    sizeBytes: 0,
    mtimeMs: 0,
    cuePath: '',
    isCueChild: 0,
    cueTrackNo: 0,
    cueStartMs: 0,
    cueEndMs: 0,
    cuePregapMs: 0,
    replayGain: 0,
    healthStatus: 'ok',
    healthDetail: '',
    searchBlob: '',
    lyrics: '',
    coverBlobPath: '',
    metaSource: '',
    metaCheckedAt: 0,
    verdict: '',
    verdictConfidence: 0,
    verdictReason: '',
    analyzedAt: 0,
    matchConfidence: 0
  };
}

export function refreshSearchBlob(t: Track): string {
  const parts: string[] = [t.title, t.artist, t.album, t.albumartist, t.fileName, t.genre, t.year];
  const out: string[] = [];
  for (const p of parts) {
    if (p.trim() != '') {
      out.push(p.trim());
    }
  }
  t.searchBlob = out.join(' ').toLowerCase();
  return t.searchBlob;
}
