// 标签编辑：按桌面版对话框结构改为「曲目信息 / 歌词 / 封面」三段卡。
import { router } from '@kit.ArkUI';
import fs from '@ohos.file.fs';
import { common } from '@kit.AbilityKit';
import { Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { Db } from '../db/Db';
import { TagPatch, TagWriter, WriteResult } from '../tags/TagWriter';
import { T } from '../theme/Tokens';
import { SectionHeader } from '../components/DesktopUI';
import { AppLog } from '../utils/AppLog';
import { fmtSpec } from '../utils/Util';

@Entry
@Component
struct TrackEditPage {
  @State private tracks: Track[] = [];
  @State private title: string = '';
  @State private artist: string = '';
  @State private album: string = '';
  @State private albumartist: string = '';
  @State private genre: string = '';
  @State private year: string = '';
  @State private trackNo: string = '';
  @State private discNo: string = '';
  @State private lyrics: string = '';
  @State private keepBackup: boolean = true;
  @State private applying: boolean = false;
  @State private results: string[] = [];
  @State private isBatch: boolean = false;
  @State private coverPath: string = '';

  aboutToAppear(): void {
    this.initAsync();
  }

  private async initAsync(): Promise<void> {
    const stored = AppStorage.get<string>('seqaudio_edit_paths');
    const raw = stored != undefined ? stored : '';
    let paths: string[] = [];
    try {
      paths = JSON.parse(raw) as string[];
    } catch (e) {
      paths = [];
    }
    if (paths.length > 0) {
      this.tracks = await Db.tracksByPaths(paths);
    }
    this.isBatch = this.tracks.length > 1;
    if (this.tracks.length == 1) {
      const t = this.tracks[0];
      this.title = t.title;
      this.artist = t.artist;
      this.album = t.album;
      this.albumartist = t.albumartist;
      this.genre = t.genre;
      this.year = t.year;
      this.trackNo = t.trackNo > 0 ? `${t.trackNo}` : '';
      this.discNo = t.discNo > 0 ? `${t.discNo}` : '';
      this.lyrics = t.lyrics;
      if (t.coverBlobPath != '') {
        this.coverPath = `file://${t.coverBlobPath}`;
      }
    }
    this.keepBackup = (await Db.getSetting('tag_write_backup', '1')) != '0';
  }

  private patchOf(): TagPatch {
    const p = new TagPatch();
    p.title = this.title;
    p.artist = this.artist;
    p.album = this.album;
    p.albumartist = this.albumartist;
    p.genre = this.genre;
    p.year = this.year;
    p.trackNo = parseInt(this.trackNo);
    if (isNaN(p.trackNo)) {
      p.trackNo = 0;
    }
    p.discNo = parseInt(this.discNo);
    if (isNaN(p.discNo)) {
      p.discNo = 0;
    }
    return p;
  }

  private previewLine(t: Track): string {
    const p = this.patchOf();
    const parts: string[] = [];
    if (p.title != '') {
      parts.push(`标题：${t.title} → ${p.title}`);
    }
    if (p.artist != '') {
      parts.push(`歌手：${t.artist} → ${p.artist}`);
    }
    if (p.album != '') {
      parts.push(`专辑：${t.album} → ${p.album}`);
    }
    if (p.albumartist != '') {
      parts.push(`专辑艺术家：${t.albumartist} → ${p.albumartist}`);
    }
    if (p.genre != '') {
      parts.push(`流派：${t.genre} → ${p.genre}`);
    }
    if (p.year != '') {
      parts.push(`年份：${t.year} → ${p.year}`);
    }
    if (p.trackNo > 0) {
      parts.push(`轨号：${t.trackNo} → ${p.trackNo}`);
    }
    if (p.discNo > 0) {
      parts.push(`碟号：${t.discNo} → ${p.discNo}`);
    }
    return parts.length > 0 ? `${t.fileName}｜${parts.join('；')}` : `${t.fileName}｜未修改`;
  }

  private async apply(): Promise<void> {
    if (this.applying || this.tracks.length == 0) {
      return;
    }
    this.applying = true;
    const patch = this.patchOf();
    await Db.setSetting('tag_write_backup', this.keepBackup ? '1' : '0');
    const out: string[] = [];
    let okCount = 0;
    for (const t of this.tracks) {
      const res: WriteResult = await TagWriter.write(t.path, patch);
      if (res.ok) {
        const updated = emptyTrack();
        updated.id = t.id;
        updated.path = t.path;
        updated.fileName = t.fileName;
        updated.ext = t.ext;
        updated.title = t.title;
        updated.artist = t.artist;
        updated.album = t.album;
        updated.albumartist = t.albumartist;
        updated.genre = t.genre;
        updated.year = t.year;
        updated.trackNo = t.trackNo;
        updated.discNo = t.discNo;
        updated.durationMs = t.durationMs;
        updated.sampleRate = t.sampleRate;
        updated.channels = t.channels;
        updated.bitDepth = t.bitDepth;
        updated.bitrateKbps = t.bitrateKbps;
        updated.sizeBytes = t.sizeBytes;
        updated.mtimeMs = t.mtimeMs;
        try {
          const st = fs.statSync(t.path);
          updated.sizeBytes = st.size;
          updated.mtimeMs = Math.round(st.mtime * 1000);
        } catch (e) {
          // 读取失败沿用旧值
        }
        updated.cuePath = t.cuePath;
        updated.isCueChild = t.isCueChild;
        updated.cueTrackNo = t.cueTrackNo;
        updated.cueStartMs = t.cueStartMs;
        updated.cueEndMs = t.cueEndMs;
        updated.cuePregapMs = t.cuePregapMs;
        updated.replayGain = t.replayGain;
        updated.healthStatus = t.healthStatus;
        updated.healthDetail = t.healthDetail;
        updated.lyrics = this.lyrics;
        updated.coverBlobPath = t.coverBlobPath;
        updated.metaSource = t.metaSource;
        updated.metaCheckedAt = t.metaCheckedAt;
        if (patch.title != '') {
          updated.title = patch.title;
        }
        if (patch.artist != '') {
          updated.artist = patch.artist;
        }
        if (patch.album != '') {
          updated.album = patch.album;
        }
        if (patch.albumartist != '') {
          updated.albumartist = patch.albumartist;
        }
        if (patch.genre != '') {
          updated.genre = patch.genre;
        }
        if (patch.year != '') {
          updated.year = patch.year;
        }
        if (patch.trackNo > 0) {
          updated.trackNo = patch.trackNo;
        }
        if (patch.discNo > 0) {
          updated.discNo = patch.discNo;
        }
        refreshSearchBlob(updated);
        await Db.updateTrack(updated);
        if (this.lyrics != '') {
          await Db.setLyrics(t.id, this.lyrics);
        }
        const i = this.tracks.indexOf(t);
        if (i >= 0) {
          this.tracks[i] = updated;
        }
        okCount++;
      }
      out.push(`${res.ok ? '成功' : '失败'}｜${t.fileName}｜${res.message}`);
    }
    this.results = out;
    AppLog.info(`标签写回：成功 ${okCount}/${this.tracks.length}（备份=${this.keepBackup ? '开' : '关'}）`);
    this.applying = false;
  }

  private fieldValue(key: string): string {
    if (key == 'title') {
      return this.title;
    }
    if (key == 'artist') {
      return this.artist;
    }
    if (key == 'album') {
      return this.album;
    }
    if (key == 'albumartist') {
      return this.albumartist;
    }
    if (key == 'genre') {
      return this.genre;
    }
    if (key == 'year') {
      return this.year;
    }
    if (key == 'discNo') {
      return this.discNo;
    }
    return this.trackNo;
  }

  private setField(key: string, v: string): void {
    if (key == 'title') {
      this.title = v;
    } else if (key == 'artist') {
      this.artist = v;
    } else if (key == 'album') {
      this.album = v;
    } else if (key == 'albumartist') {
      this.albumartist = v;
    } else if (key == 'genre') {
      this.genre = v;
    } else if (key == 'year') {
      this.year = v;
    } else if (key == 'discNo') {
      this.discNo = v;
    } else {
      this.trackNo = v;
    }
  }

  @Builder
  fieldRow(label: string, key: string, placeholder: string) {
    Row({ space: 8 }) {
      Text(label)
        .width(66)
        .fontSize(11)
        .fontColor(T.FG_DIM)
        .textAlign(TextAlign.End)
      TextInput({ text: this.fieldValue(key), placeholder: placeholder })
        .layoutWeight(1)
        .height(32)
        .fontSize(11)
        .fontColor(T.FG)
        .placeholderColor(T.FG_MUTE)
        .backgroundColor(T.INPUT)
        .borderRadius(6)
        .border({ width: 1, color: T.LINE_SOFT })
        .onChange((v: string) => {
          this.setField(key, v);
        })
    }
    .width('100%')
  }

  build() {
    Column() {
      Row({ space: 10 }) {
        Column({ space: 2 }) {
          Text('标签编辑')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text(this.isBatch ? `批量编辑 ${this.tracks.length} 首（留空表示不修改）` :
            this.tracks.length > 0 ? `单曲编辑 · ${this.tracks[0].fileName}` : '未找到曲目')
            .fontSize(10)
            .fontColor(T.FG_MUTE)
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        if (this.tracks.length == 1) {
          Button('封面搜索')
            .height(30)
            .fontSize(11)
            .fontColor(T.ACCENT)
            .backgroundColor(T.GHOST_BG)
            .border({ width: 1, color: T.LINE_SOFT })
            .borderRadius(8)
            .onClick(() => {
              AppStorage.setOrCreate('seqaudio_cover_path',
                JSON.stringify([this.tracks[0].path]));
              router.pushUrl({ url: 'pages/CoverPage' });
            })
        }
        Button('返回')
          .height(30)
          .fontSize(11)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(8)
          .onClick(() => {
            router.back();
          })
      }
      .width('100%')

      Scroll() {
        Column({ space: 10 }) {
          Column({ space: 8 }) {
            SectionHeader({ title: '曲目信息', hint: this.isBatch ? '留空不改' : 'FLAC / MP3 / M4A / WAV', accent: T.ACCENT })
            Row({ space: 10 }) {
              Column({ space: 8 }) {
                this.fieldRow('歌名', 'title', '留空不改')
                this.fieldRow('专辑', 'album', '留空不改')
                this.fieldRow('专辑艺术家', 'albumartist', '留空不改')
                this.fieldRow('年份', 'year', '留空不改')
              }
              .layoutWeight(1)
              Column({ space: 8 }) {
                this.fieldRow('歌手', 'artist', '留空不改')
                this.fieldRow('流派', 'genre', '留空不改')
                this.fieldRow('轨号', 'trackNo', '留空不改')
                this.fieldRow('碟号', 'discNo', '留空不改')
              }
              .layoutWeight(1)
            }
            .width('100%')
            Row({ space: 8 }) {
              Toggle({ type: ToggleType.Checkbox, isOn: this.keepBackup })
                .width(18)
                .height(18)
                .selectedColor(T.ACCENT)
                .onChange((v: boolean) => {
                  this.keepBackup = v;
                })
              Text('写入前保留最近 1 份 .bak（>500MB 文件拒写）')
                .fontSize(10)
                .fontColor(T.FG_MUTE)
            }
            .width('100%')
          }
          .width('100%')
          .padding(12)
          .backgroundColor(T.CARD)
          .borderRadius(T.R_CARD)
          .border({ width: 1, color: T.LINE_SOFT })
          .alignItems(HorizontalAlign.Start)

          Column({ space: 8 }) {
            SectionHeader({ title: '歌词', hint: '写入曲库缓存；LRC 时间轴保留', accent: T.CYAN })
            TextArea({ text: this.lyrics, placeholder: '粘贴 LRC 或纯文本歌词…' })
              .width('100%')
              .height(140)
              .fontSize(11)
              .fontColor(T.FG)
              .placeholderColor(T.FG_MUTE)
              .backgroundColor(T.INPUT)
              .borderRadius(8)
              .padding(10)
              .onChange((v: string) => {
                this.lyrics = v;
              })
          }
          .width('100%')
          .padding(12)
          .backgroundColor(T.CARD)
          .borderRadius(T.R_CARD)
          .border({ width: 1, color: T.LINE_SOFT })
          .alignItems(HorizontalAlign.Start)

          Column({ space: 8 }) {
            SectionHeader({ title: '封面', hint: '嵌入文件或只做展示', accent: T.PURPLE })
            Row({ space: 12 }) {
              if (this.coverPath != '') {
                Image(this.coverPath)
                  .width(112)
                  .height(112)
                  .borderRadius(10)
                  .objectFit(ImageFit.Cover)
                  .border({ width: 1, color: T.LINE })
              } else {
                Column() {
                  Text('无封面')
                    .fontSize(10)
                    .fontColor(T.FG_MUTE)
                }
                .width(112)
                .height(112)
                .justifyContent(FlexAlign.Center)
                .backgroundColor(T.INPUT)
                .borderRadius(10)
                .border({ width: 1, color: T.LINE, style: BorderStyle.Dashed })
              }
              Column({ space: 6 }) {
                Text(this.tracks.length > 0 ? fmtSpec(this.tracks[0]) : '—')
                  .fontSize(10)
                  .fontColor(T.CYAN)
                Text('支持 iTunes / 网易云搜索或从文件手动指定；嵌入后自动更新曲库缓存。')
                  .fontSize(10)
                  .fontColor(T.FG_MUTE)
                  .lineHeight(16)
              }
              .layoutWeight(1)
              .alignItems(HorizontalAlign.Start)
            }
            .width('100%')
          }
          .width('100%')
          .padding(12)
          .backgroundColor(T.CARD)
          .borderRadius(T.R_CARD)
          .border({ width: 1, color: T.LINE_SOFT })
          .alignItems(HorizontalAlign.Start)

          Column({ space: 6 }) {
            SectionHeader({ title: '写入预览', accent: T.WARN })
            ForEach(this.tracks, (t: Track) => {
              Text(this.previewLine(t))
                .fontSize(10)
                .fontColor(T.FG_DIM)
                .maxLines(3)
                .width('100%')
            }, (t: Track) => `${t.id}-${t.path}`)
            if (this.results.length > 0) {
              Divider().color(T.LINE_SOFT).margin({ top: 4, bottom: 4 })
              SectionHeader({ title: '写入结果', accent: T.OK })
              ForEach(this.results, (line: string) => {
                Text(line)
                  .fontSize(10)
                  .fontColor(line.startsWith('成功') ? T.FG_DIM : T.BAD)
                  .maxLines(2)
                  .width('100%')
              }, (line: string) => line)
            }
          }
          .width('100%')
          .padding(12)
          .backgroundColor(T.CARD)
          .borderRadius(T.R_CARD)
          .border({ width: 1, color: T.LINE_SOFT })
          .alignItems(HorizontalAlign.Start)
        }
        .width('100%')
        .constraintSize({ minHeight: '100%' })
        .padding({ top: 10, bottom: 10 })
        .alignItems(HorizontalAlign.Start)
      }
      .layoutWeight(1)
      .scrollBar(BarState.Auto)

      Row({ space: 8 }) {
        Text(this.applying ? '写入中…' : '写入采用「临时文件 → 校验 → 原子替换」')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
        Button('取消')
          .height(32)
          .fontSize(11)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(8)
          .onClick(() => {
            router.back();
          })
        Button(this.applying ? '写入中…' : '保存到列表')
          .height(32)
          .fontSize(11)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 18, right: 18 })
          .enabled(!this.applying && this.tracks.length > 0)
          .onClick(() => {
            this.apply();
          })
      }
      .width('100%')
      .height(42)
    }
    .width('100%')
    .height('100%')
    .padding({ left: 18, right: 18, top: 12, bottom: 10 })
    .backgroundColor(T.BASE)
    .alignItems(HorizontalAlign.Start)
  }
}
