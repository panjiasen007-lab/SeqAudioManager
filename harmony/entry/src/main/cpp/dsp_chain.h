#ifndef SEQAUDIO_DSP_CHAIN_H
#define SEQAUDIO_DSP_CHAIN_H

#include <atomic>
#include <cstdint>
#include <mutex>

// 31 段 1/3 倍频程 peaking biquad 级联 + soft-limiter + 增益渐变。
// 设计约束：
//  - Process() 只在音频渲染线程调用，内部零分配；
//  - SetEq() 可在任意线程调用，系数切换与处理用短临界区互斥（微秒级）；
//  - EQ 关闭时 Process() 直接返回，PCM 保持逐字节直通（bit-perfect 前提）。
class DspChain {
public:
  static constexpr int kMaxBands = 31;
  static constexpr int kMaxChannels = 2;

  // 采样率/声道/位宽确定后调用（控制线程，渲染器启动前）
  void Prepare(int sampleRate, int channels, int bytesPerSample);

  // 请求清空滤波器历史（跳转/换曲），由渲染线程在下次回调生效
  void RequestReset();

  // 设置 EQ：enabled=false 时完全旁路；gainsDb 单位 dB，长度不足补 0
  void SetEq(bool enabled, const float *gainsDb, int count);
  bool EqEnabled() const { return eqOn_.load(std::memory_order_relaxed); }
  int ActiveBands() const;

  // 渲染线程：原地处理一段交错 PCM，frames 为真实音频帧数（不含补零）
  void Process(uint8_t *data, int32_t frames);

  // 增益渐变（音频线程推进）
  void FadeIn();
  void FadeOut();
  bool FadeOutDone() const { return fadeOutDone_.load(std::memory_order_relaxed); }
  int FadeMs() const { return fadeMs_; }
  void SetFadeMs(int ms);

  // 内建数值自检：1kHz +12dB 增益与限幅上限，返回 JSON，便于上机核验
  static int SelfTest(char *json, int cap);

private:
  struct Coeff {
    float b0 = 1.0f;
    float b1 = 0.0f;
    float b2 = 0.0f;
    float a1 = 0.0f;
    float a2 = 0.0f;
  };

  template <typename Sample>
  void ProcessTyped(uint8_t *data, int32_t frames, float scale, double outScale);

  void ApplyPendingReset();

  int rate_ = 48000;
  int channels_ = 2;
  int bytesPerSample_ = 2;
  int fadeMs_ = 120;

  mutable std::mutex coeffMtx_;
  Coeff coeff_[kMaxBands];
  uint32_t activeMask_ = 0;
  std::atomic<bool> eqOn_{false};
  std::atomic<bool> eqActive_{false}; // 至少一段增益非 0，否则同样走直通

  std::atomic<uint32_t> resetReq_{0};
  uint32_t resetSeen_{0};

  // 每段每声道两级状态（转置直接 II 型）
  float z1_[kMaxBands][kMaxChannels] = {};
  float z2_[kMaxBands][kMaxChannels] = {};

  // 渐变
  std::atomic<uint32_t> fadeReq_{0};
  std::atomic<int> fadeMode_{0}; // 1 淡入 2 淡出
  std::atomic<bool> fadeOutDone_{true};
  uint32_t fadeSeen_{0};
  int fadeState_ = 0;
  float gain_ = 1.0f;
  float fadeStep_ = 1.0f / 5760.0f;
};

#endif // SEQAUDIO_DSP_CHAIN_H
