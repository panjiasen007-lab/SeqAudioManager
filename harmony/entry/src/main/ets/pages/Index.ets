import { router } from '@kit.ArkUI';
import { common } from '@kit.AbilityKit';
import fs from '@ohos.file.fs';
import picker from '@ohos.file.picker';
import { Track } from '../model/Track';
import { Db, Stats } from '../db/Db';
import { Scanner } from '../scanner/Scanner';
import { PlayerEngine, PlayMode } from '../player/PlayerEngine';
import { MiniPlayer } from '../components/MiniPlayer';
import { fmtDuration, fmtSize, fmtSpec } from '../utils/Util';

const ACCENT = '#E8B04B';

// 把选择器选中的文件复制进应用沙箱（选择器 URI 可直接 open）
async function copyUriToFile(uri: string, destDir: string): Promise<boolean> {
  try {
    const name = decodeURIComponent(uri.substring(uri.lastIndexOf('/') + 1));
    if (name == '') {
      return false;
    }
    const dest = destDir.endsWith('/') ? destDir + name : `${destDir}/${name}`;
    const src = fs.openSync(uri, fs.OpenMode.READ_ONLY);
    const dst = fs.openSync(dest, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
    const buf = new ArrayBuffer(1048576);
    try {
      let n = 0;
      do {
        n = fs.readSync(src.fd, buf, { offset: 0, length: 1048576 });
        if (n > 0) {
          fs.writeSync(dst.fd, buf, { offset: 0, length: n });
        }
      } while (n > 0);
    } finally {
      try {
        fs.closeSync(src);
      } catch (e) {
        // ignore
      }
      try {
        fs.closeSync(dst);
      } catch (e) {
        // ignore
      }
    }
    return true;
  } catch (e) {
    return false;
  }
}

@Entry
@Component
struct Index {
  @State all: Track[] = [];
  @State keyword: string = '';
  @State stats: Stats = new Stats();
  @State scanning: boolean = false;
  @State scanText: string = '';
  @State toast: string = '';
  @StorageLink('npIdx') npIdx: number = -1;
  private engine: PlayerEngine = PlayerEngine.get();

  aboutToAppear(): void {
    this.initAsync();
  }

  onPageShow(): void {
    this.refresh();
  }

  private async initAsync(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    await Db.init(ctx);
    const modeVal = parseInt(await Db.getSetting('play_mode', '1'));
    this.engine.setMode((isNaN(modeVal) ? PlayMode.LOOP : modeVal) as PlayMode);
    const vol = parseFloat(await Db.getSetting('volume', '1'));
    if (!isNaN(vol)) {
      this.engine.setVolume(vol);
    }
    await this.seedSamplesIfNeeded(ctx);
    const st = await Db.stats();
    if (st.count == 0) {
      await this.startScan();
    } else {
      await this.refresh();
    }
  }

  // 首次启动：把内置示例曲目复制进沙箱 Music，保证开箱即有内容可扫可播
  private async seedSamplesIfNeeded(ctx: common.UIAbilityContext): Promise<void> {
    const musicDir = `${ctx.filesDir}/Music`;
    let existing: string[] = [];
    try {
      existing = fs.listFileSync(musicDir);
    } catch (e) {
      existing = [];
    }
    if (existing.length > 0) {
      return;
    }
    try {
      fs.mkdirSync(musicDir, true);
      const names: string[] = ['01_yehangxing.flac', '02_chenguang.mp3', '03_sine_48k.wav'];
      for (const n of names) {
        let bytes = new Uint8Array(0);
        try {
          bytes = ctx.resourceManager.getRawFileContentSync(`rawfile/${n}`);
        } catch (e) {
          bytes = ctx.resourceManager.getRawFileContentSync(n);
        }
        const ab = new ArrayBuffer(bytes.byteLength);
        new Uint8Array(ab).set(bytes);
        const f = fs.openSync(`${musicDir}/${n}`, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC);
        try {
          fs.writeSync(f.fd, ab);
        } finally {
          fs.closeSync(f);
        }
      }
      this.toastMsg('已载入 3 首内置示例曲目');
    } catch (e) {
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

  private async startScan(): Promise<void> {
    if (this.scanning) {
      return;
    }
    this.scanning = true;
    this.scanText = '正在收集文件…';
    try {
      const ctx = getContext(this) as common.UIAbilityContext;
      let roots: string[] = [`${ctx.filesDir}/Music`];
      const saved = await Db.getSetting('scan_roots', '');
      if (saved != '') {
        try {
          const arr = JSON.parse(saved) as string[];
          if (arr.length > 0) {
            roots = arr;
          }
        } catch (e) {
          // 配置损坏则用默认根
        }
      }
      const res = await Scanner.scan(roots, (done: number, total: number, cur: string) => {
        this.scanText = total > 0 ? `扫描中 ${done}/${total}：${cur}` : '正在收集文件…';
      });
      await this.refresh();
      this.toastMsg(`扫描完成：新增 ${res.added} · 更新 ${res.updated} · 跳过 ${res.skipped} · 失败 ${res.failed}（${res.costMs}ms）`);
    } catch (e) {
      this.toastMsg('扫描失败，请查看日志');
    }
    this.scanning = false;
  }

  private async importFiles(): Promise<void> {
    const ctx = getContext(this) as common.UIAbilityContext;
    try {
      const avp = new picker.AudioViewPicker();
      const opts = new picker.AudioSelectOptions();
      opts.maxSelectNumber = 100;
      const uris = await avp.select(opts);
      if (!uris || uris.length == 0) {
        return;
      }
      const musicDir = `${ctx.filesDir}/Music`;
      try {
        fs.mkdirSync(musicDir, true);
      } catch (e) {
        // 目录已存在
      }
      let ok = 0;
      for (const uri of uris) {
        if (await copyUriToFile(uri, musicDir)) {
          ok++;
        }
      }
      this.toastMsg(`导入完成 ${ok}/${uris.length}，开始扫描…`);
      await this.startScan();
    } catch (e) {
      // 用户取消选择：静默返回
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
      Text(fmtSpec(t))
        .fontSize(10)
        .fontColor(ACCENT)
        .backgroundColor('#26221A')
        .padding({ left: 6, right: 6, top: 2, bottom: 2 })
        .borderRadius(4)
      Text(fmtDuration(t.durationMs))
        .fontSize(12)
        .fontColor('#8A8A92')
    }
      .width('100%')
      .padding({ top: 9, bottom: 9, left: 8, right: 8 })
      .borderRadius(8)
      .onClick(() => {
        this.playAt(i);
      })
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
          Text('HarmonyOS PC · v1.0')
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

      // 扫描进度
      Row({ space: 8 }) {
        LoadingProgress().width(18).height(18).color(ACCENT)
        Text(this.scanText).fontSize(12).fontColor('#B8B8C0').maxLines(1)
      }
        .width('100%')
        .margin({ top: 10 })
        .visibility(this.scanning ? Visibility.Visible : Visibility.None)

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
          }, (t: Track) => `${t.id}`)
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
