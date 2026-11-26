// 视觉主题：与桌面版 ui/theme.py 的 11 套调色板对齐。
// 切换主题时改写 T 的静态字段，并重建主页面，让所有静态取值重新执行。
import { T } from './Tokens';

export class ThemePalette {
  name: string = '深空';
  light: boolean = false;
  pattern: string = 'stars';
  base: string = '#070B14';
  panel: string = '#0E1828';
  card: string = '#111C2D';
  input: string = '#0A1320';
  hover: string = '#16233A';
  active: string = '#1B2B45';
  table: string = '#0A111D';
  log: string = '#070D17';
  line: string = '#26344A';
  lineSoft: string = '#192334';
  fg: string = '#EAF0FA';
  fgDim: string = '#9AA7BD';
  fgMute: string = '#68758C';
  accent: string = '#6EA8E8';
  accentHover: string = '#9CC4F2';
  accentDim: string = '#3C5E86';
  cyan: string = '#4EC9BE';
  ok: string = '#4FCF95';
  warn: string = '#E8C46A';
  bad: string = '#EF7A86';
  purple: string = '#A99CE0';
  onAccent: string = '#06101E';
  onOk: string = '#06180F';

  constructor(name: string, light: boolean, pattern: string, base: string, panel: string, card: string,
    input: string, hover: string, active: string, table: string, log: string,
    line: string, lineSoft: string, fg: string, fgDim: string, fgMute: string,
    accent: string, accentHover: string, accentDim: string, cyan: string,
    ok: string, warn: string, bad: string, purple: string, onAccent: string, onOk: string) {
    this.name = name;
    this.light = light;
    this.pattern = pattern;
    this.base = base;
    this.panel = panel;
    this.card = card;
    this.input = input;
    this.hover = hover;
    this.active = active;
    this.table = table;
    this.log = log;
    this.line = line;
    this.lineSoft = lineSoft;
    this.fg = fg;
    this.fgDim = fgDim;
    this.fgMute = fgMute;
    this.accent = accent;
    this.accentHover = accentHover;
    this.accentDim = accentDim;
    this.cyan = cyan;
    this.ok = ok;
    this.warn = warn;
    this.bad = bad;
    this.purple = purple;
    this.onAccent = onAccent;
    this.onOk = onOk;
  }
}

function dark(name: string, accent: string, base: string, tint: string, accentHover: string,
  cyan: string, onAccent: string, pattern: string, fg: string, fgDim: string, fgMute: string,
  line: string, lineSoft: string, ok: string, warn: string, bad: string, purple: string): ThemePalette {
  // 现代深色层级：base → panel → card 逐级提亮，输入框回落到 base 与强调色之间，
  // 悬停/激活用白色微叠，保证面板之间有可读的层次差而不是一片同色。
  const panel = T.mix(base, tint, 0.72);
  const card = T.mix(panel, '#FFFFFF', 0.05);
  return new ThemePalette(
    name, false, pattern, base,
    panel, card,
    T.mix(base, tint, 0.46), T.mix(panel, '#FFFFFF', 0.07), T.mix(panel, '#FFFFFF', 0.13),
    T.mix(base, panel, 0.55), T.mix(base, '#000000', 0.12),
    line, lineSoft, fg, fgDim, fgMute,
    accent, accentHover, T.alpha(accent, '80'), cyan,
    ok, warn, bad, purple, onAccent, onAccent
  );
}

function build(): ThemePalette[] {
  const list: ThemePalette[] = [];

  // 深空：保持与旧版 Token 完全一致的深色玻璃面板
  const space = dark('深空', '#6EA8E8', '#070B14', '#101A2C', '#9CC4F2', '#4EC9BE', '#06101E', 'stars',
    '#EAF0FA', '#9AA7BD', '#68758C', '#26344A', '#192334',
    '#4FCF95', '#E8C46A', '#EF7A86', '#A99CE0');
  space.panel = '#0E1828';
  space.card = '#111C2D';
  space.input = '#0A1320';
  space.hover = '#16233A';
  space.active = '#1B2B45';
  space.table = '#0A111D';
  space.log = '#070D17';
  space.accentDim = '#3C5E86';
  space.onOk = '#06180F';
  list.push(space);

  list.push(dark('黑胶', '#D9A05B', '#0E0B09', '#211A16', '#F0C285', '#C98F6A', '#1A0F07', 'vinyl',
    '#F0E8DC', '#A89A88', '#756757', '#3A2C20', '#251C15',
    '#8FBF6A', '#D9A05B', '#D97060', '#B99A78'));
  list.push(dark('春', '#5CC595', '#0A1411', '#142620', '#8EE0B5', '#74CFC2', '#06150F', 'blossom',
    '#E6F4ED', '#92B0A2', '#637D70', '#244336', '#172B23',
    '#5CC595', '#E8C887', '#E07D8C', '#E0A2C0'));
  list.push(dark('夏', '#F0AE5C', '#14100A', '#272015', '#F8CE8A', '#58C8B8', '#2A1806', 'sun',
    '#FDF1E0', '#B8A68D', '#86735C', '#493821', '#322719',
    '#7EC78A', '#F0AE5C', '#EC7A60', '#EC9C70'));
  list.push(dark('秋', '#D67A45', '#120E0A', '#251C14', '#ED9C68', '#C9AC74', '#1B0D06', 'leaves',
    '#F4E9DE', '#AD9784', '#796556', '#443222', '#2D211A',
    '#8AB468', '#D6A44F', '#D66F5F', '#C08B6C'));
  list.push(dark('冬', '#86C7EC', '#080E16', '#101D2D', '#B3DFF5', '#C9E9F5', '#061421', 'snow',
    '#E9F4FC', '#94ADBF', '#61788A', '#264156', '#182B3B',
    '#74CDB8', '#EACC82', '#EC8492', '#B2ADDD'));
  list.push(dark('海岸', '#3CC4B4', '#071316', '#10262A', '#6BDCD0', '#E8C69C', '#042023', 'waves',
    '#E4F8F4', '#8CB4B0', '#5C807C', '#1F4648', '#122F31',
    '#3CC4B4', '#E0BD70', '#E87D7D', '#86C9F0'));
  list.push(dark('暮光', '#B888E8', '#0D0A14', '#1E1730', '#D3AAF2', '#DFA2CF', '#160A2A', 'dusk',
    '#F0E9FB', '#A598B8', '#746686', '#3A2C4F', '#271E38',
    '#86CCAE', '#E8C680', '#EC82A0', '#B888E8'));
  list.push(dark('太极', '#EDEDED', '#070707', '#161616', '#FFFFFF', '#B8B8B8', '#0A0A0A', 'taiji',
    '#F2F2F2', '#A6A6A6', '#6E6E6E', '#3A3A3A', '#242424',
    '#9CCF9C', '#D9C27A', '#D98A8A', '#B0A8C0'));

  // 浅色主题：面板、表格、日志使用桌面版给的不透明浅色
  list.push(new ThemePalette(
    '纸白', true, 'paper', '#ECE5D8',
    '#F6F0E4', '#FDF9F0', '#FBF8F0', '#E9DFCF', '#E0D4C2', '#F9F4E9', '#F3ECDD',
    '#C9B9A2', '#DDD2C0', '#2B241A', '#655A4C', '#7A6F62',
    '#96602F', '#7C4820', '#E2C7AB', '#3F7D78',
    '#3F7D4B', '#A86A1F', '#B03A2E', '#7B4FA3', '#FFF8EE', '#FFFFFF'
  ));
  list.push(new ThemePalette(
    '黑白极简', true, 'grid', '#EEF0F3',
    '#FAFBFC', '#FBFCFD', '#FBFCFD', '#E7E9EE', '#DDE0E6', '#FAFBFC', '#F5F7F9',
    '#CCD1DA', '#E0E4EA', '#111318', '#4C515B', '#6E737D',
    '#111318', '#343945', '#C9CCD4', '#167D8D',
    '#16784A', '#A35F00', '#B3261E', '#6B4FA0', '#FFFFFF', '#FFFFFF'
  ));

  return list;
}

export class ThemeStore {
  static readonly NAMES: string[] = ['深空', '黑胶', '春', '夏', '秋', '冬', '海岸', '暮光', '太极', '纸白', '黑白极简'];
  private static readonly LIST: ThemePalette[] = build();
  private static current: string = '深空';

  static currentName(): string {
    return ThemeStore.current;
  }

  static normalize(name: string): string {
    return ThemeStore.NAMES.indexOf(name) >= 0 ? name : '深空';
  }

  static palette(name: string): ThemePalette {
    for (const p of ThemeStore.LIST) {
      if (p.name == name) {
        return p;
      }
    }
    return ThemeStore.LIST[0];
  }

  static apply(name: string): void {
    const p = ThemeStore.palette(name);
    T.BASE = p.base;
    T.PANEL = p.panel;
    T.CARD = p.card;
    T.INPUT = p.input;
    T.HOVER = p.hover;
    T.ACTIVE = p.active;
    T.TABLE = p.table;
    T.LOG = p.log;
    T.LINE = p.line;
    T.LINE_SOFT = p.lineSoft;
    T.FG = p.fg;
    T.FG_DIM = p.fgDim;
    T.FG_MUTE = p.fgMute;
    T.ACCENT = p.accent;
    T.ACCENT_HOVER = p.accentHover;
    T.ACCENT_DIM = p.accentDim;
    T.CYAN = p.cyan;
    T.OK = p.ok;
    T.WARN = p.warn;
    T.BAD = p.bad;
    T.PURPLE = p.purple;
    T.ON_ACCENT = p.onAccent;
    T.ON_OK = p.onOk;
    T.LIGHT = p.light;
    T.PATTERN = p.pattern;

    T.BRAND_BG = T.alpha(T.mix(p.base, p.panel, 0.35), 'CC');
    T.BADGE_BG = T.alpha(p.hover, '0F');
    T.BADGE_BORDER = T.alpha(p.fg, '12');
    T.POPUP_BG = T.alpha(T.mix(p.base, p.panel, 0.50), 'F0');
    T.PANEL_CC = T.alpha(p.panel, 'CC');
    T.PANEL_B3 = T.alpha(p.panel, 'B3');

    T.GHOST_BG = p.hover;
    T.DANGER_BG = T.mix(p.base, p.bad, 0.16);
    T.DANGER_BORDER = T.mix(p.base, p.bad, 0.33);
    T.DANGER_SOFT_BG = T.mix(p.base, p.bad, 0.12);
    T.OK_BORDER = T.mix(p.ok, '#FFFFFF', 0.45);

    T.CHIP_ON_BG = T.mix(p.panel, p.accent, 0.18);
    T.CHIP_OFF_BG = T.mix(p.base, p.panel, 0.80);

    T.ROW_PLAY_BG = T.alpha(p.accent, '14');
    T.ROW_SELECT_BG = T.alpha(p.accent, '1A');
    T.ROW_MULTI_BG = T.alpha(p.accent, '12');
    T.ROW_HOVER_BG = T.alpha(p.hover, '0E');
    T.ROW_LINE = T.alpha(p.fg, '0C');

    T.TABLE_HEAD_BG = p.table;
    T.TABLE_BG = T.alpha(p.table, 'B3');

    T.PLAYER_BG = T.alpha(p.panel, 'D9');
    T.PLAYER_BORDER = T.alpha(p.accent, '33');
    T.PLAYER_GRAD_TOP = T.mix(p.panel, p.accent, 0.10);
    T.PLAYER_GRAD_BOTTOM = T.mix(p.base, p.panel, 0.40);

    T.DOCK_BG = T.alpha(p.log, 'B3');
    T.DOCK_ALPHA = T.alpha(T.mix(p.base, p.panel, 0.30), 'CC');
    T.SIGNAL_BG = T.mix(p.panel, p.cyan, 0.10);
    T.CUE_BG = T.alpha(p.cyan, '14');
    T.DIALOG_BORDER = T.alpha(p.accent, '44');
    T.ACCENT_SHADOW = T.alpha(p.accent, '55');

    ThemeStore.current = p.name;
  }
}
