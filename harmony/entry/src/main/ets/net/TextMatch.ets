// 文本归一化与相似度：对齐桌面版 core/text.py 的口径。
const PUNCT: RegExp = /[\s\-_·、,，.。!！?？'"“”‘’()（）\[\]【】~～]+/g;

export function normalizeText(text: string): string {
  if (text == '') {
    return '';
  }
  return text.replace(PUNCT, '').toLowerCase();
}

function levenshtein(a: string, b: string): number {
  const n = a.length;
  const m = b.length;
  if (n == 0) {
    return m;
  }
  if (m == 0) {
    return n;
  }
  let prev: number[] = [];
  for (let j = 0; j <= m; j++) {
    prev.push(j);
  }
  for (let i = 1; i <= n; i++) {
    const cur: number[] = [i];
    for (let j = 1; j <= m; j++) {
      const cost = a.charCodeAt(i - 1) == b.charCodeAt(j - 1) ? 0 : 1;
      const v = Math.min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost);
      cur.push(v);
    }
    prev = cur;
  }
  return prev[m];
}

export function similarityStrict(a: string, b: string): number {
  const na = normalizeText(a);
  const nb = normalizeText(b);
  if (na == '' || nb == '') {
    return 0;
  }
  if (na == nb) {
    return 1;
  }
  if (na.includes(nb) || nb.includes(na)) {
    const short = na.length <= nb.length ? na : nb;
    const long = na.length <= nb.length ? nb : na;
    if (short.length >= long.length * 0.55) {
      return 0.92;
    }
  }
  const dist = levenshtein(na, nb);
  return 1 - dist / Math.max(na.length, nb.length);
}

export function similarity(a: string, b: string): number {
  const ratio = similarityStrict(a, b);
  if (ratio <= 0 || ratio >= 1) {
    return ratio;
  }
  const na = normalizeText(a);
  const nb = normalizeText(b);
  if (na.length == nb.length && ratio < 0.85) {
    return 0.85;
  }
  return ratio;
}

function isAscii(text: string): boolean {
  for (let i = 0; i < text.length; i++) {
    if (text.charCodeAt(i) >= 128) {
      return false;
    }
  }
  return true;
}

export function artistMatch(expected: string, actual: string): number {
  const left = expected.trim();
  const right = actual.trim();
  if (left == '' || right == '') {
    return 1;
  }
  const score = similarityStrict(left, right);
  if (score >= 0.5) {
    return score;
  }
  if (isAscii(left) != isAscii(right)) {
    return 0.6;
  }
  return score;
}

export function durationScore(expectedSec: number, actualSec: number): number {
  if (expectedSec <= 0 || actualSec <= 0) {
    return 0.5;
  }
  const delta = Math.abs(expectedSec - actualSec);
  if (delta <= 2) {
    return 1;
  }
  if (delta <= 5) {
    return 0.85;
  }
  if (delta <= 12) {
    return 0.6;
  }
  if (delta <= 30) {
    return 0.3;
  }
  return 0;
}

const VERSION_HINTS: string[] = ['live', 'remix', 'acoustic', 'instrumental', '伴奏', '现场', '翻唱',
  'dj', 'demo', '演唱会', '演奏会', '音乐会', 'karaoke', 'ktv', 'remastered', '重制'];

export function versionPenalty(title: string, candidateTitle: string, candidateAlbum: string): number {
  const base = normalizeText(title);
  for (const hint of VERSION_HINTS) {
    const inTitle = normalizeText(candidateTitle).includes(hint);
    const inAlbum = normalizeText(candidateAlbum).includes(hint);
    if ((inTitle || inAlbum) && !base.includes(hint)) {
      return 0.18;
    }
  }
  return 0;
}

export function similarityLabel(confidence: number): string {
  return `${Math.round(confidence * 100)}%`;
}
