// SACD ISO → DSF 位完美分轨。
// 音频来源：
//   * 未压缩 DSD（SACD 3-in-14 / 3-in-16）：直接取 demuxer 的 DSD 字节；
//   * DST：调用打过 raw DSD 补丁的 FFmpeg dst 解码器，输出每帧 4704 字节/声道。
// 输出为 DSF 1bit/2.8224MHz（LSB-first），块大小 4096，末尾可追加 ID3（ArkTS 侧写入）。
#include "dsd_export.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

#include <hilog/log.h>

extern "C" {
#include "libavcodec/avcodec.h"
#include "libavformat/avformat.h"
#include "libavutil/channel_layout.h"
#include "libavutil/mathematics.h"
#include "libavutil/opt.h"
}

#include "audio_stream_select.h"

#undef LOG_DOMAIN
#undef LOG_TAG
#define LOG_DOMAIN 0xD003
#define LOG_TAG "SeqAudioDsd"

namespace {

constexpr int kFramesPerSecond = 75;
constexpr uint32_t kDsfBlockSize = 4096;
constexpr uint32_t kDsfSampleRate = 2822400;
constexpr int kMaxChannels = 6;

uint32_t ChannelType(int channels) {
  switch (channels) {
  case 1:
    return 1; // mono
  case 2:
    return 2; // stereo
  case 3:
    return 3;
  case 4:
    return 4; // quad
  case 5:
    return 6; // 5 channels
  case 6:
    return 7; // 5.1
  default:
    return 2;
  }
}

void BuildReverseTable(uint8_t table[256]) {
  for (int i = 0; i < 256; ++i) {
    uint8_t v = static_cast<uint8_t>(i);
    uint8_t r = 0;
    for (int b = 0; b < 8; ++b) {
      r = static_cast<uint8_t>((r << 1) | (v & 1));
      v = static_cast<uint8_t>(v >> 1);
    }
    table[i] = r;
  }
}

class DsfWriter {
public:
  bool Open(const std::string &path, int channels) {
    if (channels < 1 || channels > kMaxChannels) {
      return false;
    }
    file_ = std::fopen(path.c_str(), "wb");
    if (file_ == nullptr) {
      return false;
    }
    channels_ = channels;
    blocks_.assign(static_cast<size_t>(channels), std::vector<uint8_t>(kDsfBlockSize, 0));
    BuildReverseTable(reverse_);
    if (!WriteHeader()) {
      return false;
    }
    return true;
  }

  // interleaved: 每声道 bytesPerChannel 个 DSD 字节（MSB-first），声道交织
  bool WriteFrame(const uint8_t *interleaved, int bytesPerChannel) {
    if (interleaved == nullptr || bytesPerChannel <= 0) {
      return false;
    }
    audioBytesPerChannel_ += static_cast<uint64_t>(bytesPerChannel);
    for (int i = 0; i < bytesPerChannel; ++i) {
      const uint8_t *src = interleaved + static_cast<size_t>(i) * channels_;
      for (int ch = 0; ch < channels_; ++ch) {
        blocks_[ch][fill_] = reverse_[src[ch]];
      }
      if (++fill_ == static_cast<int>(kDsfBlockSize)) {
        if (!FlushBlock()) {
          return false;
        }
      }
    }
    return true;
  }

  bool Close() {
    if (file_ == nullptr || audioBytesPerChannel_ == 0) {
      return false;
    }
    if (fill_ > 0) {
      for (int ch = 0; ch < channels_; ++ch) {
        std::memset(blocks_[ch].data() + fill_, 0, kDsfBlockSize - static_cast<size_t>(fill_));
      }
      if (!FlushBlock()) {
        return false;
      }
    }
    const uint64_t sampleCount = audioBytesPerChannel_ * 8;
    const uint64_t fileSize = 92 + payloadBytes_;
    if (!PatchU64(12, fileSize) || !PatchU64(20, 0) || !PatchU64(64, sampleCount) ||
        !PatchU64(84, 12 + payloadBytes_)) {
      return false;
    }
    if (std::fflush(file_) != 0) {
      return false;
    }
    const bool ok = std::fclose(file_) == 0;
    file_ = nullptr;
    return ok;
  }

  void Abort() {
    if (file_ != nullptr) {
      std::fclose(file_);
      file_ = nullptr;
    }
  }

private:
  bool WriteHeader() {
    return WriteTag("DSD ", 4) && WriteU64(28) && WriteU64(0) && WriteU64(0) &&
           WriteTag("fmt ", 4) && WriteU64(52) && WriteU32(1) && WriteU32(0) &&
           WriteU32(ChannelType(channels_)) && WriteU32(static_cast<uint32_t>(channels_)) &&
           WriteU32(kDsfSampleRate) && WriteU32(1) && WriteU64(0) && WriteU32(kDsfBlockSize) &&
           WriteU32(0) && WriteTag("data", 4) && WriteU64(0);
  }

  bool WriteTag(const char *tag, size_t len) {
    return std::fwrite(tag, 1, len, file_) == len;
  }

  bool WriteU32(uint32_t value) {
    uint8_t b[4] = {static_cast<uint8_t>(value & 0xff), static_cast<uint8_t>((value >> 8) & 0xff),
                    static_cast<uint8_t>((value >> 16) & 0xff),
                    static_cast<uint8_t>((value >> 24) & 0xff)};
    return std::fwrite(b, 1, sizeof(b), file_) == sizeof(b);
  }

  bool WriteU64(uint64_t value) {
    uint8_t b[8];
    for (int i = 0; i < 8; ++i) {
      b[i] = static_cast<uint8_t>((value >> (8 * i)) & 0xff);
    }
    return std::fwrite(b, 1, sizeof(b), file_) == sizeof(b);
  }

  bool PatchU64(long offset, uint64_t value) {
    if (!WriteU64At(offset, value)) {
      return false;
    }
    return std::fseek(file_, 0, SEEK_END) == 0;
  }

  bool WriteU64At(long offset, uint64_t value) {
    if (std::fseek(file_, offset, SEEK_SET) != 0) {
      return false;
    }
    return WriteU64(value);
  }

  bool FlushBlock() {
    for (int ch = 0; ch < channels_; ++ch) {
      if (!WriteTag(reinterpret_cast<const char *>(blocks_[ch].data()), kDsfBlockSize)) {
        return false;
      }
    }
    payloadBytes_ += static_cast<uint64_t>(kDsfBlockSize) * channels_;
    fill_ = 0;
    return true;
  }

  FILE *file_ = nullptr;
  int channels_ = 0;
  int fill_ = 0;
  uint64_t audioBytesPerChannel_ = 0;
  uint64_t payloadBytes_ = 0;
  std::vector<std::vector<uint8_t>> blocks_;
  uint8_t reverse_[256] = {0};
};

int32_t Fail(const char *stage, int32_t code) {
  OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "sacd dsf fail: %{public}s (%{public}d)", stage,
               code);
  return code;
}

} // namespace

int32_t DsdExport::ExportSacdSegment(const std::string &src, int64_t startMs, int64_t durationMs,
                                    const std::string &dst) {
  if (src.empty() || dst.empty()) {
    return -1;
  }
  av_log_set_level(AV_LOG_ERROR);

  AVFormatContext *in = nullptr;
  AVCodecContext *dec = nullptr;
  AVPacket *pkt = nullptr;
  AVFrame *frame = nullptr;
  DsfWriter writer;
  int32_t result = 0;

  auto cleanup = [&]() {
    if (frame != nullptr) {
      av_frame_free(&frame);
    }
    if (pkt != nullptr) {
      av_packet_free(&pkt);
    }
    if (dec != nullptr) {
      avcodec_free_context(&dec);
    }
    if (in != nullptr) {
      avformat_close_input(&in);
    }
  };

  if (avformat_open_input(&in, src.c_str(), nullptr, nullptr) < 0) {
    return Fail("open_input", -2);
  }
  if (avformat_find_stream_info(in, nullptr) < 0) {
    cleanup();
    return Fail("stream_info", -3);
  }
  const int idx = SelectAudioStream(in);
  if (idx < 0) {
    cleanup();
    return Fail("no_audio_stream", -4);
  }
  DiscardOtherAudioStreams(in, idx);
  AVStream *st = in->streams[idx];
  const AVCodecID codecId = st->codecpar->codec_id;
  const bool isDst = codecId == AV_CODEC_ID_DST;
  if (!isDst && codecId != AV_CODEC_ID_DSD_MSBF) {
    cleanup();
    return Fail("unsupported_codec", -5);
  }
  const int channels = st->codecpar->ch_layout.nb_channels;
  if (channels < 1 || channels > kMaxChannels) {
    cleanup();
    return Fail("bad_channels", -6);
  }
  if (!writer.Open(dst, channels)) {
    cleanup();
    return Fail("open_dst", -7);
  }

  if (isDst) {
    const AVCodec *codec = avcodec_find_decoder(AV_CODEC_ID_DST);
    if (codec == nullptr) {
      writer.Abort();
      cleanup();
      return Fail("no_dst_decoder", -8);
    }
    dec = avcodec_alloc_context3(codec);
    if (dec == nullptr || avcodec_parameters_to_context(dec, st->codecpar) < 0) {
      writer.Abort();
      cleanup();
      return Fail("decoder_alloc", -9);
    }
    dec->pkt_timebase = st->time_base;
    dec->request_sample_fmt = AV_SAMPLE_FMT_U8; // raw DSD 字节（补丁）
    if (avcodec_open2(dec, codec, nullptr) < 0 || dec->sample_fmt != AV_SAMPLE_FMT_U8) {
      writer.Abort();
      cleanup();
      return Fail("dst_raw_unavailable", -10);
    }
    frame = av_frame_alloc();
  }
  pkt = av_packet_alloc();
  if (pkt == nullptr || (isDst && frame == nullptr)) {
    writer.Abort();
    cleanup();
    return Fail("alloc", -11);
  }

  const int64_t startFrame = std::llround(static_cast<double>(startMs) * kFramesPerSecond / 1000.0);
  int64_t wantFrames = std::llround(static_cast<double>(durationMs) * kFramesPerSecond / 1000.0);
  if (wantFrames <= 0) {
    wantFrames = 1;
  }
  const int64_t startPts = av_rescale_q(startFrame, AVRational{1, kFramesPerSecond}, st->time_base);
  const int64_t endPts =
      av_rescale_q(startFrame + wantFrames, AVRational{1, kFramesPerSecond}, st->time_base);
  if (av_seek_frame(in, idx, startPts, AVSEEK_FLAG_BACKWARD) < 0) {
    av_seek_frame(in, idx, 0, AVSEEK_FLAG_BACKWARD);
  }
  if (dec != nullptr) {
    avcodec_flush_buffers(dec);
  }

  int64_t framesWritten = 0;
  auto take = [&](const uint8_t *data, int bytesPerChannel) -> bool {
    if (bytesPerChannel <= 0 || framesWritten >= wantFrames) {
      return true;
    }
    if (!writer.WriteFrame(data, bytesPerChannel)) {
      return false;
    }
    ++framesWritten;
    return true;
  };

  bool readFailed = false;
  while (framesWritten < wantFrames) {
    const int r = av_read_frame(in, pkt);
    if (r == AVERROR_EOF) {
      break;
    }
    if (r < 0) {
      readFailed = true;
      break;
    }
    if (pkt->stream_index != idx) {
      av_packet_unref(pkt);
      continue;
    }
    const int64_t pts = pkt->pts;
    if (pts != AV_NOPTS_VALUE && pts >= endPts) {
      av_packet_unref(pkt);
      break;
    }
    if (pts != AV_NOPTS_VALUE && pts < startPts) {
      av_packet_unref(pkt);
      continue;
    }
    if (!isDst) {
      const int bytesPerChannel = pkt->size / channels;
      if (!take(pkt->data, bytesPerChannel)) {
        result = -12;
        av_packet_unref(pkt);
        break;
      }
      av_packet_unref(pkt);
      continue;
    }
    if (avcodec_send_packet(dec, pkt) < 0) {
      av_packet_unref(pkt);
      continue;
    }
    const int64_t packetPts = pkt->pts;
    av_packet_unref(pkt);
    while (framesWritten < wantFrames) {
      const int rr = avcodec_receive_frame(dec, frame);
      if (rr == AVERROR(EAGAIN) || rr == AVERROR_EOF) {
        break;
      }
      if (rr < 0) {
        result = -13;
        break;
      }
      int64_t framePts = frame->best_effort_timestamp;
      if (framePts == AV_NOPTS_VALUE) {
        framePts = frame->pts;
      }
      if (framePts == AV_NOPTS_VALUE) {
        framePts = packetPts;
      }
      if (framePts != AV_NOPTS_VALUE && framePts < startPts) {
        av_frame_unref(frame);
        continue;
      }
      if (framePts != AV_NOPTS_VALUE && framePts >= endPts) {
        av_frame_unref(frame);
        break;
      }
      if (frame->format != AV_SAMPLE_FMT_U8 || frame->nb_samples <= 0 ||
          !take(frame->data[0], frame->nb_samples)) {
        result = -14;
        av_frame_unref(frame);
        break;
      }
      av_frame_unref(frame);
    }
    if (result != 0) {
      break;
    }
  }
  if (result == 0 && readFailed) {
    result = -15;
  }
  if (result == 0 && framesWritten == 0) {
    result = -16;
  }
  if (result == 0 && !writer.Close()) {
    result = -17;
  }
  if (result != 0) {
    writer.Abort();
    std::remove(dst.c_str());
  }
  OH_LOG_Print(LOG_APP, LOG_INFO, LOG_DOMAIN, LOG_TAG,
               "sacd dsf %{public}s ch=%{public}d frames=%{public}lld/%{public}lld codec=%{public}s",
               result == 0 ? "ok" : "fail", channels, static_cast<long long>(framesWritten),
               static_cast<long long>(wantFrames), isDst ? "dst" : "dsd");
  cleanup();
  return result;
}
