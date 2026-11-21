"""v1.8.0 输出后端 × 输出模式「真实开流」矩阵检查。

为什么不是单测
--------------
单测用假输出验证降级分支，回答不了用户的问题「各个后端/模式实际能不能出声」。
本工具用 ``PlayerEngine`` + ``Settings`` 对真实声卡开流，从用户曲库里**只读**
播放同一首 FLAC，逐组合记录请求模式、实际模式、采样率、欠载与错误。

只读约束
--------
本工具只调用 ``core.probe.probe()`` 与引擎解码，不对样本目录做任何写操作：
不改标签、不重命名、不移动、不删除、不在同目录生成文件。默认样本是用户给的
``D:/真无损已经修改/1999 年国语流行畅销金曲/Dear John.flac``；可用 ``--file`` 换。

用法
----
    python tools/check_output_matrix.py                       # 默认放 3.2 秒
    python tools/check_output_matrix.py --seconds 4 --quiet  # 更长的样本/只写报告
    python tools/check_output_matrix.py --no-write           # 只打印，不落盘

产出
----
* 标准输出：原文矩阵表（可直接贴进验收报告）
* ``docs/v1.8.0-output-matrix.json``：机读原始记录（``--no-write`` 时不写）

样本时长注意
------------
早期 2 秒样本在 3 秒冒烟里放完还继续拉流，尾部会产生 ~42 次欠载（环境/时长问题，
不是代码缺陷）。本工具默认播放 3.2 秒并主动 stop，**播放时长必须 ≥ 3 秒**，
否则报告里的 underruns 会被尾部欠载污染。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seqaudio.core import probe as probe_mod  # noqa: E402
from seqaudio.core.authenticity import find_ffmpeg  # noqa: E402
from seqaudio.core.config import Settings  # noqa: E402
from seqaudio.player import devices as dev_mod  # noqa: E402
from seqaudio.player.engine import PlayerEngine  # noqa: E402
from seqaudio.player.queue import PlayQueue  # noqa: E402

DEFAULT_FILE = Path(
    r"D:/真无损已经修改/1999 年国语流行畅销金曲/Dear John.flac"
)
DEFAULT_JSON = ROOT / "docs" / "v1.8.0-output-matrix.json"

#: UI 允许的组合（非 WASAPI 后端独占/严格已置灰，所以只跑 shared）。
COMBOS: tuple[tuple[str, str], ...] = (
    ("mme", "shared"),
    ("directsound", "shared"),
    ("wdmks", "shared"),
    ("wasapi", "shared"),
    ("wasapi", "exclusive"),
    ("wasapi", "exclusive_strict"),
)


def _blank_row(backend: str, mode: str, requested_device: str) -> dict[str, Any]:
    """一条记录的空壳（保证所有组合字段齐全，异常时也能入表）。"""
    return {
        "backend": backend,
        "mode": mode,
        "requested_device": requested_device or "系统默认设备",
        "play_accepted": False,
        "device": "",
        "output_mode": "",
        "output_mode_requested": mode,
        "exclusive": False,
        "rate": 0,
        "underruns": 0,
        "error": "",
        "mode_note": "",
        "state": "NOT_RUN",
        "position_ms": 0,
        "elapsed_sec": 0.0,
    }


def run_one(
    backend: str,
    mode: str,
    path: Path,
    seconds: float,
    device_name: str = "",
) -> dict[str, Any]:
    """对一组 (后端, 模式) 真实开流并采样，返回原始记录。"""
    settings = Settings(
        offline_mode=True,
        online_enabled=False,
        playback_auto_lyrics=False,
        playback_output_backend=backend,
        playback_output_mode=mode,
        playback_device=device_name,
        playback_exclusive_fallback="auto",
        playback_int_pipeline=(mode == "exclusive_strict"),
        playback_dsp_bypass=True,
        playback_volume_mode="bypass",
        playback_volume_db=0.0,
        playback_follow_source_rate=True,
        playback_restore_session=False,
    )
    row = _blank_row(backend, mode, device_name)
    engine: PlayerEngine | None = None
    started = time.perf_counter()
    try:
        track = probe_mod.probe(path, with_cue=False)
        engine = PlayerEngine(settings)
        engine.set_queue(PlayQueue.from_tracks([track]))
        row["play_accepted"] = bool(engine.play())
        deadline = time.monotonic() + max(3.0, float(seconds))
        while time.monotonic() < deadline:
            engine.poll()
            time.sleep(0.1)
        # 在 stop 之前取快照：stop 后 state 变成 IDLE，看不出播放期真实状态。
        snap = engine.snapshot()
        row.update(
            {
                "device": str(snap.get("device") or ""),
                "output_mode": str(snap.get("output_mode") or ""),
                "output_mode_requested": str(
                    snap.get("output_mode_requested") or mode
                ),
                "exclusive": bool(snap.get("exclusive", False)),
                "rate": int(snap.get("rate") or 0),
                "underruns": int((snap.get("counters") or {}).get("underruns", 0) or 0),
                "error": str(snap.get("error") or ""),
                "mode_note": str(snap.get("mode_note") or ""),
                "state": str(snap.get("state") or ""),
                "position_ms": int(snap.get("position_ms") or 0),
            }
        )
    except Exception as exc:  # noqa: BLE001 - 矩阵检查要把异常如实记下来
        row["error"] = f"{type(exc).__name__}: {exc}"
        row["state"] = "EXCEPTION"
    finally:
        row["elapsed_sec"] = round(time.perf_counter() - started, 2)
        if engine is not None:
            try:
                engine.shutdown()
            except Exception as exc:  # noqa: BLE001
                if not row["error"]:
                    row["error"] = f"shutdown: {exc}"
        # 让设备真正放手再开下一组，避免「设备被占用」的环境噪声。
        time.sleep(0.4)
    return row


def environment_controls() -> dict[str, Any]:
    """环境对照：区分「设备/驱动不支持」与「程序代码缺陷」。

    * WDM-KS：直接用 ``sounddevice.OutputStream`` 开一条 30 ms 静音流。若官方
      PortAudio 自己也打不开，就不是本程序的问题。
    * WASAPI 严格档：记录当前设备的零出声能力，确认降级来自设备能力而非误判。
    """
    controls: dict[str, Any] = {}
    try:
        import sounddevice as sd

        api_index = next(
            (
                index
                for index, api in enumerate(sd.query_hostapis())
                if "wdm-ks" in str(api.get("name", "")).lower()
            ),
            -1,
        )
        direct: list[dict[str, Any]] = []
        if api_index >= 0:
            for index, info in enumerate(sd.query_devices()):
                if int(info.get("hostapi", -1)) != api_index:
                    continue
                if int(info.get("max_output_channels", 0) or 0) <= 0:
                    continue
                entry: dict[str, Any] = {
                    "index": index,
                    "name": str(info.get("name", "")),
                    "open": "?",
                    "error": "",
                }
                stream = None
                try:
                    stream = sd.OutputStream(
                        device=index, samplerate=44100, channels=2,
                        dtype="float32", blocksize=1024,
                    )
                    stream.start()
                    time.sleep(0.03)
                    stream.stop()
                    entry["open"] = "OK"
                except Exception as exc:  # noqa: BLE001 - 如实记录原始驱动错误
                    entry["open"] = "FAIL"
                    entry["error"] = str(exc)
                finally:
                    if stream is not None:
                        try:
                            stream.close()
                        except Exception:  # noqa: BLE001
                            pass
                direct.append(entry)
        controls["wdmks_direct_sounddevice_open"] = direct
    except Exception as exc:  # noqa: BLE001
        controls["wdmks_direct_sounddevice_open"] = f"对照不可用：{exc}"

    try:
        device = dev_mod.resolve_device("", "wasapi")
        capability = dev_mod.probe_capabilities(device)
        controls["wasapi_strict_capability"] = {
            "device": device.name,
            "strict_usable": bool(capability.strict_usable),
            "exclusive_usable": bool(capability.exclusive_usable),
        }
    except Exception as exc:  # noqa: BLE001
        controls["wasapi_strict_capability"] = f"对照不可用：{exc}"
    return controls


def build_report(path: Path, seconds: float, device_name: str) -> dict[str, Any]:
    """跑完所有合法组合 + ASIO 存在性检查。"""
    rows: list[dict[str, Any]] = [
        run_one(backend, mode, path, seconds, device_name)
        for backend, mode in COMBOS
    ]
    asio_present = bool(dev_mod.backend_present("asio"))
    asio_row = _blank_row("asio", "-", "（不枚举：后端不存在）")
    asio_row.update(
        {
            "state": "N/A",
            "mode_note": f"backend_present('asio') == {asio_present}；"
            "本次未开流（官方 PortAudio 构建不含 ASIO）",
        }
    )
    rows.append(asio_row)
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "platform": f"{platform.system()} {platform.release()}",
        "sample": str(path),
        "sample_note": "只读播放；每组至少 3 秒，主动 stop 避免尾部欠载",
        "play_seconds": max(3.0, float(seconds)),
        "rows": rows,
        "controls": environment_controls(),
    }


def render_table(report: dict[str, Any]) -> str:
    """原文矩阵表（Markdown，可直接贴进验收回报）。"""
    header = (
        "后端", "请求模式", "请求设备", "实际输出模式", "独占", "采样率",
        "欠载", "错误", "mode_note", "state", "位置ms", "耗时s",
    )
    lines = [
        "# v1.8.0 输出后端 × 模式真实开流矩阵",
        "",
        f"- 样本：`{report['sample']}`（只读，不改标签/不重命名/不写同目录文件）",
        f"- 每组播放 ≥ {report['play_seconds']} 秒（避开 2 秒样本在 3 秒冒烟里的尾部欠载）",
        f"- 环境：{report['platform']} / Python {report['python']}",
        "",
        "| " + " | ".join(header) + " |",
        "|" + "---|" * len(header),
    ]
    for row in report["rows"]:
        values = (
            row["backend"], row["mode"], row["requested_device"], row["output_mode"],
            "是" if row["exclusive"] else "否", str(row["rate"]), str(row["underruns"]),
            row["error"] or "—", row["mode_note"] or "—", row["state"],
            str(row["position_ms"]), str(row["elapsed_sec"]),
        )
        lines.append("| " + " | ".join(str(v).replace("|", "/") for v in values) + " |")

    controls = report.get("controls") or {}
    direct = controls.get("wdmks_direct_sounddevice_open")
    if isinstance(direct, list) and direct:
        lines.append("")
        lines.append("## 环境对照（区分环境限制与代码缺陷）")
        lines.append("")
        lines.append("WDM-KS 用官方 `sounddevice.OutputStream` 直开 30 ms 静音流：")
        for entry in direct:
            lines.append(
                f"- dev{entry['index']} {entry['name'][:40]}：{entry['open']}"
                + (f"（{entry['error'][:90]}）" if entry["error"] else "")
            )
    strict = controls.get("wasapi_strict_capability")
    if isinstance(strict, dict):
        lines.append("")
        lines.append(
            f"WASAPI 默认设备 `{strict['device']}` 零出声能力："
            f"strict_usable={strict['strict_usable']}，"
            f"exclusive_usable={strict['exclusive_usable']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", default=str(DEFAULT_FILE), help="只读样本路径")
    parser.add_argument("--seconds", type=float, default=3.2,
                        help="每组播放秒数（最小 3 秒，默认 3.2）")
    parser.add_argument("--device", default="",
                        help="指定设备名；留空 = 系统默认设备（每组由后端自行解析）")
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="机读报告路径")
    parser.add_argument("--no-write", action="store_true", help="只打印，不落盘")
    parser.add_argument("--quiet", action="store_true", help="不打印矩阵表")
    args = parser.parse_args(argv)

    path = Path(args.file).expanduser()
    if not path.is_file():
        print(f"样本不存在：{path}", file=sys.stderr)
        return 2
    if not find_ffmpeg():
        print("找不到 ffmpeg，无法解码样本；请检查 imageio-ffmpeg 依赖。", file=sys.stderr)
        return 2

    report = build_report(path, args.seconds, args.device)
    if not args.quiet:
        print(render_table(report))
    if not args.no_write:
        json_path = Path(args.json)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"\n机读报告：{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
