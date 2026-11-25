# 音序管家 · HarmonyOS PC 版

本目录是鸿蒙 PC（2in1）版，从 `main` 分支的 v1.8.2 起独立演进。
桌面版（Python + PySide6）与鸿蒙版是两套技术栈，**不是移植，是重写**。

## 当前状态（M2 推进中 · 已跑通模拟器）

M1（v1.1 曲库管理基线，T01–T05）已交付；M2（v1.2 音质与体验）已完成 T06–T10。

- 曲库扫描：递归扫描应用沙箱目录，纯 ArkTS 解析标签与规格
  - FLAC：STREAMINFO（采样率/位深/声道/时长）+ Vorbis Comment（标题/歌手/专辑/流派/年份/音轨号）
  - MP3：ID3v2.2/2.3/2.4 + ID3v1 兜底，时长按 Xing/Info 优先、CBR 兜底估算
  - WAV：fmt/data 精确时长；M4A/MP4：mvhd 时长 + ilst 标签
- 曲库持久化：`relationalStore`（SQLite），path 唯一键 upsert，mtime 未变化自动跳过；`search_blob` 冗余列 + 索引
- 扫描性能：目录级 3 并发（TaskPool）+ 200 首批量事务，进度可暂停/恢复/取消
- 标签写回：FLAC Vorbis Comment / MP3 ID3v2+APIC / M4A ilst+covr，tmp 原子替换 + 1 份 `.bak`，>500MB 拒写（T03）
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
- 界面：深色主题曲库页（搜索/统计/导入/清空/扫描进度）+ 全屏播放页 + 底部迷你播放条 + 整理/健康/封面/歌词/均衡器子页
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
DEVECO_SDK_HOME="D:\HarmonyOS\DevEcoStudio\sdk" hvigorw.bat assembleHap --mode module -p product=default -p buildMode=debug --no-daemon
# 安装（模拟器接受未签名 HAP）
hdc -t 127.0.0.1:5555 install -r entry/build/default/outputs/default/entry-default-unsigned.hap
# 启动
hdc shell aa start -a EntryAbility -b com.seqaudio.manager
```

## 目录

```
harmony/entry/src/main/ets/
├── entryability/      EntryAbility
├── pages/             Index（曲库）、PlayerPage（播放）
├── components/        MiniPlayer（迷你播放条）
├── scanner/           Scanner（目录遍历+入库）、TagParser（FLAC/MP3/WAV/M4A）
├── player/            PlayerEngine（AVPlayer 封装、四种模式）
├── db/                Db（relationalStore：tracks / settings）
├── model/             Track
└── utils/             Util（时长/大小/规格格式化）
```

## 与桌面版的能力映射

| 桌面版（Windows） | 鸿蒙版（M2） | 后续 |
|---|---|---|
| PySide6 QWidget | ArkUI 声明式组件 | — |
| SQLite 曲库 | `relationalStore` | 已对齐 |
| ffmpeg + sounddevice 播放 | Native：FFmpeg + `OH_AudioRenderer` 直通；AVPlayer 兜底 | DSD（T11） |
| 10 段 EQ（桌面实际 31 段） | 31 段 biquad + 限幅 + 淡入淡出 | 已对齐（Q1 结论） |
| ASIO / WASAPI 独占 | 低时延模式直通 | 独占能力待真机确认 |
| CUE 分轨导出 | 外置 CUE 解析 + 按轨播放（导出待 M3） | 分轨导出 |
| mutagen 元数据 | 自研 ArkTS 读写器 | 已对齐（常用格式） |
| APE / DSD 文件 | 解码器可覆盖 APE，DSD 未接 | DSD（T11） |

## 已知边界

- 鸿蒙沙箱限制：第三方应用无法自由遍历任意外部目录，曲库根目录默认为应用沙箱 `files/Music`，外部音频经系统选择器导入。
- APE/TAK/DSD 等格式不在扫描范围；APE 解码器已在 FFmpeg 裁剪清单内，DSD 随 T11 接入。
- 独占输出、DSD、fake FLAC 鉴别、频谱、桌面卡片属 M3（v2.0）；CUE 分轨导出同批交付。
