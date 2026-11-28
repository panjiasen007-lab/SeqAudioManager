// 联网元数据源：网易云 → QQ 音乐 → MusicBrainz → iTunes。
// 对齐桌面版 net/*.py 的端点、字段与候选顺序；任何单源失败都静默换下一个。
import { HttpStack } from './HttpStack';
import { MatchCandidate } from './MetadataTypes';
import { LyricsResult, LyricsService } from '../lyrics/LyricsService';
import { similarityStrict } from './TextMatch';

// ---------------------------------------------------------------- 接口定义

interface NmArtist {
  name?: string;
}

interface NmAlbum {
  name?: string;
  picUrl?: string;
  publishTime?: number;
  artist?: NmArtist;
}

interface NmSong {
  id?: number;
  name?: string;
  duration?: number;
  artists?: NmArtist[];
  album?: NmAlbum;
}

interface NmResult {
  songs?: NmSong[];
}

interface NmSearchResp {
  code?: number;
  result?: NmResult;
}

interface NmDetailSong {
  album?: NmAlbum;
}

interface NmDetailResp {
  songs?: NmDetailSong[];
}

interface NmLyricBody {
  lyric?: string;
}

interface NmLyricResp {
  lrc?: NmLyricBody;
}

interface QqSinger {
  name?: string;
}

interface QqAlbum {
  name?: string;
  mid?: string;
}

interface QqSong {
  title?: string;
  songname?: string;
  name?: string;
  singer?: QqSinger[];
  album?: QqAlbum;
  interval?: number;
  mid?: string;
  songmid?: string;
}

interface QqSongList {
  list?: QqSong[];
}

interface QqData {
  song?: QqSongList;
}

interface QqSearchResp {
  data?: QqData;
}

interface QqLyricResp {
  lyric?: string;
}

interface MbArtist {
  name?: string;
}

interface MbArtistCredit {
  name?: string;
  joinphrase?: string;
  artist?: MbArtist;
}

interface MbGenre {
  name?: string;
  count?: number;
}

interface MbTrack {
  id?: string;
  number?: string;
  title?: string;
}

interface MbMedium {
  position?: number;
  track?: MbTrack[];
}

interface MbReleaseGroup {
  primaryType?: string;
  secondaryTypes?: string[];
  genres?: MbGenre[];
}

interface MbRelease {
  id?: string;
  title?: string;
  date?: string;
  media?: MbMedium[];
  releaseGroup?: MbReleaseGroup;
}

interface MbRecording {
  id?: string;
  title?: string;
  artistCredit?: MbArtistCredit[];
  releases?: MbRelease[];
  genres?: MbGenre[];
  score?: number;
}

interface MbResp {
  recordings?: MbRecording[];
}

interface LrclibItem {
  trackName?: string;
  artistName?: string;
  syncedLyrics?: string;
  plainLyrics?: string;
}

interface ItunesItem {
  trackName?: string;
  artistName?: string;
  collectionName?: string;
  releaseDate?: string;
  primaryGenreName?: string;
  trackNumber?: number;
  discNumber?: number;
  artworkUrl100?: string;
  trackId?: number;
  trackTimeMillis?: number;
}

interface ItunesResp {
  results?: ItunesItem[];
}

const MB_BASE = 'https://musicbrainz.org/ws/2';
const MB_UA = 'SeqAudioManager/1.3.0 (harmony-pc; metadata matching)';
const QQ_REFERER = 'https://y.qq.com/';
const QQ_HEADERS: Record<string, string> = { 'Referer': QQ_REFERER };
const MB_HEADERS: Record<string, string> = { 'User-Agent': MB_UA };
let mbLastCall = 0;

async function mbRateLimit(): Promise<void> {
  const now = Date.now();
  const wait = 1120 - (now - mbLastCall);
  if (wait > 0) {
    await new Promise<void>((resolve: () => void) => {
      setTimeout(resolve, wait);
    });
  }
  mbLastCall = Date.now();
}

function ncbi(text: string): string {
  return encodeURIComponent(text.trim());
}

function cleanHttp(url: string): string {
  return url.startsWith('http://') ? `https://${url.substring(7)}` : url;
}

// ---------------------------------------------------------------- 网易云

function nmCandidate(item: NmSong, rank: number): MatchCandidate | undefined {
  const name = item.name != undefined ? item.name.trim() : '';
  if (name == '') {
    return undefined;
  }
  const album = item.album;
  const artists: NmArtist[] = item.artists != undefined ? item.artists : [];
  const names: string[] = [];
  for (const a of artists) {
    if (a.name != undefined && a.name != '') {
      names.push(a.name);
    }
  }
  let artist = names.join(' / ');
  if (artist == '' && album != undefined && album.artist != undefined && album.artist.name != undefined) {
    artist = album.artist.name;
  }
  const c = new MatchCandidate();
  c.source = '网易云';
  c.title = name;
  c.artist = artist;
  c.album = album != undefined && album.name != undefined ? album.name : '';
  c.albumartist = artist;
  c.coverUrl = album != undefined && album.picUrl != undefined ? cleanHttp(album.picUrl) : '';
  c.recordingId = item.id != undefined ? `${item.id}` : '';
  c.duration = item.duration != undefined ? item.duration / 1000 : 0;
  c.rawScore = Math.max(0, 0.85 - rank * 0.05);
  if (album != undefined && album.publishTime != undefined && album.publishTime > 0) {
    const y = new Date(album.publishTime).getFullYear();
    if (y > 1900) {
      c.date = `${y}`;
    }
  }
  c.note = c.duration > 0 ? `${Math.round(c.duration)}s` : '';
  return c;
}

export class MetadataProviders {
  static async netmusic(title: string, artist: string, limit: number = 8): Promise<MatchCandidate[]> {
    if (title.trim() == '') {
      return [];
    }
    const term = `${title} ${artist}`.trim();
    const url = `https://music.163.com/api/search/get?s=${ncbi(term)}&type=1&limit=${limit}&offset=0`;
    try {
      const text = await HttpStack.getTextEx(url, undefined, 7000);
      const resp = JSON.parse(text) as NmSearchResp;
      if (resp.result == undefined || resp.result.songs == undefined) {
        return [];
      }
      const out: MatchCandidate[] = [];
      let rank = 0;
      for (const item of resp.result.songs) {
        const c = nmCandidate(item, rank);
        if (c != undefined) {
          out.push(c);
          rank++;
        }
      }
      return out;
    } catch (e) {
      return [];
    }
  }

  static async netmusicCoverUrl(candidate: MatchCandidate): Promise<string> {
    if (candidate.recordingId == '') {
      return '';
    }
    try {
      const url = `https://music.163.com/api/song/detail?ids=[${candidate.recordingId}]`;
      const text = await HttpStack.getTextEx(url, undefined, 7000);
      const resp = JSON.parse(text) as NmDetailResp;
      if (resp.songs == undefined || resp.songs.length == 0 || resp.songs[0].album == undefined) {
        return '';
      }
      const pic = resp.songs[0].album.picUrl;
      if (pic == undefined || pic == '') {
        return '';
      }
      let out = cleanHttp(pic);
      if (!out.includes('?')) {
        out += '?param=300y300';
      }
      return out;
    } catch (e) {
      return '';
    }
  }

  // ---------------------------------------------------------------- QQ 音乐

  static async qqmusic(title: string, artist: string, limit: number = 8): Promise<MatchCandidate[]> {
    if (title.trim() == '') {
      return [];
    }
    const term = `${title} ${artist}`.trim();
    const url = `https://c.y.qq.com/soso/fcgi-bin/client_search_cp?w=${ncbi(term)}` +
      `&format=json&p=1&n=${limit}&cr=1&new_json=1&t=0`;
    try {
      const text = await HttpStack.getTextEx(url, QQ_HEADERS, 7000);
      const resp = JSON.parse(text) as QqSearchResp;
      if (resp.data == undefined || resp.data.song == undefined || resp.data.song.list == undefined) {
        return [];
      }
      const out: MatchCandidate[] = [];
      let rank = 0;
      for (const item of resp.data.song.list) {
        const name = item.title != undefined ? item.title :
          item.songname != undefined ? item.songname : item.name != undefined ? item.name : '';
        if (name.trim() == '') {
          continue;
        }
        const names: string[] = [];
        if (item.singer != undefined) {
          for (const s of item.singer) {
            if (s.name != undefined && s.name != '') {
              names.push(s.name);
            }
          }
        }
        const artistName = names.join(' / ');
        const album = item.album;
        const c = new MatchCandidate();
        c.source = 'QQ音乐';
        c.title = name.trim();
        c.artist = artistName;
        c.album = album != undefined && album.name != undefined ? album.name : '';
        c.albumartist = artistName;
        c.coverUrl = album != undefined && album.mid != undefined && album.mid != '' ?
          `https://y.gtimg.cn/music/photo_new/T002R300x300M000${album.mid}.jpg` : '';
        c.recordingId = item.mid != undefined ? item.mid : item.songmid != undefined ? item.songmid : '';
        c.duration = item.interval != undefined ? item.interval : 0;
        c.rawScore = Math.max(0, 0.82 - rank * 0.05);
        c.note = c.duration > 0 ? `${Math.round(c.duration)}s` : '';
        out.push(c);
        rank++;
      }
      return out;
    } catch (e) {
      return [];
    }
  }

  // ---------------------------------------------------------------- MusicBrainz

  private static mbEscape(value: string): string {
    return value.replace(/([+\-&|!(){}\[\]^"~*?:\\/])/g, '\\$1').replace(/"/g, '');
  }

  static buildMbQuery(title: string, artist: string, album: string): string {
    const parts: string[] = [];
    if (title != '') {
      parts.push(`recording:"${MetadataProviders.mbEscape(title)}"`);
    }
    if (artist != '') {
      parts.push(`artist:"${MetadataProviders.mbEscape(artist)}"`);
    }
    if (album != '') {
      parts.push(`release:"${MetadataProviders.mbEscape(album)}"`);
    }
    return parts.length > 0 ? parts.join(' AND ') : `recording:"${MetadataProviders.mbEscape(title)}"`;
  }

  private static mbArtistName(item: MbRecording): string {
    if (item.artistCredit == undefined) {
      return '';
    }
    let out = '';
    for (const credit of item.artistCredit) {
      const name = credit.name != undefined ? credit.name :
        credit.artist != undefined && credit.artist.name != undefined ? credit.artist.name : '';
      if (name != '') {
        out += name + (credit.joinphrase != undefined ? credit.joinphrase : '');
      }
    }
    return out.trim();
  }

  private static mbPlaceholderAlbum(album: string): boolean {
    const low = album.trim().toLowerCase();
    if (low == '' || low == '[unknown]' || low == 'unknown' || low == '[no album]' || low == '[none]') {
      return true;
    }
    if (low.startsWith('[unknown') || low.startsWith('unknown ') || low.startsWith('(unknown')) {
      return true;
    }
    return album.trim().startsWith('[') && album.trim().endsWith(']');
  }

  private static mbReleaseScore(release: MbRelease): number {
    let weight = 0;
    const title = release.title != undefined ? release.title : '';
    const media = release.media != undefined ? release.media : [];
    let hasTracks = false;
    for (const m of media) {
      if (m.track != undefined && m.track.length > 0) {
        hasTracks = true;
      }
    }
    if (hasTracks) {
      weight += 3;
    }
    const date = release.date != undefined ? release.date : '';
    if (date != '') {
      weight += 2;
    }
    const rg = release.releaseGroup;
    const primary = rg != undefined && rg.primaryType != undefined ? rg.primaryType.toLowerCase() : '';
    if (primary == 'album' || primary == 'ep') {
      weight += 3;
    }
    if (primary == 'compilation' || primary == 'live' || primary == 'remix' ||
      primary == 'soundtrack' || primary == 'dj-mix' || primary == 'mixtape') {
      weight -= 4;
    }
    if (rg != undefined && rg.secondaryTypes != undefined) {
      for (const s of rg.secondaryTypes) {
        const low = s.toLowerCase();
        if (low == 'live' || low == 'compilation' || low == 'remix' || low == 'dj-mix' ||
          low == 'mixtape' || low == 'bootleg') {
          weight -= 3;
          break;
        }
      }
    }
    const lowTitle = title.toLowerCase();
    if (lowTitle.includes('live') || lowTitle.includes('concert') || lowTitle.includes('演唱会') ||
      lowTitle.includes('精选') || lowTitle.includes('合集') || lowTitle.includes('remix')) {
      weight -= 5;
    }
    if (MetadataProviders.mbPlaceholderAlbum(title)) {
      weight -= 6;
    }
    return weight;
  }

  private static mbPickRelease(item: MbRecording): MbRelease | undefined {
    const releases = item.releases != undefined ? item.releases : [];
    if (releases.length == 0) {
      return undefined;
    }
    let best: MbRelease = releases[0];
    let bestScore = MetadataProviders.mbReleaseScore(best);
    for (const r of releases) {
      const s = MetadataProviders.mbReleaseScore(r);
      if (s > bestScore) {
        best = r;
        bestScore = s;
      }
    }
    return best;
  }

  private static mbTrackNo(release: MbRelease, recordingId: string, title: string): string {
    const media = release.media != undefined ? release.media : [];
    for (const m of media) {
      if (m.track == undefined) {
        continue;
      }
      for (const tr of m.track) {
        if (recordingId != '' && tr.id == recordingId) {
          return tr.number != undefined ? tr.number : '';
        }
      }
      for (const tr of m.track) {
        if (title != '' && tr.title != undefined && tr.title.trim().toLowerCase() == title.trim().toLowerCase()) {
          return tr.number != undefined ? tr.number : '';
        }
      }
    }
    return '';
  }

  private static mbCandidate(item: MbRecording): MatchCandidate | undefined {
    const title = item.title != undefined ? item.title.trim() : '';
    if (title == '') {
      return undefined;
    }
    const release = MetadataProviders.mbPickRelease(item);
    const album = release != undefined && release.title != undefined &&
      !MetadataProviders.mbPlaceholderAlbum(release.title) ? release.title : '';
    const artist = MetadataProviders.mbArtistName(item);
    const c = new MatchCandidate();
    c.source = 'MusicBrainz';
    c.title = title;
    c.artist = artist;
    c.albumartist = artist;
    c.album = album;
    c.date = release != undefined && release.date != undefined ? release.date : '';
    c.track = release != undefined ? MetadataProviders.mbTrackNo(release, item.id != undefined ? item.id : '', title) : '';
    c.releaseId = release != undefined && release.id != undefined ? release.id : '';
    c.recordingId = item.id != undefined ? item.id : '';
    c.rawScore = item.score != undefined ? item.score / 100 : 0;
    const genres = item.genres != undefined ? item.genres :
      release != undefined && release.releaseGroup != undefined &&
      release.releaseGroup.genres != undefined ? release.releaseGroup.genres : [];
    if (genres.length > 0) {
      let bestCount = -1;
      for (const g of genres) {
        const count = g.count != undefined ? g.count : 0;
        if (count > bestCount && g.name != undefined) {
          bestCount = count;
          c.genre = g.name;
        }
      }
    }
    return c;
  }

  static async musicbrainz(title: string, artist: string, album: string, limit: number = 6): Promise<MatchCandidate[]> {
    if (title.trim() == '') {
      return [];
    }
    const run = async (query: string): Promise<MatchCandidate[]> => {
      await mbRateLimit();
      const url = `${MB_BASE}/recording?query=${ncbi(query)}&fmt=json&limit=${limit}`;
      try {
        const text = await HttpStack.getTextEx(url, MB_HEADERS, 9000);
        const norm = text.replace(/"artist-credit"/g, '"artistCredit"')
          .replace(/"release-group"/g, '"releaseGroup"')
          .replace(/"primary-type"/g, '"primaryType"')
          .replace(/"secondary-types"/g, '"secondaryTypes"');
        const resp = JSON.parse(norm) as MbResp;
        if (resp.recordings == undefined) {
          return [];
        }
        const out: MatchCandidate[] = [];
        for (const item of resp.recordings) {
          const c = MetadataProviders.mbCandidate(item);
          if (c != undefined) {
            out.push(c);
          }
        }
        out.sort((a: MatchCandidate, b: MatchCandidate) => b.rawScore - a.rawScore);
        return out;
      } catch (e) {
        return [];
      }
    };
    let out = await run(MetadataProviders.buildMbQuery(title, artist, album));
    if (out.length == 0 && artist != '') {
      let hasCjk = false;
      for (let i = 0; i < artist.length; i++) {
        const code = artist.charCodeAt(i);
        if (code >= 0x4e00 && code <= 0x9fff) {
          hasCjk = true;
          break;
        }
      }
      if (hasCjk) {
        out = await run(MetadataProviders.buildMbQuery(title, '', album));
      }
    }
    return out;
  }

  // ---------------------------------------------------------------- iTunes

  static async itunes(title: string, artist: string, limit: number = 8): Promise<MatchCandidate[]> {
    if (title.trim() == '') {
      return [];
    }
    const term = `${title} ${artist}`.trim();
    const url = `https://itunes.apple.com/search?term=${ncbi(term)}&media=music&entity=song&limit=${limit}`;
    try {
      const text = await HttpStack.getTextEx(url, undefined, 7000);
      const resp = JSON.parse(text) as ItunesResp;
      if (resp.results == undefined) {
        return [];
      }
      const out: MatchCandidate[] = [];
      let rank = 0;
      for (const item of resp.results) {
        const name = item.trackName != undefined ? item.trackName.trim() : '';
        const artistName = item.artistName != undefined ? item.artistName.trim() : '';
        if (name == '' || artistName == '') {
          continue;
        }
        const c = new MatchCandidate();
        c.source = 'iTunes';
        c.title = name;
        c.artist = artistName;
        c.albumartist = artistName;
        c.album = item.collectionName != undefined ? item.collectionName.trim() : '';
        c.date = item.releaseDate != undefined ? item.releaseDate.substring(0, 4) : '';
        c.genre = item.primaryGenreName != undefined ? item.primaryGenreName : '';
        c.track = item.trackNumber != undefined ? `${item.trackNumber}` : '';
        c.disc = item.discNumber != undefined ? `${item.discNumber}` : '';
        let art = item.artworkUrl100 != undefined ? item.artworkUrl100 : '';
        art = art.replace('100x100bb', '600x600bb').replace('100x100', '600x600');
        c.coverUrl = art;
        c.recordingId = item.trackId != undefined ? `${item.trackId}` : '';
        c.duration = item.trackTimeMillis != undefined ? item.trackTimeMillis / 1000 : 0;
        c.rawScore = Math.max(0, 0.9 - rank * 0.06);
        c.note = c.duration > 0 ? `${Math.round(c.duration)}s` : '';
        out.push(c);
        rank++;
      }
      return out;
    } catch (e) {
      return [];
    }
  }

  // ---------------------------------------------------------------- 歌词

  private static pickBest(cands: MatchCandidate[], title: string, durationSec: number): MatchCandidate | undefined {
    const usable: MatchCandidate[] = [];
    for (const c of cands) {
      if (c.recordingId != '') {
        usable.push(c);
      }
    }
    if (usable.length == 0) {
      return undefined;
    }
    const byTitle: MatchCandidate[] = [];
    for (const c of usable) {
      if (similarityStrict(title, c.title) >= 0.7) {
        byTitle.push(c);
      }
    }
    if (byTitle.length == 0) {
      return undefined;
    }
    if (durationSec > 0) {
      let best: MatchCandidate | undefined = undefined;
      let bestDelta = 99999;
      for (const c of byTitle) {
        if (c.duration <= 0) {
          continue;
        }
        const delta = Math.abs(c.duration - durationSec);
        if (delta <= 30 && delta < bestDelta) {
          best = c;
          bestDelta = delta;
        }
      }
      return best;
    }
    return byTitle[0];
  }

  private static parseLrcResult(text: string, source: string, cand: MatchCandidate): LyricsResult | undefined {
    const trimmed = text.trim();
    if (trimmed == '') {
      return undefined;
    }
    const res = new LyricsResult();
    res.source = source;
    res.lines = LyricsService.parseLrc(trimmed);
    if (res.lines.length > 0) {
      return res;
    }
    const plain = new LyricsResult();
    plain.source = source;
    plain.plain = trimmed;
    plain.lines = LyricsService.parseLrc(trimmed);
    return plain.lines.length > 0 ? plain : undefined;
  }

  static async netmusicLyrics(title: string, artist: string, durationSec: number): Promise<LyricsResult | undefined> {
    const cands = await MetadataProviders.netmusic(title, artist, 5);
    const picked = MetadataProviders.pickBest(cands, title, durationSec);
    if (picked == undefined) {
      return undefined;
    }
    try {
      const url = `https://music.163.com/api/song/lyric?id=${ncbi(picked.recordingId)}&lv=1&kv=1&tv=-1`;
      const text = await HttpStack.getTextEx(url, undefined, 7000);
      const resp = JSON.parse(text) as NmLyricResp;
      if (resp.lrc == undefined || resp.lrc.lyric == undefined) {
        return undefined;
      }
      return MetadataProviders.parseLrcResult(resp.lrc.lyric, '网易云', picked);
    } catch (e) {
      return undefined;
    }
  }

  static async qqmusicLyrics(title: string, artist: string, durationSec: number): Promise<LyricsResult | undefined> {
    const cands = await MetadataProviders.qqmusic(title, artist, 5);
    const picked = MetadataProviders.pickBest(cands, title, durationSec);
    if (picked == undefined) {
      return undefined;
    }
    try {
      const url = `https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg?songmid=${ncbi(picked.recordingId)}` +
        `&format=json&nobase64=1&g_tk=5381`;
      const text = await HttpStack.getTextEx(url, QQ_HEADERS, 7000);
      const resp = JSON.parse(text) as QqLyricResp;
      if (resp.lyric == undefined) {
        return undefined;
      }
      return MetadataProviders.parseLrcResult(resp.lyric, 'QQ音乐', picked);
    } catch (e) {
      return undefined;
    }
  }

  static async lrclibLyrics(title: string, artist: string, album: string): Promise<LyricsResult | undefined> {
    try {
      const tries: string[] = [];
      if (artist != '') {
        tries.push(`track_name=${ncbi(title)}&artist_name=${ncbi(artist)}`);
      }
      tries.push(`track_name=${ncbi(title)}`);
      for (const q of tries) {
        const text = await HttpStack.getTextEx(`https://lrclib.net/api/search?${q}`, undefined, 7000);
        const arr = JSON.parse(text) as LrclibItem[];
        if (arr.length > 0) {
          const item = arr[0];
          const synced = item.syncedLyrics != undefined ? item.syncedLyrics : '';
          const plain = item.plainLyrics != undefined ? item.plainLyrics : '';
          const body = synced != '' ? synced : plain;
          if (body.trim() != '') {
            const res = new LyricsResult();
            res.source = 'lrclib';
            res.plain = plain;
            res.lines = LyricsService.parseLrc(body);
            if (res.lines.length > 0) {
              return res;
            }
          }
        }
      }
    } catch (e) {
      // 静默降级
    }
    return undefined;
  }

  static async fetchLyrics(title: string, artist: string, album: string,
    durationSec: number, useNetmusic: boolean, useQq: boolean, useLrclib: boolean): Promise<LyricsResult | undefined> {
    if (useNetmusic) {
      const r = await MetadataProviders.netmusicLyrics(title, artist, durationSec);
      if (r != undefined) {
        return r;
      }
    }
    if (useQq) {
      const r = await MetadataProviders.qqmusicLyrics(title, artist, durationSec);
      if (r != undefined) {
        return r;
      }
    }
    if (useLrclib) {
      const r = await MetadataProviders.lrclibLyrics(title, artist, album);
      if (r != undefined) {
        return r;
      }
    }
    // 歌手字段常是拼音/群星/写错，带歌手搜不到时只按歌名再试一轮（对齐桌面版）
    if (artist.trim() != '') {
      if (useNetmusic) {
        const r = await MetadataProviders.netmusicLyrics(title, '', durationSec);
        if (r != undefined) {
          return r;
        }
      }
      if (useQq) {
        const r = await MetadataProviders.qqmusicLyrics(title, '', durationSec);
        if (r != undefined) {
          return r;
        }
      }
      if (useLrclib) {
        return await MetadataProviders.lrclibLyrics(title, '', album);
      }
    }
    return undefined;
  }

  static async resolveCover(cand: MatchCandidate): Promise<string> {
    if (cand.coverUrl != '') {
      return cand.coverUrl;
    }
    if (cand.source == '网易云' && cand.recordingId != '') {
      return await MetadataProviders.netmusicCoverUrl(cand);
    }
    // MusicBrainz 候选只带 releaseId，封面在 Cover Art Archive
    if (cand.source == 'MusicBrainz' && cand.releaseId != '') {
      return `https://coverartarchive.org/release/${cand.releaseId}/front-500`;
    }
    return '';
  }
}
