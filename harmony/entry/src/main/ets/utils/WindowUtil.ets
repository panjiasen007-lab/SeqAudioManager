// 独立窗口工具：把 EQ / 编辑标签 / 体检 / 批量整理 / 封面 / 歌词等做成真正子窗口，
// 并把主窗口让位给小窗口播放器。子窗口不可用时自动回退 router 页面跳转。
import { window, router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import display from '@ohos.display';
import { AppLog } from './AppLog';

const KEY_STAGE = 'seqaudio_window_stage';
const KEY_SUB_PAGES = 'seqaudio_subwin_pages';

export class WindowUtil {
  private static dragWin: window.Window | undefined = undefined;
  private static dragLeft: number = 0;
  private static dragTop: number = 0;
  private static dragDensity: number = 2;

  static stage(): window.WindowStage | undefined {
    return AppStorage.get<window.WindowStage>(KEY_STAGE);
  }

  static mainMinimized(): boolean {
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      return false;
    }
    try {
      return stage.getMainWindowSync().getWindowStatus() == window.WindowStatusType.MINIMIZE;
    } catch (e) {
      return false;
    }
  }

  static isMainMaximized(): boolean {
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      return false;
    }
    try {
      return stage.getMainWindowSync().getWindowStatus() == window.WindowStatusType.MAXIMIZE;
    } catch (e) {
      return false;
    }
  }

  private static wasMaximized: boolean = false;

  static minimizeMain(): void {
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      return;
    }
    try {
      const win = stage.getMainWindowSync();
      // 记录最小化前是否处于最大化，恢复时保持同样的窗口形态
      try {
        const d = display.getDefaultDisplaySync();
        const r = win.getWindowProperties().windowRect;
        WindowUtil.wasMaximized = r.width >= d.width - 8 && r.height >= d.height * 0.85;
      } catch (e) {
        WindowUtil.wasMaximized = false;
      }
      win.minimize().catch((e: Error) => {
      });
    } catch (e) {
    }
  }

  static restoreMain(): void {
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      return;
    }
    try {
      const win = stage.getMainWindowSync();
      if (WindowUtil.wasMaximized) {
        WindowUtil.resizeMainToMax();
      } else {
        // 自定义标题栏窗口：用 resize 回到默认窗口化形态，可同时退出全屏/最大化
        WindowUtil.resizeMainToDefault();
      }
    } catch (e) {
    }
  }

  // 无边框迷你窗拖动：手势开始记录窗口原始位置，移动时按 vp→px 换算
  static beginDrag(ctx: common.UIAbilityContext): void {
    try {
      const d = display.getDefaultDisplaySync();
      WindowUtil.dragDensity = d.densityPixels > 0 ? d.densityPixels : 2;
    } catch (e) {
      WindowUtil.dragDensity = 2;
    }
    window.getLastWindow(ctx).then((win: window.Window) => {
      WindowUtil.dragWin = win;
      try {
        const r = win.getWindowProperties().windowRect;
        WindowUtil.dragLeft = r.left;
        WindowUtil.dragTop = r.top;
      } catch (e) {
        WindowUtil.dragLeft = 0;
        WindowUtil.dragTop = 0;
      }
    }).catch((e: Error) => {
    });
  }

  // 主窗口自绘标题栏拖动（不依赖子窗口/迷你窗的 context）
  static beginDragMain(): void {
    try {
      const d = display.getDefaultDisplaySync();
      WindowUtil.dragDensity = d.densityPixels > 0 ? d.densityPixels : 2;
    } catch (e) {
      WindowUtil.dragDensity = 2;
    }
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      return;
    }
    try {
      const win = stage.getMainWindowSync();
      WindowUtil.dragWin = win;
      try {
        const r = win.getWindowProperties().windowRect;
        WindowUtil.dragLeft = r.left;
        WindowUtil.dragTop = r.top;
      } catch (e) {
        WindowUtil.dragLeft = 0;
        WindowUtil.dragTop = 0;
      }
    } catch (e) {
    }
  }

  static dragBy(ctx: common.UIAbilityContext, dx: number, dy: number): void {
    if (WindowUtil.dragWin == undefined) {
      return;
    }
    WindowUtil.dragWin.moveWindowTo(
      Math.round(WindowUtil.dragLeft + dx * WindowUtil.dragDensity),
      Math.round(WindowUtil.dragTop + dy * WindowUtil.dragDensity)).catch((e: Error) => {
    });
  }

  // 主窗口标题栏拖动（调用方无需传 context）
  static dragByMain(dx: number, dy: number): void {
    if (WindowUtil.dragWin == undefined) {
      return;
    }
    WindowUtil.dragWin.moveWindowTo(
      Math.round(WindowUtil.dragLeft + dx * WindowUtil.dragDensity),
      Math.round(WindowUtil.dragTop + dy * WindowUtil.dragDensity)).catch((e: Error) => {
    });
  }

  static endDrag(): void {
    WindowUtil.dragWin = undefined;
  }

  // 模拟最大化：铺满屏幕宽度，底部留出 Dock，避免系统全屏状态无法可靠还原
  static resizeMainToMax(): void {
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      return;
    }
    let sw = 3120;
    let sh = 2080;
    try {
      const d = display.getDefaultDisplaySync();
      sw = d.width;
      sh = d.height;
    } catch (e) {
    }
    const h = Math.max(600, sh - 120);
    try {
      const win = stage.getMainWindowSync();
      win.resize(sw, h).then(() => {
        win.moveWindowTo(0, 0).catch((e: Error) => {
        });
      }).catch((e: Error) => {
      });
    } catch (e) {
    }
  }

  // 主窗口默认窗口化尺寸：屏幕 84%×80%，居中（与桌面版启动形态一致）
  static resizeMainToDefault(): void {
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      return;
    }
    let density = 2;
    let sw = 3120;
    let sh = 2080;
    try {
      const d = display.getDefaultDisplaySync();
      density = d.densityPixels > 0 ? d.densityPixels : 2;
      sw = d.width;
      sh = d.height;
    } catch (e) {
      density = 2;
    }
    const w = Math.round(sw * 0.84);
    const h = Math.round(sh * 0.80);
    const x = Math.round((sw - w) / 2);
    const y = Math.round((sh - h) / 2);
    try {
      const win = stage.getMainWindowSync();
      win.restore().catch((e: Error) => {
      });
      setTimeout(() => {
        win.resize(w, h).then(() => {
          win.moveWindowTo(x, y).catch((e: Error) => {
          });
        }).catch((e: Error) => {
        });
      }, 80);
    } catch (e) {
    }
  }

  private static subPages(): string[] {
    const raw = AppStorage.get<string[]>(KEY_SUB_PAGES);
    return raw != undefined ? raw : [];
  }

  private static addSubPage(url: string): void {
    const pages = WindowUtil.subPages().slice();
    if (pages.indexOf(url) < 0) {
      pages.push(url);
      AppStorage.setOrCreate(KEY_SUB_PAGES, pages);
    }
  }

  private static removeSubPage(url: string): void {
    AppStorage.setOrCreate(KEY_SUB_PAGES, WindowUtil.subPages().filter((p: string) => p != url));
  }

  // 打开独立子窗口；失败时回退为页面跳转，保证功能不丢失
  static openPage(url: string, width: number, height: number): void {
    const stage = WindowUtil.stage();
    if (stage == undefined) {
      router.pushUrl({ url: url });
      return;
    }
    const name = `seqaudio_${url.replace(/[^a-zA-Z0-9]/g, '_')}`;
    stage.createSubWindow(name).then((win: window.Window) => {
      WindowUtil.addSubPage(url);
      try {
        win.setWindowBackgroundColor('#0B1220');
      } catch (e) {
        // 部分设备在窗口尚未 attach 时不允许改背景色，忽略即可
      }
      // resize 使用物理像素：把 vp 尺寸按屏幕密度换算，并把窗口居中
      let density = 2;
      let screenW = 0;
      let screenH = 0;
      try {
        const d = display.getDefaultDisplaySync();
        density = d.densityPixels > 0 ? d.densityPixels : 2;
        screenW = d.width;
        screenH = d.height;
      } catch (e) {
        density = 2;
      }
      const wPx = Math.round(width * density);
      const hPx = Math.round(height * density);
      win.setWindowLimits({ minWidth: 320 * density, minHeight: 320 * density }).catch((e: Error) => {
        AppLog.warn(`子窗口限制失败：${e.message}`);
      });
      win.resize(wPx, hPx).catch((e: Error) => {
        AppLog.warn(`子窗口尺寸失败：${e.message}`);
      });
      if (screenW > wPx && screenH > hPx) {
        win.moveWindowTo(Math.round((screenW - wPx) / 2), Math.round((screenH - hPx) / 2)).catch((e: Error) => {
        });
      }
      win.setUIContent(url).then(() => {
        win.showWindow().then(() => {
          AppLog.info(`子窗口已打开：${url}`);
        }).catch((e: Error) => {
          AppLog.warn(`子窗口显示失败：${e.message}`);
          router.pushUrl({ url: url });
        });
      }).catch((e: Error) => {
        AppLog.warn(`子窗口页面加载失败：${e.message}`);
        router.pushUrl({ url: url });
      });
    }).catch((e: Error) => {
      AppLog.warn(`子窗口创建失败：${e.message}`);
      // 同名子窗口已存在：直接唤起，不再重复创建
      try {
        const exist = window.findWindow(name);
        exist.showWindow();
        AppLog.info(`子窗口已存在，唤起到前层：${url}`);
        return;
      } catch (e2) {
      }
      router.pushUrl({ url: url });
    });
  }

  // 页面里的「返回/关闭」：子窗口内销毁自身；普通路由页则回退
  static closeSelf(url: string): void {
    if (WindowUtil.subPages().indexOf(url) < 0) {
      router.back();
      return;
    }
    const ctx = AppStorage.get<common.UIAbilityContext>('seqaudio_context');
    if (ctx == undefined) {
      router.back();
      return;
    }
    window.getLastWindow(ctx).then((win: window.Window) => {
      WindowUtil.removeSubPage(url);
      win.destroyWindow().catch((e: Error) => {
        router.back();
      });
    }).catch((e: Error) => {
      router.back();
    });
  }
}
