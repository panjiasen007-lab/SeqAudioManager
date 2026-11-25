#ifndef SEQAUDIO_AUDIO_ENGINE_H
#define SEQAUDIO_AUDIO_ENGINE_H

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <mutex>
#include <string>
#include <thread>
#include <vector>

extern "C" {
#include "libavcodec/avcodec.h"
#include "libavformat/avformat.h"
#include "libavutil/channel_layout.h"
#include "libavutil/opt.h"
#include "libswresample/swresample.h"
}

#include "ohaudio/native_audiorenderer.h"
#include "ohaudio/native_audiostreambuilder.h"

// 解码 -> 重采样 -> 环形缓冲 -> OH_AudioRenderer 直通输出
class AudioEngine {
public:
  static AudioEngine &Get();

  // 打开文件并准备渲染器；返回 0 成功，负值为 FFmpeg/OHOS 错误码
  int32_t Open(const std::string &path);
  int32_t Start();
  int32_t Pause();
  int32_t Stop();
  int32_t Seek(int64_t ms);
  int32_t SetVolume(float volume);
  int64_t PositionMs() const;
  int64_t DurationMs() const;
  int32_t TakeInterrupt(); // 返回并清空一次中断提示（0 无，1 恢复，2 暂停，3 停止）
  std::string InfoJson() const;
  bool IsOpen() const { return opened_.load(); }
  void Close();

private:
  AudioEngine() = default;
  ~AudioEngine();

  int32_t PrepareDecoder(const std::string &path);
  int32_t PrepareRenderer();
  void ReleaseAll();
  void DecodeLoop();
  bool DecodeOnePacket(bool &eofReached);
  void DrainDecoder();
  void PushSamples(const uint8_t *data, int32_t bytes);
  void HandleSeek(int64_t ms);
  void ClearRing();
  OH_AudioData_Callback_Result RenderTo(void *audioData, int32_t audioDataSize);
  static OH_AudioData_Callback_Result OnWriteData(OH_AudioRenderer *renderer, void *userData,
                                                 void *audioData, int32_t audioDataSize);
  static void OnInterrupt(OH_AudioRenderer *renderer, void *userData,
                          OH_AudioInterrupt_ForceType type, OH_AudioInterrupt_Hint hint);

  // ---- FFmpeg ----
  AVFormatContext *fmt_ = nullptr;
  AVCodecContext *dec_ = nullptr;
  SwrContext *swr_ = nullptr;
  AVPacket *pkt_ = nullptr;
  AVFrame *frame_ = nullptr;
  int audioIdx_ = -1;
  int64_t durationMs_ = 0;
  int srcRate_ = 0;
  int srcChannels_ = 0;
  int srcBits_ = 0;
  std::string codecName_;

  // ---- OHOS 渲染器 ----
  OH_AudioStreamBuilder *builder_ = nullptr;
  OH_AudioRenderer *renderer_ = nullptr;
  int outRate_ = 48000;
  int outChannels_ = 2;
  int outBytesPerSample_ = 2;

  // ---- 环形缓冲 ----
  std::vector<uint8_t> ring_;
  std::vector<uint8_t> conv_;
  size_t ringCap_ = 0;
  size_t readPos_ = 0;
  size_t writePos_ = 0;
  size_t avail_ = 0;
  mutable std::mutex ringMtx_;
  std::condition_variable ringCv_;

  std::thread worker_;
  std::atomic<bool> running_{false};
  std::atomic<bool> playing_{false};
  std::atomic<bool> opened_{false};
  std::atomic<bool> eof_{false};
  std::atomic<int64_t> playedFrames_{0};
  std::atomic<int64_t> seekReqMs_{-1};
  std::atomic<int32_t> interruptHint_{0};
  std::atomic<float> volume_{1.0f};

  std::mutex ctrlMtx_; // 串行化 Open/Close/Seek
};

#endif // SEQAUDIO_AUDIO_ENGINE_H
