import { Db } from '../db/Db';
import { PlayerEngine, PlayMode } from '../player/PlayerEngine';

const SEVEN_DAYS_MS: number = 7 * 24 * 3600 * 1000;
const SAVE_INTERVAL_MS: number = 5000;

export class SessionSnapshot {
  queuePaths: string[] = [];
  lastTrackPath: string = '';
  lastPositionMs: number = 0;
  playMode: number = PlayMode.LOOP;
  volume: number = 1;
  eqEnabled: boolean = false;
  eqBandsJson: string = '';
  outputMode: string = 'shared';
  followSourceRate: boolean = true;
  savedAtMs: number = 0;
}

// 播放会话持久化：settings KV，5s 节流 + 关键事件立即写入
export class SessionStore {
  private static lastSavedAt: number = 0;
  private static timer: number = -1;

  static async load(): Promise<SessionSnapshot> {
    const s = new SessionSnapshot();
    const pathsRaw = await Db.getSetting('last_queue_json', '[]');
    try {
      const arr = JSON.parse(pathsRaw) as string[];
      if (arr.length > 0) {
        s.queuePaths = arr;
      }
    } catch (e) {
      // 配置损坏：不恢复队列
    }
    s.lastTrackPath = await Db.getSetting('last_track_id', '');
    s.lastPositionMs = parseInt(await Db.getSetting('last_position_ms', '0'));
    if (isNaN(s.lastPositionMs) || s.lastPositionMs < 0) {
      s.lastPositionMs = 0;
    }
    const mode = parseInt(await Db.getSetting('play_mode', `${PlayMode.LOOP}`));
    s.playMode = isNaN(mode) ? PlayMode.LOOP : mode;
    const vol = parseFloat(await Db.getSetting('volume', '1'));
    s.volume = isNaN(vol) ? 1 : Math.max(0, Math.min(1, vol));
    s.eqEnabled = (await Db.getSetting('eq_enabled', '0')) == '1';
    s.eqBandsJson = await Db.getSetting('eq_bands_json', '');
    s.outputMode = await Db.getSetting('output_mode', 'shared');
    s.followSourceRate = (await Db.getSetting('follow_source_rate', '1')) != '0';
    s.savedAtMs = parseInt(await Db.getSetting('session_saved_at', '0'));
    if (isNaN(s.savedAtMs)) {
      s.savedAtMs = 0;
    }
    // 超过 7 天的会话不再恢复队列
    if (s.savedAtMs > 0 && Date.now() - s.savedAtMs > SEVEN_DAYS_MS) {
      s.queuePaths = [];
      s.lastPositionMs = 0;
    }
    return s;
  }

  static async saveNow(engine: PlayerEngine): Promise<void> {
    const snapshot = new SessionSnapshot();
    if (engine.queue.length > 0) {
      snapshot.queuePaths = engine.queue.map((t) => t.path);
      const cur = engine.current();
      snapshot.lastTrackPath = cur ? cur.path : '';
      snapshot.lastPositionMs = Math.max(0, Math.round(engine.positionMs));
    }
    snapshot.playMode = engine.mode;
    snapshot.volume = engine.volume;
    snapshot.savedAtMs = Date.now();
    await Db.setSetting('last_queue_json', JSON.stringify(snapshot.queuePaths));
    await Db.setSetting('last_track_id', snapshot.lastTrackPath);
    await Db.setSetting('last_position_ms', `${snapshot.lastPositionMs}`);
    await Db.setSetting('play_mode', `${snapshot.playMode}`);
    await Db.setSetting('volume', `${snapshot.volume}`);
    await Db.setSetting('session_saved_at', `${snapshot.savedAtMs}`);
    SessionStore.lastSavedAt = snapshot.savedAtMs;
  }

  // 关键事件（切歌/seek/暂停/音量/模式）立即落库；进度由定时节流负责
  static markDirty(engine: PlayerEngine, force: boolean): void {
    if (force || Date.now() - SessionStore.lastSavedAt >= SAVE_INTERVAL_MS) {
      SessionStore.saveNow(engine);
    }
  }

  static startAutoSave(engine: PlayerEngine): void {
    if (SessionStore.timer >= 0) {
      return;
    }
    SessionStore.timer = setInterval(() => {
      if (engine.queue.length > 0) {
        SessionStore.saveNow(engine);
      }
    }, SAVE_INTERVAL_MS);
  }

  static stopAutoSave(): void {
    if (SessionStore.timer >= 0) {
      clearInterval(SessionStore.timer);
      SessionStore.timer = -1;
    }
  }
}
