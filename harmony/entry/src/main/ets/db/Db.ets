import { relationalStore } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import { Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { DbMigrate } from './DbMigrate';

const SQL_TRACKS_V1 = `CREATE TABLE IF NOT EXISTS tracks (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  path TEXT NOT NULL UNIQUE,
  file_name TEXT NOT NULL,
  ext TEXT,
  title TEXT,
  artist TEXT,
  album TEXT,
  genre TEXT,
  year TEXT,
  track_no INTEGER,
  duration_ms INTEGER,
  sample_rate INTEGER,
  channels INTEGER,
  bit_depth INTEGER,
  bitrate_kbps INTEGER,
  size_bytes INTEGER,
  mtime_ms INTEGER,
  added_at_ms INTEGER
)`;
const SQL_SETTINGS = `CREATE TABLE IF NOT EXISTS settings (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
)`;

export class Stats {
  count: number = 0;
  totalMs: number = 0;
  totalSize: number = 0;
}

function colIndex(rs: relationalStore.ResultSet, name: string): number {
  return rs.getColumnIndex(name);
}

function readStr(rs: relationalStore.ResultSet, name: string): string {
  const i = colIndex(rs, name);
  return i >= 0 ? rs.getString(i) : '';
}

function readLong(rs: relationalStore.ResultSet, name: string): number {
  const i = colIndex(rs, name);
  return i >= 0 ? rs.getLong(i) : 0;
}

function readDouble(rs: relationalStore.ResultSet, name: string): number {
  const i = colIndex(rs, name);
  return i >= 0 ? rs.getDouble(i) : 0;
}

export class Db {
  private static store?: relationalStore.RdbStore;

  static async init(ctx: common.UIAbilityContext): Promise<void> {
    if (Db.store) {
      return;
    }
    const cfg: relationalStore.StoreConfig = {
      name: 'seqaudio.db',
      securityLevel: relationalStore.SecurityLevel.S1
    };
    const store = await relationalStore.getRdbStore(ctx, cfg);
    await store.executeSql(SQL_TRACKS_V1);
    await store.executeSql(SQL_SETTINGS);
    await DbMigrate.migrate(store);
    Db.store = store;
  }

  private static rowToTrack(rs: relationalStore.ResultSet): Track {
    const t = emptyTrack();
    t.id = readLong(rs, 'id');
    t.path = readStr(rs, 'path');
    t.fileName = readStr(rs, 'file_name');
    t.ext = readStr(rs, 'ext');
    t.title = readStr(rs, 'title');
    t.artist = readStr(rs, 'artist');
    t.album = readStr(rs, 'album');
    t.genre = readStr(rs, 'genre');
    t.year = readStr(rs, 'year');
    t.trackNo = readLong(rs, 'track_no');
    t.durationMs = readLong(rs, 'duration_ms');
    t.sampleRate = readLong(rs, 'sample_rate');
    t.channels = readLong(rs, 'channels');
    t.bitDepth = readLong(rs, 'bit_depth');
    t.bitrateKbps = readLong(rs, 'bitrate_kbps');
    t.sizeBytes = readLong(rs, 'size_bytes');
    t.mtimeMs = readLong(rs, 'mtime_ms');
    t.cuePath = readStr(rs, 'cue_path');
    t.isCueChild = readLong(rs, 'is_cue_child');
    t.cueTrackNo = readLong(rs, 'cue_track_no');
    t.cueStartMs = readLong(rs, 'cue_start_ms');
    t.cueEndMs = readLong(rs, 'cue_end_ms');
    t.replayGain = readDouble(rs, 'replay_gain');
    t.healthStatus = readStr(rs, 'health_status') || 'ok';
    t.healthDetail = readStr(rs, 'health_detail');
    t.searchBlob = readStr(rs, 'search_blob');
    t.lyrics = readStr(rs, 'lyrics');
    t.coverBlobPath = readStr(rs, 'cover_blob_path');
    t.metaSource = readStr(rs, 'meta_source');
    t.metaCheckedAt = readLong(rs, 'meta_checked_at');
    return t;
  }

  private static argsOf(t: Track, withId: boolean): Array<relationalStore.ValueType> {
    refreshSearchBlob(t);
    const args: Array<relationalStore.ValueType> = [
      t.path, t.fileName, t.ext, t.title, t.artist, t.album, t.genre, t.year, t.trackNo,
      t.durationMs, t.sampleRate, t.channels, t.bitDepth, t.bitrateKbps, t.sizeBytes, t.mtimeMs,
      t.cuePath, t.isCueChild, t.cueTrackNo, t.cueStartMs, t.cueEndMs, t.replayGain,
      t.healthStatus, t.healthDetail, t.searchBlob, t.lyrics, t.coverBlobPath,
      t.metaSource, t.metaCheckedAt
    ];
    if (withId) {
      args.push(t.id);
    } else {
      args.push(Date.now()); // added_at_ms
    }
    return args;
  }

  private static readonly INSERT_COLS: string =
    `path, file_name, ext, title, artist, album, genre, year, track_no,
     duration_ms, sample_rate, channels, bit_depth, bitrate_kbps, size_bytes, mtime_ms,
     cue_path, is_cue_child, cue_track_no, cue_start_ms, cue_end_ms, replay_gain,
     health_status, health_detail, search_blob, lyrics, cover_blob_path, meta_source, meta_checked_at,
     added_at_ms`;

  // 批量写入（path 唯一，冲突则更新）；返回写入条数
  static async upsertTracks(tracks: Track[]): Promise<number> {
    const store = Db.store;
    if (!store || tracks.length == 0) {
      return 0;
    }
    // 每批 30 首（900 个占位符），兼容 SQLite 变量数上限
    const marks: string[] = [];
    for (let k = 0; k < 30; k++) {
      marks.push('?');
    }
    const sql = `INSERT INTO tracks (${Db.INSERT_COLS}) VALUES (${marks.join(',')})
      ON CONFLICT(path) DO UPDATE SET
       file_name=excluded.file_name, ext=excluded.ext, title=excluded.title, artist=excluded.artist,
       album=excluded.album, genre=excluded.genre, year=excluded.year, track_no=excluded.track_no,
       duration_ms=excluded.duration_ms, sample_rate=excluded.sample_rate, channels=excluded.channels,
       bit_depth=excluded.bit_depth, bitrate_kbps=excluded.bitrate_kbps, size_bytes=excluded.size_bytes,
       mtime_ms=excluded.mtime_ms, cue_path=excluded.cue_path, is_cue_child=excluded.is_cue_child,
       cue_track_no=excluded.cue_track_no, cue_start_ms=excluded.cue_start_ms, cue_end_ms=excluded.cue_end_ms,
       replay_gain=excluded.replay_gain, health_status=excluded.health_status,
       health_detail=excluded.health_detail, search_blob=excluded.search_blob, lyrics=excluded.lyrics,
       cover_blob_path=excluded.cover_blob_path, meta_source=excluded.meta_source,
       meta_checked_at=excluded.meta_checked_at`;
    let written = 0;
    for (let i = 0; i < tracks.length; i += 30) {
      const part = tracks.slice(i, i + 30);
      const args: Array<relationalStore.ValueType> = [];
      for (const t of part) {
        const one = Db.argsOf(t, false);
        for (const v of one) {
          args.push(v);
        }
      }
      try {
        await store.executeSql(sql, args);
        written += part.length;
      } catch (e) {
        const err = e as BusinessError;
        console.error(`Db.upsertTracks fail: ${err.message}`);
      }
    }
    return written;
  }

  // 按 id 全量更新（标签编辑/健康管理用）
  static async updateTrack(t: Track): Promise<boolean> {
    const store = Db.store;
    if (!store) {
      return false;
    }
    const cols = Db.INSERT_COLS.split(',').map((s: string) => s.trim());
    const sets = cols.filter((c: string) => c != 'added_at_ms').map((c: string) => `${c}=?`).join(', ');
    const sql = `UPDATE tracks SET ${sets} WHERE id = ?`;
    try {
      await store.executeSql(sql, Db.argsOf(t, true));
      return true;
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.updateTrack fail: ${err.message}`);
      return false;
    }
  }

  static async getTrackById(id: number): Promise<Track | undefined> {
    const store = Db.store;
    if (!store) {
      return undefined;
    }
    try {
      const rs = await store.querySql('SELECT * FROM tracks WHERE id = ?', [id]);
      let out: Track | undefined = undefined;
      if (rs.goToNextRow()) {
        out = Db.rowToTrack(rs);
      }
      rs.close();
      return out;
    } catch (e) {
      return undefined;
    }
  }

  static async tracksByPaths(paths: string[]): Promise<Track[]> {
    const out: Track[] = [];
    const store = Db.store;
    if (!store || paths.length == 0) {
      return out;
    }
    const map = new Map<string, Track>();
    // 分批查询，避免一次拼接过多占位符
    for (let i = 0; i < paths.length; i += 400) {
      const part = paths.slice(i, i + 400);
      const marks = part.map(() => '?').join(',');
      try {
        const rs = await store.querySql(`SELECT * FROM tracks WHERE path IN (${marks})`, part);
        while (rs.goToNextRow()) {
          const t = Db.rowToTrack(rs);
          map.set(t.path, t);
        }
        rs.close();
      } catch (e) {
        const err = e as BusinessError;
        console.error(`Db.tracksByPaths fail: ${err.message}`);
      }
    }
    for (const p of paths) {
      const t = map.get(p);
      if (t) {
        out.push(t);
      }
    }
    return out;
  }

  static async listTracks(keyword: string): Promise<Track[]> {
    const out: Track[] = [];
    const store = Db.store;
    if (!store) {
      return out;
    }
    let sql = 'SELECT * FROM tracks';
    const args: Array<relationalStore.ValueType> = [];
    if (keyword.trim() != '') {
      sql += ' WHERE search_blob LIKE ?';
      args.push(`%${keyword.trim().toLowerCase()}%`);
    }
    sql += ' ORDER BY artist, album, track_no, title';
    try {
      const rs = await store.querySql(sql, args);
      while (rs.goToNextRow()) {
        out.push(Db.rowToTrack(rs));
      }
      rs.close();
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.listTracks fail: ${err.message}`);
    }
    return out;
  }

  static async stats(): Promise<Stats> {
    const st = new Stats();
    const store = Db.store;
    if (!store) {
      return st;
    }
    try {
      const rs = await store.querySql(
        'SELECT COUNT(*) AS c, COALESCE(SUM(duration_ms),0) AS d, COALESCE(SUM(size_bytes),0) AS s FROM tracks');
      if (rs.goToNextRow()) {
        st.count = rs.getLong(0);
        st.totalMs = rs.getLong(1);
        st.totalSize = rs.getLong(2);
      }
      rs.close();
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.stats fail: ${err.message}`);
    }
    return st;
  }

  // 已入库 path -> mtime，用于跳过未变化文件
  static async pathMtimes(): Promise<Map<string, number>> {
    const m = new Map<string, number>();
    const store = Db.store;
    if (!store) {
      return m;
    }
    try {
      const rs = await store.querySql('SELECT path, mtime_ms FROM tracks');
      while (rs.goToNextRow()) {
        m.set(rs.getString(0), rs.getLong(1));
      }
      rs.close();
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.pathMtimes fail: ${err.message}`);
    }
    return m;
  }

  static async clearTracks(): Promise<void> {
    const store = Db.store;
    if (!store) {
      return;
    }
    await store.executeSql('DELETE FROM tracks');
  }

  static async deleteTracksUnderRoot(root: string): Promise<void> {
    const store = Db.store;
    if (!store) {
      return;
    }
    const prefix = root.endsWith('/') ? root : `${root}/`;
    try {
      await store.executeSql('DELETE FROM tracks WHERE path LIKE ?', [`${prefix}%`]);
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.deleteTracksUnderRoot fail: ${err.message}`);
    }
  }

  static async getSetting(key: string, defVal: string): Promise<string> {
    const store = Db.store;
    if (!store) {
      return defVal;
    }
    try {
      const rs = await store.querySql('SELECT value FROM settings WHERE key = ?', [key]);
      let v = defVal;
      if (rs.goToNextRow()) {
        v = rs.getString(0);
      }
      rs.close();
      return v;
    } catch (e) {
      return defVal;
    }
  }

  static async setSetting(key: string, value: string): Promise<void> {
    const store = Db.store;
    if (!store) {
      return;
    }
    await store.executeSql(
      'INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
      [key, value]);
  }
}
