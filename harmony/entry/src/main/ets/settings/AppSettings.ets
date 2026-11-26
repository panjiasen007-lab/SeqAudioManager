import { Db } from '../db/Db';

// 鸿蒙版可落地的设置子集：只保留真正影响行为的字段。
export class AppSettingsData {
  backup: boolean = true;
  autoLyrics: boolean = false;
  opacity: number = 95;
  volume: number = 80;
  playMode: number = 1;
  // 联网匹配
  onlineEnabled: boolean = true;
  useNetmusic: boolean = true;
  useQqmusic: boolean = true;
  useMusicBrainz: boolean = true;
  useItunes: boolean = true;
  useLrclib: boolean = true;
  useCoverart: boolean = true;
  // 已有标签冲突策略：0 不覆盖已有信息 / 1 强制覆盖 / 2 仅空白才填充
  conflictMode: number = 2;
  threshold: number = 62;
  autoCover: boolean = true;
  autoRename: boolean = false;
  renameTemplate: string = '{artist} - {title}';
  // 扫描过滤：短于该时长的音频不入库（0 = 不过滤）
  minDurationSec: number = 0;
  // 清理假无损时是否连同「疑似」一起隔离（桌面版默认开启）
  cleanupIncludeSuspect: boolean = true;
  // 视觉主题（与桌面版 ui/theme.py 对齐）
  theme: string = '深空';
  // CUE 分轨在下一轨 INDEX 00 截止（去掉轨尾静音预间隙）
  cueSplitTrimPregap: boolean = false;
  // 曲库表格列显隐
  showAlbum: boolean = true;
  showFormat: boolean = true;
  showSpec: boolean = true;
  showConfidence: boolean = true;
  showDuration: boolean = true;
  showFileName: boolean = true;
  showYear: boolean = false;
  showKind: boolean = false;
  showSource: boolean = false;
}

export class AppSettings {
  // 音量键独立于会话快照（后者以 0~1 保存），并兼容旧版混写过的 volume 值
  private static async loadVolume(): Promise<number> {
    const raw = await Db.getSetting('ui_volume', '');
    let v = NaN;
    if (raw != '') {
      v = parseFloat(raw);
    } else {
      v = parseFloat(await Db.getSetting('volume', '80'));
      if (!isNaN(v) && v > 0 && v <= 1) {
        v = v * 100;
      }
    }
    return isNaN(v) ? 80 : Math.max(0, Math.min(100, Math.round(v)));
  }

  static async load(): Promise<AppSettingsData> {
    const d = new AppSettingsData();
    d.backup = (await Db.getSetting('tag_write_backup', '1')) != '0';
    d.autoLyrics = (await Db.getSetting('lyrics_auto', '0')) == '1';
    // 透明度默认 95%，最低 25%；旧版本（80/95/100 等）做一次性迁移
    const opacity = parseInt(await Db.getSetting('ui_opacity', '95'));
    const opacityMigrated = await Db.getSetting('ui_opacity_v3', '');
    if (opacityMigrated == '') {
      d.opacity = 95;
      await Db.setSetting('ui_opacity', '95');
      await Db.setSetting('ui_opacity_v3', '1');
    } else {
      d.opacity = isNaN(opacity) ? 95 : Math.max(25, Math.min(100, opacity));
    }
    const volume = await AppSettings.loadVolume();
    d.volume = volume;
    const mode = parseInt(await Db.getSetting('play_mode', '1'));
    d.playMode = isNaN(mode) ? 1 : mode;
    d.useNetmusic = (await Db.getSetting('match_netmusic', '1')) == '1';
    d.useQqmusic = (await Db.getSetting('match_qqmusic', '1')) == '1';
    d.useMusicBrainz = (await Db.getSetting('match_musicbrainz', '1')) == '1';
    d.useItunes = (await Db.getSetting('match_itunes', '1')) == '1';
    d.useLrclib = (await Db.getSetting('match_lrclib', '1')) == '1';
    d.useCoverart = (await Db.getSetting('match_coverart', '1')) == '1';
    d.onlineEnabled = (await Db.getSetting('match_online', '1')) == '1';
    const conflict = parseInt(await Db.getSetting('match_conflict', '2'));
    d.conflictMode = isNaN(conflict) ? 2 : Math.max(0, Math.min(2, conflict));
    const minDur = parseInt(await Db.getSetting('scan_min_duration', '0'));
    d.minDurationSec = isNaN(minDur) ? 0 : Math.max(0, Math.min(600, minDur));
    const threshold = parseInt(await Db.getSetting('match_threshold', '62'));
    d.threshold = isNaN(threshold) ? 62 : Math.max(30, Math.min(95, threshold));
    d.autoCover = (await Db.getSetting('match_auto_cover', '1')) == '1';
    d.autoRename = (await Db.getSetting('match_auto_rename', '0')) == '1';
    d.renameTemplate = await Db.getSetting('rename_template', '{artist} - {title}');
    if (d.renameTemplate.trim() == '') {
      d.renameTemplate = '{artist} - {title}';
    }
    d.cleanupIncludeSuspect = (await Db.getSetting('cleanup_include_suspect', '1')) == '1';
    d.theme = await Db.getSetting('ui_theme', '深空');
    if (d.theme.trim() == '') {
      d.theme = '深空';
    }
    d.cueSplitTrimPregap = (await Db.getSetting('cue_split_trim_pregap', '0')) == '1';
    d.showAlbum = (await Db.getSetting('ui_col_album', '1')) == '1';
    d.showFormat = (await Db.getSetting('ui_col_format', '1')) == '1';
    d.showSpec = (await Db.getSetting('ui_col_spec', '1')) == '1';
    d.showConfidence = (await Db.getSetting('ui_col_confidence', '1')) == '1';
    d.showDuration = (await Db.getSetting('ui_col_duration', '1')) == '1';
    d.showFileName = (await Db.getSetting('ui_col_filename', '1')) == '1';
    d.showYear = (await Db.getSetting('ui_col_year', '0')) == '1';
    d.showKind = (await Db.getSetting('ui_col_kind', '0')) == '1';
    d.showSource = (await Db.getSetting('ui_col_source', '0')) == '1';
    return d;
  }

  static async save(d: AppSettingsData): Promise<void> {
    await Db.setSetting('tag_write_backup', d.backup ? '1' : '0');
    await Db.setSetting('lyrics_auto', d.autoLyrics ? '1' : '0');
    await Db.setSetting('ui_opacity', `${Math.round(d.opacity)}`);
    await Db.setSetting('ui_volume', `${Math.round(d.volume)}`);
    await Db.setSetting('play_mode', `${d.playMode}`);
    await Db.setSetting('match_netmusic', d.useNetmusic ? '1' : '0');
    await Db.setSetting('match_qqmusic', d.useQqmusic ? '1' : '0');
    await Db.setSetting('match_musicbrainz', d.useMusicBrainz ? '1' : '0');
    await Db.setSetting('match_itunes', d.useItunes ? '1' : '0');
    await Db.setSetting('match_lrclib', d.useLrclib ? '1' : '0');
    await Db.setSetting('match_coverart', d.useCoverart ? '1' : '0');
    await Db.setSetting('match_online', d.onlineEnabled ? '1' : '0');
    await Db.setSetting('match_conflict', `${d.conflictMode}`);
    await Db.setSetting('scan_min_duration', `${Math.round(d.minDurationSec)}`);
    await Db.setSetting('match_threshold', `${Math.round(d.threshold)}`);
    await Db.setSetting('match_auto_cover', d.autoCover ? '1' : '0');
    await Db.setSetting('match_auto_rename', d.autoRename ? '1' : '0');
    await Db.setSetting('rename_template', d.renameTemplate);
    await Db.setSetting('cleanup_include_suspect', d.cleanupIncludeSuspect ? '1' : '0');
    await Db.setSetting('ui_theme', d.theme);
    await Db.setSetting('cue_split_trim_pregap', d.cueSplitTrimPregap ? '1' : '0');
    await Db.setSetting('ui_col_album', d.showAlbum ? '1' : '0');
    await Db.setSetting('ui_col_format', d.showFormat ? '1' : '0');
    await Db.setSetting('ui_col_spec', d.showSpec ? '1' : '0');
    await Db.setSetting('ui_col_confidence', d.showConfidence ? '1' : '0');
    await Db.setSetting('ui_col_duration', d.showDuration ? '1' : '0');
    await Db.setSetting('ui_col_filename', d.showFileName ? '1' : '0');
    await Db.setSetting('ui_col_year', d.showYear ? '1' : '0');
    await Db.setSetting('ui_col_kind', d.showKind ? '1' : '0');
    await Db.setSetting('ui_col_source', d.showSource ? '1' : '0');
  }
}
