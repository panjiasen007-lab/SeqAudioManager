"""独占能力探测：设备 × 采样率 × 模式（共享 / 独占 / 严格独占）零出声矩阵。

为什么要有这个工具
------------------
v1.6 的独占「接好了一半」：`output.py` 确实传了 `WasapiSettings(exclusive=True)`，
但**采样率探测走的是共享模式**，于是 44.1k 源在探测阶段就被判失败并重采样到 48k
—— 用户看到「已重采样」，独占等于白开。v1.7 的前提是「探测必须与被测模式一致」。

架构师已实测：`sd.check_output_settings(..., extra_settings=WasapiSettings(...))`
与真实开流结论 12/12 一致，因此**逐设备能力可以零出声探测**，不需要试错开流。
本工具就是把这套探测固化成可复跑的东西，产出给设计文档、设置面板与验收用例共用。

用法
----
    python tools/probe_exclusive.py                 # 默认只探 WASAPI 设备，写 docs/
    python tools/probe_exclusive.py --all-backends  # 连 MME/DirectSound/WDM-KS 一起探
    python tools/probe_exclusive.py --no-write      # 只打印，不落盘
    python tools/probe_exclusive.py --devices 19,21 # 只探指定索引（排障用）

产出
----
* ``docs/1.7-独占能力矩阵.md``  —— 人读：汇总 + 宽表 + 每台设备的明细
* ``docs/1.7-独占能力矩阵.json`` —— 机读：设置面板缓存与测试断言的数据源

三条容易踩的坑（都已写进实现）
------------------------------
1. **声道数不能写死 2**：dev19「Strix 7.1」是 8 声道设备，2ch 探测必然假阴性。
   这里按 ``min(max_output_channels, 8)`` 试，全失败时再补试 8ch。
2. **严格模式只能整数**：``explicit_sample_format=True`` 下 float32 直接被拒
   （实测 -9997），所以 strict 一档的 dtype 固定 int32。
3. **-9997 是双义的**：既可能是"采样率真不支持"，也可能是"格式被 explicit 拒绝"。
   工具把原始错误文本一起记下来，避免下游把两件事混成一句"不支持该采样率"。
"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import sounddevice as sd  # noqa: E402

#: 界面与文档约定的六个采样率（88.2k / 176.4k 是 44.1k 家族的整数倍）
RATES = (44100, 48000, 88200, 96000, 176400, 192000)

#: 三档模式的键（与 v1.7 设计的 OutputMode 对齐）
MODE_KEYS = ("shared", "exclusive", "strict")

MODE_LABELS = {
    "shared": "共享",
    "exclusive": "独占",
    "strict": "严格",
}

#: 严格模式允许的 dtype（float32 会被 explicit_sample_format 拒绝）
STRICT_DTYPES = ("int32",)
#: 共享/宽松模式依次尝试的 dtype（顺序即优先级：整数优先，浮点兜底）
LOOSE_DTYPES = ("int32", "int16", "float32")

DEFAULT_MD = ROOT / "docs" / "1.7-独占能力矩阵.md"
DEFAULT_JSON = ROOT / "docs" / "1.7-独占能力矩阵.json"


# ---------------------------------------------------------------- 探测原语


def hostapi_name(index: int) -> str:
    try:
        return str(sd.query_hostapis(index).get("name", ""))
    except Exception:  # noqa: BLE001
        return ""


def is_wasapi(device_index: int) -> bool:
    """只有 WASAPI 设备才接受 ``WasapiSettings``；别的后端传了会直接报错。"""
    try:
        api = int(sd.query_devices(device_index).get("hostapi", -1))
    except Exception:  # noqa: BLE001
        return False
    return "wasapi" in hostapi_name(api).lower()


def extra_settings_for(mode: str, device_index: int) -> Optional[Any]:
    """模式 → ``sd.WasapiSettings``；非 WASAPI 设备返回 ``None``（= 该模式不可用）。"""
    if mode == "shared":
        return None
    if not is_wasapi(device_index):
        return None
    if mode == "exclusive":
        return sd.WasapiSettings(
            exclusive=True, auto_convert=False, explicit_sample_format=False
        )
    if mode == "strict":
        return sd.WasapiSettings(
            exclusive=True, auto_convert=False, explicit_sample_format=True
        )
    raise ValueError(f"未知模式：{mode!r}")


def check(
    device_index: int,
    rate: int,
    channels: int,
    dtypes: tuple[str, ...],
    mode: str,
) -> dict[str, Any]:
    """查一个 (设备, 采样率, 模式) 组合：返回可否 + 可用 dtype + 原始错误。

    只做 ``Pa_IsFormatSupported`` 查询，**绝不开流、绝不发声**。
    """
    extra = extra_settings_for(mode, device_index)
    if mode != "shared" and extra is None:
        return {
            "ok": False,
            "dtypes": [],
            "error": "非 WASAPI 设备不支持该模式",
            "na": True,
        }

    errors: list[str] = []
    working: list[str] = []
    for dtype in dtypes:
        try:
            sd.check_output_settings(
                device=device_index,
                samplerate=int(rate),
                channels=int(channels),
                dtype=dtype,
                extra_settings=extra,
            )
        except Exception as exc:  # noqa: BLE001 - 任何拒绝都只记录，不中断
            errors.append(f"{dtype}: {exc}")
            continue
        working.append(dtype)
    return {
        "ok": bool(working),
        "dtypes": working,
        "error": "" if working else ("; ".join(errors[:2]) if errors else ""),
    }


def verify_open(device_index: int, rate: int, channels: int, mode: str) -> dict[str, Any]:
    """**可选**：真开一条 30 ms 静音流再关，用于校验探测结论。

    默认不调用（工具默认纯查询）。但本机实测发现 ``check_output_settings``
    存在**假阳性**：dev18 的 44.1k 独占查询说可用，真实开流却报
    ``-9996 Invalid device``。所以「探测能不能信」这件事本身必须可复验 ——
    这就是本函数存在的理由。写入的是静音，听不到，但会短暂独占设备。
    """
    extra = extra_settings_for(mode, device_index)
    try:
        stream = sd.OutputStream(
            device=device_index,
            samplerate=int(rate),
            channels=int(channels),
            dtype="float32",
            blocksize=1024,
            extra_settings=extra,
        )
        stream.start()
        time.sleep(0.03)
        stream.stop()
        stream.close()
        return {"open": "OK", "error": ""}
    except Exception as exc:  # noqa: BLE001
        return {"open": "FAIL", "error": str(exc)}


def channels_to_probe(max_out: int) -> list[int]:
    """探测声道：``min(max_out, 8)``；全失败时下游会补试 8。

    多声道设备（dev19 = 8ch）用 2ch 探测会一律假阴性，这是设计文档补测 B 的结论。
    """
    primary = max(1, min(int(max_out), 8))
    candidates = [primary]
    if primary != 8 and int(max_out) >= 8:
        candidates.append(8)
    return candidates


def probe_device(
    device_index: int, rates: tuple[int, ...], *, verify: bool = False
) -> dict[str, Any]:
    """一台设备的三档矩阵。声道先按 max_out 试，全失败再补试 8ch。"""
    info = sd.query_devices(device_index)
    max_out = int(info.get("max_output_channels", 0) or 0)
    api = hostapi_name(int(info.get("hostapi", -1)))

    best: Optional[dict[str, Any]] = None
    for channels in channels_to_probe(max_out):
        modes: dict[str, dict[str, Any]] = {}
        for mode in MODE_KEYS:
            dtypes = STRICT_DTYPES if mode == "strict" else LOOSE_DTYPES
            modes[mode] = {
                str(rate): check(device_index, rate, channels, dtypes, mode)
                for rate in rates
            }
        if verify:
            modes["open_check"] = {
                str(rate): verify_open(device_index, rate, channels, "exclusive")
                for rate in rates
            }
        candidate = {
            "index": int(device_index),
            "name": str(info.get("name", "")),
            "backend": api,
            "max_output_channels": max_out,
            "channels_probed": channels,
            "default_samplerate": int(round(float(info.get("default_samplerate", 0) or 0))),
            "wasapi": is_wasapi(device_index),
            "modes": modes,
        }
        if best is None:
            best = candidate
        # 只要有一档能通，就认为这个声道数是「对的」，不再降级去试 8ch
        if any(
            entry["ok"]
            for mode_map in modes.values()
            for entry in mode_map.values()
        ):
            return candidate
    assert best is not None
    return best


def unlocked_rates(device: dict[str, Any]) -> list[int]:
    """独占比共享多出来的采样率 —— 本版最硬的产品卖点，必须自动列出来。"""
    result: list[int] = []
    for rate_text, shared in device["modes"]["shared"].items():
        if shared["ok"]:
            continue
        exclusive = device["modes"]["exclusive"].get(rate_text, {"ok": False})
        if exclusive["ok"]:
            result.append(int(rate_text))
    return sorted(result)


def describe_capability(device: dict[str, Any]) -> dict[str, Any]:
    """压成一句能直接给界面用的话。"""
    unlocked = unlocked_rates(device)
    strict_any = any(entry["ok"] for entry in device["modes"]["strict"].values())
    exclusive_any = any(entry["ok"] for entry in device["modes"]["exclusive"].values())
    if unlocked:
        hint = "独占可解锁 " + " / ".join(f"{rate / 1000:g}k" for rate in unlocked)
    elif exclusive_any:
        hint = "独占可用，但不解锁额外采样率"
    elif strict_any:
        hint = "仅严格独占可用"
    else:
        hint = "独占不可用（仅共享）"
    return {
        "unlocked_rates": unlocked,
        "exclusive_usable": exclusive_any,
        "strict_usable": strict_any,
        "hint": hint,
    }


# ---------------------------------------------------------------- 渲染


def _mark(entry: dict[str, Any]) -> str:
    if entry.get("na"):
        return "—"
    if not entry["ok"]:
        return "✗"
    dtypes = entry.get("dtypes") or []
    if len(dtypes) >= 3:
        return "✓"
    if dtypes == ["int32"]:
        return "✓32"
    if dtypes == ["int16"]:
        return "✓16"
    if dtypes == ["float32"]:
        return "✓f"
    return "✓" + "/".join(dt.replace("int", "").replace("float32", "f") for dt in dtypes)


def consistency(device: dict[str, Any]) -> tuple[int, int, list[str]]:
    """（一致条数, 总条数, 不一致明细）——只在做过真开流校验时有意义。"""
    checks = device["modes"].get("open_check")
    if not checks:
        return 0, 0, []
    same = 0
    total = 0
    bad: list[str] = []
    for rate_text, opened in checks.items():
        probed = device["modes"]["exclusive"].get(rate_text, {"ok": False})
        total += 1
        if probed["ok"] == (opened["open"] == "OK"):
            same += 1
        elif probed["ok"]:
            bad.append(f"{rate_text} Hz：探测说可用，真实开流 {opened['open']}"
                       f"（{opened['error'][:40]}）→ **假阳性**")
        else:
            bad.append(f"{rate_text} Hz：探测说不可用，真实开流 OK → **假阴性**")
    return same, total, bad


def render_markdown(report: dict[str, Any]) -> str:
    rates = report["rates"]
    devices = report["devices"]
    lines: list[str] = []
    lines.append("# v1.7 独占能力矩阵（本机实测）")
    lines.append("")
    lines.append("> 由 `tools/probe_exclusive.py` 生成，**零出声**（只做 "
                 "`Pa_IsFormatSupported` 查询，不开流）。")
    lines.append(f"> 生成时间：{report['generated_at']} ｜ "
                 f"Python {report['python']} ｜ sounddevice {report['sounddevice']} ｜ "
                 f"{report['platform']}")
    lines.append("> 复跑：`python tools/probe_exclusive.py`（`--all-backends` 连 MME/"
                 "DirectSound/WDM-KS 一起探；`--verify-open` 额外逐格真开 30 ms "
                 "静音流校验探测结论）")
    lines.append("")
    lines.append("## 一、结论摘要")
    lines.append("")
    unlocked_lines = [
        device for device in devices if describe_capability(device)["unlocked_rates"]
    ]
    if unlocked_lines:
        lines.append("**本机有设备在独占下解锁了共享模式放不了的采样率：**")
        lines.append("")
        for device in unlocked_lines:
            info = describe_capability(device)
            lines.append(
                f"- **{device['name']}**（{device['backend']}，dev{device['index']}，"
                f"{device['channels_probed']}ch）："
                + " / ".join(f"{rate} Hz" for rate in info["unlocked_rates"])
                + " 共享 ✗ → 独占 ✓"
            )
    else:
        lines.append("本机没有设备在独占下解锁额外采样率（独占仍可用于避免 44.1k 被重采样）。")
    lines.append("")
    verified = [device for device in devices if device["modes"].get("open_check")]
    if verified:
        same = sum(consistency(device)[0] for device in verified)
        total = sum(consistency(device)[1] for device in verified)
        lines.append("")
        lines.append(f"**探测可信度：{same}/{total} 与真实开流一致**"
                     "（`--verify-open` 逐格开过 30 ms 静音流）")
        lines.append("")
        for device in verified:
            same_one, total_one, bad = consistency(device)
            if not bad:
                continue
            lines.append(f"- dev{device['index']} {device['name']}：{same_one}/{total_one} 一致")
            for item in bad:
                lines.append(f"  - {item}")
        lines.append("")
    lines.append("| 设备 | 后端 | dev | 探测声道 | 独占可用 | 严格可用 | 说明 |")
    lines.append("|---|---|---|---|---|---|---|")
    for device in devices:
        info = describe_capability(device)
        lines.append(
            f"| {device['name']} | {device['backend']} | {device['index']} | "
            f"{device['channels_probed']} | {'✓' if info['exclusive_usable'] else '✗'} | "
            f"{'✓' if info['strict_usable'] else '✗'} | {info['hint']} |"
        )
    lines.append("")
    lines.append("## 二、总表（✓ 可用 / ✗ 不可用 / — 该后端无此模式）")
    lines.append("")
    lines.append("> `✓` = int16/int32/float32 均可用；`✓32` / `✓16` / `✓f` = "
                 "只有那一种可用；`—` = 该后端没有这个模式。严格模式固定只试 int32。")
    lines.append("")
    header = ["设备", "后端", "声道"]
    for rate in rates:
        for mode in MODE_KEYS:
            header.append(f"{rate / 1000:g}k {MODE_LABELS[mode]}")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "---|" * len(header))
    for device in devices:
        row = [device["name"], device["backend"], str(device["channels_probed"])]
        for rate in rates:
            for mode in MODE_KEYS:
                row.append(_mark(device["modes"][mode][str(rate)]))
        lines.append("| " + " | ".join(row) + " |")
    lines.append("")
    lines.append("## 三、逐设备明细（含原始错误，便于判断 -9997 是率还是格式）")
    for device in devices:
        lines.append("")
        lines.append(
            f"### dev{device['index']} · {device['name']}（{device['backend']}，"
            f"max_out={device['max_output_channels']}，探测 {device['channels_probed']}ch，"
            f"默认率 {device['default_samplerate']}）"
        )
        lines.append("")
        checks = device["modes"].get("open_check")
        has_verify = bool(checks)
        lines.append("| 采样率 | 共享 | 独占 | 严格 | 真开流（宽松独占 f32） | 首个错误 |"
                     if has_verify else "| 采样率 | 共享 | 独占 | 严格 | 首个错误 |")
        lines.append("|---|---|---|---|---|---|" if has_verify else "|---|---|---|---|---|")
        for rate in rates:
            cells = []
            first_error = ""
            for mode in MODE_KEYS:
                entry = device["modes"][mode][str(rate)]
                cells.append(_mark(entry))
                if not first_error and entry.get("error") and not entry.get("na"):
                    first_error = (
                        f"{MODE_LABELS[mode]}: " + str(entry["error"])[:84]
                    ).replace("|", "/")
            if has_verify:
                opened = checks.get(str(rate), {"open": "?", "error": ""})
                mark = "OK" if opened["open"] == "OK" else "FAIL"
                probed_ok = device["modes"]["exclusive"][str(rate)]["ok"]
                if probed_ok != (mark == "OK"):
                    mark += " **≠探测**"
                cells.append(mark)
            lines.append(f"| {rate} | " + " | ".join(cells) + f" | {first_error} |")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- 入口


def build_report(
    devices_filter: Optional[list[int]], all_backends: bool, *, verify: bool = False
) -> dict[str, Any]:
    indices: list[int] = []
    for index, info in enumerate(sd.query_devices()):
        if int(info.get("max_output_channels", 0) or 0) <= 0:
            continue
        if devices_filter and index not in devices_filter:
            continue
        if not all_backends and not is_wasapi(index):
            continue
        indices.append(index)
    devices = [probe_device(index, RATES, verify=verify) for index in indices]
    return {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "python": platform.python_version(),
        "sounddevice": getattr(sd, "__version__", "?"),
        "platform": f"{platform.system()} {platform.release()}",
        "rates": list(RATES),
        "modes": list(MODE_KEYS),
        "devices": devices,
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="WASAPI 独占能力探测（零出声）")
    parser.add_argument("--all-backends", action="store_true",
                        help="连 MME/DirectSound/WDM-KS 一起探（默认只探 WASAPI）")
    parser.add_argument("--devices", default="",
                        help="只探指定设备索引，逗号分隔，如 19,21")
    parser.add_argument("--md", default=str(DEFAULT_MD), help="Markdown 输出路径")
    parser.add_argument("--json", default=str(DEFAULT_JSON), help="JSON 输出路径")
    parser.add_argument("--no-write", action="store_true", help="只打印，不写文件")
    parser.add_argument("--verify-open", action="store_true",
                        help="逐个组合真开 30 ms 静音流校验探测结论（默认不开；"
                             "会短暂独占设备。用于回答「探测能不能信」）")
    parser.add_argument("--quiet", action="store_true", help="不打印明细")
    args = parser.parse_args(argv)

    devices_filter = [
        int(part) for part in args.devices.replace("，", ",").split(",") if part.strip()
    ]
    report = build_report(devices_filter or None, args.all_backends,
                         verify=args.verify_open)

    if not args.quiet:
        for device in report["devices"]:
            info = describe_capability(device)
            suffix = ""
        if device["modes"].get("open_check"):
            same_one, total_one, _ = consistency(device)
            suffix = f"  探测可信 {same_one}/{total_one}"
        print(f"dev{device['index']:02d} {device['name'][:44]:44s} "
                  f"{device['backend'][:18]:18s} {device['channels_probed']}ch  "
                  f"{info['hint']}{suffix}")
        print()

    if args.no_write:
        print(render_markdown(report))
        return 0

    md_path = Path(args.md)
    json_path = Path(args.json)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"已写入：{md_path}")
    print(f"已写入：{json_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
