// 迷你播放器独立 UIAbility：拥有自己的窗口，主窗口最小化后仍保持显示，
// 与 Windows 版“小窗口替换主界面”的交互一致。
import { AbilityConstant, ConfigurationConstant, UIAbility, Want } from '@kit.AbilityKit';
import { window } from '@kit.ArkUI';
import { hilog } from '@kit.PerformanceAnalysisKit';
import display from '@ohos.display';
import { AppLog } from '../utils/AppLog';
import { MiniBridge } from '../mini/MiniBridge';
import { ThemeStore } from '../theme/ThemeStore';

const DOMAIN = 0x0000;

export default class MiniAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    // 深色应用：系统窗口标题栏/按钮跟随深色，避免白条与暗色播放器割裂
    this.context.getApplicationContext().setColorMode(ConfigurationConstant.ColorMode.COLOR_MODE_DARK);
    // 迷你窗是独立 UIAbility，主题不会自动继承，需要按启动参数重建调色板
    const params = want.parameters;
    if (params != undefined) {
      const theme = params['theme'];
      if (theme != undefined) {
        ThemeStore.apply(`${theme}`);
      }
    }
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'MiniAbility onCreate');
  }

  onDestroy(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'MiniAbility onDestroy');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.loadContent('pages/MiniPlayer', (err) => {
      if (err.code) {
        hilog.error(DOMAIN, 'testTag', 'Failed to load mini content: %{public}s', JSON.stringify(err));
        return;
      }
      try {
        const win = windowStage.getMainWindowSync();
        // 透明窗口 + 隐藏系统标题栏；部分设备首次调用尚未 attach，延迟重试
        const fixChrome = (): void => {
          try {
            win.setWindowBackgroundColor('#00000000');
          } catch (e) {
          }
          try {
            win.setWindowContainerColor('#01000000', '#FF0B1220');
          } catch (e) {
          }
          try {
            win.setWindowDecorVisible(false);
            AppLog.info(`迷你窗标题栏可见：${win.getWindowDecorVisible()}`);
          } catch (e) {
            AppLog.warn(`迷你窗标题栏隐藏失败：${JSON.stringify(e)}`);
          }
          try {
            win.setWindowTitle('音序管家 · 迷你播放');
          } catch (e) {
          }
          // 关闭标题栏/任务栏悬浮显示，避免与页面自绘的窗口按钮重复
          try {
            win.setTitleAndDockHoverShown(false, false).catch((e: Error) => {
            });
          } catch (e) {
          }
          // 隐藏系统标题栏三键，窗口按钮完全由页面自绘（最小化/最大化/关闭）
          try {
            win.setWindowTitleButtonVisible(false, false, false);
          } catch (e) {
          }
        };
        fixChrome();
        setTimeout(fixChrome, 260);
        setTimeout(fixChrome, 1000);
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
        const w = Math.round(560 * density);
        const h = Math.round(320 * density);
        win.setWindowLimits({
          minWidth: Math.round(480 * density),
          minHeight: Math.round(280 * density)
        }).catch((e: Error) => {
        });
        win.resize(w, h).catch((e: Error) => {
          AppLog.warn(`迷你窗尺寸设置失败：${e.message}`);
        });
        if (screenW > w && screenH > h) {
          win.moveWindowTo(screenW - w - Math.round(24 * density),
            screenH - h - Math.round(56 * density)).catch((e: Error) => {
          });
        }
        win.raiseToAppTop().catch((e: Error) => {
        });
        AppLog.info('迷你播放器窗口已创建');
      } catch (e) {
        AppLog.warn(`迷你窗初始化失败：${JSON.stringify(e)}`);
      }
    });
  }

  onWindowStageDestroy(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'MiniAbility onWindowStageDestroy');
    // 主窗口据此恢复并刷新曲库
    MiniBridge.sendCommand('closed', 0);
  }

  onForeground(): void {
  }

  onBackground(): void {
  }
}
