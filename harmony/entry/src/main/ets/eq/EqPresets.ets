import { Db } from '../db/Db';

export class EqPreset {
  name: string = '';
  gains: number[] = [];
}

export class EqState {
  enabled: boolean = false;
  currentName: string = '平坦';
  gains: number[] = [];
}

// Q1 拍板：对齐桌面版 EQ_BANDS_HZ（31 段 1/3 倍频程，Q=1.41，±12dB）
export class EqPresets {
  static readonly BANDS_HZ: number[] = [
    20, 25, 31, 40, 50, 63, 80, 100, 125, 160,
    200, 250, 315, 400, 500, 630, 800, 1000, 1250, 1600,
    2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000,
    20000
  ];
  static readonly Q: number = 1.41;
  static readonly MIN_DB: number = -12;
  static readonly MAX_DB: number = 12;

  static clamp(v: number): number {
    return Math.max(EqPresets.MIN_DB, Math.min(EqPresets.MAX_DB, v));
  }

  static flat(): EqPreset {
    const p = new EqPreset();
    p.name = '平坦';
    for (let i = 0; i < EqPresets.BANDS_HZ.length; i++) {
      p.gains.push(0);
    }
    return p;
  }

  static presets(): EqPreset[] {
    const out: EqPreset[] = [EqPresets.flat()];
    const defs: string[] = [
      '流行|0,0,0,1,2,3,4,4,3,2,1,0,0,0,1,2,3,3,2,1,0,0,0,1,2,2,1,0,0,0,0',
      '摇滚|4,4,3,2,0,-1,-2,-1,0,2,3,3,2,1,0,-1,-1,0,1,2,3,4,4,4,3,2,1,0,0,0,0',
      '古典|3,2,1,0,-1,-2,-2,-1,0,0,1,1,0,0,-1,-1,0,1,2,2,1,0,-1,-2,-2,-1,0,0,0,0',
      '爵士|2,1,0,0,1,2,2,1,0,1,2,2,1,0,0,1,1,2,2,1,0,0,1,2,2,1,0,0,0,0'
    ];
    for (const def of defs) {
      const parts = def.split('|');
      const p = new EqPreset();
      p.name = parts[0];
      const vals = parts[1].split(',');
      for (const v of vals) {
        p.gains.push(EqPresets.clamp(parseInt(v) || 0));
      }
      while (p.gains.length < EqPresets.BANDS_HZ.length) {
        p.gains.push(0);
      }
      out.push(p);
    }
    return out;
  }

  static async load(): Promise<EqState> {
    const state = new EqState();
    state.enabled = (await Db.getSetting('eq_enabled', '0')) == '1';
    state.currentName = await Db.getSetting('eq_preset_name', '平坦');
    const raw = await Db.getSetting('eq_bands_json', '');
    if (raw != '') {
      try {
        const arr = JSON.parse(raw) as number[];
        if (arr.length == EqPresets.BANDS_HZ.length) {
          for (const v of arr) {
            state.gains.push(EqPresets.clamp(v));
          }
        }
      } catch (e) {
        // 配置损坏：回落平坦
      }
    }
    if (state.gains.length != EqPresets.BANDS_HZ.length) {
      state.gains = EqPresets.flat().gains.slice();
    }
    return state;
  }

  static async save(state: EqState): Promise<void> {
    await Db.setSetting('eq_enabled', state.enabled ? '1' : '0');
    await Db.setSetting('eq_preset_name', state.currentName);
    await Db.setSetting('eq_bands_json', JSON.stringify(state.gains));
  }

  static async applyPreset(state: EqState, name: string): Promise<EqState> {
    const preset = EqPresets.presets().find((p: EqPreset) => p.name == name);
    if (preset) {
      state.currentName = preset.name;
      state.gains = preset.gains.slice();
    }
    await EqPresets.save(state);
    return state;
  }
}
