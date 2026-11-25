import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import { BusinessError } from '@ohos.base';
import fs from '@ohos.file.fs';
import picker from '@ohos.file.picker';
import { Track } from '../model/Track';
import { Db, Stats } from '../db/Db';
import { Scanner, ScanController, ScanRoots } from '../scanner/Scanner';
import { ImportManager, ImportReport } from '../import/ImportManager';
import { CueIndexer } from '../cue/CueIndexer';
import { PlayerEngine, PlayMode } from '../player/PlayerEngine';
import { NativePlayer } from '../player/NativePlayer';
import { EqPresets } from '../eq/EqPresets';
import { MiniPlayer } from '../components/MiniPlayer';
import { SessionStore } from '../session/SessionStore';
import { fmtDuration, fmtSize, fmtSpec } from '../utils/Util';

const ACCENT = '#E8B04B';

@Entry
@Component
struct Index {
  @State all: Track[] = [];
  @State keyword: string = '';
  @State stats: Stats = new Stats();
  @State scanning: boolean = false;
  @State scanText: string = '';
  @State toast: string = '';
  @State roots: string[] = [];
  @State newRoot: string = '';
  @State showRoots: boolean = false;
  @State scanPaused: boolean = false;
  @State failedUris: string[] = [];
  @StorageLink('npIdx') npIdx: number = -1;
  private engine: PlayerEngine = PlayerEngine.get();
  private scanCtrl: ScanController = new ScanController();

  aboutToAppear(): void {
    this.initAsync();
  }

  onPageShow(): void {
    this.refresh();
  }

  private async initAsync(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    await Db.init(ctx);
    // 启动即把 EQ 设置下发给原生单例：首曲开播时按同一组参数重建 DSP 链
    const eq = await EqPresets.load();
    NativePlayer.setEq(eq.enabled, eq.gains);
    // 启动自检：把编译进包内的 DSP 数值结果打到日志（1kHz +12dB / 限幅 / 直通）
    const dspReport = NativePlayer.selfTest();
    if (dspReport != '') {
      console.info(`dsp selftest ${dspReport}`);
    }
    this.roots = await ScanRoots.load(ctx);
    const session = await SessionStore.load();
    this.engine.setMode(session.playMode as PlayMode);
    this.engine.setVolume(session.volume);
    this.engine.onSessionDirty = (force: boolean): void => {
      SessionStore.markDirty(this.engine, force);
    };
    this.engine.addListener((): void => {
      SessionStore.markDirty(this.engine, false);
    });
    await this.seedSamplesIfNeeded(ctx);
    const st = await Db.stats();
    if (st.count == 0) {
      await this.startScan();
    } else {
      await this.refresh();
    }
    await this.rebuildCues();
    await this.restoreSession(session.queuePaths, session.lastTrackPath, session.lastPositionMs);
    SessionStore.startAutoSave(this.engine);
  }

  aboutToDisappear(): void {
    SessionStore.saveNow(this.engine);
    SessionStore.stopAutoSave();
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
    // 只有「恢复到的曲目就是上次那首」才带进度，且进度不能超过该曲时长，
    // 否则换曲/换队列后会直接显示 0:05 / 0:03 这种越界进度并秒跳下一首
    let pos = 0;
    if (lastPath != '' && tracks[idx].path == lastPath) {
      const t = tracks[idx];
      const dur = t.isCueChild == 1 ? Math.max(0, t.cueEndMs - t.cueStartMs) : t.durationMs;
      pos = dur > 1000 ? Math.min(positionMs, dur - 800) : 0;
      pos = Math.max(0, pos);
    }
    this.engine.restoreQueue(tracks, idx, pos);
    this.toastMsg(`已恢复上次会话：${tracks[idx].title}`);
  }

  // 首次启动：把内置示例曲目复制进沙箱 Music，保证开箱即有内容可扫可播
  private async seedSamplesIfNeeded(ctx: common.UIAbilityContext): Promise<void> {
    const musicDir = `${ctx.filesDir}/Music`;
    // 只在首次启动播种：否则用户把示例曲改名/删掉后，每次启动都会被补回一份，变成重复曲目
    const seeded = await Db.getSetting('seed_samples_done', '');
    if (seeded == '1') {
      return;
    }
    console.info(`seedSamples: start dir=${musicDir}`);
    try {
      try {
        fs.mkdirSync(musicDir, true);
      } catch (e) {
        // 目录已存在：继续检查缺失的示例文件
      }
      const names: string[] = ['01_yehangxing.flac', '02_chenguang.mp3', '03_sine_48k.wav',
        '04_sine24_96k.wav', 'sample.cue'];
      let copied = 0;
      for (const n of names) {
        try {
          fs.statSync(`${musicDir}/${n}`);
          continue;
        } catch (e) {
          // 缺失的示例文件补拷
        }
        let bytes = new Uint8Array(0);
        try {
          try {
            bytes = ctx.resourceManager.getRawFileContentSync(`rawfile/${n}`);
          } catch (e) {
            bytes = ctx.resourceManager.getRawFileContentSync(n);
          }
        } catch (e) {
          console.error(`seedSamples: 读取 ${n} 失败：${(e as BusinessError).message}`);
          continue;
        }
        const ab = new ArrayBuffer(bytes.byteLength);
        new Uint8Array(ab).set(bytes);
        const f = fs.openSync(`${musicDir}/${n}`, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
        try {
          fs.writeSync(f.fd, ab);
        } finally {
          fs.closeSync(f);
        }
        copied++;
      }
      if (copied > 0) {
        this.toastMsg(`已载入 ${copied} 个内置示例文件`);
      }
      await Db.setSetting('seed_samples_done', '1');
      console.info(`seedSamples: copied=${copied} dir=${musicDir} files=${fs.listFileSync(musicDir).join(',')}`);
    } catch (e) {
      console.error(`seedSamples: 失败 ${(e as BusinessError).message}`);
      // 示例载入失败不影响正常使用
    }
  }

  private async refresh(): Promise<void> {
    try {
      this.all = await Db.listTracks('');
      this.stats = await Db.stats();
    } catch (e) {
      // 数据库未就绪时忽略
    }
  }

  private async rebuildCues(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      const roots = await ScanRoots.load(ctx);
      const res = await CueIndexer.rebuild(roots);
      console.info(`CueIndexer: roots=${roots.join(',')} files=${res.cueFiles} children=${res.children} unmatched=${res.unmatched.join(',')}`);
      if (res.children > 0) {
        await this.refresh();
        this.toastMsg(`CUE 已索引：${res.cueFiles} 个文件 · ${res.children} 个逻辑轨`);
      }
    } catch (e) {
      // CUE 索引失败不影响普通曲目播放
    }
  }

  private filtered(): Track[] {
    const kw = this.keyword.trim().toLowerCase();
    if (kw == '') {
      return this.all;
    }
    return this.all.filter((t: Track) =>
      t.title.toLowerCase().includes(kw) ||
      t.artist.toLowerCase().includes(kw) ||
      t.album.toLowerCase().includes(kw) ||
      t.fileName.toLowerCase().includes(kw));
  }

  private toastMsg(msg: string): void {
    this.toast = msg;
    setTimeout(() => {
      if (this.toast == msg) {
        this.toast = '';
      }
    }, 6000);
  }

  private isPlayingTrack(t: Track): boolean {
    if (this.npIdx < 0) {
      return false;
    }
    const cur = this.engine.current();
    return cur != undefined && cur.path == t.path;
  }

  private subLine(t: Track): string {
    if (t.artist == '' && t.album == '') {
      return t.fileName;
    }
    if (t.album == '') {
      return t.artist;
    }
    if (t.artist == '') {
      return t.album;
    }
    return `${t.artist} - ${t.album}`;
  }

  private playAt(i: number): void {
    const list = this.filtered();
    if (list.length == 0) {
      return;
    }
    this.engine.setQueue(list, i);
    router.pushUrl({ url: 'pages/PlayerPage' });
  }

  private openEditor(t: Track): void {
    const physical = t.isCueChild == 1 ? t.cuePath : t.path;
    AppStorage.setOrCreate('seqaudio_edit_paths', JSON.stringify([physical]));
    router.pushUrl({ url: 'pages/TrackEditPage' });
  }

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
    try {
      const res = await Scanner.scan(this.roots, this.scanCtrl, (done: number, total: number, cur: string) => {
        this.scanText = total > 0 ? `扫描中 ${done}/${total}：${cur}` : '正在收集文件…';
      });
      await this.rebuildCues();
      await this.refresh();
      const issueText = res.failed > 0 ? ` · 失败明细 ${res.issues.slice(0, 3).join(' / ')}` : '';
      this.toastMsg(`扫描完成：新增 ${res.added} · 更新 ${res.updated} · 跳过 ${res.skipped} · 失败 ${res.failed}（${res.costMs}ms）${issueText}`);
    } catch (e) {
      this.toastMsg('扫描失败，请查看日志');
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
    } else {
      this.scanCtrl.pause();
      this.scanPaused = true;
    }
  }

  private cancelScan(): void {
    if (!this.scanning) {
      return;
    }
    this.scanCtrl.cancel();
    this.scanPaused = false;
  }

  private async addRoot(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    this.roots = await ScanRoots.add(ctx, this.newRoot);
    this.newRoot = '';
  }

  private async removeRoot(root: string): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    this.roots = await ScanRoots.remove(ctx, root, false);
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
      // 用户取消选择：静默返回
    }
  }

  private async importUris(uris: string[]): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    const rep: ImportReport = await ImportManager.importFiles(uris, `${ctx.filesDir}/Music`, false);
    this.failedUris = rep.failed;
    this.toastMsg(`导入 ${rep.ok}/${rep.total} · 跳过重复 ${rep.skipped} · 失败 ${rep.failed.length}`);
    if (rep.ok > 0) {
      await this.startScan();
    }
  }

  private clearLib(): void {
    if (this.all.length == 0) {
      return;
    }
    AlertDialog.show({
      message: '清空曲库？（不会删除音乐文件本身）',
      primaryButton: { value: '取消', action: () => { } },
      secondaryButton: {
        value: '清空',
        fontColor: ACCENT,
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
    await this.refresh();
    this.toastMsg('曲库已清空');
  }

  @Builder
  trackRow(t: Track, i: number) {
    Row({ space: 12 }) {
      // 播放点击只挂在左侧文本区：行容器不绑 onClick，编辑按钮天然不会误触播放
      Row({ space: 12 }) {
        Text(this.isPlayingTrack(t) ? '▶' : `${i + 1}`)
          .width(30)
          .textAlign(TextAlign.Center)
          .fontSize(14)
          .fontColor(this.isPlayingTrack(t) ? ACCENT : '#6E6E76')
        Column({ space: 3 }) {
          Text(t.title)
            .fontSize(15)
            .fontColor(this.isPlayingTrack(t) ? ACCENT : '#F0F0F2')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
          Text(this.subLine(t))
            .fontSize(12)
            .fontColor('#8A8A92')
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
        }
          .alignItems(HorizontalAlign.Start)
          .layoutWeight(1)
      }
        .layoutWeight(1)
        .onClick(() => {
          this.playAt(i);
        })
      Text(fmtSpec(t))
        .fontSize(10)
        .fontColor(ACCENT)
        .backgroundColor('#26221A')
        .padding({ left: 6, right: 6, top: 2, bottom: 2 })
        .borderRadius(4)
      Text(fmtDuration(t.durationMs))
        .fontSize(12)
        .fontColor('#8A8A92')
      Button('编辑')
        .fontSize(10)
        .height(24)
        .backgroundColor('#26221A')
        .fontColor(ACCENT)
        .onClick(() => {
          this.openEditor(t);
        })
    }
      .width('100%')
      .padding({ top: 9, bottom: 9, left: 8, right: 8 })
      .borderRadius(8)
  }

  build() {
    Column() {
      // 头部
      Row({ space: 10 }) {
        Column({ space: 2 }) {
          Text('音序管家')
            .fontSize(24)
            .fontWeight(FontWeight.Bold)
            .fontColor('#F5F5F7')
          Text('HarmonyOS PC · v1.1-dev')
            .fontSize(11)
            .fontColor('#8A8A92')
        }
          .alignItems(HorizontalAlign.Start)

        Blank()

        Button('扫描曲库')
          .fontSize(13)
          .height(34)
          .backgroundColor(ACCENT)
          .fontColor('#1A1A1C')
          .enabled(!this.scanning)
          .onClick(() => {
            this.startScan();
          })
        Button('导入文件')
          .fontSize(13)
          .height(34)
          .backgroundColor('#2A2A30')
          .fontColor('#F0F0F2')
          .onClick(() => {
            this.importFiles();
          })
        Button('目录')
          .fontSize(13)
          .height(34)
          .backgroundColor('#2A2A30')
          .fontColor('#F0F0F2')
          .onClick(() => {
            this.showRoots = !this.showRoots;
          })
        Button('健康')
          .fontSize(13)
          .height(34)
          .backgroundColor('#2A2A30')
          .fontColor('#F0F0F2')
          .onClick(() => {
            router.pushUrl({ url: 'pages/HealthPage' });
          })
        Button('整理')
          .fontSize(13)
          .height(34)
          .backgroundColor('#2A2A30')
          .fontColor('#F0F0F2')
          .onClick(() => {
            router.pushUrl({ url: 'pages/LibraryToolsPage' });
          })
      }
        .width('100%')

      // 搜索 + 统计
      Row({ space: 12 }) {
        TextInput({ placeholder: '搜索 曲名 / 歌手 / 专辑', text: this.keyword })
          .height(36)
          .fontSize(13)
          .backgroundColor('#1E1E22')
          .borderRadius(8)
          .layoutWeight(1)
          .onChange((v: string) => {
            this.keyword = v;
          })
        Text(`共 ${this.stats.count} 首 · ${fmtDuration(this.stats.totalMs)} · ${fmtSize(this.stats.totalSize)}`)
          .fontSize(11)
          .fontColor('#8A8A92')
        Button('清空')
          .fontSize(12)
          .height(30)
          .backgroundColor('#26221A')
          .fontColor(ACCENT)
          .onClick(() => {
            this.clearLib();
          })
      }
        .width('100%')
        .margin({ top: 14 })

      // 扫描根目录管理
      if (this.showRoots) {
        Column({ space: 8 }) {
          Text('扫描根目录（绝对路径，支持多个）')
            .fontSize(12).fontColor('#8A8A92')
          Row({ space: 8 }) {
            TextInput({ placeholder: '例如 /storage/users/.../Music', text: this.newRoot })
              .height(32).fontSize(12).backgroundColor('#1E1E22').borderRadius(6)
              .layoutWeight(1)
              .onChange((v: string) => {
                this.newRoot = v;
              })
            Button('添加').fontSize(12).height(30).backgroundColor('#26221A').fontColor(ACCENT)
              .onClick(() => {
                this.addRoot();
              })
          }
            .width('100%')
          ForEach(this.roots, (r: string) => {
            Row({ space: 8 }) {
              Text(r).fontSize(11).fontColor('#B8B8C0').layoutWeight(1).maxLines(1)
              Button('移除').fontSize(11).height(26).backgroundColor('#2A2A30').fontColor('#F0F0F2')
                .onClick(() => {
                  this.removeRoot(r);
                })
            }
              .width('100%')
          }, (r: string) => r)
        }
          .width('100%')
          .padding(10)
          .margin({ top: 8 })
          .backgroundColor('#1A1A1E')
          .borderRadius(8)
          .alignItems(HorizontalAlign.Start)
      }

      // 扫描进度
      Row({ space: 8 }) {
        LoadingProgress().width(18).height(18).color(ACCENT)
        Text(this.scanText).fontSize(12).fontColor('#B8B8C0').maxLines(1).layoutWeight(1)
        Button(this.scanPaused ? '继续' : '暂停').fontSize(11).height(26)
          .backgroundColor('#26221A').fontColor(ACCENT)
          .onClick(() => {
            this.toggleScanPause();
          })
        Button('取消').fontSize(11).height(26)
          .backgroundColor('#2A2A30').fontColor('#F0F0F2')
          .onClick(() => {
            this.cancelScan();
          })
      }
        .width('100%')
        .margin({ top: 10 })
        .visibility(this.scanning ? Visibility.Visible : Visibility.None)

      if (this.failedUris.length > 0) {
        Button(`重试导入失败项（${this.failedUris.length}）`)
          .fontSize(11).height(28).backgroundColor('#26221A').fontColor(ACCENT)
          .margin({ top: 6 })
          .onClick(() => {
            const retry = this.failedUris.slice();
            this.failedUris = [];
            this.importUris(retry);
          })
      }

      // 提示
      Text(this.toast)
        .fontSize(12)
        .fontColor(ACCENT)
        .width('100%')
        .margin({ top: 8 })
        .maxLines(2)
        .visibility(this.toast == '' ? Visibility.None : Visibility.Visible)

      // 曲库列表
      if (this.filtered().length == 0) {
        Column({ space: 10 }) {
          Text('♪').fontSize(48).fontColor('#3A3A42')
          Text(this.keyword == '' ? '曲库为空' : '无匹配结果')
            .fontSize(16)
            .fontColor('#8A8A92')
          if (this.keyword == '') {
            Text('点「导入文件」选择音频复制进曲库；\n示例曲目已随应用内置，点「扫描曲库」即可载入')
              .fontSize(12)
              .fontColor('#6E6E76')
              .textAlign(TextAlign.Center)
              .lineHeight(20)
          }
        }
          .width('100%')
          .layoutWeight(1)
          .justifyContent(FlexAlign.Center)
      } else {
        List({ space: 2 }) {
          ForEach(this.filtered(), (t: Track, i: number) => {
            ListItem() {
              this.trackRow(t, i)
            }
          }, (t: Track) => `${t.id}-${t.path}`)
        }
          .width('100%')
          .layoutWeight(1)
          .margin({ top: 10 })
          .scrollBar(BarState.Auto)
      }

      // 迷你播放条
      if (this.npIdx >= 0) {
        MiniPlayer()
      }
    }
      .width('100%')
      .height('100%')
      .padding({ left: 24, right: 24, top: 18, bottom: 10 })
      .backgroundColor('#111114')
      .alignItems(HorizontalAlign.Start)
  }
}
