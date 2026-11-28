// 桌面版三个弹层的鸿蒙复刻：播放列表 / 关于 / 扫描根目录。
import { common } from '@kit.AbilityKit';
import { fileUri } from '@kit.CoreFileKit';
import fs from '@ohos.file.fs';
import picker from '@ohos.file.picker';
import { promptAction } from '@kit.ArkUI';
import { T } from '../theme/Tokens';
import { Playlist, PlaylistStore } from '../playlist/PlaylistStore';
import { SectionHeader } from './DesktopUI';

@CustomDialog
export struct PlaylistDialog {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  currentPaths: string[] = [];
  onLoad: (paths: string[], append: boolean) => void = () => {
  };
  @State private items: Playlist[] = [];
  @State private selectedName: string = '';
  @State private selectedIndex: number = -1;
  @State private nameInput: string = '';
  @State private hint: string = '';

  aboutToAppear(): void {
    this.reload();
  }

  private ctx(): common.UIAbilityContext {
    return getContext(this) as common.UIAbilityContext;
  }

  private reload(): void {
    this.items = PlaylistStore.load(this.ctx());
    if (this.selectedName != '' && !this.hasName(this.selectedName)) {
      this.selectedName = '';
    }
    if (this.selectedName == '' && this.items.length > 0) {
      this.selectedName = this.items[0].name;
    }
  }

  private hasName(name: string): boolean {
    return this.items.some((p: Playlist) => p.name == name);
  }

  private selected(): Playlist | undefined {
    for (const p of this.items) {
      if (p.name == this.selectedName) {
        return p;
      }
    }
    return undefined;
  }

  private selectedPaths(): string[] {
    const p = this.selected();
    return p != undefined ? p.paths : [];
  }

  private baseName(path: string): string {
    const i = path.lastIndexOf('/');
    return i >= 0 ? path.substring(i + 1) : path;
  }

  private saveCurrent(): void {
    const name = this.nameInput.trim() != '' ? this.nameInput.trim() : `列表 ${this.items.length + 1}`;
    if (PlaylistStore.create(this.ctx(), name, this.currentPaths)) {
      this.selectedName = name;
      this.nameInput = '';
      this.hint = `已保存「${name}」${this.currentPaths.length} 首`;
      this.reload();
    } else {
      this.hint = '保存失败：名称为空或文件不可写';
    }
  }

  private overwrite(): void {
    const p = this.selected();
    if (!p) {
      this.hint = '先选择一个列表';
      return;
    }
    if (PlaylistStore.create(this.ctx(), p.name, this.currentPaths)) {
      this.hint = `已用当前列表覆盖「${p.name}」`;
      this.reload();
    }
  }

  private rename(): void {
    const p = this.selected();
    const name = this.nameInput.trim();
    if (!p || name == '') {
      this.hint = '先选择列表并输入新名称';
      return;
    }
    if (PlaylistStore.rename(this.ctx(), p.name, name)) {
      this.selectedName = name;
      this.nameInput = '';
      this.hint = '已重命名';
      this.reload();
    } else {
      this.hint = '重命名失败：名称重复或为空';
    }
  }

  private remove(): void {
    const p = this.selected();
    if (!p) {
      return;
    }
    PlaylistStore.remove(this.ctx(), p.name);
    this.selectedName = '';
    this.hint = `已删除「${p.name}」`;
    this.reload();
  }

  private move(delta: number): void {
    const p = this.selected();
    if (!p) {
      return;
    }
    const idx = this.items.indexOf(p);
    const target = idx + delta;
    if (idx < 0 || target < 0 || target >= this.items.length) {
      return;
    }
    const tmp = this.items[idx];
    this.items[idx] = this.items[target];
    this.items[target] = tmp;
    this.items = this.items.slice();
    const updated = this.selected();
    if (updated) {
      PlaylistStore.create(this.ctx(), updated.name, updated.paths);
    }
  }

  private removeTrackAt(index: number): void {
    const p = this.selected();
    if (!p || index < 0 || index >= p.paths.length) {
      return;
    }
    const next = p.paths.slice();
    next.splice(index, 1);
    if (PlaylistStore.create(this.ctx(), p.name, next)) {
      this.hint = `已从「${p.name}」移除 1 首`;
      this.selectedIndex = -1;
      this.reload();
    } else {
      this.hint = '移除失败：文件不可写';
    }
  }

  @Builder
  savedRow(p: Playlist) {
    Row({ space: 8 }) {
      Text(p.name)
        .fontSize(11)
        .fontColor(this.selectedName == p.name ? T.ACCENT : T.FG_DIM)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .layoutWeight(1)
      Text(`${p.paths.length}`)
        .fontSize(9)
        .fontColor(T.FG_MUTE)
    }
    .width('100%')
    .height(30)
    .padding({ left: 8, right: 8 })
    .backgroundColor(this.selectedName == p.name ? T.CHIP_ON_BG : Color.Transparent)
    .borderRadius(6)
    .onClick(() => {
      this.selectedName = p.name;
    })
  }

  build() {
    Column({ space: 10 }) {
      Row() {
        Text('播放列表')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .fontColor(T.FG)
        Blank()
        Text('保存 / 载入 / 重命名 / 排序')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')

      Row({ space: 10 }) {
        Column({ space: 8 }) {
          SectionHeader({ title: '已保存的列表', accent: T.CYAN })
          List() {
            ForEach(this.items, (p: Playlist) => {
              ListItem() {
                this.savedRow(p)
              }
            }, (p: Playlist) => `${p.name}-${p.updatedAt}`)
          }
          .width('100%')
          .layoutWeight(1)
          .scrollBar(BarState.Auto)
          .backgroundColor(T.INPUT)
          .borderRadius(8)
          .padding(4)

          TextInput({ text: this.nameInput, placeholder: '列表名称' })
            .height(30)
            .fontSize(11)
            .fontColor(T.FG)
            .placeholderColor(T.FG_MUTE)
            .backgroundColor(T.INPUT)
            .borderRadius(6)
            .onChange((v: string) => {
              this.nameInput = v;
            })
          Row({ space: 6 }) {
            Button('保存当前列表')
              .height(28)
              .fontSize(10)
              .fontColor(T.ON_ACCENT)
              .backgroundColor(T.ACCENT)
              .borderRadius(6)
              .padding({ left: 10, right: 10 })
              .onClick(() => {
                this.saveCurrent();
              })
            Button('覆盖')
              .height(28)
              .fontSize(10)
              .fontColor(T.FG)
              .backgroundColor(T.GHOST_BG)
              .borderRadius(6)
              .onClick(() => {
                this.overwrite();
              })
            Button('重命名')
              .height(28)
              .fontSize(10)
              .fontColor(T.FG)
              .backgroundColor(T.GHOST_BG)
              .borderRadius(6)
              .onClick(() => {
                this.rename();
              })
            Button('删除')
              .height(28)
              .fontSize(10)
              .fontColor(T.BAD)
              .backgroundColor(T.DANGER_SOFT_BG)
              .borderRadius(6)
              .onClick(() => {
                this.remove();
              })
          }
        }
        .layoutWeight(1)
        .height('100%')
        .padding(10)
        .backgroundColor(T.CARD)
        .borderRadius(T.R_CARD)
        .border({ width: 1, color: T.LINE_SOFT })
        .alignItems(HorizontalAlign.Start)

        Column({ space: 8 }) {
          SectionHeader({ title: '列表内容', accent: T.PURPLE })
          if (this.selected() == undefined) {
            Column() {
              Text('选择左侧列表查看内容')
                .fontSize(11)
                .fontColor(T.FG_MUTE)
            }
            .width('100%')
            .layoutWeight(1)
            .justifyContent(FlexAlign.Center)
          } else {
            List() {
              ForEach(this.selectedPaths(), (p: string, i: number) => {
                ListItem() {
                  Row({ space: 8 }) {
                    Text(`${i + 1}`)
                      .width(22)
                      .fontSize(9)
                      .fontColor(T.FG_MUTE)
                    Text(this.baseName(p))
                      .fontSize(10)
                      .fontColor(T.FG_DIM)
                      .maxLines(1)
                      .textOverflow({ overflow: TextOverflow.Ellipsis })
                      .layoutWeight(1)
                  }
                  .width('100%')
                  .height(28)
                  .backgroundColor(this.selectedIndex == i ? T.CHIP_ON_BG : Color.Transparent)
                  .borderRadius(5)
                  .onClick(() => {
                    this.selectedIndex = i;
                  })
                }
              }, (p: string, i: number) => `${p}-${i}`)
            }
            .width('100%')
            .layoutWeight(1)
            .scrollBar(BarState.Auto)
            .backgroundColor(T.INPUT)
            .borderRadius(8)
            .padding(4)
          }
          Row({ space: 6 }) {
            Button('上移')
              .height(26)
              .fontSize(10)
              .fontColor(T.FG_DIM)
              .backgroundColor(T.GHOST_BG)
              .borderRadius(6)
              .onClick(() => {
                this.move(-1);
              })
            Button('下移')
              .height(26)
              .fontSize(10)
              .fontColor(T.FG_DIM)
              .backgroundColor(T.GHOST_BG)
              .borderRadius(6)
              .onClick(() => {
                this.move(1);
              })
            Button('从列表移除')
              .height(26)
              .fontSize(10)
              .fontColor(T.BAD)
              .backgroundColor(T.DANGER_SOFT_BG)
              .borderRadius(6)
              .enabled(this.selectedIndex >= 0)
              .opacity(this.selectedIndex >= 0 ? 1 : 0.42)
              .onClick(() => {
                this.removeTrackAt(this.selectedIndex);
              })
          }
        }
        .layoutWeight(1)
        .height('100%')
        .padding(10)
        .backgroundColor(T.CARD)
        .borderRadius(T.R_CARD)
        .border({ width: 1, color: T.LINE_SOFT })
        .alignItems(HorizontalAlign.Start)
      }
      .width('100%')
      .layoutWeight(1)

      Row({ space: 8 }) {
        Text(this.hint)
          .fontSize(10)
          .fontColor(this.hint.indexOf('失败') >= 0 ? T.BAD : T.ACCENT)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })
          .layoutWeight(1)
        Button('追加到当前列表')
          .height(30)
          .fontSize(10)
          .fontColor(T.FG)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(8)
          .onClick(() => {
            const p = this.selected();
            if (p) {
              this.onLoad(p.paths, true);
              this.onClose();
            }
          })
        Button('载入到当前列表')
          .height(30)
          .fontSize(10)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 14, right: 14 })
          .onClick(() => {
            const p = this.selected();
            if (p) {
              this.onLoad(p.paths, false);
              this.onClose();
            }
          })
        Button('关闭')
          .height(30)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(8)
          .onClick(() => {
            this.onClose();
          })
      }
      .width('100%')
    }
    .width(760)
    .height(480)
    .padding(14)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }
}

class PaletteItem {
  key: string = '';
  label: string = '';
  group: string = '';

  constructor(key: string, label: string, group: string) {
    this.key = key;
    this.label = label;
    this.group = group;
  }
}

@CustomDialog
export struct CommandPalette {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  onRun: (key: string) => void = () => {
  };
  @State private query: string = '';
  private items: PaletteItem[] = [
    new PaletteItem('scan', '扫描曲库', '曲库'),
    new PaletteItem('import', '添加文件…', '曲库'),
    new PaletteItem('browseimport', '浏览文件导入…', '曲库'),
    new PaletteItem('playlists', '播放列表…', '曲库'),
    new PaletteItem('health', '曲库体检…', '曲库'),
    new PaletteItem('auto', '一键全自动（当前列表）', '处理'),
    new PaletteItem('autoselected', '仅处理选中项', '处理'),
    new PaletteItem('candidates', '查看候选匹配…', '处理'),
    new PaletteItem('confirmmatch', '确认匹配（采纳最佳候选）', '处理'),
    new PaletteItem('revert', '撤销本地修改', '处理'),
    new PaletteItem('exportlog', '导出运行日志…', '处理'),
    new PaletteItem('auth', '真假无损鉴定', '处理'),
    new PaletteItem('cleanup', '清理假无损', '处理'),
    new PaletteItem('rename', '重命名预览…', '处理'),
    new PaletteItem('renamedir', '按标签重命名本目录…', '处理'),
    new PaletteItem('tools', '批量整理…', '处理'),
    new PaletteItem('report', '导出处理报表…', '处理'),
    new PaletteItem('stripno', '去除标题序号', '处理'),
    new PaletteItem('exportcovers', '批量导出封面…', '处理'),
    new PaletteItem('foldercovers', '生成文件夹封面', '处理'),
    new PaletteItem('queuepanel', '播放队列', '播放'),
    new PaletteItem('mute', '静音 / 取消静音', '播放'),
    new PaletteItem('eq', '均衡器…', '播放'),
    new PaletteItem('spectrum', '查看频谱…', '查看'),
    new PaletteItem('cuesplit', '分轨导出（CUE / SACD）…', '查看'),
    new PaletteItem('quarantine', '隔离区管理…', '查看'),
    new PaletteItem('cover', '封面搜索…', '查看'),
    new PaletteItem('lyrics', '歌词…', '查看'),
    new PaletteItem('about', '关于音序管家…', '帮助')
  ];

  private filtered(): PaletteItem[] {
    const q = this.query.trim().toLowerCase();
    if (q == '') {
      return this.items;
    }
    return this.items.filter((it: PaletteItem) =>
      it.label.toLowerCase().indexOf(q) >= 0 || it.key.indexOf(q) >= 0);
  }

  build() {
    Column({ space: 10 }) {
      Row({ space: 8 }) {
        Text('命令面板')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .fontColor(T.FG)
        Text('输入关键字筛选动作')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
        Blank()
        Text('Esc 关闭')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')

      TextInput({ text: this.query, placeholder: '搜索动作，例如：队列 / 匹配 / 分轨' })
        .width('100%')
        .height(34)
        .fontSize(12)
        .fontColor(T.FG)
        .placeholderColor(T.FG_MUTE)
        .backgroundColor(T.INPUT)
        .borderRadius(8)
        .onChange((v: string) => {
          this.query = v;
        })

      List() {
        ForEach(this.filtered(), (it: PaletteItem) => {
          ListItem() {
            Row({ space: 10 }) {
              Text(it.group)
                .width(42)
                .fontSize(9)
                .fontColor(T.FG_MUTE)
              Text(it.label)
                .fontSize(12)
                .fontColor(T.FG_DIM)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
                .layoutWeight(1)
              Text('›')
                .fontSize(12)
                .fontColor(T.FG_MUTE)
            }
            .width('100%')
            .height(32)
            .padding({ left: 10, right: 10 })
            .borderRadius(6)
            .onClick(() => {
              this.onClose();
              this.onRun(it.key);
            })
          }
        }, (it: PaletteItem) => it.key)
      }
      .width('100%')
      .layoutWeight(1)
      .scrollBar(BarState.Auto)
      .backgroundColor(T.INPUT)
      .borderRadius(8)
      .padding(4)

      Row() {
        Text(this.filtered().length == 0 ? '没有匹配的动作' : `${this.filtered().length} 个动作`)
          .fontSize(9)
          .fontColor(T.FG_MUTE)
        Blank()
        Button('关闭')
          .height(28)
          .fontSize(10)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(7)
          .onClick(() => {
            this.onClose();
          })
      }
      .width('100%')
    }
    .width(560)
    .height(460)
    .padding(14)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }
}

@CustomDialog
export struct AboutDialog {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  @State private tab: number = 0;
  private version: string = '1.8.2';

  @Builder
  tabButton(label: string, index: number) {
    Text(label)
      .fontSize(11)
      .fontColor(this.tab == index ? T.ACCENT : T.FG_DIM)
      .padding({ left: 10, right: 10, top: 5, bottom: 5 })
      .border({
        width: { bottom: 2 },
        color: { bottom: this.tab == index ? T.ACCENT : Color.Transparent }
      })
      .onClick(() => {
        this.tab = index;
      })
  }

  @Builder
  paragraph(title: string, body: string) {
    Column({ space: 4 }) {
      Text(title)
        .fontSize(12)
        .fontWeight(FontWeight.Medium)
        .fontColor(T.FG)
      Text(body)
        .fontSize(10)
        .fontColor(T.FG_DIM)
        .lineHeight(17)
    }
    .width('100%')
    .alignItems(HorizontalAlign.Start)
  }

  build() {
    Column({ space: 10 }) {
      Row({ space: 12 }) {
        Image($r('app.media.startIcon'))
          .width(56)
          .height(56)
          .borderRadius(12)
        Column({ space: 2 }) {
          Text('音序管家')
            .fontSize(18)
            .fontWeight(FontWeight.Bold)
            .fontColor(T.FG)
          Text(`SeqAudio Manager · HarmonyOS PC v${this.version}`)
            .fontSize(10)
            .fontColor(T.FG_MUTE)
          Text('本地音乐元数据批量处理 · 播放 · 纯离线可用')
            .fontSize(10)
            .fontColor(T.FG_DIM)
        }
        .alignItems(HorizontalAlign.Start)
        Blank()
        Text('GPLv3')
          .fontSize(10)
          .fontColor(T.OK)
          .padding({ left: 9, right: 9, top: 3, bottom: 3 })
          .borderRadius(8)
          .backgroundColor(T.alpha(T.OK, '15'))
      }
      .width('100%')

      Row({ space: 2 }) {
        this.tabButton('概览', 0)
        this.tabButton('开源协议', 1)
        this.tabButton('第三方组件', 2)
        this.tabButton('运行环境', 3)
        Blank()
      }
      .width('100%')
      .border({ width: { bottom: 1 }, color: { bottom: T.LINE_SOFT } })

      Scroll() {
        Column({ space: 12 }) {
          if (this.tab == 0) {
            this.paragraph('这个软件做什么',
              '把本地曲库整理成干净可用的元数据：读取与写回标签、CUE 逻辑轨、歌词、封面、健康检查、批量重命名与重复检测，并提供原生直通播放与 31 段均衡器。')
            this.paragraph('数据去哪了',
              '全部留在本机。歌词与封面只在用户主动点击时访问 lrclib / iTunes 等公开接口，不上传音频或个人信息；断网可用。')
            this.paragraph('鸿蒙版边界',
              '已实现联网元数据匹配、真假无损鉴定/频谱、CUE 与 SACD 分轨导出、31 段均衡器；WASAPI/ASIO 独占与系统托盘不在鸿蒙版范围。')
          } else if (this.tab == 1) {
            this.paragraph('GNU General Public License v3.0',
              '本软件为自由软件：你可以依据自由软件基金会发布的 GNU GPL v3 条款重新分发或修改它。分发时须提供完整源码并保留同一授权。本软件不提供任何担保。')
            this.paragraph('随包字体',
              'HarmonyOS 系统字体；桌面版随包的 SIL OFL 字体授权说明见桌面仓库 THIRD-PARTY.md。')
          } else if (this.tab == 2) {
            this.paragraph('FFmpeg 7.1.1',
              'LGPL-2.1+，静态链入 libseqaudio_core.so，仅用于音频解码与重采样；源码与编译脚本见 harmony/tools/ffmpeg-ohos。')
            this.paragraph('ArkUI / HarmonyOS SDK',
              '应用框架与系统能力由 HarmonyOS SDK 提供。')
            this.paragraph('lrclib / iTunes',
              '仅作为歌词与封面的公开检索源，遵循其各自服务条款。')
          } else {
            this.paragraph('运行环境',
              'HarmonyOS PC（2in1 / API 26）。音频输出：OH_AudioRenderer 直通；AVPlayer 作为降级路径。')
            this.paragraph('构建',
              'DevEco Studio 26.0.1.290 / hvigor CLI；compatibleSdkVersion 26.0.0；bundleName com.seqaudio.manager。')
            this.paragraph('内核自检',
              '31 段 peaking biquad（20 Hz–20 kHz，Q=1.41，±12 dB）+ soft-limiter（-0.1 dBFS）+ 120 ms 淡入淡出。')
          }
        }
        .width('100%')
        .padding({ top: 4, bottom: 8 })
        .alignItems(HorizontalAlign.Start)
      }
      .layoutWeight(1)
      .scrollBar(BarState.Auto)

      Row() {
        Text('© 2026 SeqAudio Manager Contributors')
          .fontSize(9)
          .fontColor(T.FG_MUTE)
        Blank()
        Button('关闭')
          .height(30)
          .fontSize(11)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 20, right: 20 })
          .onClick(() => {
            this.onClose();
          })
      }
      .width('100%')
    }
    .width(720)
    .height(520)
    .padding(16)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }
}

@CustomDialog
export struct ScanRootsDialog {
  controller: CustomDialogController;
  onClose: () => void = () => {
  };
  @Prop roots: string[] = [];
  onRootsChanged: (roots: string[]) => void = () => {
  };
  onScan: () => void = () => {
  };
  @State private list: string[] = [];

  aboutToAppear(): void {
    this.list = this.roots.slice();
  }

  // 系统文件夹选择器：与 Windows 版「添加文件夹」一致的浏览体验
  private async browse(): Promise<void> {
    let picked = '';
    try {
      const dp = new picker.DocumentViewPicker();
      const opts = new picker.DocumentSelectOptions();
      opts.selectMode = picker.DocumentSelectMode.FOLDER;
      opts.maxSelectNumber = 1;
      const uris = await dp.select(opts);
      if (!uris || uris.length == 0) {
        return;
      }
      picked = uris[0];
    } catch (e) {
      return;
    }
    let path = picked;
    if (path.startsWith('file://')) {
      try {
        path = new fileUri.FileUri(path).path;
      } catch (e) {
        path = decodeURIComponent(path.replace(/^file:\/\//, ''));
      }
    }
    path = path.replace(/\\/g, '/').replace(/\/+$/, '');
    try {
      fs.listFileSync(path, { recursion: false });
    } catch (e) {
      promptAction.showToast({ message: '无法读取该目录，请改用「添加文件」导入' });
      return;
    }
    if (this.list.indexOf(path) >= 0) {
      return;
    }
    this.list.push(path);
    this.list = this.list.slice();
  }

  private remove(root: string): void {
    this.list = this.list.filter((r: string) => r != root);
  }

  private apply(): void {
    this.onRootsChanged(this.list);
    this.onClose();
    this.onScan();
  }

  build() {
    Column({ space: 10 }) {
      Row() {
        Text('扫描根目录')
          .fontSize(15)
          .fontWeight(FontWeight.Bold)
          .fontColor(T.FG)
        Blank()
        Text('浏览选择目录')
          .fontSize(10)
          .fontColor(T.FG_MUTE)
      }
      .width('100%')

      Text('点击下方按钮浏览并选择音乐文件夹；外部目录若受系统权限限制，可用「添加文件」导入到沙箱。')
        .fontSize(10)
        .fontColor(T.FG_MUTE)
        .lineHeight(16)
        .width('100%')

      Row({ space: 8 }) {
        Button('＋ 浏览添加文件夹…')
          .layoutWeight(1)
          .height(32)
          .fontSize(11)
          .fontColor(T.ACCENT)
          .backgroundColor(T.GHOST_BG)
          .border({ width: 1, color: T.LINE_SOFT })
          .borderRadius(T.R_BTN)
          .onClick(() => {
            this.browse();
          })
      }
      .width('100%')

      List({ space: 4 }) {
        ForEach(this.list, (root: string) => {
          ListItem() {
            Row({ space: 8 }) {
              Text(root)
                .fontSize(10)
                .fontColor(T.FG_DIM)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
                .layoutWeight(1)
              Button('移除')
                .height(24)
                .fontSize(9)
                .fontColor(T.BAD)
                .backgroundColor(T.DANGER_SOFT_BG)
                .borderRadius(5)
                .onClick(() => {
                  this.remove(root);
                })
            }
            .width('100%')
            .height(30)
            .padding({ left: 8, right: 8 })
            .backgroundColor(T.INPUT)
            .borderRadius(6)
          }
        }, (root: string) => root)
      }
      .width('100%')
      .height(190)
      .scrollBar(BarState.Auto)

      Row({ space: 8 }) {
        Blank()
        Button('取消')
          .height(30)
          .fontSize(11)
          .fontColor(T.FG_DIM)
          .backgroundColor(T.GHOST_BG)
          .borderRadius(8)
          .onClick(() => {
            this.onClose();
          })
        Button('保存并扫描')
          .height(30)
          .fontSize(11)
          .fontColor(T.ON_ACCENT)
          .backgroundColor(T.ACCENT)
          .borderRadius(8)
          .padding({ left: 16, right: 16 })
          .onClick(() => {
            this.apply();
          })
      }
      .width('100%')
    }
    .width(620)
    .height(380)
    .padding(16)
    .backgroundColor(T.POPUP_BG)
    .borderRadius(14)
    .border({ width: 1, color: T.LINE })
  }
}
