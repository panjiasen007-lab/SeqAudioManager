// 多源候选校验与置信度评分：对齐桌面版 pipeline/matcher.py。
import { Track } from '../model/Track';
import { CoverCandidate, CoverService } from '../cover/CoverService';
import { MetadataProviders } from '../net/MetadataProviders';
import { MatchCandidate, MatchResult, MatchSettings } from '../net/MetadataTypes';
import {
  artistMatch, durationScore, similarity, similarityStrict, versionPenalty
} from '../net/TextMatch';

export class ScoreWeights {
  source: number = 0.34;
  title: number = 0.28;
  artist: number = 0.20;
  album: number = 0.10;
  duration: number = 0.08;
}

export class NameGuess {
  title: string = '';
  artist: string = '';
}

// 文件名兜底：去掉扩展名/下划线/前导序号，再按「歌手 - 歌名」拆分。
// 与桌面版 probe.guess_from_filename 的行为一致，用于标签为空或只有文件名时。
export function guessFromFileName(pathOrName: string): NameGuess {
  const slash = Math.max(pathOrName.lastIndexOf('/'), pathOrName.lastIndexOf('\\'));
  let name = slash >= 0 ? pathOrName.substring(slash + 1) : pathOrName;
  const dot = name.lastIndexOf('.');
  if (dot > 0) {
    name = name.substring(0, dot);
  }
  let text = name.replace(/_/g, ' ').trim();
  text = text.replace(/^\d{1,3}\s*[.\-、)．]\s*/, '').trim();
  const g = new NameGuess();
  const dash = text.indexOf(' - ');
  if (dash > 0) {
    g.artist = text.substring(0, dash).trim();
    g.title = text.substring(dash + 3).trim();
  } else {
    g.title = text;
  }
  if (g.title == '') {
    g.title = name.trim();
  }
  return g;
}

function stemOf(track: Track): string {
  const dot = track.fileName.lastIndexOf('.');
  return dot > 0 ? track.fileName.substring(0, dot) : track.fileName;
}

function probeTitle(track: Track): string {
  const title = track.title.trim();
  if (title != '' && title != stemOf(track)) {
    return title;
  }
  return guessFromFileName(track.fileName).title;
}

function probeArtist(track: Track): string {
  if (track.artist.trim() != '') {
    return track.artist;
  }
  return guessFromFileName(track.fileName).artist;
}

function probeAlbum(track: Track): string {
  return track.album;
}

function directoryHint(track: Track): string {
  if (track.album.trim() != '') {
    return '';
  }
  const slash = track.path.lastIndexOf('/');
  if (slash <= 0) {
    return '';
  }
  const parent = track.path.substring(0, slash);
  const slash2 = parent.lastIndexOf('/');
  const name = slash2 >= 0 ? parent.substring(slash2 + 1) : parent;
  // 目录名过短或明显是沙箱目录时不当专辑先验
  if (name.length < 2 || name == 'Music' || name == 'files' || name == 'entry') {
    return '';
  }
  return name;
}

export class MetadataMatcher {
  private static weights: ScoreWeights = new ScoreWeights();

  private static accept(cand: MatchCandidate, title: string, artist: string, durationSec: number): boolean {
    const durationOk = durationSec > 0 && cand.duration > 0 &&
      Math.abs(cand.duration - durationSec) <= 3.0;
    const artistOk = artist == '' ? true : artistMatch(artist, cand.artist) >= 0.5;
    const titleOk = similarityStrict(title, cand.title) >= 0.75;
    if (!titleOk) {
      return durationOk && artistOk;
    }
    if (artistOk) {
      return true;
    }
    return durationOk;
  }

  private static rankValue(cand: MatchCandidate, title: string, artist: string, durationSec: number): number {
    const titleSim = similarityStrict(title, cand.title);
    const artistSim = artist != '' ? artistMatch(artist, cand.artist) : 1.0;
    let durSim = 0.5;
    if (durationSec > 0 && cand.duration > 0) {
      durSim = Math.max(0, 1 - Math.abs(cand.duration - durationSec) / 30);
    }
    return titleSim * 0.55 + artistSim * 0.25 + durSim * 0.20;
  }

  static async query(track: Track, settings: MatchSettings): Promise<MatchCandidate[]> {
    const title = probeTitle(track);
    const artist = probeArtist(track);
    const album = probeAlbum(track);
    const durationSec = track.durationMs > 0 ? track.durationMs / 1000 : 0;
    const providers: string[] = [];
    if (settings.useNetmusic) {
      providers.push('netease');
    }
    if (settings.useQqmusic) {
      providers.push('qq');
    }
    if (settings.useMusicBrainz) {
      providers.push('mb');
    }
    if (settings.useItunes) {
      providers.push('itunes');
    }
    let fallback: MatchCandidate[] = [];
    for (const p of providers) {
      let cands: MatchCandidate[] = [];
      if (p == 'netease') {
        cands = await MetadataProviders.netmusic(title, artist);
      } else if (p == 'qq') {
        cands = await MetadataProviders.qqmusic(title, artist);
      } else if (p == 'mb') {
        cands = await MetadataProviders.musicbrainz(title, artist, album);
      } else {
        cands = await MetadataProviders.itunes(title, artist);
      }
      if (cands.length == 0) {
        continue;
      }
      const kept: MatchCandidate[] = [];
      for (const c of cands) {
        if (MetadataMatcher.accept(c, title, artist, durationSec)) {
          kept.push(c);
        }
      }
      if (kept.length > 0) {
        kept.sort((a: MatchCandidate, b: MatchCandidate) =>
          MetadataMatcher.rankValue(b, title, artist, durationSec) -
          MetadataMatcher.rankValue(a, title, artist, durationSec));
        return kept;
      }
      if (fallback.length == 0) {
        for (const c of cands) {
          if (similarityStrict(title, c.title) >= 0.5) {
            fallback.push(c);
          }
        }
      }
    }
    if (fallback.length > 0) {
      return fallback;
    }
    // 全空时放宽歌手：只按标题再试一次（仍按优先级）
    if (settings.useNetmusic) {
      const c = await MetadataProviders.netmusic(title, '');
      if (c.length > 0) {
        return c;
      }
    }
    if (settings.useQqmusic) {
      const c = await MetadataProviders.qqmusic(title, '');
      if (c.length > 0) {
        return c;
      }
    }
    if (settings.useMusicBrainz) {
      const c = await MetadataProviders.musicbrainz(title, '', album);
      if (c.length > 0) {
        return c;
      }
    }
    if (settings.useItunes) {
      return await MetadataProviders.itunes(title, '');
    }
    return [];
  }

  static score(track: Track, cand: MatchCandidate, albumHint: string): number {
    const w = MetadataMatcher.weights;
    const title = probeTitle(track);
    const artist = probeArtist(track);
    const album = probeAlbum(track);
    const durationSec = track.durationMs > 0 ? track.durationMs / 1000 : 0;
    const sTitle = similarity(title, cand.title);
    const sArtist = artist != '' ? similarity(artist, cand.artist) : 0.6;
    const sAlbum = album != '' ? similarity(album, cand.album) : 0.6;
    const sDur = durationScore(durationSec, cand.duration);
    let total = w.source * Math.max(cand.rawScore, 0.5) +
      w.title * sTitle + w.artist * sArtist + w.album * sAlbum + w.duration * sDur;
    if (albumHint != '') {
      const hintSim = cand.album != '' ? similarity(albumHint, cand.album) : 0;
      if (hintSim >= 0.80) {
        total += 0.14;
      } else if (hintSim >= 0.55) {
        total += 0.05;
      } else if (cand.album != '' && hintSim < 0.30) {
        total -= 0.06;
      }
    }
    total -= versionPenalty(title, cand.title, cand.album);
    return Math.max(0, Math.min(1, total));
  }

  static rank(track: Track, cands: MatchCandidate[], albumHint: string): MatchCandidate[] {
    const out = cands.slice();
    for (const c of out) {
      c.confidence = MetadataMatcher.score(track, c, albumHint);
    }
    out.sort((a: MatchCandidate, b: MatchCandidate) => b.confidence - a.confidence);
    return out;
  }

  static async match(track: Track, settings: MatchSettings): Promise<MatchResult> {
    const hint = directoryHint(track);
    let cands = await MetadataMatcher.query(track, settings);
    cands = MetadataMatcher.rank(track, cands, hint);
    const result = new MatchResult();
    result.candidates = cands;
    if (cands.length == 0) {
      result.reason = '联网未找到候选';
      return result;
    }
    const best = cands[0];
    result.candidate = best;
    result.confidence = best.confidence;
    result.accepted = best.confidence >= settings.threshold;
    if (!result.accepted) {
      result.reason = `最高置信度 ${Math.round(best.confidence * 100)}% 低于阈值 ` +
        `${Math.round(settings.threshold * 100)}%`;
    }
    if (result.accepted && settings.useCoverart) {
      let url = await MetadataProviders.resolveCover(best);
      if (url == '') {
        // 候选没有可用封面源时，按「专辑 + 歌手」独立再搜一张兜底
        const covers: CoverCandidate[] = await CoverService.search(
          best.artist, best.album != '' ? best.album : best.title);
        if (covers.length > 0) {
          url = covers[0].url;
        }
      }
      if (url != '') {
        best.coverUrl = url;
      }
    }
    return result;
  }
}
