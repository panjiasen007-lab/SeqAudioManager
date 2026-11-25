#include <string>

#include "napi/native_api.h"

#include "audio_engine.h"

namespace {

napi_value Int32Result(napi_env env, int32_t v) {
  napi_value out = nullptr;
  napi_create_int32(env, v, &out);
  return out;
}

napi_value Int64Result(napi_env env, int64_t v) {
  napi_value out = nullptr;
  napi_create_int64(env, v, &out);
  return out;
}

std::string ReadString(napi_env env, napi_value value) {
  size_t len = 0;
  if (napi_get_value_string_utf8(env, value, nullptr, 0, &len) != napi_ok) {
    return std::string();
  }
  std::string out(len, '\0');
  size_t copied = 0;
  napi_get_value_string_utf8(env, value, &out[0], len + 1, &copied);
  out.resize(copied);
  return out;
}

napi_value Open(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value args[1] = {nullptr};
  napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
  if (argc < 1) {
    return Int32Result(env, -1);
  }
  std::string path = ReadString(env, args[0]);
  return Int32Result(env, AudioEngine::Get().Open(path));
}

napi_value Start(napi_env env, napi_callback_info info) {
  (void)info;
  return Int32Result(env, AudioEngine::Get().Start());
}

napi_value Pause(napi_env env, napi_callback_info info) {
  (void)info;
  return Int32Result(env, AudioEngine::Get().Pause());
}

napi_value Stop(napi_env env, napi_callback_info info) {
  (void)info;
  return Int32Result(env, AudioEngine::Get().Stop());
}

napi_value Release(napi_env env, napi_callback_info info) {
  (void)info;
  AudioEngine::Get().Close();
  napi_value undef = nullptr;
  napi_get_undefined(env, &undef);
  return undef;
}

napi_value Seek(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value args[1] = {nullptr};
  napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
  if (argc < 1) {
    return Int32Result(env, -1);
  }
  int64_t ms = 0;
  napi_get_value_int64(env, args[0], &ms);
  return Int32Result(env, AudioEngine::Get().Seek(ms));
}

napi_value SetVolume(napi_env env, napi_callback_info info) {
  size_t argc = 1;
  napi_value args[1] = {nullptr};
  napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
  if (argc < 1) {
    return Int32Result(env, -1);
  }
  double v = 1.0;
  napi_get_value_double(env, args[0], &v);
  return Int32Result(env, AudioEngine::Get().SetVolume(static_cast<float>(v)));
}

napi_value SetEq(napi_env env, napi_callback_info info) {
  size_t argc = 2;
  napi_value args[2] = {nullptr, nullptr};
  napi_get_cb_info(env, info, &argc, args, nullptr, nullptr);
  if (argc < 2) {
    return Int32Result(env, -1);
  }
  bool enabled = false;
  napi_get_value_bool(env, args[0], &enabled);

  bool isArray = false;
  napi_is_array(env, args[1], &isArray);
  float gains[DspChain::kMaxBands] = {};
  uint32_t count = 0;
  if (isArray) {
    uint32_t len = 0;
    napi_get_array_length(env, args[1], &len);
    count = len < static_cast<uint32_t>(DspChain::kMaxBands) ? len
                                                             : static_cast<uint32_t>(DspChain::kMaxBands);
    for (uint32_t i = 0; i < count; ++i) {
      napi_value el = nullptr;
      double v = 0.0;
      if (napi_get_element(env, args[1], i, &el) == napi_ok) {
        napi_get_value_double(env, el, &v);
      }
      gains[i] = static_cast<float>(v);
    }
  }
  return Int32Result(env, AudioEngine::Get().SetEq(enabled, gains, static_cast<int>(count)));
}

napi_value SelfTest(napi_env env, napi_callback_info info) {
  (void)info;
  char buf[256] = {0};
  DspChain::SelfTest(buf, static_cast<int>(sizeof(buf)));
  napi_value out = nullptr;
  napi_create_string_utf8(env, buf, NAPI_AUTO_LENGTH, &out);
  return out;
}

napi_value Position(napi_env env, napi_callback_info info) {
  (void)info;
  return Int64Result(env, AudioEngine::Get().PositionMs());
}

napi_value Duration(napi_env env, napi_callback_info info) {
  (void)info;
  return Int64Result(env, AudioEngine::Get().DurationMs());
}

napi_value TakeInterrupt(napi_env env, napi_callback_info info) {
  (void)info;
  return Int32Result(env, AudioEngine::Get().TakeInterrupt());
}

napi_value InfoJson(napi_env env, napi_callback_info info) {
  (void)info;
  std::string json = AudioEngine::Get().InfoJson();
  napi_value out = nullptr;
  napi_create_string_utf8(env, json.c_str(), json.size(), &out);
  return out;
}

napi_value IsOpen(napi_env env, napi_callback_info info) {
  (void)info;
  napi_value out = nullptr;
  napi_get_boolean(env, AudioEngine::Get().IsOpen(), &out);
  return out;
}

} // namespace

EXTERN_C_START
static napi_value Init(napi_env env, napi_value exports) {
  napi_property_descriptor desc[] = {
      {"open", nullptr, Open, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"start", nullptr, Start, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"pause", nullptr, Pause, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"stop", nullptr, Stop, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"release", nullptr, Release, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"seek", nullptr, Seek, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"setVolume", nullptr, SetVolume, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"setEq", nullptr, SetEq, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"selfTest", nullptr, SelfTest, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"position", nullptr, Position, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"duration", nullptr, Duration, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"takeInterrupt", nullptr, TakeInterrupt, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"info", nullptr, InfoJson, nullptr, nullptr, nullptr, napi_default, nullptr},
      {"isOpen", nullptr, IsOpen, nullptr, nullptr, nullptr, napi_default, nullptr},
  };
  napi_define_properties(env, exports, sizeof(desc) / sizeof(desc[0]), desc);
  return exports;
}
EXTERN_C_END

static napi_module g_seqAudioModule = {
    .nm_version = 1,
    .nm_flags = 0,
    .nm_filename = nullptr,
    .nm_register_func = Init,
    .nm_modname = "seqaudio_core",
    .nm_priv = nullptr,
    .reserved = {0},
};

extern "C" __attribute__((constructor)) void RegisterSeqAudioModule(void) {
  napi_module_register(&g_seqAudioModule);
}
