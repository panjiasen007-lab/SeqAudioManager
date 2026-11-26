#ifndef SEQAUDIO_AUDIO_STREAM_SELECT_H
#define SEQAUDIO_AUDIO_STREAM_SELECT_H

#include <cstring>

// 选择要解码的音频流。
// SACD ISO 有两个节目（多声道区在前、立体声区在后），统一优先 2 声道；
// 其他格式沿用 FFmpeg 的最佳流选择。
static inline int SelectAudioStream(AVFormatContext *fmt) {
  int best = av_find_best_stream(fmt, AVMEDIA_TYPE_AUDIO, -1, -1, nullptr, 0);
  if (best < 0) {
    return best;
  }
  if (fmt->iformat != nullptr && fmt->iformat->name != nullptr &&
      std::strcmp(fmt->iformat->name, "sacd") == 0) {
    for (unsigned i = 0; i < fmt->nb_streams; ++i) {
      const AVCodecParameters *par = fmt->streams[i]->codecpar;
      if (par->codec_type == AVMEDIA_TYPE_AUDIO && par->ch_layout.nb_channels == 2) {
        return static_cast<int>(i);
      }
    }
  }
  return best;
}

// 丢弃未选中的音频流，避免 SACD 把另一个声道区也解出来
static inline void DiscardOtherAudioStreams(AVFormatContext *fmt, int keep) {
  for (unsigned i = 0; i < fmt->nb_streams; ++i) {
    if (static_cast<int>(i) != keep) {
      fmt->streams[i]->discard = AVDISCARD_ALL;
    }
  }
}

// DSD（1bit/2.8224MHz）解码后是 352.8kHz PCM；导出统一降到 176.4kHz，
// 保持 44.1kHz 整数族，同时把体积控制在可接受范围。
static inline bool IsDsdCodec(enum AVCodecID id) {
  return id == AV_CODEC_ID_DST || id == AV_CODEC_ID_DSD_MSBF || id == AV_CODEC_ID_DSD_LSBF ||
         id == AV_CODEC_ID_DSD_MSBF_PLANAR || id == AV_CODEC_ID_DSD_LSBF_PLANAR;
}

#endif
