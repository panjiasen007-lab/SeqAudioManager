#ifndef SEQAUDIO_AUDIO_ANALYSIS_H
#define SEQAUDIO_AUDIO_ANALYSIS_H

#include <cstdint>
#include <string>

// 离线音频分析：真假无损 / 频谱 / DSD 判据所需的底层指标。
// 只读文件、不启动渲染器；返回 JSON 字符串给 ArkTS 判定层。
class AudioAnalysis {
public:
  static std::string Analyze(const std::string &path);
  static int32_t ExportSegment(const std::string &src, int64_t startMs, int64_t durationMs,
                               const std::string &format, int32_t bitDepth, const std::string &dst);
};

#endif // SEQAUDIO_AUDIO_ANALYSIS_H
