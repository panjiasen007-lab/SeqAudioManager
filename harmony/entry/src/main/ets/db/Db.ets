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

  // 转成 ValuesBucket（列名与表结构一致）；已有 id 时一并写入以保持主键稳定
  private static toBucket(t: Track): relationalStore.ValuesBucket {
    refreshSearchBlob(t);
    const b: relationalStore.ValuesBucket = {
      'path': t.path,
      'file_name': t.fileName,
      'ext': t.ext,
      'title': t.title,
      'artist': t.artist,
      'album': t.album,
      'albumartist': t.albumartist,
      'genre': t.genre,
      'year': t.year,
      'track_no': t.trackNo,
      'disc_no': t.discNo,
      'duration_ms': t.durationMs,
      'sample_rate': t.sampleRate,
      'channels': t.channels,
      'bit_depth': t.bitDepth,
      'bitrate_kbps': t.bitrateKbps,
      'size_bytes': t.sizeBytes,
      'mtime_ms': t.mtimeMs,
      'added_at_ms': Date.now(),
      'cue_path': t.cuePath,
      'is_cue_child': t.isCueChild,
      'cue_track_no': t.cueTrackNo,
      'cue_start_ms': t.cueStartMs,
      'cue_end_ms': t.cueEndMs,
      'cue_pregap_ms': t.cuePregapMs,
      'replay_gain': t.replayGain,
      'health_status': t.healthStatus,
      'health_detail': t.healthDetail,
      'search_blob': t.searchBlob,
      'lyrics': t.lyrics,
      'cover_blob_path': t.coverBlobPath,
      'meta_source': t.metaSource,
      'meta_checked_at': t.metaCheckedAt,
      'verdict': t.verdict,
      'verdict_confidence': t.verdictConfidence,
      'verdict_reason': t.verdictReason,
      'analyzed_at': t.analyzedAt,
      'match_confidence': t.matchConfidence
    };
    if (t.id > 0) {
      b['id'] = t.id;
    }
    return b;
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
    t.albumartist = readStr(rs, 'albumartist');
    t.genre = readStr(rs, 'genre');
    t.year = readStr(rs, 'year');
    t.trackNo = readLong(rs, 'track_no');
    t.discNo = readLong(rs, 'disc_no');
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
    t.cuePregapMs = readLong(rs, 'cue_pregap_ms');
    t.replayGain = readDouble(rs, 'replay_gain');
    t.healthStatus = readStr(rs, 'health_status') || 'ok';
    t.healthDetail = readStr(rs, 'health_detail');
    t.searchBlob = readStr(rs, 'search_blob');
    t.lyrics = readStr(rs, 'lyrics');
    t.coverBlobPath = readStr(rs, 'cover_blob_path');
    t.metaSource = readStr(rs, 'meta_source');
    t.metaCheckedAt = readLong(rs, 'meta_checked_at');
    t.verdict = readStr(rs, 'verdict');
    t.verdictConfidence = readDouble(rs, 'verdict_confidence');
    t.verdictReason = readStr(rs, 'verdict_reason');
    t.analyzedAt = readLong(rs, 'analyzed_at');
    t.matchConfidence = readDouble(rs, 'match_confidence');
    return t;
  }

  private static argsOf(t: Track, withId: boolean): Array<relationalStore.ValueType> {
    refreshSearchBlob(t);
    const args: Array<relationalStore.ValueType> = [
      t.path, t.fileName, t.ext, t.title, t.artist, t.album, t.albumartist, t.genre, t.year,
      t.trackNo, t.discNo,
      t.durationMs, t.sampleRate, t.channels, t.bitDepth, t.bitrateKbps, t.sizeBytes, t.mtimeMs,
      t.cuePath, t.isCueChild, t.cueTrackNo, t.cueStartMs, t.cueEndMs, t.cuePregapMs, t.replayGain,
      t.healthStatus, t.healthDetail, t.searchBlob, t.lyrics, t.coverBlobPath,
      t.metaSource, t.metaCheckedAt, t.verdict, t.verdictConfidence, t.verdictReason,
      t.analyzedAt, t.matchConfidence
    ];
    if (withId) {
      args.push(t.id);
    } else {
      args.push(Date.now()); // added_at_ms
    }
    return args;
  }

  private static readonly INSERT_COLS: string =
    `path, file_name, ext, title, artist, album, albumartist, genre, year, track_no, disc_no,
     duration_ms, sample_rate, channels, bit_depth, bitrate_kbps, size_bytes, mtime_ms,
     cue_path, is_cue_child, cue_track_no, cue_start_ms, cue_end_ms, cue_pregap_ms, replay_gain,
     health_status, health_detail, search_blob, lyrics, cover_blob_path, meta_source, meta_checked_at,
     verdict, verdict_confidence, verdict_reason, analyzed_at, match_confidence,
     added_at_ms`;

  // 批量写入（path 唯一，冲突则替换）；返回实际写入条数
  // 注意：relationalStore 的 executeSql 对超长 upsert 语句（>1KB）会报 14800021，
  // 因此改走 batchInsert（内部使用 ON_CONFLICT_REPLACE），失败再逐条 insert 兜底。
  static async upsertTracks(tracks: Track[]): Promise<number> {
    const store = Db.store;
    if (!store || tracks.length == 0) {
      return 0;
    }
    const CHUNK = 200;
    let written = 0;
    for (let i = 0; i < tracks.length; i += CHUNK) {
      const part = tracks.slice(i, i + CHUNK);
      const buckets: relationalStore.ValuesBucket[] = part.map((t: Track) => Db.toBucket(t));
      try {
        const n = await store.batchInsert('tracks', buckets);
        if (n > 0) {
          written += n;
          continue;
        }
      } catch (e) {
        const err = e as BusinessError;
        console.error(`Db.upsertTracks batchInsert fail code=${err.code} msg=${err.message}`);
      }
      for (const b of buckets) {
        try {
          await store.insert('tracks', b, relationalStore.ConflictResolution.ON_CONFLICT_REPLACE);
          written++;
        } catch (e2) {
          const err2 = e2 as BusinessError;
          console.error(`Db.upsertTrack fail code=${err2.code} msg=${err2.message} path=${b['path']}`);
        }
      }
    }
    return written;
  }

  // 删除路径中包含双斜杠的历史脏数据（扫描会以规范路径重新写入）
  static async purgeBadPaths(): Promise<void> {
    const store = Db.store;
    if (!store) {
      return;
    }
    try {
      await store.executeSql("DELETE FROM tracks WHERE path LIKE '%//%'");
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.purgeBadPaths fail: ${err.message}`);
    }
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

  // 仅从曲库移除记录，不删除磁盘文件（桌面版「移除」的鸿蒙对应实现）
  static async deleteTrack(id: number): Promise<void> {
    const store = Db.store;
    if (!store || id <= 0) {
      return;
    }
    try {
      await store.executeSql('DELETE FROM tracks WHERE id = ?', [id]);
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.deleteTrack fail: ${err.message}`);
    }
  }

  static async deleteTracks(ids: number[]): Promise<void> {
    const store = Db.store;
    if (!store || ids.length == 0) {
      return;
    }
    const marks = ids.map(() => '?').join(',');
    try {
      await store.executeSql(`DELETE FROM tracks WHERE id IN (${marks})`, ids);
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.deleteTracks fail: ${err.message}`);
    }
  }

  // 清理 CUE 母文件时同步移除其逻辑轨
  static async deleteTracksByCuePath(cuePath: string): Promise<void> {
    const store = Db.store;
    if (!store || cuePath == '') {
      return;
    }
    try {
      await store.executeSql('DELETE FROM tracks WHERE cue_path = ?', [cuePath]);
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.deleteTracksByCuePath fail: ${err.message}`);
    }
  }

  static async setLyrics(id: number, lyrics: string): Promise<void> {
    const store = Db.store;
    if (!store) {
      return;
    }
    try {
      await store.executeSql('UPDATE tracks SET lyrics = ? WHERE id = ?', [lyrics, id]);
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.setLyrics fail: ${err.message}`);
    }
  }

  static async deleteCueChildren(): Promise<void> {
    const store = Db.store;
    if (!store) {
      return;
    }
    try {
      await store.executeSql('DELETE FROM tracks WHERE is_cue_child = 1');
    } catch (e) {
      const err = e as BusinessError;
      console.error(`Db.deleteCueChildren fail: ${err.message}`);
    }
  }

  static async findTrackByFileName(name: string): Promise<Track | undefined> {
    const store = Db.store;
    if (!store) {
      return undefined;
    }
    try {
      const rs = await store.querySql(
        'SELECT * FROM tracks WHERE is_cue_child = 0 AND lower(file_name) = lower(?) LIMIT 1', [name]);
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
