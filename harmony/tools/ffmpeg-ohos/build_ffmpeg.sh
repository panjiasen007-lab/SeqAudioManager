#!/usr/bin/env bash
# 交叉编译鸿蒙可用的裁剪版 FFmpeg 静态库
#
# 用法：
#   FFMPEG_SRC=/d/HarmonyOS/ffmpeg/FFmpeg bash build_ffmpeg.sh x86_64
#   FFMPEG_SRC=/d/HarmonyOS/ffmpeg/FFmpeg bash build_ffmpeg.sh aarch64
#
# 产物：$OUT_ROOT/static-<arch>/{lib,include}，把 lib 下的 .a 与 include 拷到
#       entry/src/main/cpp/prebuilt/<arch>/lib 与 prebuilt/include 即可。
#
# 依赖：DevEco Studio 自带的 OHOS LLVM 与 sysroot（路径见下方 NATIVE）。
set -eo pipefail

ARCH="${1:-}"
case "$ARCH" in
  x86_64)  TRIPLE="x86_64-linux-ohos" ;;
  aarch64) TRIPLE="aarch64-linux-ohos" ;;
  *) echo "usage: build_ffmpeg.sh x86_64|aarch64"; exit 2 ;;
esac

NATIVE="${OHOS_NATIVE:-D:/HarmonyOS/DevEcoStudio/sdk/default/openharmony/native}"
LLVM="$NATIVE/llvm/bin"
SYSROOT="$NATIVE/sysroot"
SRC="${FFMPEG_SRC:-/d/HarmonyOS/ffmpeg/FFmpeg}"
OUT_ROOT="${OUT_ROOT:-$(cd "$SRC/.." && pwd)}"
OUTWIN="$OUT_ROOT/static-$ARCH"
TMPDIR_POSIX="$OUT_ROOT/tmp"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SACD_PATCH="$SCRIPT_DIR/patches/ffmpeg-7.1.1-sacd-dsf.patch"

mkdir -p "$TMPDIR_POSIX" "$OUT_ROOT/static-$ARCH"
export TMPDIR="$TMPDIR_POSIX"
export TMP="$TMPDIR"
export TEMP="$TMPDIR"

cd "$SRC"
make distclean >/dev/null 2>&1 || true
# MSYS 下 distclean 可能残留对象文件，跨架构会混链，强制清干净
find "$SRC" -type f \( -name '*.o' -o -name '*.d' -o -name '*.ho' \) -delete 2>/dev/null || true
rm -f "$SRC"/ffbuild/config.mak "$SRC"/config.h "$SRC"/config_components.h 2>/dev/null || true

# SACD ISO 解复用 + DST raw DSD 输出补丁（7.1.1 主线尚未包含上游 PR #25936）
if [ -f "$SACD_PATCH" ] && ! grep -q 'CONFIG_SACD_DEMUXER' "$SRC/libavformat/Makefile"; then
  echo "=== APPLY SACD PATCH ==="
  git -C "$SRC" apply --whitespace=nowarn "$SACD_PATCH" || {
    echo "SACD 补丁应用失败：$SACD_PATCH" >&2
    exit 1
  }
fi

./configure \
  --prefix="$OUTWIN" \
  --arch="$ARCH" \
  --cc="$LLVM/clang.exe" \
  --cxx="$LLVM/clang++.exe" \
  --ar="$LLVM/llvm-ar.exe" \
  --ranlib="$LLVM/llvm-ranlib.exe" \
  --strip="$LLVM/llvm-strip.exe" \
  --sysroot="$SYSROOT" \
  --target-os=linux \
  --enable-cross-compile \
  --enable-static --disable-shared \
  --disable-programs --disable-doc --disable-avdevice --disable-avfilter \
  --disable-network --disable-autodetect --disable-asm --disable-debug \
  --disable-iconv --disable-zlib --disable-bzlib --disable-lzma \
  --disable-sdl2 --disable-xlib \
  --disable-everything \
  --enable-avcodec --enable-avformat --enable-avutil --enable-swresample \
  --enable-protocol=file \
  --enable-demuxer=flac,mp3,wav,mov,aac,ape,dsf,iff,sacd,tta,wv,ogg,aiff,tak \
  --enable-decoder=flac,mp3,aac,alac,ape,pcm_s16le,pcm_s24le,pcm_s32le,pcm_f32le,pcm_s16be,pcm_s24be,pcm_s32be,dsd_lsbf,dsd_msbf,dsd_lsbf_planar,dsd_msbf_planar,dst,tta,wavpack,vorbis,opus,tak \
  --enable-encoder=flac,pcm_s16le,pcm_s24le \
  --enable-muxer=flac,wav \
  --enable-parser=mpegaudio,flac,aac,opus,vorbis,tak \
  --extra-cflags="--target=$TRIPLE -D__MUSL__ -fPIC -O2 -fno-stack-protector" \
  --extra-ldflags="--target=$TRIPLE -fuse-ld=lld"

make -j8
make install
echo "=== DONE $ARCH ==="
find "$OUT_ROOT/static-$ARCH/lib" -name "*.a" -exec ls -lh {} \;
