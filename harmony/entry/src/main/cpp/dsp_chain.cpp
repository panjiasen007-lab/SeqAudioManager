#include "dsp_chain.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <limits>
#include <vector>

namespace {

// 与桌面版 EQ_BANDS_HZ 完全一致：31 段 1/3 倍频程
constexpr double kBandHz[DspChain::kMaxBands] = {
    20,   25,   31,   40,   50,   63,   80,   100,  125,  160,  200,  250,  315,  400,  500,  630,
    800,  1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000, 12500, 16000, 20000};

constexpr double kQ = 1.41;
constexpr float kLimitThreshold = 0.89125094f;       // -1 dBFS 起控
constexpr float kLimitCeiling = 0.98855310f;         // -0.1 dBFS 渐近上限，保证有可测余量
constexpr double kPi = 3.14159265358979323846;

inline float SoftLimit(float x) {
  const float a = fabsf(x);
  if (a <= kLimitThreshold) {
    return x;
  }
  const float over = (a - kLimitThreshold) / (kLimitCeiling - kLimitThreshold);
  const float y = kLimitThreshold + (kLimitCeiling - kLimitThreshold) * tanhf(over);
  return x < 0.0f ? -y : y;
}

double Rms(const std::vector<int16_t> &buf, int channels, int fromFrame, int toFrame) {
  double sum = 0.0;
  int count = 0;
  for (int i = fromFrame; i < toFrame; ++i) {
    for (int c = 0; c < channels; ++c) {
      const double v = static_cast<double>(buf[static_cast<size_t>(i) * channels + c]) / 32768.0;
      sum += v * v;
      ++count;
    }
  }
  return count > 0 ? sqrt(sum / count) : 0.0;
}

} // namespace

void DspChain::Prepare(int sampleRate, int channels, int bytesPerSample) {
  rate_ = sampleRate > 0 ? sampleRate : 48000;
  channels_ = channels == 1 ? 1 : 2;
  bytesPerSample_ = bytesPerSample == 4 ? 4 : 2;
  fadeStep_ = 1.0f / static_cast<float>(std::max(1, fadeMs_ * rate_ / 1000));
  RequestReset();
}

void DspChain::SetFadeMs(int ms) {
  fadeMs_ = ms < 10 ? 10 : (ms > 1000 ? 1000 : ms);
  fadeStep_ = 1.0f / static_cast<float>(std::max(1, fadeMs_ * rate_ / 1000));
}

void DspChain::RequestReset() { resetReq_.fetch_add(1, std::memory_order_release); }

void DspChain::ApplyPendingReset() {
  const uint32_t req = resetReq_.load(std::memory_order_acquire);
  if (req == resetSeen_) {
    return;
  }
  resetSeen_ = req;
  memset(z1_, 0, sizeof(z1_));
  memset(z2_, 0, sizeof(z2_));
}

void DspChain::SetEq(bool enabled, const float *gainsDb, int count) {
  std::lock_guard<std::mutex> lk(coeffMtx_);
  uint32_t mask = 0;
  for (int b = 0; b < kMaxBands; ++b) {
    float g = (gainsDb != nullptr && b < count) ? gainsDb[b] : 0.0f;
    if (g < -12.0f) {
      g = -12.0f;
    } else if (g > 12.0f) {
      g = 12.0f;
    }
    if (fabsf(g) < 0.05f) {
      coeff_[b] = Coeff{};
      continue;
    }
    const double A = pow(10.0, static_cast<double>(g) / 40.0);
    double f0 = kBandHz[b];
    const double nyquist = static_cast<double>(rate_) * 0.49;
    if (f0 > nyquist) {
      f0 = nyquist;
    }
    const double w0 = 2.0 * kPi * f0 / static_cast<double>(rate_);
    const double alpha = sin(w0) / (2.0 * kQ);
    const double a0 = 1.0 + alpha / A;
    Coeff k;
    k.b0 = static_cast<float>((1.0 + alpha * A) / a0);
    k.b1 = static_cast<float>((-2.0 * cos(w0)) / a0);
    k.b2 = static_cast<float>((1.0 - alpha * A) / a0);
    k.a1 = static_cast<float>((-2.0 * cos(w0)) / a0);
    k.a2 = static_cast<float>((1.0 - alpha / A) / a0);
    coeff_[b] = k;
    mask |= (1u << b);
  }
  activeMask_ = mask;
  eqActive_.store(mask != 0, std::memory_order_release);
  eqOn_.store(enabled, std::memory_order_release);
  // 参数变化后清一次历史，避免旧状态在新系数下产生瞬态
  resetReq_.fetch_add(1, std::memory_order_release);
}

int DspChain::ActiveBands() const {
  std::lock_guard<std::mutex> lk(coeffMtx_);
  int n = 0;
  for (uint32_t m = activeMask_; m != 0; m >>= 1) {
    n += static_cast<int>(m & 1u);
  }
  return n;
}

void DspChain::FadeIn() {
  fadeMode_.store(1, std::memory_order_relaxed);
  fadeOutDone_.store(true, std::memory_order_relaxed);
  fadeReq_.fetch_add(1, std::memory_order_release);
}

void DspChain::FadeOut() {
  fadeOutDone_.store(false, std::memory_order_relaxed);
  fadeMode_.store(2, std::memory_order_relaxed);
  fadeReq_.fetch_add(1, std::memory_order_release);
}

void DspChain::Process(uint8_t *data, int32_t frames) {
  if (data == nullptr || frames <= 0) {
    return;
  }
  const uint32_t req = fadeReq_.load(std::memory_order_acquire);
  if (req != fadeSeen_) {
    fadeSeen_ = req;
    fadeState_ = fadeMode_.load(std::memory_order_relaxed);
    if (fadeState_ == 1) {
      gain_ = 0.0f;
    }
  }
  ApplyPendingReset();

  const bool needEq = eqOn_.load(std::memory_order_relaxed) && eqActive_.load(std::memory_order_relaxed);
  const bool needFade = fadeState_ != 0 || gain_ != 1.0f;
  if (!needEq && !needFade) {
    return; // 直通，逐字节不变
  }

  std::lock_guard<std::mutex> lk(coeffMtx_);
  if (bytesPerSample_ == 4) {
    ProcessTyped<int32_t>(data, frames, 1.0f / 2147483648.0f, 2147483647.0);
  } else {
    ProcessTyped<int16_t>(data, frames, 1.0f / 32768.0f, 32767.0);
  }
}

template <typename Sample>
void DspChain::ProcessTyped(uint8_t *data, int32_t frames, float scale, double outScale) {
  Sample *pcm = reinterpret_cast<Sample *>(data);
  const int ch = channels_;
  const bool eq = eqOn_.load(std::memory_order_relaxed) && activeMask_ != 0;
  const uint32_t mask = activeMask_;
  const Coeff *coeffs = coeff_;
  const float step = fadeStep_;
  const double lo = static_cast<double>(std::numeric_limits<Sample>::min());
  const double hi = static_cast<double>(std::numeric_limits<Sample>::max());
  float gain = gain_;
  int state = fadeState_;

  for (int32_t i = 0; i < frames; ++i) {
    if (state == 1) {
      gain += step;
      if (gain >= 1.0f) {
        gain = 1.0f;
        state = 0;
      }
    } else if (state == 2) {
      gain -= step;
      if (gain <= 0.0f) {
        gain = 0.0f;
        state = 0;
        fadeOutDone_.store(true, std::memory_order_relaxed);
      }
    }
    const float g = gain;
    for (int cc = 0; cc < ch; ++cc) {
      const size_t idx = static_cast<size_t>(i) * ch + cc;
      float x = static_cast<float>(pcm[idx]) * scale;
      if (eq) {
        for (int b = 0; b < kMaxBands; ++b) {
          if ((mask & (1u << b)) == 0) {
            continue;
          }
          const Coeff &k = coeffs[b];
          const float y = k.b0 * x + z1_[b][cc];
          z1_[b][cc] = k.b1 * x - k.a1 * y + z2_[b][cc];
          z2_[b][cc] = k.b2 * x - k.a2 * y;
          x = y;
        }
        x = SoftLimit(x);
      }
      if (g != 1.0f) {
        x *= g;
      }
      double v = static_cast<double>(x) * outScale;
      if (v > hi) {
        v = hi;
      } else if (v < lo) {
        v = lo;
      }
      pcm[idx] = static_cast<Sample>(llround(v));
    }
  }
  gain_ = gain;
  fadeState_ = state;
}

int DspChain::SelfTest(char *json, int cap) {
  constexpr int kRate = 48000;
  constexpr int kChannels = 2;
  constexpr int kFrames = kRate; // 1 秒

  int band = -1;
  for (int i = 0; i < kMaxBands; ++i) {
    if (fabs(kBandHz[i] - 1000.0) < 0.5) {
      band = i;
      break;
    }
  }
  if (band < 0) {
    return snprintf(json, static_cast<size_t>(cap), "{\"error\":\"band not found\"}");
  }

  float gains[kMaxBands] = {};
  gains[band] = 12.0f;
  float flat[kMaxBands] = {};

  auto render = [&](const float *gainSet, float amplitude, bool eq, std::vector<int16_t> *out) {
    DspChain chain;
    chain.Prepare(kRate, kChannels, 2);
    chain.SetEq(eq, gainSet, kMaxBands);
    std::vector<int16_t> buf(static_cast<size_t>(kFrames) * kChannels);
    for (int i = 0; i < kFrames; ++i) {
      const float s = amplitude * sinf(2.0f * static_cast<float>(kPi) * 1000.0f * i / kRate);
      const int16_t v = static_cast<int16_t>(lrintf(s * 32767.0f));
      buf[static_cast<size_t>(i) * kChannels] = v;
      buf[static_cast<size_t>(i) * kChannels + 1] = v;
    }
    // 按 480 帧（10ms）分块处理，模拟真实回调粒度
    for (int off = 0; off < kFrames; off += 480) {
      const int n = std::min(480, kFrames - off);
      chain.Process(reinterpret_cast<uint8_t *>(buf.data() + static_cast<size_t>(off) * kChannels), n);
    }
    if (out != nullptr) {
      *out = buf;
    }
  };

  // 与渲染输入逐样本比对，确认是否真的没被 DSP 动过
  auto matchesInput = [&](const std::vector<int16_t> &buf, float amplitude) {
    for (int i = 0; i < kFrames; ++i) {
      const int16_t v = static_cast<int16_t>(
          lrintf(amplitude * sinf(2.0f * static_cast<float>(kPi) * 1000.0f * i / kRate) * 32767.0f));
      for (int c = 0; c < kChannels; ++c) {
        if (buf[static_cast<size_t>(i) * kChannels + c] != v) {
          return false;
        }
      }
    }
    return true;
  };

  std::vector<int16_t> eqOut;
  std::vector<int16_t> refOut;
  render(gains, 0.05f, true, &eqOut);
  render(gains, 0.05f, false, &refOut);
  const double refRms = Rms(refOut, kChannels, kRate / 2, kFrames);
  const double eqRms = Rms(eqOut, kChannels, kRate / 2, kFrames);
  const double gainDb = refRms > 0.0 ? 20.0 * log10(eqRms / refRms) : 0.0;

  std::vector<int16_t> hotOut;
  render(gains, 0.5f, true, &hotOut);
  double peak = 0.0;
  for (int i = kRate / 2; i < kFrames; ++i) {
    for (int c = 0; c < kChannels; ++c) {
      const double v = fabs(static_cast<double>(hotOut[static_cast<size_t>(i) * kChannels + c]) / 32768.0);
      if (v > peak) {
        peak = v;
      }
    }
  }

  // 开启但全 0dB：同样必须逐字节直通（不经过 float 往返）
  std::vector<int16_t> flatOut;
  render(flat, 0.05f, true, &flatOut);
  const bool flatPassthrough = matchesInput(flatOut, 0.05f);

  // 关闭 EQ：必须与输入完全一致
  std::vector<int16_t> offOut;
  render(flat, 0.05f, false, &offOut);
  const bool passthrough = matchesInput(offOut, 0.05f);

  return snprintf(json, static_cast<size_t>(cap),
                  "{\"band\":%d,\"gainDb\":%.2f,\"peak\":%.4f,\"passthrough\":%s,\"flat\":%s,\"bands\":%d}", band,
                  gainDb, peak, passthrough ? "true" : "false", flatPassthrough ? "true" : "false", kMaxBands);
}
