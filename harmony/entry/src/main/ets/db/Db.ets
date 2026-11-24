import { relationalStore } from '@kit.ArkData';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import { Track, emptyTrack } from '../model/Track';

const SQL_TRACKS = `CREATE TABLE IF NOT EXISTS tracks (
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
    await store.executeSql(SQL_TRACKS);
    await store.executeSql(SQL_SETTINGS);
    await store.executeSql('CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)');
    Db.store = store;
  }

  private static rowToTrack(rs: relationalStore.ResultSet): Track {
    const t = emptyTrack();
    t.id = rs.getLong(rs.getColumnIndex('id'));
    t.path = rs.getString(rs.getColumnIndex('path'));
    t.fileName = rs.getString(rs.getColumnIndex('file_name'));
    t.ext = rs.getString(rs.getColumnIndex('ext'));
    t.title = rs.getString(rs.getColumnIndex('title'));
    t.artist = rs.getString(rs.getColumnIndex('artist'));
    t.album = rs.getString(rs.getColumnIndex('album'));
    t.genre = rs.getString(rs.getColumnIndex('genre'));
    t.year = rs.getString(rs.getColumnIndex('year'));
    t.trackNo = rs.getLong(rs.getColumnIndex('track_no'));
    t.durationMs = rs.getLong(rs.getColumnIndex('duration_ms'));
    t.sampleRate = rs.getLong(rs.getColumnIndex('sample_rate'));
    t.channels = rs.getLong(rs.getColumnIndex('channels'));
    t.bitDepth = rs.getLong(rs.getColumnIndex('bit_depth'));
    t.bitrateKbps = rs.getLong(rs.getColumnIndex('bitrate_kbps'));
    t.sizeBytes = rs.getLong(rs.getColumnIndex('size_bytes'));
    t.mtimeMs = rs.getLong(rs.getColumnIndex('mtime_ms'));
    return t;
  }

  // 批量写入（path 唯一，冲突则更新）；返回写入条数
  static async upsertTracks(tracks: Track[]): Promise<number> {
    const store = Db.store;
    if (!store) {
      return 0;
    }
    const sql = `INSERT INTO tracks
      (path, file_name, ext, title, artist, album, genre, year, track_no,
       duration_ms, sample_rate, channels, bit_depth, bitrate_kbps, size_bytes, mtime_ms, added_at_ms)
      VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
      ON CONFLICT(path) DO UPDATE SET
       file_name=excluded.file_name, ext=excluded.ext, title=excluded.title, artist=excluded.artist,
       album=excluded.album, genre=excluded.genre, year=excluded.year, track_no=excluded.track_no,
       duration_ms=excluded.duration_ms, sample_rate=excluded.sample_rate, channels=excluded.channels,
       bit_depth=excluded.bit_depth, bitrate_kbps=excluded.bitrate_kbps, size_bytes=excluded.size_bytes,
       mtime_ms=excluded.mtime_ms`;
    let n = 0;
    for (const t of tracks) {
      const args: Array<relationalStore.ValueType> = [
        t.path, t.fileName, t.ext, t.title, t.artist, t.album, t.genre, t.year, t.trackNo,
        t.durationMs, t.sampleRate, t.channels, t.bitDepth, t.bitrateKbps, t.sizeBytes, t.mtimeMs,
        Date.now()
      ];
      try {
        await store.executeSql(sql, args);
        n++;
      } catch (e) {
        const err = e as BusinessError;
        console.error(`Db.upsert fail: ${err.message}`);
      }
    }
    return n;
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
      sql += ' WHERE title LIKE ? OR artist LIKE ? OR album LIKE ? OR file_name LIKE ?';
      const like = `%${keyword.trim()}%`;
      args.push(like, like, like, like);
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
