import { AbilityConstant, ConfigurationConstant, UIAbility, Want } from '@kit.AbilityKit';
import { hilog } from '@kit.PerformanceAnalysisKit';
import { window } from '@kit.ArkUI';
import display from '@ohos.display';

const DOMAIN = 0x0000;

export default class EntryAbility extends UIAbility {
  onCreate(want: Want, launchParam: AbilityConstant.LaunchParam): void {
    this.context.getApplicationContext().setColorMode(ConfigurationConstant.ColorMode.COLOR_MODE_DARK);
    AppStorage.setOrCreate('seqaudio_context', this.context);
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onCreate');
  }

  onDestroy(): void {
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onDestroy');
  }

  onWindowStageCreate(windowStage: window.WindowStage): void {
    // Main window is created, set main page for this ability
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onWindowStageCreate');

    // 供子窗口/迷你播放器复用（EQ、编辑标签等独立窗口）
    AppStorage.setOrCreate('seqaudio_window_stage', windowStage);
    windowStage.loadContent('pages/Index', (err) => {
      if (err.code) {
        hilog.error(DOMAIN, 'testTag', 'Failed to load the content. Cause: %{public}s', JSON.stringify(err));
        return;
      }
      hilog.info(DOMAIN, 'testTag', 'Succeeded in loading the content.');
      // 桌面工作台默认按桌面版窗口形态启动（非最大化），最小 1040×720
      try {
        const mainWindow = windowStage.getMainWindowSync();
        let density = 2;
        let screenW = 3120;
        let screenH = 2080;
        try {
          const d = display.getDefaultDisplaySync();
          density = d.densityPixels > 0 ? d.densityPixels : 2;
          screenW = d.width;
          screenH = d.height;
        } catch (e) {
          density = 2;
        }
        // 窗口背景透明：界面不透明度调到 0 时内容区完全穿透（内容由页面自行绘制）
        // 隐藏系统标题栏，主窗口标题栏由页面自绘，既能跟随主题又能与内容一起淡出
        const applyChrome = (): void => {
          try {
            mainWindow.setWindowBackgroundColor('#00000000');
          } catch (e) {
            hilog.warn(DOMAIN, 'testTag', 'setWindowBackgroundColor failed: %{public}s', JSON.stringify(e));
          }
          try {
            mainWindow.setWindowShadowEnabled(false).catch((err: Error) => {
            });
          } catch (e) {
          }
          try {
            mainWindow.setWindowContainerColor('#01000000', '#FF0B1220');
          } catch (e) {
            hilog.warn(DOMAIN, 'testTag', 'setWindowContainerColor failed: %{public}s', JSON.stringify(e));
          }
          try {
            mainWindow.setWindowDecorVisible(false);
          } catch (e) {
            hilog.warn(DOMAIN, 'testTag', 'setWindowDecorVisible failed: %{public}s', JSON.stringify(e));
          }
          try {
            mainWindow.setTitleAndDockHoverShown(false, false).catch((err: Error) => {
            });
          } catch (e) {
          }
          try {
            mainWindow.setWindowTitleButtonVisible(false, false, false);
          } catch (e) {
          }
        };
        applyChrome();
        setTimeout(applyChrome, 260);
        setTimeout(applyChrome, 1000);

        // 默认窗口大小约为屏幕的 84%×80%，居中（与桌面版打开时的窗口形态一致）
        const dftW = Math.round(screenW * 0.84);
        const dftH = Math.round(screenH * 0.80);
        const dftX = Math.round((screenW - dftW) / 2);
        const dftY = Math.round((screenH - dftH) / 2);
        mainWindow.setWindowLimits({
          minWidth: Math.round(1040 * density),
          minHeight: Math.round(720 * density)
        }).catch((e: Error) => {
          hilog.warn(DOMAIN, 'testTag', 'setWindowLimits failed: %{public}s', e.message);
        });
        mainWindow.restore().catch((e: Error) => {
        });
        const applyWindowed = (): void => {
          mainWindow.resize(dftW, dftH).then(() => {
            mainWindow.moveWindowTo(dftX, dftY).catch((e: Error) => {
              hilog.warn(DOMAIN, 'testTag', 'move failed: %{public}s', e.message);
            });
          }).catch((e: Error) => {
            hilog.warn(DOMAIN, 'testTag', 'resize failed: %{public}s', e.message);
          });
        };
        setTimeout(applyWindowed, 150);
        setTimeout(applyWindowed, 700);
        // 主窗口重新获得焦点：通知页面关闭迷你播放器
        mainWindow.on('windowEvent', (event: window.WindowEventType) => {
          if (event == window.WindowEventType.WINDOW_ACTIVE) {
            AppStorage.setOrCreate('seqaudio_main_active', Date.now());
          }
        });
      } catch (e) {
        hilog.warn(DOMAIN, 'testTag', 'window setup failed: %{public}s', JSON.stringify(e));
      }
    });
  }

  onWindowStageDestroy(): void {
    // Main window is destroyed, release UI related resources
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onWindowStageDestroy');
  }

  onForeground(): void {
    // Ability has brought to foreground
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onForeground');
  }

  onBackground(): void {
    // Ability has back to background
    hilog.info(DOMAIN, 'testTag', '%{public}s', 'Ability onBackground');
  }
}