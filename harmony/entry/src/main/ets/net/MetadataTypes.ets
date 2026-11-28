// 联网元数据候选与匹配配置。
export class MatchCandidate {
  source: string = '';
  title: string = '';
  artist: string = '';
  album: string = '';
  albumartist: string = '';
  date: string = '';
  genre: string = '';
  track: string = '';
  disc: string = '';
  coverUrl: string = '';
  recordingId: string = '';
  releaseId: string = '';
  duration: number = 0;
  rawScore: number = 0;
  confidence: number = 0;
  note: string = '';

  label(): string {
    return `${this.source} · ${this.title} · ${this.artist}`;
  }

  clone(): MatchCandidate {
    const c = new MatchCandidate();
    c.source = this.source;
    c.title = this.title;
    c.artist = this.artist;
    c.album = this.album;
    c.albumartist = this.albumartist;
    c.date = this.date;
    c.genre = this.genre;
    c.track = this.track;
    c.disc = this.disc;
    c.coverUrl = this.coverUrl;
    c.recordingId = this.recordingId;
    c.releaseId = this.releaseId;
    c.duration = this.duration;
    c.rawScore = this.rawScore;
    c.confidence = this.confidence;
    c.note = this.note;
    return c;
  }
}

export class MatchSettings {
  useMusicBrainz: boolean = true;
  useItunes: boolean = true;
  useNetmusic: boolean = true;
  useQqmusic: boolean = true;
  useLrclib: boolean = true;
  useCoverart: boolean = true;
  threshold: number = 0.62;
  autoCover: boolean = true;
  autoLyrics: boolean = true;
  autoRename: boolean = false;
  renameTemplate: string = '{artist} - {title}';
}

export class MatchResult {
  candidate: MatchCandidate | undefined = undefined;
  candidates: MatchCandidate[] = [];
  confidence: number = 0;
  accepted: boolean = false;
  reason: string = '';
}

export class PendingMatch {
  trackId: number = 0;
  candidate: MatchCandidate = new MatchCandidate();

  constructor(trackId: number, candidate: MatchCandidate) {
    this.trackId = trackId;
    this.candidate = candidate;
  }
}
