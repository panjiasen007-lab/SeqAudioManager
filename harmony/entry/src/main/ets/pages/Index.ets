// 音序管家 · 鸿蒙 PC 主工作台（1:1 复刻桌面版主界面）
// 布局：品牌条 → 操作栏 → 统计卡 → 曲库表格 + 右侧详情/参数 → 播放条 →
//       日志/歌词/信号路径 → 状态栏；迷你播放器为右下浮层。
import { router, promptAction } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import fs from '@ohos.file.fs';
import picker from '@ohos.file.picker';
import pasteboard from '@ohos.pasteboard';
import { Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { Db, Stats } from '../db/Db';
import { Scanner, ScanController, ScanRoots } from '../scanner/Scanner';
import { ImportManager, ImportReport } from '../import/ImportManager';
import { CueIndexer } from '../cue/CueIndexer';
import { PlayerEngine, PlayMode } from '../player/PlayerEngine';
import { NativePlayer } from '../player/NativePlayer';
import { EqPresets } from '../eq/EqPresets';
import { SessionStore } from '../session/SessionStore';
import { CoverService } from '../cover/CoverService';
import { LyricsService, LyricsResult, LyricLine } from '../lyrics/LyricsService';
import { AppLog, LogLine } from '../utils/AppLog';
import { AppSettings, AppSettingsData } from '../settings/AppSettings';
import { T } from '../theme/Tokens';
import { ThemeStore } from '../theme/ThemeStore';
import { fmtDuration, fmtSize, fmtSpec } from '../utils/Util';
import {
  ActionBar, BottomDock, BrandBar, FilterBar, Inspector, MiniOverlay, PlayerBar,
  SignalStage, StatCard, StatusBar, TrackTable, VerdictBar
} from '../components/DesktopUI';
import { SettingsPanel } from '../components/SettingsPanel';
import { AboutDialog, PlaylistDialog, ScanRootsDialog } from '../components/DesktopDialogs';
import { MatchDialog } from '../components/MatchDialog';
import { SpectrumDialog } from '../components/SpectrumDialog';
import { CueSplitDialog } from '../components/CueSplitDialog';
import { MetadataMatcher } from '../pipeline/Matcher';
import { MatchCandidate, MatchResult, MatchSettings, PendingMatch } from '../net/MetadataTypes';
import { AutoOptions, AutoReport, AutoRunner } from '../pipeline/AutoRunner';
import {
  AuthenticityResult, AuthenticityService, verdictColor, verdictGroup,
  VERDICT_FAKE, VERDICT_FAKE_DSD
} from '../core/AuthenticityService';
import { CleanupReport, CleanupService } from '../core/CleanupService';
import { QuarantineService } from '../core/QuarantineService';
import { TitleCleaner } from '../library/TitleCleaner';
import { ReportResult, ReportService } from '../library/ReportService';
import { TagPatch, TagWriter, WriteResult } from '../tags/TagWriter';
import { QuarantineDialog } from '../components/QuarantineDialog';

const APP_VERSION = '1.3.5';
const MODE_GLYPHS: string[] = ['→', '①', '↻', '⤨'];
const MODE_NAMES: string[] = ['顺序播放', '列表循环', '单曲循环', '随机'];

@Entry
@Component
struct Index {
  @State private allTracks: Track[] = [];
  @State private filtered: Track[] = [];
  @State private keyword: string = '';
  @State private losslessOnly: boolean = false;
  @State private problemsOnly: boolean = false;
  @State private specIndex: number = 0;
  @State private verdictIndex: number = 0;
  @State private counts: number[] = [0, 0, 0, 0, 0];
  @State private stats: Stats = new Stats();
  @State private doneCount: number = 0;
  @State private warnCount: number = 0;
  @State private badCount: number = 0;
  @State private selectedId: number = -1;
  @State private selectedIds: number[] = [];
  @State private selectedTrack: Track = emptyTrack();
  @State private selectedCover: string = '';
  @State private playingTrack: Track = emptyTrack();
  @State private playingPath: string = '';
  @State private playingCover: string = '';
  @State private roots: string[] = [];
  @State private scanning: boolean = false;
  @State private scanPaused: boolean = false;
  @State private scanText: string = '';
  @State private scanDone: number = 0;
  @State private scanTotal: number = 0;
  @State private rightVisible: boolean = true;
  @State private rightTab: number = 0;
  @State private bottomCollapsed: boolean = false;
  @State private signalVisible: boolean = false;
  @State private logs: LogLine[] = [];
  @State private lyrics: LyricLine[] = [];
  @State private lyricIndex: number = 0;
  @State private lyricSource: string = '';
  @State private stages: SignalStage[] = [];
  @State private appSettings: AppSettingsData = new AppSettingsData();
  @State private uiOpacity: number = 95;
  @State private miniOn: boolean = false;
  @State private eqOn: boolean = false;
  @State private windowWidth: number = 1420;
  @State private statusText: string = '就绪';
  @State private trueCount: number = 0;
  @State private fakeCountV: number = 0;
  @State private suspectCount: number = 0;
  @State private unknownCount: number = 0;
  @State private verdictFilter: string = 'all';
  @State private matchSettings: MatchSettings = new MatchSettings();
  @State private pendingMatches: PendingMatch[] = [];
  @State private autoBusy: boolean = false;
  @State private autoCancel: boolean = false;
  @State private autoText: string = '';
  @State private autoDone: number = 0;
  @State private autoTotal: number = 0;
  @State private authBusy: boolean = false;
  @State private authCancel: boolean = false;
  @State private authText: string = '';
  @State private authDone: number = 0;
  @State private authTotal: number = 0;
  @State private matchCandidates: MatchCandidate[] = [];
  @State private matchTrack: Track = emptyTrack();
  @State private matchTitle: string = '';
  @State private spectrumResult: AuthenticityResult = new AuthenticityResult();
  @State private spectrumTitle: string = '';
  @StorageLink('npPlaying') npPlaying: boolean = false;
  @StorageLink('npPos') npPos: number = 0;
  @StorageLink('npDur') npDur: number = 0;
  @StorageLink('npIdx') npIdx: number = -1;

  private engine: PlayerEngine = PlayerEngine.get();
  private scanCtrl: ScanController = new ScanController();
  private lyricPath: string = '';
  private lastAnchorId: number = -1;
  private sessionReady: boolean = false;
  private tickCount: number = 0;
  private narrowHandled: boolean = false;
  private playlistDlg: CustomDialogController | undefined = undefined;
  private aboutDlg: CustomDialogController | undefined = undefined;
  private rootsDlg: CustomDialogController | undefined = undefined;
  private matchDlg: CustomDialogController | undefined = undefined;
  private spectrumDlg: CustomDialogController | undefined = undefined;
  private cueSplitDlg: CustomDialogController | undefined = undefined;
  private quarantineDlg: CustomDialogController | undefined = undefined;
  private canvasSettings: RenderingContextSettings = new RenderingContextSettings(true);
  private canvasCtx: CanvasRenderingContext2D = new CanvasRenderingContext2D(this.canvasSettings);
  private onLogChanged = (): void => {
    this.logs = AppLog.lines.slice();
  };
  private onEngineTick = (): void => {
    const t = this.engine.current();
    if (t == undefined) {
      if (this.playingPath != '') {
        this.playingPath = '';
        this.playingTrack = emptyTrack();
        this.playingCover = '';
      }
      return;
    }
    if (this.playingPath != t.path) {
      this.playingPath = t.path;
      this.playingTrack = t;
      this.lyrics = [];
      this.lyricSource = '';
      this.lyricIndex = 0;
      AppLog.info(`播放：${t.artist == '' ? '未知歌手' : t.artist} - ${t.title}`);
      this.updatePlayingCover(t);
      this.loadLyricsFor(t, false);
    }
    this.tickCount++;
    this.updateLyricIndex();
    if (this.tickCount % 5 == 0) {
      this.updateSignal();
    }
  };

  // ------------------------------------------------------------ 生命周期

  aboutToAppear(): void {
    this.initAsync();
  }

  aboutToDisappear(): void {
    if (this.sessionReady) {
      SessionStore.saveNow(this.engine);
    }
    SessionStore.stopAutoSave();
    AppLog.unsubscribe(this.onLogChanged);
    this.engine.removeListener(this.onEngineTick);
  }

  onPageShow(): void {
    this.eqOn = NativePlayer.eqEnabled();
    this.refresh();
    this.updateSignal();
    if (this.selectedId <= 0 && this.playingTrack.id != 0) {
      this.selectedId = this.playingTrack.id;
      this.selectedTrack = this.playingTrack;
      this.updateSelectedCover(this.playingTrack);
    }
  }

  private async initAsync(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    await Db.init(ctx);
    this.appSettings = await AppSettings.load();
    const wantTheme = ThemeStore.normalize(this.appSettings.theme);
    if (wantTheme != this.appSettings.theme) {
      this.appSettings.theme = wantTheme;
    }
    if (wantTheme != ThemeStore.currentName()) {
      // 冷启动恢复已保存主题：先套用调色板，再重建页面让所有静态颜色重新取值
      ThemeStore.apply(wantTheme);
      router.replaceUrl({ url: 'pages/Index' });
      return;
    }
    this.syncMatchSettings();
    this.uiOpacity = this.appSettings.opacity;
    this.engine.setMode(this.appSettings.playMode as PlayMode);
    this.engine.setVolume(this.appSettings.volume / 100);

    const eq = await EqPresets.load();
    NativePlayer.setEq(eq.enabled, eq.gains);
    this.eqOn = eq.enabled;
    const dspReport = NativePlayer.selfTest();
    if (dspReport != '') {
      console.info(`dsp selftest ${dspReport}`);
    }

    AppLog.subscribe(this.onLogChanged);
    AppLog.info('日志：控制台 + 底部运行日志栏');
    AppLog.info('联网匹配 / 真假鉴定 / 频谱 / CUE 分轨已启用');

    this.roots = await ScanRoots.load(ctx);
    const session = await SessionStore.load();
    this.engine.setMode(session.playMode as PlayMode);
    this.engine.setVolume(session.volume);
    this.engine.onSessionDirty = (force: boolean): void => {
      SessionStore.markDirty(this.engine, force);
    };
    this.engine.addListener(this.onEngineTick);
    await this.seedSamplesIfNeeded(ctx);

    const st = await Db.stats();
    if (st.count == 0) {
      await this.startScan();
    } else {
      await this.refresh();
    }
    await this.rebuildCues();
    await this.restoreSession(session.queuePaths, session.lastTrackPath, session.lastPositionMs);
    this.sessionReady = true;
    SessionStore.startAutoSave(this.engine);
    if (this.playingTrack.id != 0) {
      this.selectedId = this.playingTrack.id;
      this.selectedTrack = this.playingTrack;
      this.updateSelectedCover(this.playingTrack);
      this.updatePlayingCover(this.playingTrack);
      this.loadLyricsFor(this.playingTrack, false);
    } else if (this.filtered.length > 0) {
      this.selectTrack(this.filtered[0]);
    }
    this.updateSignal();
    this.logs = AppLog.lines.slice();
  }

  private syncMatchSettings(): void {
    this.matchSettings.useNetmusic = this.appSettings.useNetmusic;
    this.matchSettings.useQqmusic = this.appSettings.useQqmusic;
    this.matchSettings.useMusicBrainz = this.appSettings.useMusicBrainz;
    this.matchSettings.useItunes = this.appSettings.useItunes;
    this.matchSettings.useLrclib = this.appSettings.useLrclib;
    this.matchSettings.useCoverart = this.appSettings.useCoverart;
    this.matchSettings.threshold = this.appSettings.threshold / 100;
    this.matchSettings.autoCover = this.appSettings.autoCover;
    this.matchSettings.autoRename = this.appSettings.autoRename;
    this.matchSettings.renameTemplate = this.appSettings.renameTemplate;
  }

  private async seedSamplesIfNeeded(ctx: common.UIAbilityContext): Promise<void> {
    const musicDir = `${ctx.filesDir}/Music`;
    const seeded = await Db.getSetting('seed_samples_done', '');
    if (seeded == '1') {
      return;
    }
    AppLog.info(`首次启动：载入内置示例到 ${musicDir}`);
    try {
      try {
        fs.mkdirSync(musicDir, true);
      } catch (e) {
        // 目录已存在
      }
      const names: string[] = ['01_yehangxing.flac', '02_chenguang.mp3', '03_sine_48k.wav',
        '04_sine24_96k.wav', 'sample.cue'];
      let copied = 0;
      for (const n of names) {
        try {
          fs.statSync(`${musicDir}/${n}`);
          continue;
        } catch (e) {
          // 缺失的示例补拷
        }
        let bytes = new Uint8Array(0);
        try {
          try {
            bytes = ctx.resourceManager.getRawFileContentSync(`rawfile/${n}`);
          } catch (e) {
            bytes = ctx.resourceManager.getRawFileContentSync(n);
          }
        } catch (e) {
          AppLog.error(`示例读取失败：${n}`);
          continue;
        }
        const ab = new ArrayBuffer(bytes.byteLength);
        new Uint8Array(ab).set(bytes);
        const f = fs.openSync(`${musicDir}/${n}`,
          fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
        try {
          fs.writeSync(f.fd, ab);
        } finally {
          fs.closeSync(f);
        }
        copied++;
      }
      if (copied > 0) {
        AppLog.info(`已载入 ${copied} 个内置示例文件`);
      }
      await Db.setSetting('seed_samples_done', '1');
    } catch (e) {
      const err = e as BusinessError;
      AppLog.error(`示例载入失败：${err.message}`);
    }
  }

  private async restoreSession(paths: string[], lastPath: string, positionMs: number): Promise<void> {
    if (paths.length == 0) {
      return;
    }
    const tracks = await Db.tracksByPaths(paths);
    if (tracks.length == 0) {
      return;
    }
    let idx = 0;
    for (let i = 0; i < tracks.length; i++) {
      if (tracks[i].path == lastPath) {
        idx = i;
        break;
      }
    }
    let pos = 0;
    if (lastPath != '' && tracks[idx].path == lastPath) {
      const t = tracks[idx];
      const dur = t.isCueChild == 1 ? Math.max(0, t.cueEndMs - t.cueStartMs) : t.durationMs;
      pos = dur > 1000 ? Math.min(positionMs, dur - 800) : 0;
      pos = Math.max(0, pos);
    }
    this.engine.restoreQueue(tracks, idx, pos);
    this.playingTrack = tracks[idx];
    this.playingPath = tracks[idx].path;
    AppLog.info(`已恢复上次会话：${tracks[idx].title}`);
  }

  // ------------------------------------------------------------ 数据

  private async refresh(): Promise<void> {
    try {
      this.allTracks = await Db.listTracks('');
      this.stats = await Db.stats();
      let done = 0;
      let warn = 0;
      let bad = 0;
      let lossless = 0;
      let lossy = 0;
      let hiRes = 0;
      let cue = 0;
      let vTrue = 0;
      let vFake = 0;
      let vSuspect = 0;
      let vUnknown = 0;
      for (const t of this.allTracks) {
        if (t.healthStatus == 'bad') {
          bad++;
        } else if (t.healthStatus == 'warn') {
          warn++;
        } else {
          done++;
        }
        if (T.isLossless(t)) {
          lossless++;
        } else {
          lossy++;
        }
        if (T.isHiRes(t)) {
          hiRes++;
        }
        if (t.isCueChild == 1) {
          cue++;
        }
        const group = verdictGroup(t.verdict);
        if (group == 'true') {
          vTrue++;
        } else if (group == 'fake') {
          vFake++;
        } else if (group == 'suspect') {
          vSuspect++;
        } else {
          vUnknown++;
        }
      }
      this.doneCount = done;
      this.warnCount = warn;
      this.badCount = bad;
      this.trueCount = vTrue;
      this.fakeCountV = vFake;
      this.suspectCount = vSuspect;
      this.unknownCount = vUnknown;
      this.counts = [this.allTracks.length, lossless, lossy, hiRes, cue];
      this.applyFilter();
      if (this.selectedId > 0) {
        const keep = this.allTracks.find((t: Track) => t.id == this.selectedId);
        if (keep != undefined) {
          this.selectedTrack = keep;
        }
      }
    } catch (e) {
      // 数据库未就绪时忽略
    }
  }

  private applyFilter(): void {
    const kw = this.keyword.trim().toLowerCase();
    let list = this.allTracks;
    if (kw != '') {
      list = list.filter((t: Track) =>
        t.title.toLowerCase().includes(kw) ||
        t.artist.toLowerCase().includes(kw) ||
        t.album.toLowerCase().includes(kw) ||
        t.fileName.toLowerCase().includes(kw));
    }
    if (this.losslessOnly) {
      list = list.filter((t: Track) => T.isLossless(t));
    }
    if (this.problemsOnly) {
      list = list.filter((t: Track) => t.healthStatus != 'ok');
    }
    if (this.specIndex == 1) {
      list = list.filter((t: Track) => t.sampleRate == 44100);
    } else if (this.specIndex == 2) {
      list = list.filter((t: Track) => t.sampleRate == 48000);
    } else if (this.specIndex == 3) {
      list = list.filter((t: Track) => t.sampleRate == 96000);
    } else if (this.specIndex == 4) {
      list = list.filter((t: Track) => t.sampleRate >= 176400);
    } else if (this.specIndex == 5) {
      list = list.filter((t: Track) => {
        const ext = t.ext.toLowerCase();
        return ext == '.dsf' || ext == '.dff';
      });
    } else if (this.specIndex == 6) {
      list = list.filter((t: Track) => t.bitDepth == 16);
    } else if (this.specIndex == 7) {
      list = list.filter((t: Track) => t.bitDepth == 24);
    }
    if (this.verdictIndex == 1) {
      list = list.filter((t: Track) => T.isLossless(t));
    } else if (this.verdictIndex == 2) {
      list = list.filter((t: Track) => !T.isLossless(t));
    } else if (this.verdictIndex == 3) {
      list = list.filter((t: Track) => T.isHiRes(t));
    } else if (this.verdictIndex == 4) {
      list = list.filter((t: Track) => t.isCueChild == 1);
    }
    if (this.verdictFilter == 'true') {
      list = list.filter((t: Track) => verdictGroup(t.verdict) == 'true');
    } else if (this.verdictFilter == 'fake') {
      list = list.filter((t: Track) => verdictGroup(t.verdict) == 'fake');
    } else if (this.verdictFilter == 'suspect') {
      list = list.filter((t: Track) => verdictGroup(t.verdict) == 'suspect');
    } else if (this.verdictFilter == 'unknown') {
      list = list.filter((t: Track) => {
        const g = verdictGroup(t.verdict);
        return g == 'unknown' || g == 'none';
      });
    }
    this.filtered = list;
    if (this.selectedIds.length > 0) {
      const kept = this.selectedIds.filter((id: number) => list.some((t: Track) => t.id == id));
      if (kept.length != this.selectedIds.length) {
        this.selectedIds = kept;
      }
    }
    if (this.selectedId > 0) {
      const keep = list.find((t: Track) => t.id == this.selectedId);
      if (keep == undefined) {
        this.clearSelection();
      }
    }
  }

  private async rebuildCues(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const roots = await ScanRoots.load(ctx);
      const res = await CueIndexer.rebuild(roots);
      if (res.children > 0) {
        await this.refresh();
        AppLog.info(`CUE 已索引：${res.cueFiles} 个文件 · ${res.children} 个逻辑轨`);
      }
    } catch (e) {
      AppLog.warn('CUE 索引失败，不影响普通曲目播放');
    }
  }

  // ------------------------------------------------------------ 扫描 / 导入

  private async startScan(): Promise<void> {
    if (this.scanning) {
      return;
    }
    const ctx = getContext(this) as common.UIAbilityContext;
    this.roots = await ScanRoots.load(ctx);
    this.scanCtrl = new ScanController();
    this.scanPaused = false;
    this.scanning = true;
    this.scanText = '正在收集文件…';
    this.scanDone = 0;
    this.scanTotal = 0;
    this.statusText = '扫描中';
    AppLog.info(`开始扫描 ${this.roots.length} 个根目录`);
    try {
      const res = await Scanner.scan(this.roots, this.scanCtrl,
        (done: number, total: number, cur: string) => {
          this.scanDone = done;
          this.scanTotal = total;
          this.scanText = total > 0 ? `扫描中 ${done}/${total}：${cur}` : '正在收集文件…';
        });
      await this.rebuildCues();
      await this.refresh();
      this.statusText = '就绪';
      const issueText = res.failed > 0 ? ` · 失败 ${res.failed}` : '';
      const removeText = res.removed > 0 ? ` · 失效 ${res.removed}` : '';
      AppLog.info(`扫描完成：新增 ${res.added} · 更新 ${res.updated} · 跳过 ${res.skipped} · ` +
        `失效 ${res.removed} · 失败 ${res.failed}（${res.costMs}ms）${issueText}`);
      promptAction.showToast({ message: `扫描完成：新增 ${res.added} · 更新 ${res.updated} · ` +
        `跳过 ${res.skipped}${removeText}` });
    } catch (e) {
      const err = e as BusinessError;
      this.statusText = '扫描失败';
      AppLog.error(`扫描失败：${err.message}`);
    }
    this.scanning = false;
  }

  private toggleScanPause(): void {
    if (!this.scanning) {
      return;
    }
    if (this.scanCtrl.paused) {
      this.scanCtrl.resume();
      this.scanPaused = false;
      AppLog.info('扫描已继续');
    } else {
      this.scanCtrl.pause();
      this.scanPaused = true;
      AppLog.info('扫描已暂停');
    }
  }

  private cancelScan(): void {
    if (!this.scanning) {
      return;
    }
    this.scanCtrl.cancel();
    this.scanPaused = false;
    AppLog.warn('扫描已取消');
  }

  private async importFiles(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    try {
      const avp = new picker.AudioViewPicker();
      const opts = new picker.AudioSelectOptions();
      opts.maxSelectNumber = 1000;
      const uris = await avp.select(opts);
      if (!uris || uris.length == 0) {
        return;
      }
      await this.importUris(uris);
    } catch (e) {
      // 用户取消
    }
  }

  private async importUris(uris: string[]): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    this.statusText = '导入中';
    const rep: ImportReport = await ImportManager.importFiles(uris, `${ctx.filesDir}/Music`, false);
    AppLog.info(`导入 ${rep.ok}/${rep.total} · 跳过重复 ${rep.skipped} · 失败 ${rep.failed.length}`);
    if (rep.ok > 0) {
      await this.startScan();
    } else {
      this.statusText = '就绪';
    }
  }

  private openRootsDialog(): void {
    const dlg = new CustomDialogController({
      builder: ScanRootsDialog({
        roots: this.roots.slice(),
        onClose: () => {
          if (this.rootsDlg != undefined) {
            this.rootsDlg.close();
          }
        },
        onRootsChanged: (roots: string[]) => {
          this.roots = roots;
          ScanRoots.save(roots);
          AppLog.info(`扫描根目录：${roots.join(' · ')}`);
        },
        onScan: () => {
          this.startScan();
        }
      }),
      autoCancel: true,
      alignment: DialogAlignment.Center,
      customStyle: true,
      maskColor: '#99000000'
    });
    this.rootsDlg = dlg;
    dlg.open();
  }

  // ------------------------------------------------------------ 曲库操作

  private activateTrack(t: Track): void {
    this.selectedId = t.id;
    this.selectedTrack = t;
    this.updateSelectedCover(t);
    if (t.path != this.playingPath) {
      this.loadLyricsFor(t, false);
    }
  }

  private clearSelection(): void {
    this.selectedIds = [];
    this.selectedId = -1;
    this.selectedTrack = emptyTrack();
    this.selectedCover = '';
    this.lastAnchorId = -1;
  }

  private selectTrack(t: Track): void {
    this.selectedIds = [t.id];
    this.lastAnchorId = t.id;
    this.activateTrack(t);
  }

  // Ctrl 点选切换单行，Shift 按锚点范围选择，普通点击回落单选
  private handleRowSelect(t: Track, index: number, ctrl: boolean, shift: boolean): void {
    if (shift) {
      const list = this.filtered;
      let anchorIdx = list.findIndex((x: Track) => x.id == this.lastAnchorId);
      if (anchorIdx < 0) {
        anchorIdx = index;
      }
      const lo = anchorIdx <= index ? anchorIdx : index;
      const hi = anchorIdx <= index ? index : anchorIdx;
      const ids: number[] = [];
      for (let i = lo; i <= hi; i++) {
        ids.push(list[i].id);
      }
      this.selectedIds = ids;
      this.activateTrack(t);
      return;
    }
    if (ctrl) {
      const pos = this.selectedIds.indexOf(t.id);
      if (pos >= 0) {
        const ids = this.selectedIds.slice();
        ids.splice(pos, 1);
        this.selectedIds = ids;
        if (this.selectedId == t.id) {
          if (ids.length == 0) {
            this.clearSelection();
          } else {
            const next = this.allTracks.find((x: Track) => x.id == ids[ids.length - 1]);
            if (next != undefined) {
              this.activateTrack(next);
            } else {
              this.clearSelection();
            }
          }
        }
      } else {
        const ids = this.selectedIds.slice();
        ids.push(t.id);
        this.selectedIds = ids;
        this.activateTrack(t);
      }
      this.lastAnchorId = t.id;
      return;
    }
    this.selectTrack(t);
  }

  private pickedTracks(): Track[] {
    const out: Track[] = [];
    for (const t of this.allTracks) {
      if (this.selectedIds.indexOf(t.id) >= 0) {
        out.push(t);
      }
    }
    return out;
  }

  private async updateSelectedCover(t: Track): Promise<void> {
    if (t.coverBlobPath != '') {
      this.selectedCover = `file://${t.coverBlobPath}`;
      return;
    }
    this.selectedCover = '';
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const p = await CoverService.ensureCover(ctx, t);
      if (p != '' && this.selectedId == t.id) {
        t.coverBlobPath = p;
        this.selectedCover = `file://${p}`;
      }
    } catch (e) {
      // 无内嵌封面：保留占位
    }
  }

  private async updatePlayingCover(t: Track): Promise<void> {
    if (t.coverBlobPath != '') {
      this.playingCover = `file://${t.coverBlobPath}`;
      return;
    }
    this.playingCover = '';
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const p = await CoverService.ensureCover(ctx, t);
      if (p != '' && this.playingTrack.path == t.path) {
        t.coverBlobPath = p;
        this.playingCover = `file://${p}`;
      }
    } catch (e) {
      // 忽略
    }
  }

  private physicalPath(t: Track): string {
    return t.isCueChild == 1 && t.cuePath != '' ? t.cuePath : t.path;
  }

  private openEditorFor(t: Track): void {
    AppStorage.setOrCreate('seqaudio_edit_paths', JSON.stringify([this.physicalPath(t)]));
    router.pushUrl({ url: 'pages/TrackEditPage' });
  }

  private openCoverFor(t: Track): void {
    AppStorage.setOrCreate('seqaudio_cover_path', JSON.stringify([this.physicalPath(t)]));
    router.pushUrl({ url: 'pages/CoverPage' });
  }

  private openLyricsFor(t: Track): void {
    AppStorage.setOrCreate('seqaudio_lyrics_path', JSON.stringify([this.physicalPath(t)]));
    router.pushUrl({ url: 'pages/LyricsPage' });
  }

  private batchEditFor(t: Track): void {
    let list = this.pickedTracks();
    if (t.id != 0 && !list.some((x: Track) => x.id == t.id)) {
      list = [t];
    }
    if (list.length == 0) {
      promptAction.showToast({ message: '先选择要编辑的曲目' });
      return;
    }
    const paths: string[] = [];
    for (const x of list) {
      if (x.isCueChild == 1) {
        continue;
      }
      paths.push(x.path);
    }
    if (paths.length == 0) {
      promptAction.showToast({ message: 'CUE 子轨请先分轨或改用单曲编辑' });
      return;
    }
    AppStorage.setOrCreate('seqaudio_edit_paths', JSON.stringify(paths));
    router.pushUrl({ url: 'pages/TrackEditPage' });
  }

  private queueTargets(t: Track): Track[] {
    let list = this.pickedTracks();
    if (t.id != 0 && !list.some((x: Track) => x.id == t.id)) {
      list = [t];
    }
    return list;
  }

  private playNextTracks(t: Track): void {
    const list = this.queueTargets(t);
    if (list.length == 0) {
      return;
    }
    for (let i = list.length - 1; i >= 0; i--) {
      this.engine.insertNext(list[i]);
    }
    AppLog.info(`下一首播放：${list.length} 首`);
    promptAction.showToast({ message: `已加入下一首：${list.length} 首` });
  }

  private enqueueTracks(t: Track): void {
    const list = this.queueTargets(t);
    if (list.length == 0) {
      return;
    }
    for (const x of list) {
      this.engine.enqueue(x);
    }
    AppLog.info(`加入队列：${list.length} 首`);
    promptAction.showToast({ message: `已加入队列：${list.length} 首` });
  }

  private async copyTrackPath(t: Track): Promise<void> {
    const path = this.physicalPath(t);
    try {
      const data = pasteboard.createData(pasteboard.MIMETYPE_TEXT_PLAIN, path);
      await pasteboard.getSystemPasteboard().setData(data);
      promptAction.showToast({ message: '已复制完整路径' });
    } catch (e) {
      promptAction.showToast({ message: '复制失败' });
    }
  }

  private currentTarget(): Track {
    if (this.selectedTrack.id != 0) {
      return this.selectedTrack;
    }
    if (this.playingTrack.id != 0) {
      return this.playingTrack;
    }
    return emptyTrack();
  }

  private async removeTrack(t: Track): Promise<void> {
    if (t.id <= 0) {
      return;
    }
    const playing = this.playingPath == t.path;
    await Db.deleteTrack(t.id);
    AppLog.info(`已从曲库移除：${t.fileName}（未删除磁盘文件）`);
    if (playing) {
      this.engine.stop();
    }
    await this.refresh();
    if (this.selectedIds.indexOf(t.id) >= 0) {
      this.selectedIds = this.selectedIds.filter((id: number) => id != t.id);
    }
    if (this.selectedId == t.id) {
      this.clearSelection();
    }
    promptAction.showToast({ message: '已从曲库移除（文件仍在磁盘）' });
  }

  private removeSelection(): void {
    const picked = this.pickedTracks();
    if (picked.length == 0) {
      if (this.selectedTrack.id != 0) {
        this.removeTrack(this.selectedTrack);
      } else {
        promptAction.showToast({ message: '先选择要移除的曲目' });
      }
      return;
    }
    if (picked.length == 1) {
      this.removeTrack(picked[0]);
      return;
    }
    AlertDialog.show({
      message: `将选中的 ${picked.length} 首从曲库移除？（不会删除磁盘文件）`,
      primaryButton: { value: '取消', action: () => {
      } },
      secondaryButton: {
        value: '移除',
        fontColor: T.BAD,
        action: () => {
          this.doRemoveMany(picked);
        }
      }
    });
  }

  private async doRemoveMany(list: Track[]): Promise<void> {
    let stopPlayback = false;
    for (const t of list) {
      if (this.playingPath == t.path) {
        stopPlayback = true;
      }
      await Db.deleteTrack(t.id);
    }
    if (stopPlayback) {
      this.engine.stop();
    }
    this.clearSelection();
    await this.refresh();
    AppLog.info(`已从曲库移除 ${list.length} 首（未删除磁盘文件）`);
    promptAction.showToast({ message: `已从曲库移除 ${list.length} 首（文件仍在磁盘）` });
  }

  private clearLibrary(): void {
    if (this.allTracks.length == 0) {
      return;
    }
    AlertDialog.show({
      message: '清空曲库？（不会删除音乐文件本身）',
      primaryButton: { value: '取消', action: () => {
      } },
      secondaryButton: {
        value: '清空',
        fontColor: T.BAD,
        action: () => {
          this.doClear();
        }
      }
    });
  }

  private async doClear(): Promise<void> {
    await Db.clearTracks();
    this.engine.stop();
    this.engine.queue = [];
    this.engine.index = -1;
    AppStorage.setOrCreate('npIdx', -1);
    AppStorage.setOrCreate('npTitle', '');
    AppStorage.setOrCreate('npArtist', '');
    this.clearSelection();
    this.playingTrack = emptyTrack();
    this.playingPath = '';
    this.playingCover = '';
    await this.refresh();
    AppLog.info('曲库已清空');
    promptAction.showToast({ message: '曲库已清空（文件未删除）' });
  }

  // ------------------------------------------------------------ 播放

  private playAt(t: Track, index: number): void {
    let list = this.filtered;
    let idx = index;
    if (idx < 0 || idx >= list.length || list[idx].id != t.id) {
      idx = list.findIndex((x: Track) => x.id == t.id);
    }
    if (idx < 0) {
      list = [t];
      idx = 0;
    }
    this.engine.setQueue(list, idx);
    this.selectTrack(t);
    this.statusText = '播放中';
  }

  private togglePlay(): void {
    if (this.engine.index < 0) {
      const target = this.currentTarget();
      if (target.id != 0) {
        this.playAt(target, -1);
        return;
      }
      if (this.filtered.length > 0) {
        this.playAt(this.filtered[0], 0);
      }
      return;
    }
    this.engine.toggle();
  }

  private cycleMode(): void {
    const next = ((this.engine.mode as number) + 1) % 4;
    this.engine.setMode(next as PlayMode);
    Db.setSetting('play_mode', `${next}`);
    AppLog.info(`播放模式：${MODE_NAMES[next]}`);
    promptAction.showToast({ message: `播放模式：${MODE_NAMES[next]}` });
  }

  private updateLyricIndex(): void {
    if (this.lyrics.length == 0) {
      this.lyricIndex = 0;
      return;
    }
    let idx = 0;
    for (let i = 0; i < this.lyrics.length; i++) {
      if (this.lyrics[i].timeMs <= this.npPos) {
        idx = i;
      } else {
        break;
      }
    }
    if (idx != this.lyricIndex) {
      this.lyricIndex = idx;
    }
  }

  private async loadLyricsFor(t: Track, force: boolean): Promise<void> {
    if (t.id == 0) {
      this.lyrics = [];
      this.lyricSource = '';
      return;
    }
    this.lyricPath = t.path;
    let res: LyricsResult | undefined = undefined;
    if (!force && !this.appSettings.autoLyrics) {
      // 自动加载关闭时：只读曲库缓存与同目录 .lrc，不主动联网
      const cached = new LyricsResult();
      if (t.lyrics != '') {
        cached.source = '曲库缓存';
        cached.lines = LyricsService.parseLrc(t.lyrics);
        cached.plain = t.lyrics;
      }
      if (cached.lines.length == 0) {
        const local = await LyricsService.findLocal(t);
        if (local != '') {
          cached.source = '本地 LRC';
          cached.lines = LyricsService.parseLrc(local);
          cached.plain = local;
          await Db.setLyrics(t.id, local);
        }
      }
      res = cached;
    } else {
      res = await LyricsService.load(t, force);
    }
    if (t.path != this.lyricPath || res == undefined) {
      return;
    }
    this.lyrics = res.lines;
    this.lyricSource = res.source;
    this.updateLyricIndex();
    if (res.lines.length > 0) {
      AppLog.info(`歌词：${res.source} · ${res.lines.length} 行（${t.title}）`);
    }
  }

  private reloadLyrics(): void {
    const t = this.currentTarget();
    if (t.id == 0) {
      promptAction.showToast({ message: '先选择或播放一首曲目' });
      return;
    }
    this.lyricPath = t.path;
    this.loadLyricsFor(t, true);
  }

  private updateSignal(): void {
    if (this.playingTrack.id == 0) {
      this.stages = [];
      return;
    }
    const info = NativePlayer.info();
    const stages: SignalStage[] = [];
    if (info.codec != '') {
      const same = info.outRate == info.sampleRate && info.outBits == info.bits;
      stages.push(new SignalStage('源', `${info.codec.toUpperCase()} ${info.bits}bit/${(info.sampleRate / 1000).toFixed(1)}kHz · ${info.channels}ch`, '读取', T.FG_DIM));
      stages.push(new SignalStage('解码', 'FFmpeg → PCM', '已解码', T.FG_DIM));
      stages.push(new SignalStage('增益', '0 dB', '旁路', T.FG_MUTE));
      stages.push(new SignalStage('EQ', info.eq ? `${info.eqBands} 段` : '全旁路',
        info.eq ? '已改变' : '旁路', info.eq ? T.WARN : T.FG_MUTE));
      stages.push(new SignalStage('输出',
        `OH_AudioRenderer ${info.outBits}bit/${(info.outRate / 1000).toFixed(1)}kHz`,
        same ? '直通' : '已转换', same ? T.OK : T.WARN));
    } else {
      const t = this.playingTrack;
      stages.push(new SignalStage('源', fmtSpec(t), '读取', T.FG_DIM));
      stages.push(new SignalStage('解码', 'AVPlayer（降级路径）', '已解码', T.WARN));
      stages.push(new SignalStage('输出', '系统混音器', '非直通', T.WARN));
    }
    this.stages = stages;
  }

  private kernelLabel(): string {
    const label = NativePlayer.label();
    if (label != '') {
      return label;
    }
    return NativePlayer.available ? '原生内核待命' : 'AVPlayer';
  }

  // ------------------------------------------------------------ 播放列表 / 弹层

  private currentListPaths(): string[] {
    const out: string[] = [];
    for (const t of this.filtered) {
      out.push(t.path);
    }
    return out;
  }

  private async loadPlaylist(paths: string[], append: boolean): Promise<void> {
    const tracks = await Db.tracksByPaths(paths);
    if (tracks.length == 0) {
      promptAction.showToast({ message: '列表里的曲目已不在曲库' });
      return;
    }
    let queue: Track[] = tracks;
    let index = 0;
    if (append && this.engine.queue.length > 0) {
      queue = this.engine.queue.concat(tracks);
      index = this.engine.index >= 0 ? this.engine.index : 0;
    }
    this.engine.restoreQueue(queue, index, 0);
    this.playingTrack = queue[index];
    this.playingPath = queue[index].path;
    this.selectTrack(tracks[0]);
    this.updatePlayingCover(queue[index]);
    this.loadLyricsFor(queue[index], false);
    AppLog.info(`${append ? '追加' : '载入'}播放列表：${tracks.length} 首`);
    promptAction.showToast({ message: `${append ? '已追加' : '已载入'} ${tracks.length} 首` });
  }

  private openPlaylistsDialog(): void {
    const dlg = new CustomDialogController({
      builder: PlaylistDialog({
        currentPaths: this.currentListPaths(),
        onClose: () => {
          if (this.playlistDlg != undefined) {
            this.playlistDlg.close();
          }
        },
        onLoad: (paths: string[], append: boolean) => {
          this.loadPlaylist(paths, append);
        }
      }),
      autoCancel: true,
      alignment: DialogAlignment.Center,
      customStyle: true,
      maskColor: '#99000000'
    });
    this.playlistDlg = dlg;
    dlg.open();
  }

  private openAboutDialog(): void {
    const dlg = new CustomDialogController({
      builder: AboutDialog({
        onClose: () => {
          if (this.aboutDlg != undefined) {
            this.aboutDlg.close();
          }
        }
      }),
      autoCancel: true,
      alignment: DialogAlignment.Center,
      customStyle: true,
      maskColor: '#99000000'
    });
    this.aboutDlg = dlg;
    dlg.open();
  }

  private onMore(key: string): void {
    if (key == 'rename') {
      router.pushUrl({ url: 'pages/LibraryToolsPage' });
    } else if (key == 'tools') {
      router.pushUrl({ url: 'pages/LibraryToolsPage' });
    } else if (key == 'health') {
      router.pushUrl({ url: 'pages/HealthPage' });
    } else if (key == 'batchedit') {
      this.batchEditFor(this.currentTarget());
    } else if (key == 'undoedit') {
      this.undoTagEdits(this.currentTarget());
    } else if (key == 'stripno') {
      this.stripSelectedTitles();
    } else if (key == 'report') {
      this.exportReport();
    } else if (key == 'exportcovers') {
      this.exportCovers();
    } else if (key == 'foldercovers') {
      this.exportFolderCovers();
    } else if (key == 'quarantine') {
      this.openQuarantine();
    } else if (key == 'playnext') {
      this.playNextTracks(this.currentTarget());
    } else if (key == 'queue') {
      this.enqueueTracks(this.currentTarget());
    } else if (key == 'copypath') {
      this.copyTrackPath(this.currentTarget());
    } else if (key == 'cuesplit') {
      this.openCueSplitFor(this.currentTarget());
    } else if (key == 'spectrum') {
      const t = this.currentTarget();
      if (t.id != 0) {
        this.openSpectrumFor(t);
      } else {
        promptAction.showToast({ message: '先选择一首曲目' });
      }
    } else if (key == 'playlists') {
      this.openPlaylistsDialog();
    } else if (key == 'cover') {
      const t = this.currentTarget();
      if (t.id != 0) {
        this.openCoverFor(t);
      } else {
        promptAction.showToast({ message: '先选择一首曲目' });
      }
    } else if (key == 'lyrics') {
      const t = this.currentTarget();
      if (t.id != 0) {
        this.openLyricsFor(t);
      } else {
        promptAction.showToast({ message: '先选择一首曲目' });
      }
    } else if (key == 'eq') {
      router.pushUrl({ url: 'pages/EqPage' });
    } else if (key == 'about') {
      this.openAboutDialog();
    }
  }

  // ------------------------------------------------------------ 参数

  private async saveSettings(d: AppSettingsData): Promise<void> {
    const oldTheme = this.appSettings.theme;
    this.appSettings = d;
    this.syncMatchSettings();
    this.uiOpacity = d.opacity;
    this.engine.setVolume(d.volume / 100);
    this.engine.setMode(d.playMode as PlayMode);
    await AppSettings.save(d);
    if (d.theme != oldTheme) {
      ThemeStore.apply(d.theme);
      AppLog.info(`主题切换：${oldTheme} → ${d.theme}`);
      promptAction.showToast({ message: `主题已切换：${d.theme}` });
      setTimeout(() => {
        router.replaceUrl({ url: 'pages/Index' });
      }, 300);
      return;
    }
    AppLog.info(`参数已保存：备份=${d.backup ? '开' : '关'} · 自动歌词=${d.autoLyrics ? '开' : '关'} · 不透明度=${Math.round(d.opacity)}% · 音量=${Math.round(d.volume)}%`);
    promptAction.showToast({ message: '参数已保存并生效' });
  }

  // ------------------------------------------------------------ 联网匹配 / 全自动

  private autoOptions(): AutoOptions {
    const o = new AutoOptions();
    o.settings = this.matchSettings;
    o.writeTags = true;
    o.fetchCover = this.appSettings.autoCover;
    o.fetchLyrics = true;
    return o;
  }

  private async openMatchFor(t: Track): Promise<void> {
    if (t.id == 0 || t.isCueChild == 1) {
      promptAction.showToast({ message: 'CUE 子轨请先分轨或改用标签编辑' });
      return;
    }
    AppLog.info(`联网匹配：${t.artist} - ${t.title}`);
    promptAction.showToast({ message: '正在联网检索候选…' });
    const res: MatchResult = await MetadataMatcher.match(t, this.matchSettings);
    this.matchCandidates = res.candidates;
    this.matchTrack = t;
    this.matchTitle = `${t.artist == '' ? '未知歌手' : t.artist} - ${t.title}`;
    const dlg = new CustomDialogController({
      builder: MatchDialog({
        trackTitle: this.matchTitle,
        threshold: this.matchSettings.threshold,
        candidates: this.matchCandidates,
        onClose: () => {
          if (this.matchDlg != undefined) {
            this.matchDlg.close();
          }
        },
        onApply: (cand: MatchCandidate) => {
          this.applyCandidateFor(t, cand);
        }
      }),
      autoCancel: true,
      alignment: DialogAlignment.Center,
      customStyle: true,
      maskColor: '#99000000'
    });
    this.matchDlg = dlg;
    dlg.open();
    if (res.candidates.length == 0) {
      promptAction.showToast({ message: '未找到候选，可检查网络后重试' });
    }
  }

  private async applyCandidateFor(t: Track, cand: MatchCandidate): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    const ok = await AutoRunner.applyCandidate(ctx, t, cand, this.autoOptions());
    if (ok) {
      AppLog.info(`已应用候选：${t.fileName} → ${cand.source} · ${cand.artist} - ${cand.title}`);
      promptAction.showToast({ message: `已应用 ${cand.source} 候选并写回` });
    } else {
      promptAction.showToast({ message: '标签写回失败，详见运行日志' });
    }
    await this.refresh();
    this.selectTrackById(t.id);
  }

  private async startAuto(): Promise<void> {
    if (this.autoBusy) {
      return;
    }
    const picked = this.pickedTracks();
    const tracks = picked.length > 1 ? picked :
      (this.filtered.length > 0 ? this.filtered.slice() : this.allTracks.slice());
    if (tracks.length == 0) {
      promptAction.showToast({ message: '曲库为空' });
      return;
    }
    this.autoBusy = true;
    this.autoCancel = false;
    this.autoDone = 0;
    this.autoTotal = tracks.length;
    this.autoText = '准备联网匹配…';
    this.statusText = '全自动匹配中';
    const ctx = getContext(this) as common.UIAbilityContext;
    AppLog.info(`一键全自动：${tracks.length} 首${picked.length > 1 ? '（多选）' : ''}`);
    const rep: AutoReport = await AutoRunner.run(ctx, tracks, this.autoOptions(),
      (done: number, total: number, text: string) => {
        this.autoDone = done;
        this.autoTotal = total;
        this.autoText = text;
      },
      () => this.autoCancel);
    this.pendingMatches = rep.pending;
    this.autoBusy = false;
    this.statusText = '就绪';
    await this.refresh();
    AppLog.info(`全自动完成：命中 ${rep.matched} · 写回 ${rep.written} · ` +
      `重命名 ${rep.renamed} · 待确认 ${rep.pending.length} · 失败 ${rep.failed}`);
    promptAction.showToast({
      message: `全自动完成：写回 ${rep.written} · 重命名 ${rep.renamed} · ` +
        `待确认 ${rep.pending.length} · 失败 ${rep.failed}`
    });
  }

  private cancelAuto(): void {
    if (this.autoBusy) {
      this.autoCancel = true;
      AppLog.warn('已请求停止全自动匹配');
    }
  }

  private async saveAllPending(): Promise<void> {
    if (this.pendingMatches.length == 0 || this.autoBusy) {
      return;
    }
    const list = this.pendingMatches.slice();
    this.autoBusy = true;
    this.autoCancel = false;
    this.autoDone = 0;
    this.autoTotal = list.length;
    this.autoText = '应用待确认候选…';
    const ctx = getContext(this) as common.UIAbilityContext;
    let okCount = 0;
    for (const p of list) {
      const t = await Db.getTrackById(p.trackId);
      if (t != undefined && t.id > 0) {
        const ok = await AutoRunner.applyCandidate(ctx, t, p.candidate, this.autoOptions());
        if (ok) {
          okCount++;
        }
      }
      this.autoDone++;
    }
    this.pendingMatches = [];
    this.autoBusy = false;
    await this.refresh();
    AppLog.info(`全部确认保存：${okCount}/${list.length}`);
    promptAction.showToast({ message: `已保存 ${okCount}/${list.length} 首` });
  }

  // ------------------------------------------------------------ 真假鉴定 / 频谱 / 清理

  private async startAuth(): Promise<void> {
    if (this.authBusy) {
      return;
    }
    const picked = this.pickedTracks();
    const tracks = picked.length > 1 ? picked :
      (this.filtered.length > 0 ? this.filtered.slice() : this.allTracks.slice());
    if (tracks.length == 0) {
      promptAction.showToast({ message: '曲库为空' });
      return;
    }
    this.authBusy = true;
    this.authCancel = false;
    this.authDone = 0;
    this.authTotal = tracks.length;
    this.authText = '准备鉴定…';
    this.statusText = '真假鉴定中';
    AppLog.info(`真假鉴定：${tracks.length} 首${picked.length > 1 ? '（多选）' : ''}`);
    await AuthenticityService.analyzeMany(tracks,
      (done: number, total: number, cur: string) => {
        this.authDone = done;
        this.authTotal = total;
        this.authText = cur;
      },
      () => this.authCancel);
    this.authBusy = false;
    this.statusText = '就绪';
    await this.refresh();
    const msg = `鉴定完成：真无损 ${this.trueCount} · 假无损 ${this.fakeCountV} · 疑似 ${this.suspectCount}`;
    AppLog.info(msg);
    promptAction.showToast({ message: msg });
  }

  private cancelAuth(): void {
    if (this.authBusy) {
      this.authCancel = true;
      AppLog.warn('已请求停止真假鉴定');
    }
  }

  private async authOne(t: Track): Promise<void> {
    if (t.id == 0) {
      return;
    }
    promptAction.showToast({ message: '正在解码抽样…' });
    const res = await AuthenticityService.analyzeTrack(t);
    if (res.error != '') {
      promptAction.showToast({ message: res.error });
    } else {
      promptAction.showToast({ message: `${res.verdict} · ${Math.round(res.confidence * 100)}%` });
    }
    await this.refresh();
    this.selectTrackById(t.id);
  }

  private async openSpectrumFor(t: Track): Promise<void> {
    if (t.id == 0) {
      return;
    }
    const path = t.isCueChild == 1 && t.cuePath != '' ? t.cuePath : t.path;
    const res = AuthenticityService.analyze(path);
    if (res.error != '') {
      promptAction.showToast({ message: res.error });
      return;
    }
    this.spectrumResult = res;
    this.spectrumTitle = `${t.artist == '' ? '未知歌手' : t.artist} - ${t.title}`;
    const dlg = new CustomDialogController({
      builder: SpectrumDialog({
        title: this.spectrumTitle,
        result: this.spectrumResult,
        onClose: () => {
          if (this.spectrumDlg != undefined) {
            this.spectrumDlg.close();
          }
        }
      }),
      autoCancel: true,
      alignment: DialogAlignment.Center,
      customStyle: true,
      maskColor: '#99000000'
    });
    this.spectrumDlg = dlg;
    dlg.open();
  }

  private cleanupFake(): void {
    const includeSuspect = this.appSettings.cleanupIncludeSuspect;
    const picked = this.pickedTracks();
    const pool = picked.length > 1 ? picked : this.allTracks;
    const victims = CleanupService.removable(pool, includeSuspect);
    if (victims.length == 0) {
      const scope = picked.length > 1 ? '选中曲目里' : '';
      promptAction.showToast({
        message: includeSuspect ? `${scope}没有判定为假无损/疑似的曲目` : `${scope}没有判定为假无损的曲目`
      });
      return;
    }
    const scope = includeSuspect ? '假无损/疑似' : '假无损';
    AlertDialog.show({
      message: `将 ${victims.length} 首${scope}移入隔离区？\n文件不会直接删除，可在 files/Quarantine 找回。`,
      primaryButton: { value: '取消', action: () => {
      } },
      secondaryButton: {
        value: '隔离',
        fontColor: T.BAD,
        action: () => {
          this.runCleanup(victims);
        }
      }
    });
  }

  private async runCleanup(victims: Track[]): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    this.statusText = '隔离假无损';
    const rep: CleanupReport = await CleanupService.quarantine(ctx, victims,
      (done: number, total: number, cur: string) => {
        this.statusText = `隔离 ${done}/${total}`;
      });
    this.statusText = '就绪';
    await this.refresh();
    AppLog.warn(`清理完成：隔离 ${rep.moved}/${rep.total} · 失败 ${rep.failed} · ${rep.dir}`);
    promptAction.showToast({ message: `已隔离 ${rep.moved}/${rep.total} 首` });
  }

  private quarantineOne(t: Track): void {
    let list = this.pickedTracks();
    if (t.id != 0 && !list.some((x: Track) => x.id == t.id)) {
      list = [t];
    }
    list = list.filter((x: Track) => x.isCueChild != 1);
    if (list.length == 0) {
      promptAction.showToast({ message: 'CUE 子轨请先分轨再处理' });
      return;
    }
    AlertDialog.show({
      message: `将 ${list.length} 个文件移入隔离区？\n可在「更多 → 隔离区管理」中恢复。`,
      primaryButton: { value: '取消', action: () => {
      } },
      secondaryButton: {
        value: '移入隔离区',
        fontColor: T.BAD,
        action: () => {
          this.runCleanup(list);
        }
      }
    });
  }

  private openQuarantine(): void {
    const dlg = new CustomDialogController({
      builder: QuarantineDialog({
        onClose: () => {
          if (this.quarantineDlg != undefined) {
            this.quarantineDlg.close();
          }
        },
        onChanged: () => {
          this.refresh();
          this.rebuildCues();
        }
      }),
      autoCancel: true,
      alignment: DialogAlignment.Center,
      customStyle: true,
      maskColor: '#99000000'
    });
    this.quarantineDlg = dlg;
    dlg.open();
  }

  private async stripSelectedTitles(): Promise<void> {
    const picked = this.pickedTracks();
    const list = picked.length > 0 ? picked : this.allTracks.slice();
    let changed = 0;
    let failed = 0;
    this.statusText = '去除标题序号';
    for (const t of list) {
      if (t.isCueChild == 1) {
        continue;
      }
      const title = TitleCleaner.stripNumber(t.title);
      if (title == t.title) {
        continue;
      }
      const patch = new TagPatch();
      patch.title = title;
      patch.artist = t.artist;
      patch.album = t.album;
      patch.albumartist = t.albumartist;
      patch.genre = t.genre;
      patch.year = t.year;
      patch.trackNo = t.trackNo;
      patch.discNo = t.discNo;
      const wr: WriteResult = await TagWriter.write(t.path, patch);
      if (!wr.ok) {
        failed++;
        AppLog.warn(`去序号写回失败：${t.fileName} · ${wr.message}`);
        continue;
      }
      t.title = title;
      refreshSearchBlob(t);
      await Db.updateTrack(t);
      changed++;
    }
    this.statusText = '就绪';
    await this.refresh();
    AppLog.info(`去除标题序号：更新 ${changed} 首${failed > 0 ? ` · 失败 ${failed}` : ''}`);
    promptAction.showToast({
      message: `已去除 ${changed} 个标题的序号前缀${failed > 0 ? ` · 失败 ${failed}` : ''}`
    });
  }

  private async undoTagEdits(target: Track): Promise<void> {
    let list = this.pickedTracks();
    if (target.id != 0 && !list.some((x: Track) => x.id == target.id)) {
      list = [target];
    }
    list = list.filter((x: Track) => x.isCueChild != 1);
    if (list.length == 0) {
      promptAction.showToast({ message: '先选择要撤销的曲目' });
      return;
    }
    let ok = 0;
    let failed = 0;
    const dirs: string[] = [];
    this.statusText = '撤销标签编辑';
    for (const t of list) {
      if (!TagWriter.hasBackup(t.path)) {
        continue;
      }
      if (TagWriter.restoreBackup(t.path)) {
        ok++;
        const slash = t.path.lastIndexOf('/');
        if (slash > 0) {
          const dir = t.path.substring(0, slash);
          if (dirs.indexOf(dir) < 0) {
            dirs.push(dir);
          }
        }
      } else {
        failed++;
        AppLog.warn(`撤销标签失败：${t.fileName}`);
      }
    }
    for (const dir of dirs) {
      await Scanner.scan([dir], new ScanController(), () => {
      });
    }
    this.statusText = '就绪';
    if (ok > 0) {
      await this.rebuildCues();
    }
    await this.refresh();
    AppLog.info(`撤销标签编辑：恢复 ${ok} 首${failed > 0 ? ` · 失败 ${failed}` : ''}`);
    if (ok == 0) {
      promptAction.showToast({ message: failed > 0 ? `恢复失败 ${failed} 首` : '没有可恢复的 .bak 备份' });
    } else {
      promptAction.showToast({ message: `已从 .bak 恢复 ${ok} 首${failed > 0 ? ` · 失败 ${failed}` : ''}` });
    }
  }

  private async exportReport(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    const res: ReportResult = await ReportService.export(ctx, this.allTracks);
    if (res.canceled) {
      return;
    }
    if (res.ok) {
      AppLog.info(`报表已导出：${res.path}`);
      promptAction.showToast({ message: `报表已导出：${res.path}` });
    } else {
      promptAction.showToast({ message: `导出失败：${res.message}` });
    }
  }

  private async exportCovers(): Promise<void> {
    const list = this.allTracks.slice();
    if (list.length == 0) {
      promptAction.showToast({ message: '曲库为空' });
      return;
    }
    const ctx = getContext(this) as common.UIAbilityContext;
    this.statusText = '导出封面';
    const rep = await CoverService.exportCovers(ctx, list);
    this.statusText = '就绪';
    AppLog.info(`导出封面：${rep.exported}/${rep.total} · 跳过 ${rep.skipped} · ${rep.dir}`);
    promptAction.showToast({
      message: `已导出 ${rep.exported} 张封面${rep.skipped > 0 ? ` · 跳过 ${rep.skipped}` : ''}`
    });
  }

  private async exportFolderCovers(): Promise<void> {
    if (this.allTracks.length == 0) {
      promptAction.showToast({ message: '曲库为空' });
      return;
    }
    const ctx = getContext(this) as common.UIAbilityContext;
    this.statusText = '生成文件夹封面';
    const rep = await CoverService.exportFolderCovers(ctx, this.allTracks);
    this.statusText = '就绪';
    AppLog.info(`文件夹封面：${rep.folders} 个目录 · 跳过 ${rep.skipped}`);
    promptAction.showToast({ message: `已为 ${rep.folders} 个文件夹生成封面` });
  }

  // ------------------------------------------------------------ CUE 分轨

  private cueChildrenFor(t: Track): Track[] {
    if (t.isCueChild == 1) {
      return this.allTracks.filter((x: Track) => x.isCueChild == 1 && x.cuePath == t.cuePath);
    }
    return this.allTracks.filter((x: Track) => x.isCueChild == 1 && x.cuePath == t.path);
  }

  private openCueSplitFor(t: Track): void {
    let children: Track[] = [];
    if (t.id != 0) {
      children = this.cueChildrenFor(t);
    }
    if (children.length == 0) {
      children = this.allTracks.filter((x: Track) => x.isCueChild == 1);
    }
    if (children.length == 0) {
      promptAction.showToast({ message: '没有可导出的 CUE 子轨' });
      return;
    }
    const dlg = new CustomDialogController({
      builder: CueSplitDialog({
        tracks: children,
        trimPregap: this.appSettings.cueSplitTrimPregap,
        onClose: () => {
          if (this.cueSplitDlg != undefined) {
            this.cueSplitDlg.close();
          }
        },
        onDone: () => {
          this.refresh();
        }
      }),
      autoCancel: true,
      alignment: DialogAlignment.Center,
      customStyle: true,
      maskColor: '#99000000'
    });
    this.cueSplitDlg = dlg;
    dlg.open();
  }

  private selectTrackById(id: number): void {
    const t = this.allTracks.find((x: Track) => x.id == id);
    if (t != undefined) {
      this.selectTrack(t);
    }
  }

  // ------------------------------------------------------------ 背景

  private drawBackdrop(): void {
    const w = this.canvasCtx.width;
    const h = this.canvasCtx.height;
    const ctx = this.canvasCtx;
    const top = T.mix(T.BASE, T.ACCENT, T.LIGHT ? 0.06 : 0.10);
    const bottom = T.LIGHT ? T.mix(T.BASE, '#FFFFFF', 0.18) : T.mix(T.BASE, '#000000', 0.22);
    const gradient = ctx.createLinearGradient(0, 0, 0, h);
    gradient.addColorStop(0, top);
    gradient.addColorStop(0.55, T.BASE);
    gradient.addColorStop(1, bottom);
    ctx.fillStyle = gradient;
    ctx.fillRect(0, 0, w, h);

    if (T.PATTERN == 'grid') {
      ctx.strokeStyle = T.rgba(T.LINE, 0.35);
      ctx.lineWidth = 1;
      for (let x = 0; x <= w; x += 64) {
        ctx.beginPath();
        ctx.moveTo(x, 0);
        ctx.lineTo(x, h);
        ctx.stroke();
      }
      for (let y = 0; y <= h; y += 64) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }
    } else if (T.PATTERN == 'paper') {
      ctx.strokeStyle = T.rgba(T.LINE, 0.22);
      ctx.lineWidth = 1;
      for (let y = 0; y <= h; y += 26) {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y);
        ctx.stroke();
      }
      for (let i = 0; i < 40; i++) {
        ctx.fillStyle = T.rgba(T.LINE, Math.random() * 0.10 + 0.03);
        ctx.beginPath();
        ctx.arc(Math.random() * w, Math.random() * h, Math.random() * 2 + 0.5, 0, Math.PI * 2);
        ctx.fill();
      }
    } else if (T.PATTERN == 'waves') {
      ctx.lineWidth = 1.2;
      for (let k = 0; k < 3; k++) {
        const baseY = h * (0.70 + k * 0.08);
        ctx.beginPath();
        ctx.moveTo(0, baseY);
        for (let x = 0; x <= w; x += 18) {
          const y = baseY + Math.sin((x / w) * Math.PI * 2 + k * 0.9) * h * 0.018;
          ctx.lineTo(x, y);
        }
        ctx.strokeStyle = T.rgba(T.ACCENT, 0.12 - k * 0.03);
        ctx.stroke();
      }
    } else if (T.PATTERN == 'vinyl') {
      const cx = w * 0.86;
      const cy = h * 0.48;
      ctx.lineWidth = 1;
      for (let i = 1; i <= 12; i++) {
        ctx.beginPath();
        ctx.strokeStyle = T.rgba(T.FG, 0.05);
        ctx.arc(cx, cy, h * 0.04 + i * h * 0.026, 0, Math.PI * 2);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.fillStyle = T.rgba(T.ACCENT, 0.10);
      ctx.arc(cx, cy, h * 0.026, 0, Math.PI * 2);
      ctx.fill();
    } else if (T.PATTERN == 'sun') {
      const cx = w * 0.84;
      const cy = h * 0.20;
      ctx.strokeStyle = T.rgba(T.ACCENT, 0.10);
      ctx.lineWidth = 1.2;
      for (let i = 0; i < 24; i++) {
        const a = (i / 24) * Math.PI * 2;
        ctx.beginPath();
        ctx.moveTo(cx + Math.cos(a) * h * 0.10, cy + Math.sin(a) * h * 0.10);
        ctx.lineTo(cx + Math.cos(a) * h * 0.46, cy + Math.sin(a) * h * 0.46);
        ctx.stroke();
      }
      ctx.beginPath();
      ctx.fillStyle = T.rgba(T.ACCENT, 0.08);
      ctx.arc(cx, cy, h * 0.12, 0, Math.PI * 2);
      ctx.fill();
    } else if (T.PATTERN == 'blossom' || T.PATTERN == 'leaves') {
      for (let i = 0; i < 46; i++) {
        const color = i % 3 == 0 ? T.PURPLE : T.ACCENT;
        ctx.fillStyle = T.rgba(color, Math.random() * 0.10 + 0.03);
        ctx.beginPath();
        ctx.arc(Math.random() * w, Math.random() * h, Math.random() * 5 + 2, 0, Math.PI * 2);
        ctx.fill();
      }
    } else if (T.PATTERN == 'dusk') {
      for (let i = 0; i < 4; i++) {
        const cx = w * (0.15 + i * 0.24);
        const cy = h * (0.25 + (i % 2) * 0.42);
        ctx.beginPath();
        ctx.fillStyle = T.rgba(i % 2 == 0 ? T.PURPLE : T.ACCENT, 0.05);
        ctx.arc(cx, cy, h * 0.30, 0, Math.PI * 2);
        ctx.fill();
      }
    } else if (T.PATTERN == 'taiji') {
      const cx = w * 0.80;
      const cy = h * 0.46;
      ctx.lineWidth = 1.2;
      ctx.strokeStyle = T.rgba(T.FG, 0.08);
      ctx.beginPath();
      ctx.arc(cx, cy, h * 0.30, 0, Math.PI * 2);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(cx, cy - h * 0.15, h * 0.15, Math.PI * 0.5, Math.PI * 1.5);
      ctx.stroke();
      ctx.beginPath();
      ctx.arc(cx, cy + h * 0.15, h * 0.15, Math.PI * 1.5, Math.PI * 0.5);
      ctx.stroke();
    } else if (T.PATTERN == 'snow') {
      for (let i = 0; i < 150; i++) {
        ctx.fillStyle = T.rgba(i % 2 == 0 ? T.FG : T.CYAN, Math.random() * 0.28 + 0.06);
        ctx.beginPath();
        ctx.arc(Math.random() * w, Math.random() * h, Math.random() * 1.4 + 0.3, 0, Math.PI * 2);
        ctx.fill();
      }
    } else {
      // 深空 / 默认：星域 + 极淡斜线
      for (let i = 0; i < 110; i++) {
        const x = Math.random() * w;
        const y = Math.random() * h;
        const r = Math.random() * 1.3 + 0.3;
        const alpha = Math.random() * 0.35 + 0.08;
        ctx.beginPath();
        ctx.fillStyle = T.LIGHT ? T.rgba(T.mix(T.FG, T.BASE, 0.30), alpha) : `rgba(160, 196, 235, ${alpha})`;
        ctx.arc(x, y, r, 0, Math.PI * 2);
        ctx.fill();
      }
      ctx.strokeStyle = T.rgba(T.ACCENT, 0.05);
      ctx.lineWidth = 1;
      for (let i = 0; i < 6; i++) {
        const y = h * (0.15 + i * 0.13);
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(w, y - h * 0.18);
        ctx.stroke();
      }
    }
  }

  // ------------------------------------------------------------ 视图

  @Builder
  rightPanel() {
    Column() {
      Row({ space: 14 }) {
        Text('详情')
          .fontSize(11)
          .fontColor(this.rightTab == 0 ? T.ACCENT : T.FG_DIM)
          .padding({ bottom: 5 })
          .border({
            width: { bottom: 2 },
            color: { bottom: this.rightTab == 0 ? T.ACCENT : Color.Transparent }
          })
          .onClick(() => {
            this.rightTab = 0;
          })
        Text('参数')
          .fontSize(11)
          .fontColor(this.rightTab == 1 ? T.ACCENT : T.FG_DIM)
          .padding({ bottom: 5 })
          .border({
            width: { bottom: 2 },
            color: { bottom: this.rightTab == 1 ? T.ACCENT : Color.Transparent }
          })
          .onClick(() => {
            this.rightTab = 1;
          })
        Blank()
        Text('收起')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
          .onClick(() => {
            this.rightVisible = false;
          })
      }
      .width('100%')
      .height(32)
      .padding({ left: 10, right: 10 })
      .border({ width: { bottom: 1 }, color: { bottom: T.LINE_SOFT } })

      if (this.rightTab == 0) {
        Column() {
          Inspector({
            track: this.selectedTrack,
            coverPath: this.selectedCover,
            onEdit: () => {
              if (this.selectedTrack.id != 0) {
                this.openEditorFor(this.selectedTrack);
              }
            },
            onCover: () => {
              if (this.selectedTrack.id != 0) {
                this.openCoverFor(this.selectedTrack);
              }
            },
            onLyrics: () => {
              if (this.selectedTrack.id != 0) {
                this.openLyricsFor(this.selectedTrack);
              }
            },
            onRemove: () => {
              if (this.selectedTrack.id != 0) {
                this.removeTrack(this.selectedTrack);
              }
            },
            onAuth: () => {
              this.authOne(this.selectedTrack);
            },
            onSpectrum: () => {
              this.openSpectrumFor(this.selectedTrack);
            },
            onMatch: () => {
              this.openMatchFor(this.selectedTrack);
            }
          })
        }
        .width('100%')
        .layoutWeight(1)
      } else {
        Column() {
          SettingsPanel({
            settings: this.appSettings,
            onSave: (d: AppSettingsData) => {
              this.saveSettings(d);
            },
            onPreviewOpacity: (v: number) => {
              this.uiOpacity = v;
            },
            onManageRoots: () => {
              this.openRootsDialog();
            },
            onOpenEq: () => {
              router.pushUrl({ url: 'pages/EqPage' });
            }
          })
        }
        .width('100%')
        .layoutWeight(1)
      }
    }
    .width(430)
    .height('100%')
    .backgroundColor(T.PANEL_CC)
    .borderRadius(T.R_CARD)
    .border({ width: 1, color: T.LINE_SOFT })
  }

  @Builder
  scanBanner() {
    Row({ space: 8 }) {
      LoadingProgress()
        .width(16)
        .height(16)
        .color(T.ACCENT)
      Text(this.scanText)
        .fontSize(10)
        .fontColor(T.FG_DIM)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .layoutWeight(1)
      if (this.scanTotal > 0) {
        Progress({
          value: this.scanDone,
          total: this.scanTotal,
          type: ProgressType.Linear
        })
          .width(120)
          .height(3)
          .color(T.ACCENT)
      }
      Button(this.scanPaused ? '继续' : '暂停')
        .height(22)
        .fontSize(9)
        .fontColor(T.ACCENT)
        .backgroundColor(T.GHOST_BG)
        .borderRadius(6)
        .onClick(() => {
          this.toggleScanPause();
        })
      Button('取消')
        .height(22)
        .fontSize(9)
        .fontColor(T.FG_DIM)
        .backgroundColor(T.GHOST_BG)
        .borderRadius(6)
        .onClick(() => {
          this.cancelScan();
        })
    }
    .width('100%')
    .height(28)
    .padding({ left: 10, right: 10 })
    .backgroundColor(T.PANEL_B3)
    .borderRadius(8)
    .border({ width: 1, color: T.LINE_SOFT })
  }

  build() {
    Stack({ alignContent: Alignment.TopStart }) {
      Canvas(this.canvasCtx)
        .width('100%')
        .height('100%')
        .onReady(() => {
          this.drawBackdrop();
        })

      Column() {
        BrandBar({
          version: APP_VERSION,
          statusText: this.statusText,
          uiOpacity: this.uiOpacity,
          onOpacityChange: (v: number) => {
            this.uiOpacity = v;
          }
        })

        Column({ space: 10 }) {
          ActionBar({
            busy: this.scanning,
            autoBusy: this.autoBusy,
            authBusy: this.authBusy,
            hasPending: this.pendingMatches.length > 0,
            onAddFiles: () => {
              this.importFiles();
            },
            onAddFolder: () => {
              this.openRootsDialog();
            },
            onRemove: () => {
              this.removeSelection();
            },
            onClear: () => {
              this.clearLibrary();
            },
            onPlaylists: () => {
              this.openPlaylistsDialog();
            },
            onScan: () => {
              this.startScan();
            },
            onStop: () => {
              this.cancelScan();
            },
            onAuto: () => {
              this.startAuto();
            },
            onSaveAll: () => {
              this.saveAllPending();
            },
            onAuth: () => {
              this.startAuth();
            },
            onCleanup: () => {
              this.cleanupFake();
            },
            onDeleteSelected: () => {
              this.removeSelection();
            },
            onCueSplit: () => {
              this.openCueSplitFor(this.selectedTrack);
            },
            onHealth: () => {
              router.pushUrl({ url: 'pages/HealthPage' });
            },
            onTools: () => {
              router.pushUrl({ url: 'pages/LibraryToolsPage' });
            },
            onCover: () => {
              const t = this.currentTarget();
              if (t.id != 0) {
                this.openCoverFor(t);
              }
            },
            onLyrics: () => {
              const t = this.currentTarget();
              if (t.id != 0) {
                this.openLyricsFor(t);
              }
            },
            onEq: () => {
              router.pushUrl({ url: 'pages/EqPage' });
            },
            onTogglePanel: () => {
              this.rightVisible = !this.rightVisible;
            },
            onMore: (key: string) => {
              this.onMore(key);
            }
          })

          Row({ space: 8 }) {
            StatCard({
              label: '曲目总数', value: this.stats.count, color: T.FG,
              onTap: () => {
                this.verdictFilter = 'all';
                this.verdictIndex = 0;
                this.applyFilter();
              }
            })
            StatCard({
              label: '无损音源', value: this.counts[1], color: T.CYAN,
              onTap: () => {
                this.losslessOnly = true;
                this.applyFilter();
              }
            })
            StatCard({
              label: '假无损', value: this.fakeCountV, color: T.BAD,
              onTap: () => {
                this.verdictFilter = 'fake';
                this.applyFilter();
              }
            })
            StatCard({
              label: '已完成', value: this.doneCount, color: T.OK,
              onTap: () => {
                this.problemsOnly = false;
                this.applyFilter();
              }
            })
            StatCard({
              label: '待确认', value: this.pendingMatches.length + this.warnCount, color: T.WARN,
              onTap: () => {
                this.verdictFilter = 'unknown';
                this.applyFilter();
              }
            })
            StatCard({
              label: '失败', value: this.badCount, color: T.BAD,
              onTap: () => {
                this.problemsOnly = true;
                this.applyFilter();
              }
            })
          }
          .width('100%')

          Row({ space: 10 }) {
            Column({ space: 8 }) {
              FilterBar({
                keyword: this.keyword,
                losslessOnly: this.losslessOnly,
                problemsOnly: this.problemsOnly,
                specIndex: this.specIndex,
                countAll: this.counts[0],
                countLossless: this.counts[1],
                countLossy: this.counts[2],
                countHiRes: this.counts[3],
                countCue: this.counts[4],
                verdictIndex: this.verdictIndex,
                onKeyword: (v: string) => {
                  this.keyword = v;
                  this.applyFilter();
                },
                onToggle: (key: string) => {
                  if (key == 'lossless') {
                    this.losslessOnly = !this.losslessOnly;
                  } else if (key == 'problems') {
                    this.problemsOnly = !this.problemsOnly;
                  }
                  this.applyFilter();
                },
                onSpec: (index: number) => {
                  this.specIndex = index;
                  this.applyFilter();
                },
                onVerdict: (index: number) => {
                  this.verdictIndex = index;
                  this.applyFilter();
                }
              })
              VerdictBar({
                countAll: this.allTracks.length,
                countTrue: this.trueCount,
                countFake: this.fakeCountV,
                countSuspect: this.suspectCount,
                countUnknown: this.unknownCount,
                filter: this.verdictFilter,
                onFilter: (key: string) => {
                  this.verdictFilter = key;
                  this.applyFilter();
                }
              })
              if (this.scanning) {
                this.scanBanner()
              }
              TrackTable({
                tracks: this.filtered,
                selectedId: this.selectedId,
                selectedIds: this.selectedIds,
                playingPath: this.playingPath,
                compact: this.windowWidth < 1280,
                onSelect: (t: Track, i: number, ctrl: boolean, shift: boolean) => {
                  this.handleRowSelect(t, i, ctrl, shift);
                },
                onPlay: (t: Track, i: number) => {
                  this.playAt(t, i);
                },
                onPlayNext: (t: Track) => {
                  this.playNextTracks(t);
                },
                onQueue: (t: Track) => {
                  this.enqueueTracks(t);
                },
                onCopyPath: (t: Track) => {
                  this.copyTrackPath(t);
                },
                onEdit: (t: Track) => {
                  this.openEditorFor(t);
                },
                onBatchEdit: (t: Track) => {
                  this.batchEditFor(t);
                },
                onUndoEdit: (t: Track) => {
                  this.undoTagEdits(t);
                },
                onQuarantine: (t: Track) => {
                  this.quarantineOne(t);
                },
                onCover: (t: Track) => {
                  this.openCoverFor(t);
                },
                onLyrics: (t: Track) => {
                  this.openLyricsFor(t);
                },
                onRemove: (t: Track) => {
                  this.removeTrack(t);
                },
                onMatch: (t: Track) => {
                  this.openMatchFor(t);
                },
                onAuth: (t: Track) => {
                  this.authOne(t);
                },
                onSpectrum: (t: Track) => {
                  this.openSpectrumFor(t);
                },
                onCueSplit: (t: Track) => {
                  this.openCueSplitFor(t);
                }
              })
                .layoutWeight(1)
            }
            .layoutWeight(1)
            .height('100%')
            .alignItems(HorizontalAlign.Start)

            if (this.rightVisible) {
              this.rightPanel()
            }
          }
          .width('100%')
          .layoutWeight(1)
          .alignItems(VerticalAlign.Top)

          PlayerBar({
            track: this.playingTrack,
            coverPath: this.playingCover,
            playing: this.npPlaying,
            playPosition: this.npPos,
            duration: this.npDur,
            volume: this.appSettings.volume,
            modeGlyph: MODE_GLYPHS[this.engine.mode as number],
            eqOn: this.eqOn,
            signalOn: this.signalVisible,
            miniOn: this.miniOn,
            onToggle: () => {
              this.togglePlay();
            },
            onPrev: () => {
              this.engine.prevByUser();
            },
            onNext: () => {
              this.engine.nextByUser();
            },
            onStop: () => {
              this.engine.stop();
              this.statusText = '已停止';
            },
            onSeek: (ms: number) => {
              this.engine.seek(ms);
            },
            onVolume: (v: number) => {
              this.appSettings.volume = v;
              this.engine.setVolume(v / 100);
            },
            onMode: () => {
              this.cycleMode();
            },
            onSignal: () => {
              this.signalVisible = !this.signalVisible;
              this.updateSignal();
            },
            onEq: () => {
              router.pushUrl({ url: 'pages/EqPage' });
            },
            onMini: () => {
              this.miniOn = !this.miniOn;
            }
          })

          BottomDock({
            logs: this.logs,
            lyrics: this.lyrics,
            lyricIndex: this.lyricIndex,
            lyricSource: this.lyricSource,
            signalVisible: this.signalVisible,
            stages: this.stages,
            collapsed: this.bottomCollapsed,
            onToggleCollapse: () => {
              this.bottomCollapsed = !this.bottomCollapsed;
            },
            onClearLog: () => {
              AppLog.clear();
            },
            onReloadLyrics: () => {
              this.reloadLyrics();
            }
          })
        }
        .width('100%')
        .layoutWeight(1)
        .padding({ left: 14, right: 14, top: 10, bottom: 8 })
        .alignItems(HorizontalAlign.Start)

        StatusBar({
          hint: this.scanning ? this.scanText :
            this.selectedTrack.id == 0 ? '拖入音频文件，或点击「添加文件」' :
              this.selectedIds.length > 1 ? `已选 ${this.selectedIds.length} 首 · ${this.selectedTrack.path}` :
                `${this.selectedTrack.path}`,
          right: `共 ${this.stats.count} 首 · ${fmtDuration(this.stats.totalMs)} · ${fmtSize(this.stats.totalSize)}`,
          kernel: this.kernelLabel(),
          badge: this.eqOn ? 'EQ' : ''
        })
      }
      .width('100%')
      .height('100%')
      .opacity(this.uiOpacity / 100)

      if (this.miniOn) {
        Column() {
          MiniOverlay({
            track: this.playingTrack,
            coverPath: this.playingCover,
            playing: this.npPlaying,
            playPosition: this.npPos,
            duration: this.npDur,
            uiOpacity: this.uiOpacity,
            lyrics: this.lyrics,
            lyricIndex: this.lyricIndex,
            onToggle: () => {
              this.togglePlay();
            },
            onPrev: () => {
              this.engine.prevByUser();
            },
            onNext: () => {
              this.engine.nextByUser();
            },
            onSeek: (ms: number) => {
              this.engine.seek(ms);
            },
            onOpacity: (v: number) => {
              this.uiOpacity = v;
            },
            onExpand: () => {
              this.miniOn = false;
            },
            onClose: () => {
              this.miniOn = false;
            }
          })
        }
        .width('100%')
        .height('100%')
        .padding(18)
        .justifyContent(FlexAlign.End)
        .alignItems(HorizontalAlign.End)
        .hitTestBehavior(HitTestMode.Transparent)
      }

      if (this.autoBusy || this.authBusy) {
        Column() {
          Column({ space: 10 }) {
            Row() {
              Text(this.autoBusy ? '一键全自动' : '真假无损鉴定')
                .fontSize(15)
                .fontWeight(FontWeight.Bold)
                .fontColor(T.FG)
              Blank()
              Text(this.autoBusy ? `${this.autoDone}/${this.autoTotal}` :
                `${this.authDone}/${this.authTotal}`)
                .fontSize(10)
                .fontColor(T.FG_MUTE)
            }
            .width('100%')
            Text(this.autoBusy ? this.autoText : this.authText)
              .fontSize(11)
              .fontColor(T.ACCENT)
              .maxLines(2)
              .textOverflow({ overflow: TextOverflow.Ellipsis })
              .width('100%')
            Progress({
              value: this.autoBusy ? this.autoDone : this.authDone,
              total: this.autoBusy ? (this.autoTotal > 0 ? this.autoTotal : 1) :
                (this.authTotal > 0 ? this.authTotal : 1),
              type: ProgressType.Linear
            })
              .width('100%')
              .height(5)
              .color(T.ACCENT)
            Row() {
              Text(this.autoBusy ? '低置信度会进入「待确认」，可点「全部确认保存」' :
                '解码 4 段抽样频谱，检测转码/升频/升位深')
                .fontSize(9)
                .fontColor(T.FG_MUTE)
              Blank()
              Button('停止')
                .height(26)
                .fontSize(10)
                .fontColor(T.BAD)
                .backgroundColor(T.DANGER_BG)
                .borderRadius(6)
                .onClick(() => {
                  if (this.autoBusy) {
                    this.cancelAuto();
                  } else {
                    this.cancelAuth();
                  }
                })
            }
            .width('100%')
          }
          .width(520)
          .padding(16)
          .backgroundColor(T.POPUP_BG)
          .borderRadius(14)
          .border({ width: 1, color: T.LINE })
          .shadow({ radius: 24, color: '#88000000', offsetY: 8 })
        }
        .width('100%')
        .height('100%')
        .justifyContent(FlexAlign.Center)
        .alignItems(HorizontalAlign.Center)
        .backgroundColor('#99000000')
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor(T.BASE)
    .onAreaChange((oldArea: Area, newArea: Area) => {
      const width = Number(newArea.width);
      if (!isNaN(width) && width > 0) {
        this.windowWidth = width;
        if (width < 1180 && !this.narrowHandled) {
          this.rightVisible = false;
          this.narrowHandled = true;
        } else if (width >= 1180 && this.narrowHandled) {
          this.rightVisible = true;
          this.narrowHandled = false;
        }
      }
    })
  }
}
