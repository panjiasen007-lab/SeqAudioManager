# 音序管家 · HarmonyOS PC 版

本目录是鸿蒙 PC（2in1）版，从 `main` 分支的 v1.8.2 起独立演进。
桌面版（Python + PySide6）与鸿蒙版是两套技术栈，**不是移植，是重写**。

## 当前状态（v1.0 · 已跑通模拟器）

- 曲库扫描：递归扫描应用沙箱目录，纯 ArkTS 解析标签与规格
  - FLAC：STREAMINFO（采样率/位深/声道/时长）+ Vorbis Comment（标题/歌手/专辑/流派/年份/音轨号）
  - MP3：ID3v2.2/2.3/2.4 + ID3v1 兜底，时长按 Xing/Info 优先、CBR 兜底估算
  - WAV：fmt/data 精确时长；M4A/MP4：mvhd 时长 + ilst 标签
- 曲库持久化：`relationalStore`（SQLite），path 唯一键 upsert，mtime 未变化自动跳过
- 播放：`AVPlayer`，播放/暂停/seek/上一首/下一首，四种模式（顺序/列表循环/单曲循环/随机），音量
- 界面：深色主题曲库页（搜索/统计/导入/清空/扫描进度）+ 全屏播放页 + 底部迷你播放条
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

| 桌面版（Windows） | 鸿蒙版 v1.0 | 后续 |
|---|---|---|
| PySide6 QWidget | ArkUI 声明式组件 | — |
| SQLite 曲库 | `relationalStore` | 已对齐 |
| ffmpeg + sounddevice 播放 | `AVPlayer` | EQ/DSD 需 Native C++ + NAPI |
| ASIO / WASAPI 独占 | 暂无（系统混音） | `AudioRenderer` 低延迟模式调研 |
| CUE 整轨分轨 | 暂无 | 规划中 |
| mutagen 元数据 | 自研 ArkTS 解析器 | 已对齐（常用格式） |
| APE / DSD 文件 | 不支持（未纳入扫描后缀） | 视 AVPlayer/自研解码能力 |

## 已知边界

- 鸿蒙沙箱限制：第三方应用无法自由遍历任意外部目录，曲库根目录默认为应用沙箱 `files/Music`，外部音频经系统选择器导入。
- APE/TAK/DSD 等格式不在 v1.0 扫描与播放范围。
- CUE 整轨、10 段 EQ、独占输出、迷你窗旋转封面：属于后续版本（对齐桌面版路线不变）。
