// 主题 token：默认「深空」与桌面版 v1.8.2 的 ui/theme.py 调色板对齐。
// 所有组件只从这里取色/取尺寸；切换主题时由 ThemeStore.apply() 改写字段，
// 再重建主页面，让所有静态取值重新执行。
import { Track } from '../model/Track';

export class T {
  // 基础面板（深空默认值；ThemeStore 会整体替换）
  static BASE: string = '#070B14';
  static PANEL: string = '#0E1828';
  static CARD: string = '#111C2D';
  static INPUT: string = '#0A1320';
  static HOVER: string = '#16233A';
  static ACTIVE: string = '#1B2B45';
  static TABLE: string = '#0A111D';
  static LOG: string = '#070D17';

  static LINE: string = '#26344A';
  static LINE_SOFT: string = '#192334';

  static FG: string = '#EAF0FA';
  static FG_DIM: string = '#9AA7BD';
  static FG_MUTE: string = '#68758C';

  static ACCENT: string = '#6EA8E8';
  static ACCENT_HOVER: string = '#9CC4F2';
  static ACCENT_DIM: string = '#3C5E86';
  static CYAN: string = '#4EC9BE';
  static OK: string = '#4FCF95';
  static WARN: string = '#E8C46A';
  static BAD: string = '#EF7A86';
  static PURPLE: string = '#A99CE0';
  static ON_ACCENT: string = '#06101E';
  static ON_OK: string = '#06180F';

  // 主题元信息
  static LIGHT: boolean = false;
  static PATTERN: string = 'stars';

  // 半透明派生色（替代组件里的硬编码）
  static BRAND_BG: string = '#CC0A1220';
  static BADGE_BG: string = '#0F162336';
  static BADGE_BORDER: string = '#12FFFFFF';
  static POPUP_BG: string = '#F00B1220';
  static PANEL_CC: string = '#CC0E1828';
  static PANEL_B3: string = '#B30E1828';

  static GHOST_BG: string = '#16243A';
  static DANGER_BG: string = '#2A1720';
  static DANGER_BORDER: string = '#55303C';
  static DANGER_SOFT_BG: string = '#1E141C';
  static OK_BORDER: string = '#7FE0B6';

  static CHIP_ON_BG: string = '#1E3550';
  static CHIP_OFF_BG: string = '#111D2E';

  static ROW_PLAY_BG: string = '#146EA8E8';
  static ROW_SELECT_BG: string = '#1A6EA8E8';
  static ROW_MULTI_BG: string = '#126EA8E8';
  static ROW_HOVER_BG: string = '#0E16243A';
  static ROW_LINE: string = '#0CFFFFFF';

  static TABLE_HEAD_BG: string = '#0A111D';
  static TABLE_BG: string = '#B30A111D';

  static PLAYER_BG: string = '#D9101A2C';
  static PLAYER_BORDER: string = '#336EA8E8';
  static PLAYER_GRAD_TOP: string = '#1A2740';
  static PLAYER_GRAD_BOTTOM: string = '#0B1523';

  static DOCK_BG: string = '#B3070D17';
  static DOCK_ALPHA: string = '#CC080E1A';
  static SIGNAL_BG: string = '#14242C';
  static CUE_BG: string = '#144EC9BE';
  static DIALOG_BORDER: string = '#446EA8E8';
  static ACCENT_SHADOW: string = '#556EA8E8';

  static FONT: string = 'HarmonyOS Sans SC';
  static MONO: string = 'HarmonyOS Sans';

  static R_CARD: number = 14;
  static R_INPUT: number = 9;
  static R_BTN: number = 9;
  static R_BAR: number = 14;

  static GAP: number = 8;
  static PAGE_PAD_H: number = 14;

  // 曲库行状态（鸿蒙版没有联网匹配状态，用健康检查 + CUE 派生）
  static statusLabel(t: Track): string {
    if (t.healthStatus == 'bad') {
      return '损坏';
    }
    if (t.healthStatus == 'warn') {
      return '警告';
    }
    if (t.isCueChild == 1) {
      return t.ext == '.iso' ? 'SACD 子轨' : 'CUE 子轨';
    }
    return '完成';
  }

  static statusColor(t: Track): string {
    if (t.healthStatus == 'bad') {
      return T.BAD;
    }
    if (t.healthStatus == 'warn') {
      return T.WARN;
    }
    if (t.isCueChild == 1) {
      return T.CYAN;
    }
    return T.OK;
  }

  static healthColor(s: string): string {
    if (s == 'bad') {
      return T.BAD;
    }
    if (s == 'warn') {
      return T.WARN;
    }
    return T.OK;
  }

  static isLossless(t: Track): boolean {
    const ext = t.ext.toLowerCase();
    if (ext == '.mp3' || ext == '.aac' || ext == '.ogg' || ext == '.opus' || ext == '.wma') {
      return false;
    }
    return t.bitDepth >= 16 || ext == '.flac' || ext == '.wav' || ext == '.ape' ||
      ext == '.dsf' || ext == '.dff' || ext == '.iso';
  }

  static isHiRes(t: Track): boolean {
    return t.sampleRate >= 96000 || t.ext.toLowerCase() == '.dsf' || t.ext.toLowerCase() == '.dff';
  }

  // '#RRGGBB' + 'AARRGGBB' 的 alpha 前缀，ArkUI 只认 #AARRGGBB
  static alpha(hex: string, alphaHex: string): string {
    return `#${alphaHex}${hex.replace('#', '')}`;
  }

  // Canvas 用 rgba 字符串（ArkUI Canvas 不识别 #AARRGGBB）
  static rgba(hex: string, a: number): string {
    const v = hex.replace('#', '');
    const r = parseInt(v.substring(0, 2), 16);
    const g = parseInt(v.substring(2, 4), 16);
    const b = parseInt(v.substring(4, 6), 16);
    return `rgba(${r}, ${g}, ${b}, ${a})`;
  }

  // 两色线性混合，weightB 为 b 的占比（0~1），返回 '#RRGGBB'
  static mix(a: string, b: string, weightB: number): string {
    const va = a.replace('#', '');
    const vb = b.replace('#', '');
    const parts: string[] = [];
    for (let i = 0; i < 3; i++) {
      const ca = parseInt(va.substring(i * 2, i * 2 + 2), 16);
      const cb = parseInt(vb.substring(i * 2, i * 2 + 2), 16);
      const v = Math.round(ca * (1 - weightB) + cb * weightB);
      const s = v.toString(16).toUpperCase();
      parts.push(s.length < 2 ? `0${s}` : s);
    }
    return `#${parts[0]}${parts[1]}${parts[2]}`;
  }
}
