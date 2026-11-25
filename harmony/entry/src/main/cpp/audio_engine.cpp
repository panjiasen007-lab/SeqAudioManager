#include "audio_engine.h"

#include <algorithm>
#include <chrono>
#include <cstring>

#include <hilog/log.h>

#undef LOG_DOMAIN
#undef LOG_TAG
#define LOG_DOMAIN 0xD003
#define LOG_TAG "SeqAudioCore"

namespace {
constexpr int64_t kRingSeconds = 2;
constexpr int kMaxOutSamples = 65536;

int32_t FrameBytesOf(int channels, int bytesPerSample) {
  return channels * bytesPerSample;
}
} // namespace

AudioEngine &AudioEngine::Get() {
  static AudioEngine inst;
  return inst;
}

AudioEngine::~AudioEngine() { Close(); }

int32_t AudioEngine::Open(const std::string &path) {
  std::lock_guard<std::mutex> guard(ctrlMtx_);
  ReleaseAll();
  av_log_set_level(AV_LOG_WARNING);

  int32_t ret = PrepareDecoder(path);
  if (ret < 0) {
    OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "PrepareDecoder failed: %{public}d", ret);
    ReleaseAll();
    return ret;
  }
  ret = PrepareRenderer();
  if (ret != 0) {
    OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "PrepareRenderer failed: %{public}d", ret);
    ReleaseAll();
    return ret;
  }

  const int frameBytes = FrameBytesOf(outChannels_, outBytesPerSample_);
  ringCap_ = static_cast<size_t>(outRate_) * frameBytes * kRingSeconds;
  ring_.assign(ringCap_, 0);
  conv_.resize(static_cast<size_t>(kMaxOutSamples) * frameBytes);
  readPos_ = 0;
  writePos_ = 0;
  avail_ = 0;
  playedFrames_.store(0);
  eof_.store(false);
  seekReqMs_.store(-1);
  interruptHint_.store(0);

  running_.store(true);
  worker_ = std::thread([this]() { DecodeLoop(); });
  opened_.store(true);

  OH_LOG_Print(LOG_APP, LOG_INFO, LOG_DOMAIN, LOG_TAG,
               "opened %{public}s codec=%{public}s %{public}dHz/%{public}dch/%{public}dbit -> %{public}dHz/%{public}dbit",
               path.c_str(), codecName_.c_str(), srcRate_, srcChannels_, srcBits_, outRate_,
               outBytesPerSample_ * 8);
  return 0;
}

int32_t AudioEngine::PrepareDecoder(const std::string &path) {
  int ret = avformat_open_input(&fmt_, path.c_str(), nullptr, nullptr);
  if (ret < 0) {
    return ret;
  }
  ret = avformat_find_stream_info(fmt_, nullptr);
  if (ret < 0) {
    return ret;
  }
  ret = av_find_best_stream(fmt_, AVMEDIA_TYPE_AUDIO, -1, -1, nullptr, 0);
  if (ret < 0) {
    return ret;
  }
  audioIdx_ = ret;
  AVStream *st = fmt_->streams[audioIdx_];
  const AVCodec *codec = avcodec_find_decoder(st->codecpar->codec_id);
  if (codec == nullptr) {
    return AVERROR_DECODER_NOT_FOUND;
  }
  dec_ = avcodec_alloc_context3(codec);
  if (dec_ == nullptr) {
    return AVERROR(ENOMEM);
  }
  ret = avcodec_parameters_to_context(dec_, st->codecpar);
  if (ret < 0) {
    return ret;
  }
  dec_->pkt_timebase = st->time_base;
  ret = avcodec_open2(dec_, codec, nullptr);
  if (ret < 0) {
    return ret;
  }

  codecName_ = codec->name;
  srcRate_ = dec_->sample_rate > 0 ? dec_->sample_rate : 44100;
  if (dec_->ch_layout.nb_channels > 0) {
    srcChannels_ = dec_->ch_layout.nb_channels;
  } else {
    srcChannels_ = 2;
    av_channel_layout_default(&dec_->ch_layout, 2);
  }
  srcBits_ = dec_->bits_per_raw_sample;
  if (srcBits_ <= 0) {
    srcBits_ = av_get_bytes_per_sample(dec_->sample_fmt) * 8;
  }
  if (st->duration > 0) {
    durationMs_ = av_rescale_q(st->duration, st->time_base, AVRational{1, 1000});
  }

  // 输出参数：常见采样率直通（不做重采样），>16bit 走 32bit 容器保真
  static const int kPassthroughRates[] = {44100, 48000, 88200, 96000, 176400, 192000};
  outRate_ = 48000;
  for (int r : kPassthroughRates) {
    if (r == srcRate_) {
      outRate_ = r;
      break;
    }
  }
  outChannels_ = srcChannels_ == 1 ? 1 : 2;
  outBytesPerSample_ = srcBits_ > 16 ? 4 : 2;

  AVSampleFormat outFmt = outBytesPerSample_ == 4 ? AV_SAMPLE_FMT_S32 : AV_SAMPLE_FMT_S16;
  AVChannelLayout outLayout;
  av_channel_layout_default(&outLayout, outChannels_);
  ret = swr_alloc_set_opts2(&swr_, &outLayout, outFmt, outRate_, &dec_->ch_layout, dec_->sample_fmt,
                            srcRate_, 0, nullptr);
  av_channel_layout_uninit(&outLayout);
  if (ret < 0 || swr_ == nullptr) {
    return ret < 0 ? ret : AVERROR(ENOMEM);
  }
  ret = swr_init(swr_);
  if (ret < 0) {
    return ret;
  }

  pkt_ = av_packet_alloc();
  frame_ = av_frame_alloc();
  if (pkt_ == nullptr || frame_ == nullptr) {
    return AVERROR(ENOMEM);
  }
  return 0;
}

int32_t AudioEngine::PrepareRenderer() {
  // 输出参数已确定，先按实际输出格式准备 DSP 链并应用当前 EQ
  dsp_.Prepare(outRate_, outChannels_, outBytesPerSample_);
  {
    float gains[DspChain::kMaxBands];
    bool enabled = false;
    {
      std::lock_guard<std::mutex> lk(eqMtx_);
      enabled = eqEnabled_;
      memcpy(gains, eqGains_, sizeof(gains));
    }
    dsp_.SetEq(enabled, gains, DspChain::kMaxBands);
  }

  OH_AudioStream_Result r = OH_AudioStreamBuilder_Create(&builder_, AUDIOSTREAM_TYPE_RENDERER);
  if (r != AUDIOSTREAM_SUCCESS || builder_ == nullptr) {
    return -1000 - static_cast<int32_t>(r);
  }
  OH_AudioStreamBuilder_SetSamplingRate(builder_, outRate_);
  OH_AudioStreamBuilder_SetChannelCount(builder_, outChannels_);
  OH_AudioStreamBuilder_SetSampleFormat(
      builder_, outBytesPerSample_ == 4 ? AUDIOSTREAM_SAMPLE_S32LE : AUDIOSTREAM_SAMPLE_S16LE);
  OH_AudioStreamBuilder_SetEncodingType(builder_, AUDIOSTREAM_ENCODING_TYPE_RAW);
  OH_AudioStreamBuilder_SetLatencyMode(builder_, AUDIOSTREAM_LATENCY_MODE_NORMAL);
  OH_AudioStreamBuilder_SetRendererInfo(builder_, AUDIOSTREAM_USAGE_MUSIC);
  OH_AudioStreamBuilder_SetRendererWriteDataCallback(builder_, AudioEngine::OnWriteData, this);
  OH_AudioStreamBuilder_SetRendererInterruptCallback(builder_, AudioEngine::OnInterrupt, this);

  r = OH_AudioStreamBuilder_GenerateRenderer(builder_, &renderer_);
  if (r != AUDIOSTREAM_SUCCESS || renderer_ == nullptr) {
    return -2000 - static_cast<int32_t>(r);
  }
  OH_AudioRenderer_SetVolume(renderer_, volume_.load());
  return 0;
}

void AudioEngine::ReleaseAll() {
  if (worker_.joinable()) {
    {
      std::lock_guard<std::mutex> lk(ringMtx_);
      running_.store(false);
    }
    ringCv_.notify_all();
    worker_.join();
  }
  running_.store(false);
  playing_.store(false);
  opened_.store(false);

  if (renderer_ != nullptr) {
    OH_AudioRenderer_Stop(renderer_);
    OH_AudioRenderer_Release(renderer_);
    renderer_ = nullptr;
  }
  if (builder_ != nullptr) {
    OH_AudioStreamBuilder_Destroy(builder_);
    builder_ = nullptr;
  }
  if (swr_ != nullptr) {
    swr_free(&swr_);
  }
  if (pkt_ != nullptr) {
    av_packet_free(&pkt_);
  }
  if (frame_ != nullptr) {
    av_frame_free(&frame_);
  }
  if (dec_ != nullptr) {
    avcodec_free_context(&dec_);
  }
  if (fmt_ != nullptr) {
    avformat_close_input(&fmt_);
  }
  audioIdx_ = -1;
  ring_.clear();
  conv_.clear();
  ringCap_ = 0;
  avail_ = 0;
  readPos_ = 0;
  writePos_ = 0;
  playedFrames_.store(0);
}

int32_t AudioEngine::Start() {
  if (renderer_ == nullptr) {
    return -1;
  }
  int64_t resumeMs = -1;
  if (eof_.load()) {
    resumeMs = 0;
  }
  if (resumeMs >= 0) {
    seekReqMs_.store(resumeMs);
  }
  dsp_.FadeIn(); // 起播/续播从 0 淡入，消除首帧爆音
  OH_AudioStream_Result r = OH_AudioRenderer_Start(renderer_);
  if (r != AUDIOSTREAM_SUCCESS) {
    return -2 - static_cast<int32_t>(r);
  }
  playing_.store(true);
  ringCv_.notify_all();
  return 0;
}

int32_t AudioEngine::Pause() {
  if (renderer_ == nullptr) {
    return -1;
  }
  // 先做 ~120ms 淡出再暂停渲染器，避免暂停瞬间的满幅截断爆音
  if (playing_.load()) {
    dsp_.FadeOut();
    for (int i = 0; i < 60 && !dsp_.FadeOutDone(); ++i) {
      std::this_thread::sleep_for(std::chrono::milliseconds(3));
    }
  }
  OH_AudioStream_Result r = OH_AudioRenderer_Pause(renderer_);
  playing_.store(false);
  return r == AUDIOSTREAM_SUCCESS ? 0 : -3 - static_cast<int32_t>(r);
}

int32_t AudioEngine::Stop() {
  if (renderer_ == nullptr) {
    return -1;
  }
  OH_AudioRenderer_Stop(renderer_);
  OH_AudioRenderer_Flush(renderer_);
  playing_.store(false);
  ClearRing();
  playedFrames_.store(0);
  eof_.store(false);
  seekReqMs_.store(0);
  ringCv_.notify_all();
  return 0;
}

void AudioEngine::Close() {
  std::lock_guard<std::mutex> guard(ctrlMtx_);
  ReleaseAll();
}

int32_t AudioEngine::Seek(int64_t ms) {
  if (fmt_ == nullptr) {
    return -1;
  }
  seekReqMs_.store(ms < 0 ? 0 : ms);
  dsp_.RequestReset(); // 清滤波器历史，避免跳转后拖尾
  ringCv_.notify_all();
  return 0;
}

int32_t AudioEngine::SetEq(bool enabled, const float *gainsDb, int count) {
  float gains[DspChain::kMaxBands];
  for (int i = 0; i < DspChain::kMaxBands; ++i) {
    gains[i] = (gainsDb != nullptr && i < count) ? gainsDb[i] : 0.0f;
  }
  {
    std::lock_guard<std::mutex> lk(eqMtx_);
    eqEnabled_ = enabled;
    memcpy(eqGains_, gains, sizeof(gains));
  }
  dsp_.SetEq(enabled, gains, DspChain::kMaxBands);
  OH_LOG_Print(LOG_APP, LOG_INFO, LOG_DOMAIN, LOG_TAG,
               "eq %{public}s bands=%{public}d", enabled ? "on" : "off", dsp_.ActiveBands());
  return 0;
}

int32_t AudioEngine::SetVolume(float volume) {
  float v = volume < 0.0f ? 0.0f : (volume > 1.0f ? 1.0f : volume);
  volume_.store(v);
  if (renderer_ != nullptr) {
    OH_AudioRenderer_SetVolume(renderer_, v);
  }
  return 0;
}

int64_t AudioEngine::PositionMs() const {
  if (outRate_ <= 0) {
    return 0;
  }
  return playedFrames_.load() * 1000 / outRate_;
}

int64_t AudioEngine::DurationMs() const { return durationMs_; }

int32_t AudioEngine::TakeInterrupt() {
  int32_t v = interruptHint_.exchange(0);
  return v;
}

std::string AudioEngine::InfoJson() const {
  bool eqOn = false;
  {
    std::lock_guard<std::mutex> lk(eqMtx_);
    eqOn = eqEnabled_;
  }
  char buf[640];
  int n = snprintf(buf, sizeof(buf),
                   "{\"codec\":\"%s\",\"sampleRate\":%d,\"channels\":%d,\"bits\":%d,"
                   "\"durationMs\":%lld,\"outRate\":%d,\"outChannels\":%d,\"outBits\":%d,"
                   "\"eq\":%s,\"eqBands\":%d,\"fadeMs\":%d}",
                   codecName_.c_str(), srcRate_, srcChannels_, srcBits_,
                   static_cast<long long>(durationMs_), outRate_, outChannels_,
                   outBytesPerSample_ * 8, eqOn ? "true" : "false", dsp_.ActiveBands(),
                   dsp_.FadeMs());
  if (n <= 0) {
    return std::string("{}");
  }
  return std::string(buf, static_cast<size_t>(n < static_cast<int>(sizeof(buf)) ? n : sizeof(buf) - 1));
}

void AudioEngine::ClearRing() {
  std::lock_guard<std::mutex> lk(ringMtx_);
  readPos_ = 0;
  writePos_ = 0;
  avail_ = 0;
  memset(ring_.data(), 0, ring_.size());
}

void AudioEngine::PushSamples(const uint8_t *data, int32_t bytes) {
  if (bytes <= 0 || ringCap_ == 0) {
    return;
  }
  std::unique_lock<std::mutex> lk(ringMtx_);
  ringCv_.wait(lk, [&]() {
    return !running_.load() || seekReqMs_.load() >= 0 ||
           static_cast<size_t>(bytes) <= ringCap_ - avail_;
  });
  if (!running_.load() || seekReqMs_.load() >= 0) {
    return; // 丢弃本次数据，交由主循环处理跳转
  }
  size_t n = static_cast<size_t>(bytes);
  size_t first = std::min(n, ringCap_ - writePos_);
  memcpy(ring_.data() + writePos_, data, first);
  if (n > first) {
    memcpy(ring_.data(), data + first, n - first);
  }
  writePos_ = (writePos_ + n) % ringCap_;
  avail_ += n;
}

void AudioEngine::HandleSeek(int64_t ms) {
  ClearRing();
  playedFrames_.store(ms * outRate_ / 1000);
  if (fmt_ == nullptr || dec_ == nullptr || audioIdx_ < 0) {
    return;
  }
  int64_t ts = ms * AV_TIME_BASE / 1000;
  int ret = av_seek_frame(fmt_, -1, ts, AVSEEK_FLAG_BACKWARD);
  if (ret < 0) {
    ret = avformat_seek_file(fmt_, audioIdx_, INT64_MIN, ms, INT64_MAX, 0);
  }
  if (ret >= 0) {
    avcodec_flush_buffers(dec_);
    swr_convert(swr_, nullptr, 0, nullptr, 0);
    eof_.store(false);
  } else {
    OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "seek %{public}lld failed: %{public}d",
                 static_cast<long long>(ms), ret);
  }
}

bool AudioEngine::DecodeOnePacket(bool &eofReached) {
  eofReached = false;
  int ret = av_read_frame(fmt_, pkt_);
  if (ret == AVERROR_EOF) {
    if (!eof_.exchange(true)) {
      avcodec_send_packet(dec_, nullptr);
      DrainDecoder();
    }
    eofReached = true;
    return true;
  }
  if (ret < 0) {
    return false;
  }
  if (pkt_->stream_index != audioIdx_) {
    av_packet_unref(pkt_);
    return true;
  }
  if (avcodec_send_packet(dec_, pkt_) < 0) {
    av_packet_unref(pkt_);
    return true;
  }
  av_packet_unref(pkt_);
  DrainDecoder();
  return true;
}

void AudioEngine::DrainDecoder() {
  const int frameBytes = FrameBytesOf(outChannels_, outBytesPerSample_);
  while (true) {
    int r = avcodec_receive_frame(dec_, frame_);
    if (r == AVERROR(EAGAIN) || r == AVERROR_EOF || r < 0) {
      break;
    }
    int outSamples = swr_get_out_samples(swr_, frame_->nb_samples);
    if (outSamples > 0) {
      if (static_cast<size_t>(outSamples) * frameBytes > conv_.size()) {
        outSamples = static_cast<int>(conv_.size() / frameBytes);
      }
      uint8_t *out = conv_.data();
      int got = swr_convert(swr_, &out, outSamples, (const uint8_t **)frame_->extended_data,
                            frame_->nb_samples);
      if (got > 0) {
        PushSamples(conv_.data(), got * frameBytes);
      }
    }
    av_frame_unref(frame_);
    if (seekReqMs_.load() >= 0) {
      break;
    }
  }
}

void AudioEngine::DecodeLoop() {
  while (running_.load()) {
    int64_t st = seekReqMs_.exchange(-1);
    if (st >= 0) {
      HandleSeek(st);
    }
    bool eofReached = false;
    bool ok = DecodeOnePacket(eofReached);
    if (!ok) {
      OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "decode stopped unexpectedly");
      break;
    }
    if (eofReached) {
      std::unique_lock<std::mutex> lk(ringMtx_);
      ringCv_.wait_for(lk, std::chrono::milliseconds(100),
                       [&]() { return !running_.load() || seekReqMs_.load() >= 0; });
    }
  }
}

OH_AudioData_Callback_Result AudioEngine::OnWriteData(OH_AudioRenderer *renderer, void *userData,
                                                     void *audioData, int32_t audioDataSize) {
  (void)renderer;
  AudioEngine *eng = static_cast<AudioEngine *>(userData);
  if (eng == nullptr) {
    memset(audioData, 0, static_cast<size_t>(audioDataSize));
    return AUDIO_DATA_CALLBACK_RESULT_VALID;
  }
  return eng->RenderTo(audioData, audioDataSize);
}

OH_AudioData_Callback_Result AudioEngine::RenderTo(void *audioData, int32_t audioDataSize) {
  uint8_t *out = static_cast<uint8_t *>(audioData);
  int32_t n = 0;
  {
    std::lock_guard<std::mutex> lk(ringMtx_);
    if (avail_ > 0) {
      n = static_cast<int32_t>(std::min(static_cast<size_t>(audioDataSize), avail_));
      size_t first = std::min(static_cast<size_t>(n), ringCap_ - readPos_);
      memcpy(out, ring_.data() + readPos_, first);
      if (static_cast<size_t>(n) > first) {
        memcpy(out + first, ring_.data(), static_cast<size_t>(n) - first);
      }
      readPos_ = (readPos_ + static_cast<size_t>(n)) % ringCap_;
      avail_ -= static_cast<size_t>(n);
    }
  }
  if (n < audioDataSize) {
    memset(out + n, 0, static_cast<size_t>(audioDataSize - n));
  }
  const int frameBytes = FrameBytesOf(outChannels_, outBytesPerSample_);
  if (n > 0 && frameBytes > 0) {
    // EQ/限幅/渐变在渲染回调内完成：参数改动下一个回调即生效（~10–20ms）
    dsp_.Process(out, n / frameBytes);
  }
  if (frameBytes > 0) {
    playedFrames_.fetch_add(n / frameBytes);
  }
  if (avail_ < ringCap_ / 4) {
    ringCv_.notify_all();
  }
  return AUDIO_DATA_CALLBACK_RESULT_VALID;
}

void AudioEngine::OnInterrupt(OH_AudioRenderer *renderer, void *userData,
                             OH_AudioInterrupt_ForceType type, OH_AudioInterrupt_Hint hint) {
  (void)renderer;
  (void)type;
  AudioEngine *eng = static_cast<AudioEngine *>(userData);
  if (eng == nullptr) {
    return;
  }
  switch (hint) {
  case AUDIOSTREAM_INTERRUPT_HINT_PAUSE:
    eng->interruptHint_.store(2);
    eng->playing_.store(false);
    break;
  case AUDIOSTREAM_INTERRUPT_HINT_STOP:
    eng->interruptHint_.store(3);
    eng->playing_.store(false);
    break;
  case AUDIOSTREAM_INTERRUPT_HINT_RESUME:
    eng->interruptHint_.store(1);
    break;
  default:
    break;
  }
}
