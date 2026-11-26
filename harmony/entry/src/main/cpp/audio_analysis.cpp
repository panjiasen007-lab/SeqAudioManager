#include "audio_analysis.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <string>
#include <vector>

extern "C" {
#include "libavcodec/avcodec.h"
#include "libavformat/avformat.h"
#include "libavutil/channel_layout.h"
#include "libavutil/mathematics.h"
#include "libavutil/opt.h"
#include "libswresample/swresample.h"
}

#include <hilog/log.h>
#undef LOG_DOMAIN
#undef LOG_TAG
#define LOG_DOMAIN 0xD003
#define LOG_TAG "SeqAudioExport"

#include "audio_stream_select.h"

namespace {

constexpr int kFftSize = 8192;
constexpr int kMaxSpectrumPoints = 1024;

struct Spectrum {
  std::vector<double> freqs;
  std::vector<double> db;
};

struct SegmentData {
  std::vector<int32_t> pcm; // interleaved stereo s32
  int rate = 0;
  int frames = 0;
};

struct DsdMetrics {
  double slope = 0.0;
  double ultra = 0.0;
  double baseCutoff = 0.0;
  double baseSilence = 0.0;
  double ultraBody = 0.0;
  double pcmSource = 0.0;
  double pcmStep = 0.0;
  double pcmAbove = 0.0;
};

void Fft(std::vector<double> &re, std::vector<double> &im) {
  const int n = static_cast<int>(re.size());
  for (int i = 1, j = 0; i < n; ++i) {
    int bit = n >> 1;
    for (; j & bit; bit >>= 1) {
      j ^= bit;
    }
    j ^= bit;
    if (i < j) {
      std::swap(re[i], re[j]);
      std::swap(im[i], im[j]);
    }
  }
  for (int len = 2; len <= n; len <<= 1) {
    const double ang = -2.0 * 3.14159265358979323846 / len;
    const double wRe = std::cos(ang);
    const double wIm = std::sin(ang);
    for (int i = 0; i < n; i += len) {
      double curRe = 1.0;
      double curIm = 0.0;
      for (int j = 0; j < len / 2; ++j) {
        const int u = i + j;
        const int v = i + j + len / 2;
        const double vr = re[v] * curRe - im[v] * curIm;
        const double vi = re[v] * curIm + im[v] * curRe;
        re[v] = re[u] - vr;
        im[v] = im[u] - vi;
        re[u] += vr;
        im[u] += vi;
        const double nRe = curRe * wRe - curIm * wIm;
        curIm = curRe * wIm + curIm * wRe;
        curRe = nRe;
      }
    }
  }
}

double Median(std::vector<double> values) {
  if (values.empty()) {
    return 0.0;
  }
  const size_t mid = values.size() / 2;
  std::nth_element(values.begin(), values.begin() + mid, values.end());
  return values[mid];
}

double MedianRange(const Spectrum &s, double lowHz, double highHz) {
  std::vector<double> vals;
  for (size_t i = 0; i < s.freqs.size(); ++i) {
    if (s.freqs[i] >= lowHz && s.freqs[i] <= highHz) {
      vals.push_back(s.db[i]);
    }
  }
  return Median(vals);
}

Spectrum AverageSpectrum(const SegmentData &seg) {
  Spectrum out;
  if (seg.frames <= 0 || seg.rate <= 0 || seg.pcm.empty()) {
    return out;
  }
  const int channels = 2;
  std::vector<double> acc(kFftSize / 2 + 1, 0.0);
  std::vector<int> counts(kFftSize / 2 + 1, 0);
  std::vector<double> window(kFftSize);
  for (int i = 0; i < kFftSize; ++i) {
    window[i] = 0.5 - 0.5 * std::cos(2.0 * 3.14159265358979323846 * i / (kFftSize - 1));
  }
  std::vector<double> re(kFftSize), im(kFftSize);
  int usedFrames = 0;
  for (int start = 0; start + kFftSize <= seg.frames; start += kFftSize / 2) {
    bool silent = true;
    for (int i = 0; i < kFftSize; ++i) {
      const int32_t l = seg.pcm[(static_cast<size_t>(start + i) * channels)];
      const int32_t r = seg.pcm[(static_cast<size_t>(start + i) * channels) + 1];
      if (l != 0 || r != 0) {
        silent = false;
      }
    }
    if (silent) {
      continue;
    }
    std::vector<double> powerL(kFftSize / 2 + 1, 0.0);
    std::vector<double> powerR(kFftSize / 2 + 1, 0.0);
    for (int ch = 0; ch < 2; ++ch) {
      for (int i = 0; i < kFftSize; ++i) {
        const int32_t v = seg.pcm[(static_cast<size_t>(start + i) * channels) + ch];
        re[i] = static_cast<double>(v) / 2147483648.0 * window[i];
        im[i] = 0.0;
      }
      Fft(re, im);
      for (int i = 0; i <= kFftSize / 2; ++i) {
        const double p = re[i] * re[i] + im[i] * im[i];
        if (ch == 0) {
          powerL[i] = p;
        } else {
          powerR[i] = p;
        }
      }
    }
    for (int i = 0; i <= kFftSize / 2; ++i) {
      const double p = std::max(powerL[i], powerR[i]);
      if (p > 0.0) {
        acc[i] += 10.0 * std::log10(p + 1e-20);
        counts[i]++;
      }
    }
    usedFrames++;
  }
  if (usedFrames == 0) {
    return out;
  }
  for (int i = 0; i <= kFftSize / 2; ++i) {
    const double db = counts[i] > 0 ? acc[i] / counts[i] : -200.0;
    out.freqs.push_back(static_cast<double>(i) * seg.rate / kFftSize);
    out.db.push_back(db);
  }
  // 9 bin 平滑，抑制单帧毛刺
  if (out.db.size() > 9) {
    std::vector<double> smooth(out.db.size(), 0.0);
    for (size_t i = 0; i < out.db.size(); ++i) {
      double sum = 0.0;
      int n = 0;
      for (int k = -4; k <= 4; ++k) {
        const int j = static_cast<int>(i) + k;
        if (j >= 0 && j < static_cast<int>(out.db.size())) {
          sum += out.db[j];
          n++;
        }
      }
      smooth[i] = n > 0 ? sum / n : out.db[i];
    }
    out.db.swap(smooth);
  }
  return out;
}

double FindCutoff(const Spectrum &s, double maxHz, bool medianRef,
                  double *steepness, double *steepOct) {
  if (steepness != nullptr) {
    *steepness = 0.0;
  }
  if (steepOct != nullptr) {
    *steepOct = 0.0;
  }
  if (s.freqs.empty()) {
    return 0.0;
  }
  size_t limit = s.freqs.size();
  if (maxHz > 0) {
    limit = 0;
    while (limit < s.freqs.size() && s.freqs[limit] <= maxHz) {
      limit++;
    }
    limit = std::max<size_t>(5, std::min(limit, s.freqs.size()));
  }
  double threshold = 0.0;
  if (medianRef) {
    threshold = MedianRange(s, 300.0, 3000.0) - 62.0;
  } else {
    double peak = -200.0;
    for (size_t i = 0; i < limit; ++i) {
      peak = std::max(peak, s.db[i]);
    }
    threshold = peak - 65.0;
  }
  int run = 0;
  size_t cutoffIndex = 0;
  for (int i = static_cast<int>(limit) - 1; i >= 0; --i) {
    if (s.db[i] > threshold) {
      run++;
      if (run >= 5) {
        cutoffIndex = static_cast<size_t>(i) + 4;
        break;
      }
    } else {
      run = 0;
    }
  }
  cutoffIndex = std::min(cutoffIndex, limit - 1);
  const double cutoff = s.freqs[cutoffIndex];
  std::vector<double> segDb;
  std::vector<double> segHz;
  for (size_t i = 0; i < s.freqs.size(); ++i) {
    if (s.freqs[i] >= std::max(1000.0, cutoff - 2000.0) && s.freqs[i] <= cutoff + 200.0) {
      segDb.push_back(s.db[i]);
      segHz.push_back(s.freqs[i]);
    }
  }
  if (segDb.size() >= 6 && steepness != nullptr) {
    const double first = Median(std::vector<double>(segDb.begin(), segDb.begin() + 3));
    const double last = Median(std::vector<double>(segDb.end() - 3, segDb.end()));
    const double drop = first - last;
    const double spanKHz = std::max(0.001, (segHz.back() - segHz.front()) / 1000.0);
    *steepness = drop / spanKHz;
    if (steepOct != nullptr && segHz.front() > 0 && segHz.back() > segHz.front()) {
      const double octaves = std::log2(segHz.back() / segHz.front());
      if (octaves > 0.02) {
        *steepOct = drop / octaves;
      }
    }
  }
  return cutoff;
}

double WallStep(const Spectrum &s, double cutoff) {
  if (cutoff <= 0 || s.freqs.size() < 8) {
    return 0.0;
  }
  std::vector<double> db;
  std::vector<double> hz;
  const double lo = cutoff - 1000.0;
  const double hi = cutoff + 1000.0;
  for (size_t i = 0; i < s.freqs.size(); ++i) {
    if (s.freqs[i] >= lo && s.freqs[i] <= hi) {
      db.push_back(s.db[i]);
      hz.push_back(s.freqs[i]);
    }
  }
  if (db.size() < 8) {
    return 0.0;
  }
  const double binHz = hz[1] - hz[0];
  if (binHz <= 0) {
    return 0.0;
  }
  const int cell = std::max(2, static_cast<int>(std::round(250.0 / binHz)));
  std::vector<double> cells;
  for (size_t i = 0; i + cell <= db.size(); i += cell) {
    cells.push_back(Median(std::vector<double>(db.begin() + i, db.begin() + i + cell)));
  }
  double maxDrop = 0.0;
  for (size_t i = 0; i + 1 < cells.size(); ++i) {
    maxDrop = std::max(maxDrop, cells[i] - cells[i + 1]);
  }
  return maxDrop;
}

double BandMedianRel(const Spectrum &s, double lowHz, double highHz, double refDb) {
  const double med = MedianRange(s, lowHz, highHz);
  if (med <= -199.0) {
    return -200.0;
  }
  return med - refDb;
}

double SpectralFlatness(const Spectrum &s, double lowHz, double highHz) {
  double logSum = 0.0;
  double sum = 0.0;
  int n = 0;
  for (size_t i = 0; i < s.freqs.size(); ++i) {
    if (s.freqs[i] < lowHz || s.freqs[i] > highHz) {
      continue;
    }
    const double p = std::pow(10.0, s.db[i] / 10.0);
    logSum += std::log(p + 1e-20);
    sum += p;
    n++;
  }
  if (n < 32 || sum <= 0.0) {
    return 0.0;
  }
  const double geo = std::exp(logSum / n);
  const double arith = sum / n;
  return arith > 0.0 ? geo / arith : 0.0;
}

double SpectralHoles(const Spectrum &s) {
  std::vector<double> vals;
  std::vector<double> hz;
  for (size_t i = 0; i < s.freqs.size(); ++i) {
    if (s.freqs[i] >= 4000.0 && s.freqs[i] <= 16000.0) {
      vals.push_back(s.db[i]);
      hz.push_back(s.freqs[i]);
    }
  }
  if (vals.size() < 64) {
    return 0.0;
  }
  int count = 0;
  int i = 0;
  while (i < static_cast<int>(vals.size())) {
    const int win = 31;
    std::vector<double> local;
    for (int k = std::max(0, i - win / 2); k <= std::min<int>(vals.size() - 1, i + win / 2); ++k) {
      local.push_back(vals[k]);
    }
    const double base = Median(local);
    if (base - vals[i] >= 18.0) {
      int j = i;
      while (j < static_cast<int>(vals.size()) && base - vals[j] >= 18.0) {
        j++;
      }
      const double width = hz[std::min<int>(j, hz.size() - 1)] - hz[i];
      if (width <= 600.0) {
        count++;
      }
      i = j;
    } else {
      i++;
    }
  }
  return static_cast<double>(count);
}

double Sfb21Notch(const Spectrum &s) {
  const double band = MedianRange(s, 15500.0, 16800.0);
  const double left = MedianRange(s, 14000.0, 15500.0);
  const double right = MedianRange(s, 16800.0, 18400.0);
  const double base = std::max(left, right);
  return base > -199.0 && band > -199.0 ? base - band : 0.0;
}

double ToneConcentration(const Spectrum &s) {
  std::vector<double> powers;
  double total = 0.0;
  for (size_t i = 0; i < s.freqs.size() && s.freqs[i] <= 24000.0; ++i) {
    const double p = std::pow(10.0, s.db[i] / 10.0);
    powers.push_back(p);
    total += p;
  }
  if (powers.size() < 5 || total <= 0.0) {
    return 0.0;
  }
  std::sort(powers.begin(), powers.end(), std::greater<double>());
  double top = 0.0;
  for (int i = 0; i < 5; ++i) {
    top += powers[i];
  }
  return top / total;
}

void HistogramEdges(const Spectrum &s, double &ultraDecay, double &ultraSlope, double &ultraHigh) {
  ultraDecay = 0.0;
  ultraSlope = 0.0;
  ultraHigh = -200.0;
  double peak = -200.0;
  for (size_t i = 0; i < s.freqs.size(); ++i) {
    peak = std::max(peak, s.db[i]);
  }
  auto band = [&](double lo, double hi) -> double {
    const double med = MedianRange(s, lo, hi);
    return med <= -199.0 ? -200.0 : med - peak;
  };
  const double low = band(19000.0, 22000.0);
  const double mid = band(40000.0, 50000.0);
  const double high = band(78000.0, 92000.0);
  if (low <= -199.0 || high <= -199.0) {
    return;
  }
  ultraDecay = high - low;
  ultraSlope = high - mid;
  ultraHigh = high;
}

double NoiseShapingSlope(const Spectrum &s, double nyquist) {
  const double lo = 50000.0;
  const double hi = std::min(100000.0, nyquist);
  std::vector<double> x;
  std::vector<double> y;
  for (size_t i = 0; i < s.freqs.size(); ++i) {
    if (s.freqs[i] >= lo && s.freqs[i] <= hi && s.freqs[i] > 0) {
      x.push_back(std::log2(s.freqs[i]));
      y.push_back(s.db[i]);
    }
  }
  if (x.size() < 8) {
    return 0.0;
  }
  double mx = 0.0;
  double my = 0.0;
  for (size_t i = 0; i < x.size(); ++i) {
    mx += x[i];
    my += y[i];
  }
  mx /= x.size();
  my /= x.size();
  double num = 0.0;
  double den = 0.0;
  for (size_t i = 0; i < x.size(); ++i) {
    num += (x[i] - mx) * (y[i] - my);
    den += (x[i] - mx) * (x[i] - mx);
  }
  return den > 0 ? num / den : 0.0;
}

double PcmSourceCliff(const Spectrum &s, double nyquist, double bodyRef, double &stepOut, double &aboveOut) {
  const double grid[] = {22050.0, 24000.0, 32000.0, 44100.0, 48000.0, 88200.0, 96000.0};
  for (double edge : grid) {
    if (edge >= nyquist * 0.97) {
      continue;
    }
    const double low = MedianRange(s, std::max(1000.0, edge - 500.0), edge - 50.0);
    const double high = MedianRange(s, edge + 50.0, std::min(nyquist, edge + 500.0));
    const double above = MedianRange(s, edge + 1000.0, std::min(nyquist * 0.98, edge + 6000.0));
    if (low <= -199.0 || high <= -199.0) {
      continue;
    }
    const double drop = low - high;
    const double aboveRel = above > -199.0 ? above - bodyRef : -120.0;
    if (drop >= 20.0 && aboveRel < -75.0) {
      stepOut = drop;
      aboveOut = aboveRel;
      return edge * 2.0;
    }
  }
  stepOut = 0.0;
  aboveOut = 0.0;
  return 0.0;
}

class Decoder {
public:
  ~Decoder() { Close(); }

  bool Open(const std::string &path) {
    int ret = avformat_open_input(&fmt_, path.c_str(), nullptr, nullptr);
    if (ret < 0) {
      return false;
    }
    if (avformat_find_stream_info(fmt_, nullptr) < 0) {
      return false;
    }
    idx_ = SelectAudioStream(fmt_);
    if (idx_ < 0) {
      return false;
    }
    DiscardOtherAudioStreams(fmt_, idx_);
    AVStream *st = fmt_->streams[idx_];
    const AVCodec *codec = avcodec_find_decoder(st->codecpar->codec_id);
    if (codec == nullptr) {
      return false;
    }
    dec_ = avcodec_alloc_context3(codec);
    if (dec_ == nullptr || avcodec_parameters_to_context(dec_, st->codecpar) < 0 ||
        avcodec_open2(dec_, codec, nullptr) < 0) {
      return false;
    }
    codecName_ = codec->name != nullptr ? codec->name : "";
    sampleRate_ = dec_->sample_rate > 0 ? dec_->sample_rate : 44100;
    channels_ = dec_->ch_layout.nb_channels > 0 ? dec_->ch_layout.nb_channels : 2;
    bits_ = dec_->bits_per_raw_sample;
    if (bits_ <= 0) {
      bits_ = av_get_bytes_per_sample(dec_->sample_fmt) * 8;
    }
    if (fmt_->duration > 0) {
      durationMs_ = fmt_->duration / (AV_TIME_BASE / 1000);
    } else if (st->duration > 0) {
      durationMs_ = av_rescale_q(st->duration, st->time_base, AVRational{1, 1000});
    }
    isDsd_ = codecName_.find("dsd") != std::string::npos || sampleRate_ >= 2822400 || bits_ == 1;
    packet_ = av_packet_alloc();
    frame_ = av_frame_alloc();
    return packet_ != nullptr && frame_ != nullptr;
  }

  bool DecodeSegment(int64_t startMs, double seconds, int targetRate, SegmentData &out) {
    if (dec_ == nullptr || fmt_ == nullptr) {
      return false;
    }
    const int64_t ts = av_rescale_q(startMs, AVRational{1, 1000}, fmt_->streams[idx_]->time_base);
    if (av_seek_frame(fmt_, idx_, ts, AVSEEK_FLAG_BACKWARD) < 0) {
      av_seek_frame(fmt_, idx_, 0, AVSEEK_FLAG_BACKWARD);
    }
    avcodec_flush_buffers(dec_);
    if (swr_ != nullptr) {
      swr_free(&swr_);
    }
    AVChannelLayout outLayout;
    av_channel_layout_default(&outLayout, 2);
    if (swr_alloc_set_opts2(&swr_, &outLayout, AV_SAMPLE_FMT_S32, targetRate,
                            &dec_->ch_layout, dec_->sample_fmt, sampleRate_, 0, nullptr) < 0 ||
        swr_ == nullptr || swr_init(swr_) < 0) {
      av_channel_layout_uninit(&outLayout);
      return false;
    }
    av_channel_layout_uninit(&outLayout);
    out.pcm.clear();
    out.rate = targetRate;
    out.frames = 0;
    const int64_t needFrames = static_cast<int64_t>(seconds * targetRate);
    bool done = false;
    while (!done) {
      int ret = av_read_frame(fmt_, packet_);
      if (ret == AVERROR_EOF) {
        avcodec_send_packet(dec_, nullptr);
      } else if (ret < 0) {
        break;
      } else {
        if (packet_->stream_index != idx_) {
          av_packet_unref(packet_);
          continue;
        }
        avcodec_send_packet(dec_, packet_);
        av_packet_unref(packet_);
      }
      while (true) {
        ret = avcodec_receive_frame(dec_, frame_);
        if (ret == AVERROR(EAGAIN)) {
          break;
        }
        if (ret == AVERROR_EOF) {
          done = true;
          break;
        }
        if (ret < 0) {
          done = true;
          break;
        }
        const int outSamples = static_cast<int>(av_rescale_rnd(
            swr_get_delay(swr_, sampleRate_) + frame_->nb_samples, targetRate, sampleRate_,
            AV_ROUND_UP));
        if (outSamples > 0) {
          const size_t old = out.pcm.size();
          out.pcm.resize(old + static_cast<size_t>(outSamples) * 2);
          uint8_t *dst = reinterpret_cast<uint8_t *>(out.pcm.data() + old);
          const int got = swr_convert(swr_, &dst, outSamples,
                                      const_cast<const uint8_t **>(frame_->extended_data),
                                      frame_->nb_samples);
          if (got > 0) {
            out.pcm.resize(old + static_cast<size_t>(got) * 2);
            out.frames += got;
          } else {
            out.pcm.resize(old);
          }
        }
        av_frame_unref(frame_);
        if (out.frames >= needFrames) {
          done = true;
          break;
        }
        if (ret == AVERROR_EOF) {
          done = true;
          break;
        }
      }
      if (ret == AVERROR_EOF) {
        break;
      }
    }
    if (out.frames > needFrames) {
      out.pcm.resize(static_cast<size_t>(needFrames) * 2);
      out.frames = static_cast<int>(needFrames);
    }
    return out.frames > 0;
  }

  int sampleRate() const { return sampleRate_; }
  int channels() const { return channels_; }
  int bits() const { return bits_; }
  int64_t durationMs() const { return durationMs_; }
  bool isDsd() const { return isDsd_; }
  const std::string &codecName() const { return codecName_; }

private:
  void Close() {
    if (swr_ != nullptr) {
      swr_free(&swr_);
    }
    if (frame_ != nullptr) {
      av_frame_free(&frame_);
    }
    if (packet_ != nullptr) {
      av_packet_free(&packet_);
    }
    if (dec_ != nullptr) {
      avcodec_free_context(&dec_);
    }
    if (fmt_ != nullptr) {
      avformat_close_input(&fmt_);
    }
  }

  AVFormatContext *fmt_ = nullptr;
  AVCodecContext *dec_ = nullptr;
  SwrContext *swr_ = nullptr;
  AVPacket *packet_ = nullptr;
  AVFrame *frame_ = nullptr;
  int idx_ = -1;
  int sampleRate_ = 0;
  int channels_ = 0;
  int bits_ = 0;
  int64_t durationMs_ = 0;
  bool isDsd_ = false;
  std::string codecName_;
};

std::string BoolText(bool v) {
  return v ? "true" : "false";
}

void WriteSpectrum(std::ostringstream &os, const Spectrum &s) {
  os << "\"freqs\":[";
  const double fMin = 20.0;
  const double fMax = s.freqs.empty() ? 20000.0 : s.freqs.back();
  for (int k = 0; k < kMaxSpectrumPoints; ++k) {
    const double target = fMin * std::pow(fMax / fMin, static_cast<double>(k) / (kMaxSpectrumPoints - 1));
    size_t best = 0;
    double bestDist = 1e30;
    for (size_t i = 0; i < s.freqs.size(); ++i) {
      const double d = std::fabs(s.freqs[i] - target);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    }
    if (k > 0) {
      os << ",";
    }
    os << static_cast<int>(target);
    if (best > s.freqs.size()) {
      best = s.freqs.size() - 1;
    }
  }
  os << "],\"db\":[";
  for (int k = 0; k < kMaxSpectrumPoints; ++k) {
    const double target = fMin * std::pow(fMax / fMin, static_cast<double>(k) / (kMaxSpectrumPoints - 1));
    size_t best = 0;
    double bestDist = 1e30;
    for (size_t i = 0; i < s.freqs.size(); ++i) {
      const double d = std::fabs(s.freqs[i] - target);
      if (d < bestDist) {
        bestDist = d;
        best = i;
      }
    }
    if (k > 0) {
      os << ",";
    }
    os << (best < s.db.size() ? s.db[best] : -200.0);
  }
  os << "]";
}

} // namespace

std::string AudioAnalysis::Analyze(const std::string &path) {
  std::ostringstream os;
  Decoder dec;
  if (!dec.Open(path)) {
    return "{\"ok\":false,\"error\":\"无法打开或解码音频\"}";
  }
  const bool isDsd = dec.isDsd();
  const int srcRate = dec.sampleRate();
  const int analysisRate = isDsd ? 352800 : srcRate;
  const double segSeconds = isDsd ? 2.0 : 4.0;
  const double positions[] = {0.15, 0.35, 0.55, 0.75};
  const int64_t durationMs = std::max<int64_t>(1, dec.durationMs());
  // 与桌面版一致：不足 2 秒不做鉴定（取样段无法覆盖，结论不可靠）
  if (durationMs < 2000) {
    return "{\"ok\":false,\"error\":\"时长过短，无法分析\"}";
  }
  std::vector<Spectrum> spectra;
  int failCount = 0;
  int64_t analyzedFrames = 0;
  int declaredBits = dec.bits();
  int minTrailing = 32;
  int64_t lowByteZero = 0;
  int64_t nonzeroSamples = 0;
  double peakAbs = 0.0;
  for (double pos : positions) {
    int64_t startMs = static_cast<int64_t>(durationMs * pos - segSeconds * 500.0);
    startMs = std::max<int64_t>(0, std::min<int64_t>(durationMs - static_cast<int64_t>(segSeconds * 1000), startMs));
    SegmentData seg;
    if (!dec.DecodeSegment(startMs, segSeconds, analysisRate, seg)) {
      failCount++;
      continue;
    }
    analyzedFrames += seg.frames;
    Spectrum sp = AverageSpectrum(seg);
    if (!sp.freqs.empty()) {
      spectra.push_back(sp);
    }
    for (size_t i = 0; i < seg.pcm.size(); i += 2) {
      for (int ch = 0; ch < 2; ++ch) {
        const int32_t v = seg.pcm[i + ch];
        if (v == 0) {
          continue;
        }
        nonzeroSamples++;
        peakAbs = std::max(peakAbs, std::fabs(static_cast<double>(v)));
        int64_t a = std::llabs(static_cast<int64_t>(v));
        int tz = 0;
        while (a > 0 && (a & 1) == 0 && tz < 32) {
          a >>= 1;
          tz++;
        }
        minTrailing = std::min(minTrailing, tz);
        if ((v & 0xFF) == 0) {
          lowByteZero++;
        }
      }
    }
  }
  if (spectra.empty()) {
    return "{\"ok\":false,\"error\":\"解码不完整或文件损坏\"}";
  }
  std::vector<double> cutoffs;
  double steepness = 0.0;
  double steepOct = 0.0;
  for (const Spectrum &sp : spectra) {
    double s = 0.0;
    double so = 0.0;
    const double c = FindCutoff(sp, 0.0, false, &s, &so);
    if (c > 0) {
      cutoffs.push_back(c);
    }
  }
  if (cutoffs.empty()) {
    return "{\"ok\":false,\"error\":\"频谱中未检测到有效信号\"}";
  }
  std::sort(cutoffs.begin(), cutoffs.end());
  const double medianCutoff = cutoffs[cutoffs.size() / 2];
  size_t chosen = 0;
  double bestDist = 1e30;
  for (size_t i = 0; i < spectra.size(); ++i) {
    double s = 0.0;
    double so = 0.0;
    const double c = FindCutoff(spectra[i], 0.0, false, &s, &so);
    if (std::fabs(c - medianCutoff) < bestDist) {
      bestDist = std::fabs(c - medianCutoff);
      chosen = i;
    }
  }
  const Spectrum &sp = spectra[chosen];
  const double cutoff = FindCutoff(sp, 0.0, false, &steepness, &steepOct);
  const double nyquist = analysisRate / 2.0;
  double peak = -200.0;
  for (double v : sp.db) {
    peak = std::max(peak, v);
  }
  const double bodyRef = MedianRange(sp, 300.0, 3000.0);
  const double ratio = nyquist > 0 ? cutoff / nyquist : 0.0;
  const double edgeDb = BandMedianRel(sp, cutoff + 1000.0, nyquist * 0.98, peak);
  const double edgeBody = BandMedianRel(sp, cutoff + 1000.0, nyquist * 0.98, bodyRef);
  const double wallStep = isDsd ? 0.0 : WallStep(sp, cutoff);
  const double holes = SpectralHoles(sp);
  const double sfb21 = Sfb21Notch(sp);
  const double flatness = (!isDsd && nyquist >= 23000 && cutoff < nyquist * 0.95) ?
      SpectralFlatness(sp, cutoff, nyquist) : 0.0;
  double ultraDecay = 0.0;
  double ultraSlope = 0.0;
  double ultraHigh = -200.0;
  if (!isDsd && nyquist >= 90000) {
    HistogramEdges(sp, ultraDecay, ultraSlope, ultraHigh);
  }
  const double tone = ToneConcentration(sp);
  // 有效位深：s32 解码后的尾随零位数
  int effDepth = minTrailing < 32 ? 32 - minTrailing : declaredBits;
  if (effDepth <= 0 || effDepth > 32) {
    effDepth = declaredBits;
  }
  if (declaredBits > 0 && declaredBits <= 24) {
    effDepth = std::min(effDepth, declaredBits);
  }
  const double padRatio = nonzeroSamples > 0 ? static_cast<double>(lowByteZero) / nonzeroSamples : 0.0;
  const double peakDbfs = peakAbs > 0 ? 20.0 * std::log10(peakAbs / 2147483648.0) : -200.0;
  const bool above16 = peakAbs > 32768.0;
  // DSD 指标
  DsdMetrics dsd;
  if (isDsd) {
    dsd.slope = NoiseShapingSlope(sp, nyquist);
    dsd.ultra = BandMedianRel(sp, 50000.0, std::min(120000.0, nyquist), peak);
    double bs = 0.0;
    double bo = 0.0;
    dsd.baseCutoff = FindCutoff(sp, 24000.0, true, &bs, &bo);
    dsd.baseSilence = BandMedianRel(sp, 22500.0, std::min(24000.0, nyquist), bodyRef);
    dsd.ultraBody = BandMedianRel(sp, 30000.0, std::min(40000.0, nyquist), bodyRef);
    dsd.pcmSource = PcmSourceCliff(sp, nyquist, bodyRef, dsd.pcmStep, dsd.pcmAbove);
  }
  // PCM 升频台阶
  double upSrc = 0.0;
  double upStep = 0.0;
  if (!isDsd && nyquist >= 23000) {
    const double grid[] = {22050.0, 24000.0, 32000.0, 44100.0, 48000.0, 88200.0, 96000.0};
    for (double edge : grid) {
      if (edge >= nyquist * 0.97) {
        continue;
      }
      const double low = MedianRange(sp, std::max(1000.0, edge - 500.0), edge - 50.0);
      const double high = MedianRange(sp, edge + 50.0, std::min(nyquist, edge + 500.0));
      if (low > -199.0 && high > -199.0 && low - high >= 18.0) {
        upSrc = edge * 2.0;
        upStep = low - high;
        break;
      }
    }
    if (upSrc == 0.0 && std::fabs(cutoff - 22050.0) <= 400.0) {
      upSrc = 44100.0;
    }
  }
  os << std::fixed;
  os.precision(2);
  os << "{\"ok\":true";
  os << ",\"codec\":\"" << dec.codecName() << "\"";
  os << ",\"sampleRate\":" << srcRate;
  os << ",\"analysisRate\":" << analysisRate;
  os << ",\"channels\":" << dec.channels();
  os << ",\"bits\":" << declaredBits;
  os << ",\"durationMs\":" << durationMs;
  os << ",\"isDsd\":" << BoolText(isDsd);
  os << ",\"analyzedSeconds\":" << (analyzedFrames / static_cast<double>(analysisRate));
  os << ",\"failCount\":" << failCount;
  os << ",\"nyquistHz\":" << nyquist;
  os << ",\"cutoffHz\":" << cutoff;
  os << ",\"cutoffMin\":" << cutoffs.front();
  os << ",\"cutoffMax\":" << cutoffs.back();
  os << ",\"steepness\":" << steepness;
  os << ",\"steepOct\":" << steepOct;
  os << ",\"wallStep\":" << wallStep;
  os << ",\"peakDb\":" << peak;
  os << ",\"bodyDb\":" << bodyRef;
  os << ",\"edgeDb\":" << edgeDb;
  os << ",\"edgeBodyDb\":" << edgeBody;
  os << ",\"ratio\":" << ratio;
  os << ",\"holes\":" << holes;
  os << ",\"sfb21\":" << sfb21;
  os << ",\"flatness\":" << flatness;
  os << ",\"toneConcentration\":" << tone;
  os << ",\"ultraDecay\":" << ultraDecay;
  os << ",\"ultraSlope\":" << ultraSlope;
  os << ",\"ultraHigh\":" << ultraHigh;
  os << ",\"effDepth\":" << effDepth;
  os << ",\"padRatio\":" << padRatio;
  os << ",\"bitAbove16\":" << BoolText(above16);
  os << ",\"peakDbfs\":" << peakDbfs;
  os << ",\"upsampleSrc\":" << upSrc;
  os << ",\"upsampleStep\":" << upStep;
  os << ",\"dsdSlope\":" << dsd.slope;
  os << ",\"dsdUltra\":" << dsd.ultra;
  os << ",\"baseCutoff\":" << dsd.baseCutoff;
  os << ",\"baseSilence\":" << dsd.baseSilence;
  os << ",\"ultraBody\":" << dsd.ultraBody;
  os << ",\"pcmSource\":" << dsd.pcmSource;
  os << ",\"pcmStep\":" << dsd.pcmStep;
  os << ",\"pcmAbove\":" << dsd.pcmAbove;
  os << ",";
  WriteSpectrum(os, sp);
  os << "}";
  return os.str();
}

int32_t AudioAnalysis::ExportSegment(const std::string &src, int64_t startMs, int64_t durationMs,
                                     const std::string &format, int32_t bitDepth,
                                     const std::string &dst) {
  const bool wantWav = format == "wav";
  if (!wantWav && format != "flac") {
    return -2;
  }
  AVFormatContext *inFmt = nullptr;
  AVCodecContext *dec = nullptr;
  AVPacket *pkt = nullptr;
  AVFrame *frame = nullptr;
  AVFormatContext *outFmt = nullptr;
  AVCodecContext *enc = nullptr;
  AVFrame *encFrame = nullptr;
  SwrContext *swr = nullptr;
  AVPacket *encPkt = nullptr;
  int audioIdx = -1;
  int ret = 0;

  auto fail = [&](const char *stage) -> int32_t {
    OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                 "ExportSegment fail at %{public}s", stage);
    if (swr != nullptr) {
      swr_free(&swr);
    }
    if (encFrame != nullptr) {
      av_frame_free(&encFrame);
    }
    if (frame != nullptr) {
      av_frame_free(&frame);
    }
    if (pkt != nullptr) {
      av_packet_free(&pkt);
    }
    if (encPkt != nullptr) {
      av_packet_free(&encPkt);
    }
    if (enc != nullptr) {
      avcodec_free_context(&enc);
    }
    if (outFmt != nullptr) {
      if (outFmt->pb != nullptr) {
        avio_closep(&outFmt->pb);
      }
      avformat_free_context(outFmt);
    }
    if (dec != nullptr) {
      avcodec_free_context(&dec);
    }
    if (inFmt != nullptr) {
      avformat_close_input(&inFmt);
    }
    return -1;
  };

  if (avformat_open_input(&inFmt, src.c_str(), nullptr, nullptr) < 0) {
    return fail("open_input");
  }
  if (avformat_find_stream_info(inFmt, nullptr) < 0) {
    return fail("stream_info");
  }
  audioIdx = SelectAudioStream(inFmt);
  if (audioIdx < 0) {
    return fail("no_audio_stream");
  }
  DiscardOtherAudioStreams(inFmt, audioIdx);
  AVStream *inStream = inFmt->streams[audioIdx];
  const AVCodec *decoder = avcodec_find_decoder(inStream->codecpar->codec_id);
  if (decoder == nullptr) {
    return fail("no_decoder");
  }
  dec = avcodec_alloc_context3(decoder);
  if (dec == nullptr || avcodec_parameters_to_context(dec, inStream->codecpar) < 0 ||
      avcodec_open2(dec, decoder, nullptr) < 0) {
    return fail("decoder_open");
  }
  int outRate = dec->sample_rate > 0 ? dec->sample_rate : 44100;
  // SACD/DSD 解码输出 352.8kHz PCM；导出降到 176.4kHz，兼顾体积与 44.1kHz 整数族
  if (IsDsdCodec(dec->codec_id) && outRate > 176400) {
    outRate = 176400;
  }
  const int channels = dec->ch_layout.nb_channels > 0 ? dec->ch_layout.nb_channels : 2;

  const AVSampleFormat outSampleFmt = bitDepth > 16 ? AV_SAMPLE_FMT_S32 : AV_SAMPLE_FMT_S16;
  const AVCodecID codecId = wantWav ?
      (bitDepth > 16 ? AV_CODEC_ID_PCM_S24LE : AV_CODEC_ID_PCM_S16LE) : AV_CODEC_ID_FLAC;
  const AVCodec *encoder = avcodec_find_encoder(codecId);
  if (encoder == nullptr) {
    return fail("no_encoder");
  }
  enc = avcodec_alloc_context3(encoder);
  if (enc == nullptr) {
    return fail("enc_alloc");
  }
  AVChannelLayout layout;
  av_channel_layout_default(&layout, channels);
  enc->sample_rate = outRate;
  enc->ch_layout = layout;
  enc->sample_fmt = outSampleFmt;
  enc->time_base = AVRational{1, outRate};
  enc->bits_per_raw_sample = bitDepth;
  enc->compression_level = 5;
  if (avcodec_open2(enc, encoder, nullptr) < 0) {
    return fail("enc_open");
  }
  const int frameSize = enc->frame_size > 0 ? enc->frame_size : 4096;
  if (avformat_alloc_output_context2(&outFmt, nullptr, format.c_str(), dst.c_str()) < 0 ||
      outFmt == nullptr) {
    return fail("out_alloc");
  }
  AVStream *outStream = avformat_new_stream(outFmt, nullptr);
  if (outStream == nullptr || avcodec_parameters_from_context(outStream->codecpar, enc) < 0) {
    return fail("out_stream");
  }
  outStream->time_base = enc->time_base;
  ret = avio_open(&outFmt->pb, dst.c_str(), AVIO_FLAG_WRITE);
  if (ret < 0) {
    OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "avio_open failed: %{public}d dst=%{public}s",
                 ret, dst.c_str());
    return fail("avio_open");
  }
  ret = avformat_write_header(outFmt, nullptr);
  if (ret < 0) {
    OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG, "write_header failed: %{public}d", ret);
    return fail("write_header");
  }
  if (swr_alloc_set_opts2(&swr, &layout, outSampleFmt, outRate,
                          &dec->ch_layout, dec->sample_fmt, dec->sample_rate, 0, nullptr) < 0 ||
      swr == nullptr || swr_init(swr) < 0) {
    return fail("swr_init");
  }
  pkt = av_packet_alloc();
  frame = av_frame_alloc();
  encPkt = av_packet_alloc();
  encFrame = av_frame_alloc();
  if (pkt == nullptr || frame == nullptr || encPkt == nullptr || encFrame == nullptr) {
    return fail("alloc_packets");
  }
  encFrame->format = outSampleFmt;
  encFrame->ch_layout = layout;
  encFrame->sample_rate = outRate;
  encFrame->nb_samples = frameSize;
  if (av_frame_get_buffer(encFrame, 0) < 0) {
    return fail("frame_buffer");
  }
  const int64_t ts = av_rescale_q(startMs, AVRational{1, 1000}, inStream->time_base);
  if (av_seek_frame(inFmt, audioIdx, ts, AVSEEK_FLAG_BACKWARD) < 0) {
    av_seek_frame(inFmt, audioIdx, 0, AVSEEK_FLAG_BACKWARD);
  }
  avcodec_flush_buffers(dec);
  const int bytesPerSample = av_get_bytes_per_sample(outSampleFmt);
  const int64_t startSample = startMs * outRate / 1000;
  const int64_t wantSamples = std::max<int64_t>(1, durationMs * outRate / 1000);
  const int64_t spanEnd = startSample + wantSamples;
  int64_t fallbackOut = 0;
  int64_t emitted = 0;
  int fill = 0;
  bool done = false;

  auto flushFrame = [&]() -> bool {
    if (fill <= 0) {
      return true;
    }
    encFrame->nb_samples = fill;
    encFrame->pts = emitted;
    int r = avcodec_send_frame(enc, encFrame);
    if (r < 0) {
      OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                   "send_frame failed: %{public}d samples=%{public}d", r, fill);
      return false;
    }
    emitted += fill;
    fill = 0;
    while (true) {
      r = avcodec_receive_packet(enc, encPkt);
      if (r == AVERROR(EAGAIN) || r == AVERROR_EOF) {
        break;
      }
      if (r < 0) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "receive_packet failed: %{public}d", r);
        return false;
      }
      av_packet_rescale_ts(encPkt, enc->time_base, outStream->time_base);
      encPkt->stream_index = outStream->index;
      r = av_interleaved_write_frame(outFmt, encPkt);
      if (r < 0) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "write_frame failed: %{public}d", r);
        return false;
      }
      av_packet_unref(encPkt);
    }
    return true;
  };

  // 按解码帧的绝对 PTS 裁剪，seek 落在关键帧前后都不会重复跳过
  auto feed = [&](const uint8_t *data, int samples, int64_t frameStartOut) -> bool {
    if (frameStartOut < 0) {
      frameStartOut = 0;
    }
    const int64_t from = std::max<int64_t>(frameStartOut, startSample);
    const int64_t to = std::min<int64_t>(frameStartOut + samples, spanEnd);
    if (to <= from) {
      if (frameStartOut + samples >= spanEnd) {
        done = true;
      }
      return true;
    }
    int offset = static_cast<int>(from - frameStartOut);
    int remain = static_cast<int>(to - from);
    if (frameStartOut + samples >= spanEnd) {
      done = true;
    }
    while (remain > 0) {
      const int take = std::min(remain, frameSize - fill);
      const int wr = av_frame_make_writable(encFrame);
      if (wr < 0) {
        OH_LOG_Print(LOG_APP, LOG_ERROR, LOG_DOMAIN, LOG_TAG,
                     "make_writable failed: %{public}d nb=%{public}d", wr, encFrame->nb_samples);
        return false;
      }
      if (av_sample_fmt_is_planar(outSampleFmt) != 0) {
        // 平面格式：逐声道写入（打包格式只有 data[0] 有效，写 data[1] 会空指针崩溃）
        for (int ch = 0; ch < channels; ++ch) {
          const uint8_t *srcCh = data + (static_cast<size_t>(offset) * channels + ch) * bytesPerSample;
          uint8_t *dstCh = encFrame->data[ch] + static_cast<size_t>(fill) * bytesPerSample;
          for (int i = 0; i < take; ++i) {
            memcpy(dstCh + static_cast<size_t>(i) * bytesPerSample,
                   srcCh + static_cast<size_t>(i) * channels * bytesPerSample, bytesPerSample);
          }
        }
      } else {
        // 打包（交织）格式：整块拷贝
        const uint8_t *srcBase = data + static_cast<size_t>(offset) * channels * bytesPerSample;
        uint8_t *dstBase = encFrame->data[0] + static_cast<size_t>(fill) * channels * bytesPerSample;
        memcpy(dstBase, srcBase, static_cast<size_t>(take) * channels * bytesPerSample);
      }
      fill += take;
      offset += take;
      remain -= take;
      if (fill >= frameSize && !flushFrame()) {
        return false;
      }
    }
    return true;
  };

  while (!done) {
    ret = av_read_frame(inFmt, pkt);
    if (ret == AVERROR_EOF) {
      avcodec_send_packet(dec, nullptr);
    } else if (ret < 0) {
      break;
    } else {
      if (pkt->stream_index != audioIdx) {
        av_packet_unref(pkt);
        continue;
      }
      avcodec_send_packet(dec, pkt);
      av_packet_unref(pkt);
    }
    while (true) {
      const int r = avcodec_receive_frame(dec, frame);
      if (r == AVERROR(EAGAIN)) {
        break;
      }
      if (r == AVERROR_EOF) {
        done = true;
        break;
      }
      if (r < 0) {
        done = true;
        break;
      }
      const int outSamples = static_cast<int>(av_rescale_rnd(
          swr_get_delay(swr, dec->sample_rate) + frame->nb_samples, outRate, dec->sample_rate,
          AV_ROUND_UP));
      int64_t frameStartOut = fallbackOut;
      if (frame->best_effort_timestamp != AV_NOPTS_VALUE && outRate > 0) {
        frameStartOut = av_rescale_q(frame->best_effort_timestamp, inStream->time_base,
                                     AVRational{1, outRate});
      }
      if (outSamples > 0) {
        std::vector<uint8_t> buf(static_cast<size_t>(outSamples) * channels * bytesPerSample);
        uint8_t *dst = buf.data();
        const int got = swr_convert(swr, &dst, outSamples,
                                    const_cast<const uint8_t **>(frame->extended_data),
                                    frame->nb_samples);
        av_frame_unref(frame);
        fallbackOut = frameStartOut + outSamples;
        if (got > 0 && !feed(buf.data(), got, frameStartOut)) {
          done = true;
          break;
        }
      } else {
        av_frame_unref(frame);
        fallbackOut = frameStartOut;
      }
      if (done) {
        break;
      }
    }
    if (ret == AVERROR_EOF) {
      break;
    }
  }
  if (!flushFrame()) {
    return fail("flush_final");
  }
  avcodec_send_frame(enc, nullptr);
  while (true) {
    const int r = avcodec_receive_packet(enc, encPkt);
    if (r == AVERROR(EAGAIN) || r == AVERROR_EOF) {
      break;
    }
    if (r < 0) {
      break;
    }
    av_packet_rescale_ts(encPkt, enc->time_base, outStream->time_base);
    encPkt->stream_index = outStream->index;
    av_interleaved_write_frame(outFmt, encPkt);
    av_packet_unref(encPkt);
  }
  av_write_trailer(outFmt);
  av_channel_layout_uninit(&layout);
  if (swr != nullptr) {
    swr_free(&swr);
  }
  if (encFrame != nullptr) {
    av_frame_free(&encFrame);
  }
  if (frame != nullptr) {
    av_frame_free(&frame);
  }
  if (pkt != nullptr) {
    av_packet_free(&pkt);
  }
  if (encPkt != nullptr) {
    av_packet_free(&encPkt);
  }
  if (enc != nullptr) {
    avcodec_free_context(&enc);
  }
  if (outFmt != nullptr) {
    if (outFmt->pb != nullptr) {
      avio_closep(&outFmt->pb);
    }
    avformat_free_context(outFmt);
  }
  if (dec != nullptr) {
    avcodec_free_context(&dec);
  }
  if (inFmt != nullptr) {
    avformat_close_input(&inFmt);
  }
  return emitted > 0 ? 0 : -1;
}
