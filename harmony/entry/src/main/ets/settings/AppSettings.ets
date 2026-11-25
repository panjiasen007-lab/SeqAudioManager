import { Db } from '../db/Db';

// 鸿蒙版可落地的设置子集：只保留真正影响行为的字段。
export class AppSettingsData {
  backup: boolean = true;
  autoLyrics: boolean = false;
  opacity: number = 95;
  volume: number = 80;
  playMode: number = 1;
  // 联网匹配
  useNetmusic: boolean = true;
  useQqmusic: boolean = true;
  useMusicBrainz: boolean = true;
  useItunes: boolean = true;
  useLrclib: boolean = true;
  useCoverart: boolean = true;
  threshold: number = 62;
  autoCover: boolean = true;
  autoRename: boolean = false;
  renameTemplate: string = '{artist} - {title}';
  // 清理假无损时是否连同「疑似」一起隔离（桌面版默认开启）
  cleanupIncludeSuspect: boolean = true;
  // 视觉主题（与桌面版 ui/theme.py 对齐）
  theme: string = '深空';
  // CUE 分轨在下一轨 INDEX 00 截止（去掉轨尾静音预间隙）
  cueSplitTrimPregap: boolean = false;
}

export class AppSettings {
  static async load(): Promise<AppSettingsData> {
    const d = new AppSettingsData();
    d.backup = (await Db.getSetting('tag_write_backup', '1')) != '0';
    d.autoLyrics = (await Db.getSetting('lyrics_auto', '0')) == '1';
    const opacity = parseInt(await Db.getSetting('ui_opacity', '95'));
    d.opacity = isNaN(opacity) ? 95 : Math.max(10, Math.min(100, opacity));
    const volume = parseInt(await Db.getSetting('volume', '80'));
    d.volume = isNaN(volume) ? 80 : Math.max(0, Math.min(100, volume));
    const mode = parseInt(await Db.getSetting('play_mode', '1'));
    d.playMode = isNaN(mode) ? 1 : mode;
    d.useNetmusic = (await Db.getSetting('match_netmusic', '1')) == '1';
    d.useQqmusic = (await Db.getSetting('match_qqmusic', '1')) == '1';
    d.useMusicBrainz = (await Db.getSetting('match_musicbrainz', '1')) == '1';
    d.useItunes = (await Db.getSetting('match_itunes', '1')) == '1';
    d.useLrclib = (await Db.getSetting('match_lrclib', '1')) == '1';
    d.useCoverart = (await Db.getSetting('match_coverart', '1')) == '1';
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
    return d;
  }

  static async save(d: AppSettingsData): Promise<void> {
    await Db.setSetting('tag_write_backup', d.backup ? '1' : '0');
    await Db.setSetting('lyrics_auto', d.autoLyrics ? '1' : '0');
    await Db.setSetting('ui_opacity', `${Math.round(d.opacity)}`);
    await Db.setSetting('volume', `${Math.round(d.volume)}`);
    await Db.setSetting('play_mode', `${d.playMode}`);
    await Db.setSetting('match_netmusic', d.useNetmusic ? '1' : '0');
    await Db.setSetting('match_qqmusic', d.useQqmusic ? '1' : '0');
    await Db.setSetting('match_musicbrainz', d.useMusicBrainz ? '1' : '0');
    await Db.setSetting('match_itunes', d.useItunes ? '1' : '0');
    await Db.setSetting('match_lrclib', d.useLrclib ? '1' : '0');
    await Db.setSetting('match_coverart', d.useCoverart ? '1' : '0');
    await Db.setSetting('match_threshold', `${Math.round(d.threshold)}`);
    await Db.setSetting('match_auto_cover', d.autoCover ? '1' : '0');
    await Db.setSetting('match_auto_rename', d.autoRename ? '1' : '0');
    await Db.setSetting('rename_template', d.renameTemplate);
    await Db.setSetting('cleanup_include_suspect', d.cleanupIncludeSuspect ? '1' : '0');
    await Db.setSetting('ui_theme', d.theme);
    await Db.setSetting('cue_split_trim_pregap', d.cueSplitTrimPregap ? '1' : '0');
  }
}
