# 音序管家 · HarmonyOS PC 版

本目录是鸿蒙 PC（2in1）版工程骨架，从 `main` 分支的 v1.8.2 起独立演进。
桌面版（Python + PySide6）与鸿蒙版是两套技术栈，**不是移植，是重写**。

## 环境

| 项 | 值 |
|---|---|
| DevEco Studio | 26.0.1.290（安装在 `D:\HarmonyOS\DevEcoStudio`） |
| SDK | HarmonyOS 26.0.1 / API 26（内置） |
| 编译目标 | `compatibleSdkVersion: 7.0.0(26)` |
| 模拟器镜像 | HarmonyOS 7.0.0 PC 版（`D:\HarmonyOS\Emulator\image`，9.1 GB） |
| bundleName | `com.seqaudio.manager` |

## 目录

```
harmony/
├── AppScope/            应用级配置（bundleName、版本号、图标）
├── entry/               主模块（Stage 模型）
│   └── src/main/ets/    ArkTS 源码（entryability / pages）
└── build-profile.json5  构建配置（API 26）
```

## 三步路线

1. **POC：能出声** — 本地目录扫描 + 播放一首 FLAC（`AudioRenderer` PCM 直出，验证低延迟链路）
2. **骨架：像样** — 主窗口三栏（曲库树 / 播放列表 / 播放器）、深色主题、旋转封面
3. **能力：对齐桌面版** — 10 段 EQ、独占输出、DSD/DoP（Native C++ + NAPI 自研 DSP）、CUE 分轨

## 与桌面版的能力映射

| 桌面版（Windows） | 鸿蒙版 |
|---|---|
| ASIO / WASAPI 独占 | `AudioRenderer`（低延迟模式） |
| PySide6 QWidget | ArkUI 声明式组件 |
| SQLite 曲库 | `relationalStore` |
| mutagen 元数据解析 | 自研 C++ 解析器 + NAPI |
| BASS / 自研 DSP | `AudioRenderer` + Native C++ DSP |

## 打开方式

DevEco Studio → 打开项目 → 选择本目录 `harmony/` → 首次同步会自动下载 hvigor 依赖。
