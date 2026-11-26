#ifndef SEQAUDIO_DSD_EXPORT_H
#define SEQAUDIO_DSD_EXPORT_H

#include <cstdint>
#include <string>

// SACD → DSF 位完美分轨：按 TOC 帧边界取 DSD 原始字节（含 DST 解码），
// 按 DSF 规范写入 1bit/2.8224MHz 音频；标签由 ArkTS 侧追加 ID3。
class DsdExport {
public:
  static int32_t ExportSacdSegment(const std::string &src, int64_t startMs, int64_t durationMs,
                                   const std::string &dst);
};

#endif
