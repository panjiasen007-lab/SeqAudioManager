// 真假无损判定：调用 C++ 解码/FFT 指标，再按桌面版 authenticity._judge 的核心分支裁决。
import core from 'libseqaudio_core.so';
import { Track } from '../model/Track';
import { Db } from '../db/Db';
import { AppLog } from '../utils/AppLog';
import { T } from '../theme/Tokens';

export class AnalysisMetrics {
  ok: boolean = false;
  error: string = '';
  codec: string = '';
  sampleRate: number = 0;
  analysisRate: number = 0;
  channels: number = 0;
  bits: number = 0;
  durationMs: number = 0;
  isDsd: boolean = false;
  analyzedSeconds: number = 0;
  failCount: number = 0;
  nyquistHz: number = 0;
  cutoffHz: number = 0;
  cutoffMin: number = 0;
  cutoffMax: number = 0;
  steepness: number = 0;
  steepOct: number = 0;
  wallStep: number = 0;
  peakDb: number = 0;
  bodyDb: number = 0;
  edgeDb: number = 0;
  edgeBodyDb: number = 0;
  ratio: number = 0;
  holes: number = 0;
  sfb21: number = 0;
  flatness: number = 0;
  toneConcentration: number = 0;
  ultraDecay: number = 0;
  ultraSlope: number = 0;
  ultraHigh: number = 0;
  effDepth: number = 0;
  padRatio: number = 0;
  bitAbove16: boolean = false;
  peakDbfs: number = 0;
  upsampleSrc: number = 0;
  upsampleStep: number = 0;
  dsdSlope: number = 0;
  dsdUltra: number = 0;
  baseCutoff: number = 0;
  baseSilence: number = 0;
  ultraBody: number = 0;
  pcmSource: number = 0;
  pcmStep: number = 0;
  pcmAbove: number = 0;
}

export const VERDICT_TRUE: string = '真无损';
export const VERDICT_SUSPECT: string = '疑似假无损';
export const VERDICT_FAKE: string = '假无损';
export const VERDICT_UNKNOWN: string = '无法判定';
export const VERDICT_DSD: string = '真 DSD';
export const VERDICT_FAKE_DSD: string = '疑似假 DSD';
export const VERDICT_UPSAMPLED: string = '疑似升频';
export const VERDICT_UPSCALED: string = '疑似升位深';

export function verdictColor(verdict: string): string {
  if (verdict == VERDICT_TRUE) {
    return T.OK;
  }
  if (verdict == VERDICT_DSD) {
    return T.CYAN;
  }
  if (verdict == VERDICT_FAKE || verdict == VERDICT_FAKE_DSD) {
    return T.BAD;
  }
  if (verdict == VERDICT_SUSPECT || verdict == VERDICT_UPSAMPLED || verdict == VERDICT_UPSCALED) {
    return T.WARN;
  }
  return T.FG_DIM;
}

export function verdictGroup(verdict: string): string {
  if (verdict == VERDICT_TRUE || verdict == VERDICT_DSD) {
    return 'true';
  }
  if (verdict == VERDICT_FAKE || verdict == VERDICT_FAKE_DSD) {
    return 'fake';
  }
  if (verdict == VERDICT_SUSPECT || verdict == VERDICT_UPSAMPLED || verdict == VERDICT_UPSCALED) {
    return 'suspect';
  }
  if (verdict == '') {
    return 'none';
  }
  return 'unknown';
}

export class AuthenticityResult {
  verdict: string = VERDICT_UNKNOWN;
  confidence: number = 0;
  reasons: string[] = [];
  error: string = '';
  metrics: AnalysisMetrics = new AnalysisMetrics();
  freqs: number[] = [];
  db: number[] = [];
}

interface RawAnalysis {
  ok: boolean;
  error?: string;
  codec?: string;
  sampleRate?: number;
  analysisRate?: number;
  channels?: number;
  bits?: number;
  durationMs?: number;
  isDsd?: boolean;
  analyzedSeconds?: number;
  failCount?: number;
  nyquistHz?: number;
  cutoffHz?: number;
  cutoffMin?: number;
  cutoffMax?: number;
  steepness?: number;
  steepOct?: number;
  wallStep?: number;
  peakDb?: number;
  bodyDb?: number;
  edgeDb?: number;
  edgeBodyDb?: number;
  ratio?: number;
  holes?: number;
  sfb21?: number;
  flatness?: number;
  toneConcentration?: number;
  ultraDecay?: number;
  ultraSlope?: number;
  ultraHigh?: number;
  effDepth?: number;
  padRatio?: number;
  bitAbove16?: boolean;
  peakDbfs?: number;
  upsampleSrc?: number;
  upsampleStep?: number;
  dsdSlope?: number;
  dsdUltra?: number;
  baseCutoff?: number;
  baseSilence?: number;
  ultraBody?: number;
  pcmSource?: number;
  pcmStep?: number;
  pcmAbove?: number;
  freqs?: number[];
  db?: number[];
}

function judgeDsd(m: AnalysisMetrics): AuthenticityResult {
  const res = new AuthenticityResult();
  res.metrics = m;
  if (m.pcmSource > 0) {
    res.verdict = VERDICT_FAKE_DSD;
    res.confidence = 0.85;
    res.reasons.push(`基带上出现 ${m.pcmSource / 2000}kHz PCM 源码断崖（${Math.round(m.pcmStep)}dB），` +
      '断崖之上是数字静音，符合 PCM 转 DSD');
    res.reasons.push('PCM 转制的带宽边界不会有 Sigma-Delta 噪声整形');
    return res;
  }
  if (m.baseCutoff > 0 && m.baseCutoff < 22550 && m.baseSilence < -78) {
    res.verdict = VERDICT_FAKE_DSD;
    res.confidence = 0.8;
    res.reasons.push(`基带在 ${(m.baseCutoff / 1000).toFixed(1)}kHz 截止，` +
      `22.5~24kHz 仅 ${Math.round(m.baseSilence)}dB（数字静音）`);
    res.reasons.push('真 DSD 的噪声整形在 CD 边界之上仍有能量，不会出现数字静音');
    return res;
  }
  if (m.dsdSlope >= 6.0) {
    res.verdict = VERDICT_DSD;
    res.confidence = 0.85;
    res.reasons.push(`50~100kHz 噪声整形斜率 ${m.dsdSlope.toFixed(1)}dB/oct（明显上升）`);
    res.reasons.push('Sigma-Delta 量化噪声被整形到超声区，符合原生 DSD 特征');
    return res;
  }
  if (m.dsdSlope < 0.5) {
    res.verdict = VERDICT_FAKE_DSD;
    res.confidence = 0.55;
    res.reasons.push(`50~100kHz 噪声整形斜率仅 ${m.dsdSlope.toFixed(1)}dB/oct（接近平坦）`);
    res.reasons.push('PCM 转 DSD 在超声区没有噪声整形斜率，疑似假 DSD');
    return res;
  }
  res.verdict = VERDICT_SUSPECT;
  res.confidence = 0.4;
  res.reasons.push(`噪声整形斜率 ${m.dsdSlope.toFixed(1)}dB/oct 处于灰区，建议结合频谱复核`);
  return res;
}

function judgePcm(m: AnalysisMetrics): AuthenticityResult {
  const res = new AuthenticityResult();
  res.metrics = m;
  const sampleRate = m.sampleRate > 0 ? m.sampleRate : 44100;
  const nyq = sampleRate / 2;
  const cutoff = m.cutoffHz;
  const ratio = m.ratio > 0 ? m.ratio : (nyq > 0 ? cutoff / nyq : 0);
  if (sampleRate > 0 && sampleRate < 44100) {
    res.verdict = VERDICT_FAKE;
    res.confidence = 0.9;
    res.reasons.push(`采样率 ${sampleRate / 1000}kHz 低于 CD 基线 44.1kHz`);
    return res;
  }
  if (m.bits > 0 && m.bits < 16) {
    res.verdict = VERDICT_FAKE;
    res.confidence = 0.9;
    res.reasons.push(`位深 ${m.bits}bit 低于 CD 基线 16bit`);
    return res;
  }
  if (m.channels == 1) {
    res.verdict = VERDICT_FAKE;
    res.confidence = 0.8;
    res.reasons.push('单声道低于收藏最低规格（要求立体声）');
    return res;
  }
  // 内容铺满声明带宽
  if (ratio >= 0.95) {
    res.verdict = VERDICT_TRUE;
    res.confidence = 0.85;
    res.reasons.push(`内容延伸到 ${(cutoff / 1000).toFixed(1)}kHz，占声明 Nyquist 的 ${Math.round(ratio * 100)}%`);
    res.reasons.push('没有采样率或位深注水迹象');
    return res;
  }
  // 升采样
  if (nyq >= 23000 && ratio < 0.96 && m.edgeDb < -95) {
    const atCdEdge = Math.abs(cutoff - 22050) <= 400;
    if (atCdEdge || m.edgeDb < -105) {
      res.verdict = VERDICT_UPSAMPLED;
      res.confidence = 0.3;
      res.reasons.push(`声明 ${(sampleRate / 1000).toFixed(1)}kHz，但内容在 ` +
        `${(cutoff / 1000).toFixed(1)}kHz 就结束（带宽 ${Math.round(ratio * 100)}%）`);
      if (atCdEdge) {
        res.reasons.push('截止位置正好落在 CD 的 22.05kHz 边界，符合 44.1kHz 升采样特征');
      }
      res.reasons.push('扩展带为空，疑似采样率注水');
      return res;
    }
  }
  if (nyq >= 23000 && m.upsampleSrc > 0 &&
    Math.abs(cutoff - m.upsampleSrc / 2) <= 600) {
    res.verdict = VERDICT_UPSAMPLED;
    res.confidence = 0.3;
    res.reasons.push(`内容在 ${(m.upsampleSrc / 2000).toFixed(1)}kHz 出现 ${Math.round(m.upsampleStep)}dB 台阶，` +
      '正是低采样率 PCM 的 Nyquist 边界');
    res.reasons.push('台阶之上是被填充的噪声，不是真实录音');
    return res;
  }
  const nearBoundary = [22050, 24000, 32000, 44100, 48000, 88200, 96000].some((b: number) =>
    b < nyq * 0.97 && Math.abs(cutoff - b) <= Math.max(400, b * 0.02));
  if (nyq >= 23000 && nearBoundary && m.flatness >= 0.7 && m.edgeDb > -95) {
    res.verdict = VERDICT_UPSAMPLED;
    res.confidence = 0.4;
    res.reasons.push(`内容停在 ${(cutoff / 1000).toFixed(1)}kHz，扩展带是平坦噪声（平坦度 ` +
      `${m.flatness.toFixed(2)}）`);
    res.reasons.push('真实乐器超声内容是离散谐波峰，平坦噪声说明是 dither 填充');
    return res;
  }
  // 升位深
  if (m.bits >= 24 && m.padRatio >= 0.9 && m.effDepth > 0 && m.effDepth <= 17) {
    res.verdict = VERDICT_UPSCALED;
    res.confidence = 0.6;
    res.reasons.push(`声明 ${m.bits}bit，但 24bit 解码后最低字节全为零（有效位深 ${m.effDepth}bit）`);
    res.reasons.push('位深扩容不会增加真实精度，属于规格注水');
    return res;
  }
  // 数字静音 = 有损转码铁证
  if (cutoff > 0 && cutoff < nyq * 0.95 && m.edgeDb < -90 && m.edgeBodyDb < -64) {
    res.reasons.push(`内容在 ${(cutoff / 1000).toFixed(1)}kHz 结束（带宽 ${Math.round(ratio * 100)}%），` +
      `截止之上为数字静音（${Math.round(m.edgeDb)}dB）`);
    res.reasons.push('母带/磁带在截止之上会有模拟底噪，数字空白说明高频被编码器砍掉');
    if (ratio < 0.85) {
      res.verdict = VERDICT_FAKE;
      res.confidence = 0.85;
    } else {
      res.verdict = VERDICT_SUSPECT;
      res.confidence = 0.5;
    }
    return res;
  }
  const hasMp3Marks = m.holes >= 3 || m.sfb21 >= 8;
  if (nyq <= 24000) {
    const isWall = m.wallStep >= 12;
    if (cutoff < 19500 && hasMp3Marks) {
      res.verdict = VERDICT_FAKE;
      res.confidence = 0.85;
      res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 截止，并伴随窄带空洞/凹陷，` +
        '符合有损编码器量化特征');
      return res;
    }
    if (cutoff < 14000 && ratio < 0.65) {
      res.verdict = VERDICT_SUSPECT;
      res.confidence = 0.55;
      res.reasons.push(`内容只到 ${(cutoff / 1000).toFixed(1)}kHz（带宽 ${Math.round(ratio * 100)}%），` +
        '低于正常音乐母带的带宽下限，疑似有损源');
      return res;
    }
    if (cutoff < 17000) {
      if (!isWall) {
        res.verdict = VERDICT_TRUE;
        res.confidence = 0.7;
        res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 平滑滚降，` +
          `截止处 250Hz 内仅落差 ${m.wallStep.toFixed(1)}dB（斜坡，非编码器低通）`);
      } else {
        res.verdict = VERDICT_FAKE;
        res.confidence = 0.9;
        res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 断崖式截止` +
          `（250Hz 内落差 ${Math.round(m.wallStep)}dB），符合 128kbps 有损转码特征`);
      }
      return res;
    }
    if (cutoff < 19500) {
      if (!isWall) {
        res.verdict = VERDICT_TRUE;
        res.confidence = 0.7;
        res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 平滑滚降，符合老录音/母带自然衰减`);
      } else if (hasMp3Marks) {
        res.verdict = VERDICT_FAKE;
        res.confidence = 0.75;
        res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 截止并伴随空洞，符合 192kbps 转码特征`);
      } else {
        res.verdict = VERDICT_SUSPECT;
        res.confidence = 0.55;
        res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 断崖式截止，疑似 192kbps 有损转码`);
      }
      return res;
    }
    if (cutoff < 20500) {
      if (!isWall) {
        res.verdict = VERDICT_TRUE;
        res.confidence = 0.72;
        res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 平滑滚降，符合母带自然衰减`);
      } else if (m.edgeDb >= -85) {
        res.verdict = VERDICT_TRUE;
        res.confidence = 0.7;
        res.reasons.push(`截止之上仍有 ${Math.round(m.edgeDb)}dB 模拟底噪，更像母带低通，不是编码器空白`);
      } else {
        res.verdict = VERDICT_SUSPECT;
        res.confidence = 0.45;
        res.reasons.push(`频谱在 ${(cutoff / 1000).toFixed(1)}kHz 断崖，截止之上仅 ` +
          `${Math.round(m.edgeDb)}dB（数字空白），疑似高码率转码`);
      }
      return res;
    }
    res.verdict = VERDICT_TRUE;
    res.confidence = 0.85;
    res.reasons.push(`频谱平滑延伸到 ${(cutoff / 1000).toFixed(1)}kHz（上限 ${(nyq / 1000).toFixed(1)}kHz）`);
    if (m.effDepth > 0 && m.bits > 0 && m.effDepth < m.bits - 2) {
      res.reasons.push(`源精度约 ${m.effDepth}bit（声明 ${m.bits}bit）：不是零填充，仍按真无损`);
    }
    return res;
  }
  // hi-res
  if (nyq >= 90000 &&
    (m.ultraDecay > -35 || m.ultraSlope > -15 || m.ultraHigh > -85)) {
    res.verdict = VERDICT_UPSAMPLED;
    res.confidence = 0.6;
    res.reasons.push(`采样率 ${(sampleRate / 1000).toFixed(1)}kHz，但超声区衰减异常` +
      `（80k 相对 20k ${Math.round(m.ultraDecay)}dB / 40k→80k ${Math.round(m.ultraSlope)}dB）`);
    res.reasons.push('真母带超声内容会快速衰减；平坦的宽带噪声说明是往超声区灌噪声冒充 hi-res');
    return res;
  }
  if (sampleRate >= 88200 && ratio < 0.55 && m.edgeDb < -95) {
    res.verdict = VERDICT_UPSAMPLED;
    res.confidence = 0.35;
    res.reasons.push(`采样率 ${(sampleRate / 1000).toFixed(1)}kHz，但内容只到 ` +
      `${(cutoff / 1000).toFixed(1)}kHz（带宽 ${Math.round(ratio * 100)}%），超声区为空`);
    return res;
  }
  if (ratio < 0.70) {
    const edge = [22050, 24000, 32000, 44100, 48000, 88200, 96000].some((b: number) =>
      b < nyq * 0.97 && Math.abs(cutoff - b) <= Math.max(400, b * 0.02));
    if (m.edgeDb < -95 || (edge && m.wallStep >= 12)) {
      res.verdict = VERDICT_UPSAMPLED;
      res.confidence = 0.35;
      res.reasons.push(`采样率 ${(sampleRate / 1000).toFixed(1)}kHz，但频谱只到 ` +
        `${(cutoff / 1000).toFixed(1)}kHz（带宽 ${Math.round(ratio * 100)}%），疑似低采样率内容升频`);
      return res;
    }
    res.verdict = VERDICT_TRUE;
    res.confidence = 0.7;
    res.reasons.push(`内容到 ${(cutoff / 1000).toFixed(1)}kHz，高频稀疏但扩展带仍有 ` +
      `${Math.round(m.edgeDb)}dB 模拟底噪，属母带自然滚降`);
    return res;
  }
  res.verdict = VERDICT_TRUE;
  res.confidence = 0.8;
  res.reasons.push(`高频延伸到 ${(cutoff / 1000).toFixed(1)}kHz（带宽 ${Math.round(ratio * 100)}%），` +
    '符合高解析音源特征');
  return res;
}

export class AuthenticityService {
  static analyze(path: string): AuthenticityResult {
    const res = new AuthenticityResult();
    let text = '';
    try {
      text = core.analyze(path);
    } catch (e) {
      res.error = '原生分析内核不可用';
      return res;
    }
    if (text == '') {
      res.error = '原生分析返回为空';
      return res;
    }
    let raw: RawAnalysis;
    try {
      raw = JSON.parse(text) as RawAnalysis;
    } catch (e) {
      res.error = '分析结果解析失败';
      return res;
    }
    if (!raw.ok) {
      res.error = raw.error != undefined ? raw.error : '分析失败';
      return res;
    }
    const m = new AnalysisMetrics();
    m.ok = true;
    m.codec = raw.codec != undefined ? raw.codec : '';
    m.sampleRate = raw.sampleRate != undefined ? raw.sampleRate : 0;
    m.analysisRate = raw.analysisRate != undefined ? raw.analysisRate : 0;
    m.channels = raw.channels != undefined ? raw.channels : 0;
    m.bits = raw.bits != undefined ? raw.bits : 0;
    m.durationMs = raw.durationMs != undefined ? raw.durationMs : 0;
    m.isDsd = raw.isDsd != undefined ? raw.isDsd : false;
    m.analyzedSeconds = raw.analyzedSeconds != undefined ? raw.analyzedSeconds : 0;
    m.failCount = raw.failCount != undefined ? raw.failCount : 0;
    m.nyquistHz = raw.nyquistHz != undefined ? raw.nyquistHz : 0;
    m.cutoffHz = raw.cutoffHz != undefined ? raw.cutoffHz : 0;
    m.cutoffMin = raw.cutoffMin != undefined ? raw.cutoffMin : 0;
    m.cutoffMax = raw.cutoffMax != undefined ? raw.cutoffMax : 0;
    m.steepness = raw.steepness != undefined ? raw.steepness : 0;
    m.steepOct = raw.steepOct != undefined ? raw.steepOct : 0;
    m.wallStep = raw.wallStep != undefined ? raw.wallStep : 0;
    m.peakDb = raw.peakDb != undefined ? raw.peakDb : 0;
    m.bodyDb = raw.bodyDb != undefined ? raw.bodyDb : 0;
    m.edgeDb = raw.edgeDb != undefined ? raw.edgeDb : 0;
    m.edgeBodyDb = raw.edgeBodyDb != undefined ? raw.edgeBodyDb : 0;
    m.ratio = raw.ratio != undefined ? raw.ratio : 0;
    m.holes = raw.holes != undefined ? raw.holes : 0;
    m.sfb21 = raw.sfb21 != undefined ? raw.sfb21 : 0;
    m.flatness = raw.flatness != undefined ? raw.flatness : 0;
    m.toneConcentration = raw.toneConcentration != undefined ? raw.toneConcentration : 0;
    m.ultraDecay = raw.ultraDecay != undefined ? raw.ultraDecay : 0;
    m.ultraSlope = raw.ultraSlope != undefined ? raw.ultraSlope : 0;
    m.ultraHigh = raw.ultraHigh != undefined ? raw.ultraHigh : 0;
    m.effDepth = raw.effDepth != undefined ? raw.effDepth : 0;
    m.padRatio = raw.padRatio != undefined ? raw.padRatio : 0;
    m.bitAbove16 = raw.bitAbove16 != undefined ? raw.bitAbove16 : false;
    m.peakDbfs = raw.peakDbfs != undefined ? raw.peakDbfs : 0;
    m.upsampleSrc = raw.upsampleSrc != undefined ? raw.upsampleSrc : 0;
    m.upsampleStep = raw.upsampleStep != undefined ? raw.upsampleStep : 0;
    m.dsdSlope = raw.dsdSlope != undefined ? raw.dsdSlope : 0;
    m.dsdUltra = raw.dsdUltra != undefined ? raw.dsdUltra : 0;
    m.baseCutoff = raw.baseCutoff != undefined ? raw.baseCutoff : 0;
    m.baseSilence = raw.baseSilence != undefined ? raw.baseSilence : 0;
    m.ultraBody = raw.ultraBody != undefined ? raw.ultraBody : 0;
    m.pcmSource = raw.pcmSource != undefined ? raw.pcmSource : 0;
    m.pcmStep = raw.pcmStep != undefined ? raw.pcmStep : 0;
    m.pcmAbove = raw.pcmAbove != undefined ? raw.pcmAbove : 0;
    res.metrics = m;
    res.freqs = raw.freqs != undefined ? raw.freqs : [];
    res.db = raw.db != undefined ? raw.db : [];
    const judged = m.isDsd ? judgeDsd(m) : judgePcm(m);
    judged.metrics = m;
    judged.freqs = res.freqs;
    judged.db = res.db;
    return judged;
  }

  static async analyzeTrack(track: Track): Promise<AuthenticityResult> {
    const path = track.isCueChild == 1 && track.cuePath != '' ? track.cuePath : track.path;
    const res = AuthenticityService.analyze(path);
    if (res.error == '') {
      track.verdict = res.verdict;
      track.verdictConfidence = res.confidence;
      track.verdictReason = res.reasons.join('；');
      track.analyzedAt = Date.now();
      await Db.updateTrack(track);
      AppLog.info(`真假鉴定：${track.title} → ${res.verdict}（${Math.round(res.confidence * 100)}%）`);
    } else {
      // 与桌面版一致：分析失败也落一条「无法判定」，并保留原因供详情查看
      track.verdict = VERDICT_UNKNOWN;
      track.verdictConfidence = 0;
      track.verdictReason = res.error;
      track.analyzedAt = Date.now();
      await Db.updateTrack(track);
      AppLog.warn(`真假鉴定失败：${track.fileName} · ${res.error}`);
    }
    return res;
  }

  static async analyzeMany(tracks: Track[], onProgress: (done: number, total: number, cur: string) => void,
    cancelled: () => boolean): Promise<Map<number, AuthenticityResult>> {
    const out = new Map<number, AuthenticityResult>();
    // CUE 子轨共用同一个源文件：同一路径只解码一次，结论复制给每个子轨
    const cache = new Map<string, AuthenticityResult>();
    let done = 0;
    for (const t of tracks) {
      if (cancelled()) {
        break;
      }
      const appExt = t.ext.toLowerCase();
      let res: AuthenticityResult;
      if (appExt == '.iso') {
        res = new AuthenticityResult();
        res.verdict = VERDICT_UNKNOWN;
        res.error = 'SACD ISO 为光盘镜像，需先分轨导出为 FLAC/WAV 后鉴定';
        res.reasons.push(res.error);
      } else if (appExt == '.mp3' || appExt == '.aac' || appExt == '.ogg' || appExt == '.opus' ||
        (appExt == '.m4a' && t.bitDepth <= 0)) {
        // M4A 容器可能装 ALAC（无损）或 AAC（有损）：位深为 0 说明是 AAC，跳过鉴定
        res = new AuthenticityResult();
        res.verdict = VERDICT_UNKNOWN;
        res.error = `有损格式（${appExt.substring(1).toUpperCase()}），不做无损鉴定`;
        res.reasons.push(res.error);
      } else {
        const path = t.isCueChild == 1 && t.cuePath != '' ? t.cuePath : t.path;
        const hit = cache.get(path);
        if (hit != undefined) {
          res = hit;
        } else {
          res = AuthenticityService.analyze(path);
          cache.set(path, res);
        }
      }
      out.set(t.id, res);
      if (res.error == '') {
        t.verdict = res.verdict;
        t.verdictConfidence = res.confidence;
        t.verdictReason = res.reasons.join('；');
      } else {
        t.verdict = VERDICT_UNKNOWN;
        t.verdictConfidence = 0;
        t.verdictReason = res.error;
      }
      t.analyzedAt = Date.now();
      await Db.updateTrack(t);
      done++;
      onProgress(done, tracks.length, t.fileName);
      await new Promise<void>((resolve: () => void) => {
        setTimeout(resolve, 0);
      });
    }
    return out;
  }
}
