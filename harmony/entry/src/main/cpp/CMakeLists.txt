cmake_minimum_required(VERSION 3.5.0)
project(seqaudio_core)

set(CMAKE_C_STANDARD 11)
set(CMAKE_CXX_STANDARD 17)

if(DEFINED PACKAGE_FIND_FILE)
  include(${PACKAGE_FIND_FILE})
endif()

set(ROOT ${CMAKE_CURRENT_SOURCE_DIR})

# OHOS_ARCH 由 SDK 工具链注入：arm64-v8a / x86_64
if(OHOS_ARCH STREQUAL "x86_64")
  set(FF_ARCH "x86_64")
else()
  set(FF_ARCH "aarch64")
endif()
set(FF_LIB_DIR ${ROOT}/prebuilt/${FF_ARCH}/lib)

include_directories(${ROOT})
include_directories(${ROOT}/prebuilt/include)

add_library(seqaudio_core SHARED
  napi_init.cpp
  audio_engine.cpp
  audio_analysis.cpp
  dsp_chain.cpp)

target_link_libraries(seqaudio_core PUBLIC
  ${FF_LIB_DIR}/libavformat.a
  ${FF_LIB_DIR}/libavcodec.a
  ${FF_LIB_DIR}/libswresample.a
  ${FF_LIB_DIR}/libavutil.a
  ohaudio
  ace_napi.z
  hilog_ndk.z
  m)
