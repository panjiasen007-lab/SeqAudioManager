import { relationalStore } from '@kit.ArkData';
import { BusinessError } from '@ohos.base';

// 数据层迁移：v1（tracks）→ v2（tracks 增列 + search_blob + 索引）→ v3（鉴定/匹配字段）
// → v4（albumartist / disc_no）→ v5（CUE 预间隙起点）
// 可重入：按 settings.schema_version / PRAGMA user_version 判定，成功后旧表保留为 tracks_v1_backup。
const TARGET_VERSION: number = 5;

const SQL_TRACKS_V2 = `CREATE TABLE IF NOT EXISTS tracks_v2 (
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
  added_at_ms INTEGER,
  cue_path TEXT,
  is_cue_child INTEGER DEFAULT 0,
  cue_track_no INTEGER DEFAULT 0,
  cue_start_ms INTEGER DEFAULT 0,
  cue_end_ms INTEGER DEFAULT 0,
  replay_gain REAL DEFAULT 0,
  health_status TEXT DEFAULT 'ok',
  health_detail TEXT,
  search_blob TEXT,
  lyrics TEXT,
  cover_blob_path TEXT,
  meta_source TEXT,
  meta_checked_at INTEGER DEFAULT 0
)`;

const SQL_COPY = `INSERT INTO tracks_v2
  (id, path, file_name, ext, title, artist, album, genre, year, track_no,
   duration_ms, sample_rate, channels, bit_depth, bitrate_kbps, size_bytes, mtime_ms, added_at_ms,
   cue_path, is_cue_child, cue_track_no, cue_start_ms, cue_end_ms, replay_gain,
   health_status, health_detail, search_blob, lyrics, cover_blob_path, meta_source, meta_checked_at)
  SELECT id, path, file_name, ext, title, artist, album, genre, year, track_no,
   duration_ms, sample_rate, channels, bit_depth, bitrate_kbps, size_bytes, mtime_ms, added_at_ms,
   '', 0, 0, 0, 0, 0, 'ok', '',
   LOWER(COALESCE(title,'') || ' ' || COALESCE(artist,'') || ' ' || COALESCE(album,'') ||
         ' ' || COALESCE(file_name,'') || ' ' || COALESCE(genre,'') || ' ' || COALESCE(year,'')),
   '', '', '', 0
  FROM tracks`;

const SQL_INDEXES: string[] = [
  'CREATE INDEX IF NOT EXISTS idx_tracks_artist ON tracks(artist)',
  'CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album)',
  'CREATE INDEX IF NOT EXISTS idx_tracks_search ON tracks(search_blob)',
  'CREATE INDEX IF NOT EXISTS idx_tracks_health ON tracks(health_status)',
  'CREATE INDEX IF NOT EXISTS idx_tracks_cue ON tracks(cue_path)',
  'CREATE INDEX IF NOT EXISTS idx_tracks_verdict ON tracks(verdict)'
];

const SQL_V3_COLUMNS: string[] = [
  "ALTER TABLE tracks ADD COLUMN verdict TEXT DEFAULT ''",
  'ALTER TABLE tracks ADD COLUMN verdict_confidence REAL DEFAULT 0',
  "ALTER TABLE tracks ADD COLUMN verdict_reason TEXT DEFAULT ''",
  'ALTER TABLE tracks ADD COLUMN analyzed_at INTEGER DEFAULT 0',
  'ALTER TABLE tracks ADD COLUMN match_confidence REAL DEFAULT 0'
];

const SQL_V4_COLUMNS: string[] = [
  "ALTER TABLE tracks ADD COLUMN albumartist TEXT DEFAULT ''",
  'ALTER TABLE tracks ADD COLUMN disc_no INTEGER DEFAULT 0'
];

const SQL_V5_COLUMNS: string[] = [
  'ALTER TABLE tracks ADD COLUMN cue_pregap_ms INTEGER DEFAULT 0'
];

async function tableExists(store: relationalStore.RdbStore, name: string): Promise<boolean> {
  try {
    const rs = await store.querySql(
      "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?", [name]);
    const found = rs.goToNextRow();
    rs.close();
    return found;
  } catch (e) {
    const err = e as BusinessError;
    console.error(`DbMigrate.tableExists(${name}) fail: ${err.message}`);
    return false;
  }
}

async function readSetting(store: relationalStore.RdbStore, key: string): Promise<string> {
  try {
    const rs = await store.querySql('SELECT value FROM settings WHERE key = ?', [key]);
    let v = '';
    if (rs.goToNextRow()) {
      v = rs.getString(0);
    }
    rs.close();
    return v;
  } catch (e) {
    return '';
  }
}

async function writeSetting(store: relationalStore.RdbStore, key: string, value: string): Promise<void> {
  await store.executeSql(
    'INSERT INTO settings(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value',
    [key, value]);
}

async function readPragmaVersion(store: relationalStore.RdbStore): Promise<number> {
  try {
    const rs = await store.querySql('PRAGMA user_version');
    let v = 0;
    if (rs.goToNextRow()) {
      v = rs.getLong(0);
    }
    rs.close();
    return v;
  } catch (e) {
    return 0;
  }
}

async function currentVersion(store: relationalStore.RdbStore): Promise<number> {
  const setting = parseInt(await readSetting(store, 'schema_version'));
  if (!isNaN(setting) && setting > 0) {
    return setting;
  }
  const pragma = await readPragmaVersion(store);
  if (pragma > 0) {
    return pragma;
  }
  return (await tableExists(store, 'tracks')) ? 1 : 0;
}

async function markVersion(store: relationalStore.RdbStore): Promise<void> {
  try {
    await store.executeSql(`PRAGMA user_version = ${TARGET_VERSION}`);
  } catch (e) {
    // 部分 Rdb 实现不允许 PRAGMA 写入；settings 为准
  }
  await writeSetting(store, 'schema_version', `${TARGET_VERSION}`);
}

export class DbMigrate {
  static async migrate(store: relationalStore.RdbStore): Promise<void> {
    const ver = await currentVersion(store);
    await store.executeSql(SQL_TRACKS_V2);
    if (ver >= TARGET_VERSION) {
      // 上次迁移中途失败留下的临时表，安全清掉
      await store.executeSql('DROP TABLE IF EXISTS tracks_v2');
    } else if (ver == 1 && await tableExists(store, 'tracks')) {
      await store.executeSql('DELETE FROM tracks_v2');
      try {
        await store.executeSql(SQL_COPY);
      } catch (e) {
        // 复制失败：保留旧表，删除临时表后可重试
        await store.executeSql('DROP TABLE IF EXISTS tracks_v2');
        const err = e as BusinessError;
        console.error(`DbMigrate copy fail: ${err.message}`);
        return;
      }
      if (await tableExists(store, 'tracks_v1_backup')) {
        await store.executeSql('DROP TABLE tracks_v1_backup');
      }
      try {
        await store.executeSql('ALTER TABLE tracks RENAME TO tracks_v1_backup');
        await store.executeSql('ALTER TABLE tracks_v2 RENAME TO tracks');
      } catch (e) {
        // 重命名失败：尽力恢复旧表，下次启动可重试
        if (!(await tableExists(store, 'tracks')) && await tableExists(store, 'tracks_v1_backup')) {
          await store.executeSql('ALTER TABLE tracks_v1_backup RENAME TO tracks');
        }
        const err = e as BusinessError;
        console.error(`DbMigrate rename fail: ${err.message}`);
        return;
      }
    } else if (ver == 0) {
      // 全新库：没有老数据，直接把空 v2 表改名为 tracks
      await store.executeSql('ALTER TABLE tracks_v2 RENAME TO tracks');
    }
    for (const sql of SQL_INDEXES) {
      try {
        await store.executeSql(sql);
      } catch (e) {
        const err = e as BusinessError;
        console.error(`DbMigrate index fail: ${err.message}`);
      }
    }
    if (ver < 3) {
      for (const sql of SQL_V3_COLUMNS) {
        try {
          await store.executeSql(sql);
        } catch (e) {
          // 列已存在：可重入迁移
        }
      }
    }
    if (ver < 4) {
      for (const sql of SQL_V4_COLUMNS) {
        try {
          await store.executeSql(sql);
        } catch (e) {
          // 列已存在：可重入迁移
        }
      }
    }
    if (ver < 5) {
      for (const sql of SQL_V5_COLUMNS) {
        try {
          await store.executeSql(sql);
        } catch (e) {
          // 列已存在：可重入迁移
        }
      }
    }
    await markVersion(store);
  }
}
