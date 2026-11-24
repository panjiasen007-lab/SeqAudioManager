import { Track } from '../model/Track';

function pad2(n: number): string {
  return n < 10 ? `0${n}` : `${n}`;
}

export function fmtDuration(ms: number): string {
  if (ms <= 0) {
    return '--:--';
  }
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) {
    return `${h}:${pad2(m)}:${pad2(s)}`;
  }
  return `${m}:${pad2(s)}`;
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) {
    return `${bytes} B`;
  }
  if (bytes < 1048576) {
    return `${(bytes / 1024).toFixed(1)} KB`;
  }
  if (bytes < 1073741824) {
    return `${(bytes / 1048576).toFixed(1)} MB`;
  }
  return `${(bytes / 1073741824).toFixed(2)} GB`;
}

// 规格徽标：FLAC · 24bit/96k 之类
export function fmtSpec(t: Track): string {
  const parts: string[] = [];
  parts.push(t.ext.replace('.', '').toUpperCase());
  if (t.bitDepth > 0 && t.sampleRate > 0) {
    const khz = (t.sampleRate / 1000).toFixed(1).replace('.0', '');
    parts.push(`${t.bitDepth}bit/${khz}k`);
  } else if (t.sampleRate > 0) {
    parts.push(`${(t.sampleRate / 1000).toFixed(1)}kHz`);
  }
  return parts.join(' · ');
}
