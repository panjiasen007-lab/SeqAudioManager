# 音序管家 · HarmonyOS PC 版

本目录是鸿蒙 PC（2in1）版，从 `main` 分支的 v1.8.2 起独立演进。
桌面版（Python + PySide6）与鸿蒙版是两套技术栈，**不是移植，是重写**。

## 当前状态（v1.3.7 · 窗口化桌面工作台 + 独立迷你播放器 + 核心算法 + SACD ISO）

M1（v1.1 曲库管理基线，T01–T05）已交付；M2（v1.2 音质与体验）已完成 T06–T10；v1.3 起按
[`docs/v1.3-UI-REPLICATION-PLAN.md`](docs/v1.3-UI-REPLICATION-PLAN.md) 把界面重做为与桌面版 v1.8.2 同构的桌面工作台。

- 曲库扫描：递归扫描应用沙箱目录，纯 ArkTS 解析标签与规格；扫描时清理扫描范围内已被删除文件的数据库记录（CUE 子轨由索引重建，不受影响）
  - FLAC：STREAMINFO（采样率/位深/声道/时长）+ Vorbis Comment（标题/歌手/专辑/流派/年份/音轨号）
  - MP3：ID3v2.2/2.3/2.4 + ID3v1 兜底，时长按 Xing/Info 优先、CBR 兜底估算
  - WAV：fmt/data 精确时长；M4A/MP4：mvhd 时长 + ilst 标签
- 曲库持久化：`relationalStore`（SQLite），path 唯一键 upsert，mtime 未变化自动跳过；`search_blob` 冗余列 + 索引
- 扫描性能：目录级 3 并发（TaskPool）+ 200 首批量事务，进度可暂停/恢复/取消
- 标签写回：FLAC Vorbis Comment / MP3 ID3v2+APIC / M4A ilst+covr / WAV RIFF ID3（含 APIC）/ DSF ID3 / AIFF ID3 块 / TTA·WavPack·TAK APEv2 / OGG·Opus Vorbis Comment（重排 Ogg 页并重算 CRC），支持标题/歌手/专辑/专辑艺术家/流派/年份/轨号/碟号；tmp 原子替换 + 写后回读校验 + 1 份 `.bak`，>500MB 拒写（T03）
- CUE：外置 CUE 解析（frame=1/75s、多 FILE），逻辑轨入库与按轨播放（T04）
- 健康检查：可解码性/时长/标签一致性/截断标记 + 报告页（T05）
- 会话恢复：队列/曲目/进度/模式/音量，5s 节流 + 关键事件立即落库（T01）
- **Native 播放内核（T06）**：随包 FFmpeg 7.1.1 静态链入 `libseqaudio_core.so`，OH_AudioRenderer 直通，采样率跟随源、>16bit 走 32bit 容器；AVPlayer 保留为降级路径
- **31 段 EQ 与防爆音（T07）**：31 段 1/3 倍频程 peaking biquad（20Hz–20kHz、Q=1.41、±12dB）+ soft-limiter（-0.1dBFS 上限）+ 120ms 淡入淡出，全部在渲染回调内完成；EQ 关闭或全 0dB 时逐字节直通
  - 界面：播放页「EQ」→ 31 段图形均衡器，拖动柱体调增益，内置 平坦/流行/摇滚/古典/爵士 预设 + 自定义保存
  - 上机自检：1kHz +12dB 实测 12.00dB，限幅峰值 0.9885，关闭/全平直通均为 true
- 歌词：lrclib 获取 + 同目录 .lrc/嵌入歌词降级，逐行滚动（T08）
- 封面：多源搜索 + 曲库缓存 + 嵌入 FLAC/MP3/M4A（T09）
- 批量重命名与去重：模板预览/回滚，重复仅标记隔离不自动删除（T10）
- **v1.3 桌面工作台（UI 1:1 复刻）**：品牌条 / 双行操作栏 / 六张统计卡 / 搜索+筛选胶囊 / 曲库表格 + 右侧「详情/参数」/ 播放条 / 日志+歌词+信号路径 / 状态栏；暗色自绘菜单、播放列表/关于/扫描根目录弹层；扫描进度可暂停/继续/取消
- **v1.3.1 核心算法（1:1 复刻桌面版）**：
  - 联网元数据匹配：网易云 → QQ 音乐 → MusicBrainz → iTunes 四源接力检索，source/title/artist/album/duration 加权打分（0.34/0.28/0.20/0.10/0.08）+ 版本惩罚 + 目录专辑 hint；候选弹层可逐条应用，写回标签并补封面/歌词
  - 封面与歌词兜底：MusicBrainz 候选走 Cover Art Archive，候选无图时按「专辑 + 歌手」再搜一次；歌词按 网易云 → QQ → lrclib，全空且歌手可疑时只按歌名重试
  - 一键全自动：逐首匹配 → 同目录专辑多数收敛 → 高置信自动应用 → 低置信进入「待确认」→「全部确认保存」批量写回；可选按模板重命名（CUE 母文件自动跳过，`.lrc` 跟随改名）；CUE 子轨与 ISO 自动跳过并记录
  - 真假无损鉴定：C++ 原生解码 4×4s 采样 + 8192 点 Hann FFT，统计截止/陡度/250Hz 断崖/扩展带/空洞/sfb21/平坦度/有效位深/升频台阶/DSD 噪声整形斜率；判定阈值与桌面版 `core/authenticity.py` 对齐
  - 频谱查看：对数频率轴 + dB 曲线 + 截止/陡度/带宽/有效位深标注，支持 CUE 子轨（分析母文件）
  - 清理假无损：按判定移入 `files/Quarantine/<时间戳>/`，写 manifest，删库不删盘；「包含疑似」可配置（默认开，对齐桌面版）
  - 隔离区管理：列出 Quarantine 条目，可恢复（含 `.lrc` 侧车）、单条删除或清空；恢复后自动扫描入库
  - 桌面工具补齐：批量编辑标签、撤销标签编辑（从 `.bak` 恢复）、撤销本地修改（重读文件标签）、查看候选匹配、确认最佳匹配（低于阈值转手动）、去除标题序号、导出处理报表（CSV）、导出运行日志、批量导出封面、生成文件夹封面、下一首播放/加入队列、按标签重命名本目录、复制完整路径
- **v1.3.7 桌面交互补齐**：播放队列面板（播放条「≡」展开，支持跳播/逐项移除/清空/按当前顺序另存播放列表，载入时从首曲起播、追加不打断播放）、表格列显隐（参数 → 外观，9 列开关持久化，新增年份/无损/来源）、命令面板（操作栏「命令」，29 个动作即时筛选执行）、「更多 → 仅处理选中项 / 查看候选匹配 / 确认匹配 / 撤销本地修改 / 导出运行日志 / 浏览文件导入 / 按标签重命名本目录」、拖拽导入、播放条静音；播放条音量与界面不透明度即时持久化，暂停状态单独显示；同时拆分会话音量（0~1）与设置音量（0~100）键，修复冷启动音量变 0 的问题
- **v1.3.7 UI 收尾**：主窗口默认按屏幕 **84% × 80%** 居中窗口化启动（不再最大化）；隐藏系统标题栏，品牌、状态、界面不透明度、最小化/最大化/关闭合并为一条自绘深色顶栏，支持拖动窗口；界面与迷你窗透明度 **25–100%、默认 95%**，滑块变化带 140ms 缓动；独立迷你播放器按 Windows 版 1:1 重做为 560×320 横向面板，含旋转圆盘封面、三行歌词高亮、上一曲/播放/停止/下一曲、模式、音量/静音、进度、透明度，播放模式图标随「顺序/列表/单曲/随机」实时变化；小窗最大化/关闭均回到主界面
  - CUE 分轨导出：按子轨起点精确裁切，原生 FFmpeg 编码 FLAC/WAV（16/24bit），写 CUE 标签 + 封面 + 歌词侧车，导出后自动入库；可选「在下一轨 INDEX 00 截止」去掉轨尾静音预间隙，预间隙检测到真实音频（隐藏曲/尾奏）时自动保留
  - DSD：DSF/DFF 标签解析、FFmpeg DSD 解码播放、真假 DSD 鉴定
  - SACD ISO：纯 ArkTS 解析 Master TOC / Area TOC / Track List / SACDText（含 2064 字节原始扇区镜像）；按曲目表展开逻辑轨并直接解码播放（优先 2 声道区）；原生 FFmpeg（含上游 SACD demuxer 补丁 + DST raw DSD 解码）支持 DST/DSD 两种帧格式；分轨可导出位完美 DSF（1bit/2.8224MHz，与 sacd_extract 逐字节一致）或 24bit/176.4kHz FLAC/WAV（PCM）；导出后自动入库
- 界面：11 套主题工作台（深空 / 黑胶 / 春 / 夏 / 秋 / 冬 / 海岸 / 暮光 / 太极 / 纸白 / 黑白极简）+ 标签编辑 / 曲库体检 / 批量整理 / 封面 / 歌词 / 31 段 EQ 子页
  - 主题在「参数 → 外观」选择，保存后应用并重建工作台；纸白 / 黑白极简为浅色主题
  - 背景随主题切换：星空 / 黑胶纹 / 花瓣 / 日光 / 落叶 / 雪点 / 海浪 / 暮光 / 太极 / 纸纹 / 网格
- 音频文件来源：
  1. 页面「导入文件」：系统选择器选中的音频复制进沙箱 `files/Music` 后自动扫描
  2. 命令行推送：`hdc file send 本地文件 /data/app/el2/100/base/com.seqaudio.manager/haps/entry/files/Music/`

## 环境

| 项 | 值 |
|---|---|
| DevEco Studio | 26.0.1.290（安装在 `D:\HarmonyOS\DevEcoStudio`） |
| SDK | HarmonyOS 26.0.1 / API 26（内置） |
| 编译目标 | `compatibleSdkVersion: 26.0.0`（纯版本号，不能写成 `7.0.0(26)`；也不能用 26.0.1——PC 镜像是 7.0.0(26.0.0)，高版本 HAP 装不上） |
| 模拟器镜像 | HarmonyOS 7.0.0 PC 版（`D:\HarmonyOS\Emulator\image`） |
| bundleName | `com.seqaudio.manager` |

## 命令行构建与运行（不依赖 IDE）

```bash
export PATH=/d/HarmonyOS/DevEcoStudio/jbr/bin:$PATH
cd /d/SeqAudioManager/harmony-pc/harmony
export DEVECO_SDK_HOME="D:\HarmonyOS\DevEcoStudio\sdk"
# 调试构建
hvigorw.bat assembleHap --mode module -p product=default -p buildMode=debug --no-daemon
# 发布构建（未签名，产物 entry/build/default/outputs/default/entry-default-unsigned.hap）
hvigorw.bat assembleHap --mode module -p product=default -p buildMode=release --no-daemon
# App Pack（未签名，产物 build/outputs/default/harmony-default-unsigned.app）
hvigorw.bat assembleApp --mode project -p product=default -p buildMode=release --no-daemon
# 安装 / 启动（签名后）
hdc install -r SeqAudioManager-v1.3.7-harmony-arm64.hap
hdc shell aa start -a EntryAbility -b com.seqaudio.manager
```

> 发布 HAP 同时包含 `arm64-v8a` 与 `x86_64` 原生库：arm64 鸿蒙 PC/真机使用 arm64 库，x86_64 模拟器使用 x86_64 库。

**Release 签名**：签名材料放在本机 `C:\Users\JS199\.ohos\release\`（`seqaudio-release.p12 / .cer / .p7b`，由 AGC「证书」上传 CSR 生成发布证书、「Profile」创建发布 Profile）。用 SDK 自带 `hap-sign-tool.jar` 对未签名产物签名：

```bash
java -jar "$DEVECO_SDK_HOME/default/openharmony/toolchains/lib/hap-sign-tool.jar" sign-app \
  -mode localSign -keyAlias seqaudioRelease -signAlg SHA256withECDSA \
  -appCertFile seqaudio-release.cer -profileFile seqaudio-release.p7b \
  -keystoreFile seqaudio-release.p12 -keystorePwd ****** -keyPwd ****** \
  -inFile entry-default-unsigned.hap -outFile SeqAudioManager-v1.3.7-harmony-arm64.hap \
  -signCode 1 -compatibleVersion 26
```

`.app` 需先把已签名 HAP 放回包内（重打包 zip），再以 `-inForm zip -signCode 0` 签名。项目内 `.sign/sign-config.json5` 仍是本机调试签名，不影响上述发布签名。

## 发布包（HarmonyOS arm64）

| 项 | 值 |
|---|---|
| 版本 | v1.3.7 |
| 侧载包 | `SeqAudioManager-v1.3.7-harmony-arm64.hap`（可双击 / `hdc install -r` 安装） |
| 上架包 | `SeqAudioManager-v1.3.7-harmony-arm64.app`（App Pack，可直接上传 AppGallery） |
| 签名 | AGC 发布证书（`seqaudio-release`，有效期至 2029-11-27）+ Release Profile（`app_gallery` 分发、无设备白名单） |
| 启动 | `hdc shell aa start -a EntryAbility -b com.seqaudio.manager` |
| 校验 | 同目录 `.sha256` 文件；Profile 类型 `release` |
| 升级提示 | 从调试签名包升级会报 `install sign info inconsistent`（9568332），需先卸载再安装 |

版本说明与安装/升级/卸载验证记录见 [`docs/v1.3.7-发布说明.md`](docs/v1.3.7-发布说明.md)。

> **发布渠道**：Release 由个人开发者通过 GitHub 直接分发，仅供测试与学习，**未完成中国大陆 APP 备案**，不代表已上架应用商店；上架 AppGallery 前需先完成备案。
> 随包 FFmpeg 为 LGPL v2.1+，源码与构建方式见 [`tools/ffmpeg-ohos/README.md`](tools/ffmpeg-ohos/README.md)。

## 目录

```
harmony/entry/src/main/ets/
├── entryability/      EntryAbility（84%×80% 居中窗口化、透明容器、无边框自绘标题栏）
├── miniability/       MiniAbility（独立迷你播放器 UIAbility，560×320 窗口）
├── pages/             Index（桌面工作台）、MiniPlayer（Windows 风格迷你窗）、
│                      TrackEditPage、HealthPage、LibraryToolsPage、CoverPage、LyricsPage、EqPage
├── components/        DesktopUI（品牌条/操作栏/统计卡/表格/详情/播放条/底栏/MiniOverlay 兜底）、
│                      SettingsPanel（参数页）、DesktopDialogs（播放列表/关于/扫描根目录）
├── mini/              MiniBridge（主窗口 ↔ MiniAbility 的 emitter 状态/命令桥）
├── theme/             Tokens（深空色板与语义色）
├── settings/          AppSettings（参数落库）
├── playlist/          PlaylistStore（playlists.json 原子读写）
├── utils/             Util、AppLog、WindowUtil（窗口化/拖动/最大化）
├── scanner/           Scanner（目录遍历+入库）、TagParser（FLAC/MP3/WAV/M4A）
├── player/            PlayerEngine（队列/模式/会话）、NativePlayer（直通+DSP）
├── db/                Db（relationalStore：tracks / settings）
└── model/             Track
```

> v1.3.7 起迷你播放器是独立的第二个 UIAbility（MiniAbility）系统窗口，与主窗口通过 emitter 同步；
> 主窗口最小化时小窗仍可用，小窗最大化/关闭会回到主界面。`MiniOverlay` 仅作为 MiniAbility 启动失败时的兜底。

## 与桌面版的能力映射

| 桌面版（Windows） | 鸿蒙版（v1.3） | 后续 |
|---|---|---|
| PySide6 QWidget | ArkUI 声明式组件 | — |
| 主工作台（品牌/操作/统计/表格/详情/播放/日志/状态栏） | 1:1 复刻；右侧「详情/参数」页签、底部日志/歌词/信号路径三栏 | 主题扩展到 11 套 |
| SQLite 曲库 | `relationalStore` | 已对齐 |
| ffmpeg + sounddevice 播放 | Native：FFmpeg + `OH_AudioRenderer` 直通；AVPlayer 兜底；DSF/DFF/APE 已接 | 已对齐（APE 已支持扫描/标签/播放） |
| 10 段 EQ（桌面实际 31 段） | 31 段 biquad + 限幅 + 淡入淡出 | 已对齐（Q1 结论） |
| ASIO / WASAPI 独占 | 低时延模式直通 | 独占能力待真机确认 |
| CUE 分轨导出 | 原生 `exportSegment`：按起点裁切并编码 FLAC/WAV，写标签/封面/歌词后入库 | 已对齐 |
| mutagen 元数据 | 自研 ArkTS 读写器 | 已对齐（常用格式） |
| 联网元数据匹配 | 网易云/QQ/MusicBrainz/iTunes + 打分/候选弹层/自动应用 | 已对齐 |
| 真假无损 / 频谱 / 清理 | 原生解码 + FFT 判定 + 频谱弹层 + 隔离区 | 已对齐（阈值按桌面样本校准） |
| SACD ISO | 纯 ArkTS 解析 TOC/SACDText + 上游 SACD demuxer/raw DSD 补丁；DST/DSD 直接播放，可导出位完美 DSF | 已对齐（DSF 位完美） |
| APE / DSD 文件 | APE 解码器可覆盖；DSD（DSF/DFF）已接 | 已对齐（APE 扫描 + APEv2 标签 + 播放；标签写入只读） |

## 已知边界

- 鸿蒙沙箱限制：第三方应用无法自由遍历任意外部目录，曲库根目录默认为应用沙箱 `files/Music`，外部音频经系统选择器导入或由文件管理器拖入（拖入仅接受音频文件，文件夹请用「添加文件夹」）。
- 「打开所在文件夹」与跨轨无缝续写暂不提供：前者外部路径对第三方应用不可见，后者需要在原生渲染回调内预载下一曲。
- 扫描范围：FLAC / MP3 / WAV / M4A(ALAC/AAC) / AIFF / OGG / OPUS / AAC / TTA / WavPack / TAK / DSF / DFF / APE / SACD ISO。AIFF / TTA / WavPack / TAK / OGG / Opus 均有内建解析（时长、采样率、位深/声道）与原生 FFmpeg 解码；APE 标签写入只读。
- **SACD ISO**：内置 FFmpeg 已打上游 SACD demuxer 补丁（LGPL）并启用 DST raw DSD 输出；曲目表由纯 ArkTS 读取。2 声道区优先（没有时用多声道区）。分轨默认导出位完美 DSF（1bit/2.8224MHz，LSB-first，含 ID3v2.3 标签/封面），DSD 与 DST 两种来源都已验证（DST 解码帧 CRC 与 FFmpeg FATE 参考一致）；也可选 176.4kHz/24bit FLAC/WAV。SACD 镜像本身不做真假无损鉴定（可先分轨再鉴定）。WASAPI/ASIO 独占不在鸿蒙版范围。
- 多选：已支持 Ctrl 点选切换、Shift 按锚点范围选择；多选时移除/真假鉴定/一键全自动/清理/批量编辑作用于选中集合。
- 主题：11 套主题已接入（含纸白 / 黑白极简两套浅色）；切换保存后重建工作台，冷启动自动恢复。
- v1.3.7 的「迷你播放器」是独立 UIAbility 系统窗口（MiniAbility，560×320），不是主窗口浮层；`MiniOverlay` 仅作启动失败兜底。
- 界面不透明度范围 25–100（默认 95）；主窗口与迷你窗共用同一设置，滑动时带 140ms 缓动。
