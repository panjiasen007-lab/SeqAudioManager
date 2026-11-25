import { router } from '@kit.ArkUI';
import { Track, emptyTrack, refreshSearchBlob } from '../model/Track';
import { Db } from '../db/Db';
import { TagPatch, TagWriter, WriteResult } from '../tags/TagWriter';

const ACCENT = '#E8B04B';

@Entry
@Component
struct TrackEditPage {
  @State tracks: Track[] = [];
  @State title: string = '';
  @State artist: string = '';
  @State album: string = '';
  @State genre: string = '';
  @State year: string = '';
  @State trackNo: string = '';
  @State keepBackup: boolean = true;
  @State applying: boolean = false;
  @State results: string[] = [];
  @State isBatch: boolean = false;

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
      this.genre = t.genre;
      this.year = t.year;
      this.trackNo = t.trackNo > 0 ? `${t.trackNo}` : '';
    }
    this.keepBackup = (await Db.getSetting('tag_write_backup', '1')) != '0';
  }

  private patchOf(): TagPatch {
    const p = new TagPatch();
    p.title = this.title;
    p.artist = this.artist;
    p.album = this.album;
    p.genre = this.genre;
    p.year = this.year;
    p.trackNo = parseInt(this.trackNo);
    if (isNaN(p.trackNo)) {
      p.trackNo = 0;
    }
    return p;
  }

  private previewLine(t: Track): string {
    const p = this.patchOf();
    const parts: string[] = [];
    if (p.title != '') parts.push(`标题：${t.title} → ${p.title}`);
    if (p.artist != '') parts.push(`歌手：${t.artist} → ${p.artist}`);
    if (p.album != '') parts.push(`专辑：${t.album} → ${p.album}`);
    if (p.genre != '') parts.push(`流派：${t.genre} → ${p.genre}`);
    if (p.year != '') parts.push(`年份：${t.year} → ${p.year}`);
    if (p.trackNo > 0) parts.push(`轨号：${t.trackNo} → ${p.trackNo}`);
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
        updated.genre = t.genre;
        updated.year = t.year;
        updated.trackNo = t.trackNo;
        updated.durationMs = t.durationMs;
        updated.sampleRate = t.sampleRate;
        updated.channels = t.channels;
        updated.bitDepth = t.bitDepth;
        updated.bitrateKbps = t.bitrateKbps;
        updated.sizeBytes = t.sizeBytes;
        updated.mtimeMs = t.mtimeMs;
        updated.cuePath = t.cuePath;
        updated.isCueChild = t.isCueChild;
        updated.cueTrackNo = t.cueTrackNo;
        updated.cueStartMs = t.cueStartMs;
        updated.cueEndMs = t.cueEndMs;
        updated.replayGain = t.replayGain;
        updated.healthStatus = t.healthStatus;
        updated.healthDetail = t.healthDetail;
        updated.lyrics = t.lyrics;
        updated.coverBlobPath = t.coverBlobPath;
        updated.metaSource = t.metaSource;
        updated.metaCheckedAt = t.metaCheckedAt;
        if (patch.title != '') updated.title = patch.title;
        if (patch.artist != '') updated.artist = patch.artist;
        if (patch.album != '') updated.album = patch.album;
        if (patch.genre != '') updated.genre = patch.genre;
        if (patch.year != '') updated.year = patch.year;
        if (patch.trackNo > 0) updated.trackNo = patch.trackNo;
        refreshSearchBlob(updated);
        await Db.updateTrack(updated);
        const i = this.tracks.indexOf(t);
        if (i >= 0) {
          this.tracks[i] = updated;
        }
        okCount++;
      }
      out.push(`${res.ok ? '成功' : '失败'}｜${t.fileName}｜${res.message}`);
    }
    this.results = out;
    this.applying = false;
  }

  build() {
    Column() {
      Row({ space: 12 }) {
        Text('标签编辑').fontSize(20).fontWeight(FontWeight.Bold).fontColor('#F5F5F7')
        Blank()
        if (this.tracks.length == 1) {
          Button('封面').fontSize(12).height(30).backgroundColor('#26221A').fontColor(ACCENT)
            .onClick(() => {
              AppStorage.setOrCreate('seqaudio_cover_path', JSON.stringify([this.tracks[0].path]));
              router.pushUrl({ url: 'pages/CoverPage' });
            })
        }
        Button('返回').fontSize(12).height(30).backgroundColor('#2A2A30').fontColor('#F0F0F2')
          .onClick(() => {
            router.back();
          })
      }
        .width('100%')

      Text(this.isBatch ? `批量编辑 ${this.tracks.length} 首（留空表示不修改）` : `单曲编辑 · ${this.tracks.length > 0 ? this.tracks[0].fileName : ''}`)
        .fontSize(12).fontColor('#8A8A92').margin({ top: 8 })

      Scroll() {
        Column({ space: 8 }) {
          Row({ space: 10 }) {
            Text('标题').fontSize(12).fontColor('#B8B8C0').width(50)
            TextInput({ text: this.title, placeholder: '留空不改' }).height(32).fontSize(12)
              .backgroundColor('#1E1E22').layoutWeight(1)
              .onChange((v: string) => { this.title = v; })
          }
            .width('100%')
          Row({ space: 10 }) {
            Text('歌手').fontSize(12).fontColor('#B8B8C0').width(50)
            TextInput({ text: this.artist, placeholder: '留空不改' }).height(32).fontSize(12)
              .backgroundColor('#1E1E22').layoutWeight(1)
              .onChange((v: string) => { this.artist = v; })
          }
            .width('100%')
          Row({ space: 10 }) {
            Text('专辑').fontSize(12).fontColor('#B8B8C0').width(50)
            TextInput({ text: this.album, placeholder: '留空不改' }).height(32).fontSize(12)
              .backgroundColor('#1E1E22').layoutWeight(1)
              .onChange((v: string) => { this.album = v; })
          }
            .width('100%')
          Row({ space: 10 }) {
            Text('流派').fontSize(12).fontColor('#B8B8C0').width(50)
            TextInput({ text: this.genre, placeholder: '留空不改' }).height(32).fontSize(12)
              .backgroundColor('#1E1E22').layoutWeight(1)
              .onChange((v: string) => { this.genre = v; })
          }
            .width('100%')
          Row({ space: 10 }) {
            Text('年份').fontSize(12).fontColor('#B8B8C0').width(50)
            TextInput({ text: this.year, placeholder: '留空不改' }).height(32).fontSize(12)
              .backgroundColor('#1E1E22').layoutWeight(1)
              .onChange((v: string) => { this.year = v; })
          }
            .width('100%')
          Row({ space: 10 }) {
            Text('轨号').fontSize(12).fontColor('#B8B8C0').width(50)
            TextInput({ text: this.trackNo, placeholder: '留空不改' }).height(32).fontSize(12)
              .backgroundColor('#1E1E22').layoutWeight(1)
              .onChange((v: string) => { this.trackNo = v; })
          }
            .width('100%')

          Row({ space: 8 }) {
            Toggle({ type: ToggleType.Checkbox, isOn: this.keepBackup })
              .onChange((v: boolean) => { this.keepBackup = v; })
            Text('保留最近 1 份 .bak（>500MB 文件拒写）').fontSize(11).fontColor('#8A8A92')
          }
            .width('100%')

          Text('写入预览').fontSize(13).fontColor(ACCENT).margin({ top: 10 })
          ForEach(this.tracks, (t: Track) => {
            Text(this.previewLine(t)).fontSize(11).fontColor('#B8B8C0').maxLines(3).width('100%')
          }, (t: Track) => t.path)

          if (this.results.length > 0) {
            Text('写入结果').fontSize(13).fontColor(ACCENT).margin({ top: 10 })
            ForEach(this.results, (line: string) => {
              Text(line).fontSize(11).fontColor('#B8B8C0').maxLines(3).width('100%')
            }, (line: string) => line)
          }
        }
          .width('100%')
          .alignItems(HorizontalAlign.Start)
      }
        .layoutWeight(1)
        .margin({ top: 12 })

      Button(this.applying ? '写入中…' : '预览并写入').fontSize(14).height(38)
        .backgroundColor(ACCENT).fontColor('#1A1A1C').width('100%')
        .enabled(!this.applying && this.tracks.length > 0)
        .onClick(() => {
          this.apply();
        })
    }
      .width('100%').height('100%')
      .padding({ left: 24, right: 24, top: 18, bottom: 14 })
      .backgroundColor('#111114')
      .alignItems(HorizontalAlign.Start)
  }
}
