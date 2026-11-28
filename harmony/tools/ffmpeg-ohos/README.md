# FFmpeg for HarmonyOS（Native 播放内核依赖）

`entry/src/main/cpp/prebuilt/` 下的静态库由本目录脚本产出，**不是**第三方预编译包。

## 版本与来源

| 项 | 值 |
|---|---|
| FFmpeg | 7.1.1（`n7.1.1`）+ SACD demuxer 补丁（上游 PR #25936，LGPL） |
| 源码 | `git clone --depth 1 --branch n7.1.1 https://github.com/FFmpeg/FFmpeg.git` |
| 补丁 | `patches/ffmpeg-7.1.1-sacd-dsf.patch`（SACD demuxer + `dst` 解码器 raw DSD 输出） |
| 许可证 | LGPL v2.1+（构建时未开启 `--enable-gpl` / `--enable-nonfree`） |
| 工具链 | DevEco Studio 自带 OHOS LLVM + `native/sysroot` |
| 产物 | `libavformat.a` `libavcodec.a` `libswresample.a` `libavutil.a`（x86_64 与 aarch64 各一套） |

## 构建

```bash
git clone --depth 1 --branch n7.1.1 https://github.com/FFmpeg/FFmpeg.git /d/HarmonyOS/ffmpeg/FFmpeg

FFMPEG_SRC=/d/HarmonyOS/ffmpeg/FFmpeg bash build_ffmpeg.sh x86_64
FFMPEG_SRC=/d/HarmonyOS/ffmpeg/FFmpeg bash build_ffmpeg.sh aarch64
```

首次构建约 3 分钟/架构。产物在 `$FFMPEG_SRC/../static-<arch>/`。

## 同步进工程

```bash
cp static-x86_64/include/lib{avcodec,avformat,avutil,swresample}/*.h  entry/src/main/cpp/prebuilt/include/...
cp static-<arch>/lib/lib{avcodec,avformat,avutil,swresample}.a        entry/src/main/cpp/prebuilt/<arch>/lib/
```

`prebuilt/include` 只保留这 4 个库的头文件；`avconfig.h` 两架构一致，可共用。

## 裁剪范围

- 解复用：`flac mp3 wav mov aac ape dsf iff sacd`
- 解码：`flac mp3 aac alac ape pcm_s16le pcm_s24le pcm_s32le pcm_f32le dsd_lsbf dsd_msbf dsd_lsbf_planar dsd_msbf_planar dst`
- 解析器：`mpegaudio flac aac`
- 协议：仅 `file`
- 关闭：编码器、封装器、滤镜、avdevice、网络、zlib/bzlib/lzma/iconv、汇编优化

`sacd` 解复用器来自上游 PR #25936（尚未并入 7.1.1 主线），构建脚本会在源码树中检查
`CONFIG_SACD_DEMUXER`，未打补丁时自动 `git apply patches/ffmpeg-7.1.1-sacd-dsf.patch`。
`iff` 负责 DSDIFF（DFF）与 DST 帧，`dst` 负责 DST 解码（默认输出 352.8kHz FLT PCM）；
补丁额外让 `dst` 在 `request_sample_fmt = AV_SAMPLE_FMT_U8` 时输出原始 DSD 字节
（每字节 8 个 1bit 样本、声道交织），供 SACD → DSF 位完美分轨使用。

## 注意

- **不要用动态库**：带版本号的 `libavcodec.so.61` 在 HAP 打包与 `DT_NEEDED` 解析上有风险，静态链入 `libseqaudio_core.so` 更稳。
- `--disable-asm` 关闭了 SIMD，纯音频解码性能足够，换来无 nasm/yasm 依赖。
- 升级 FFmpeg 时需同步核对 `audio_engine.cpp` 用到的 API（7.x 起头文件需自行 `extern "C"` 包裹，见 `audio_engine.h`）。
