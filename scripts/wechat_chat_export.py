"""Capture WeChat group chat screenshots for one date through ADB.

This script automates the stable part of the workflow:

1. open WeChat on the connected Android device;
2. locate a group through WeChat search, or use the current group;
3. open group info -> chat search -> date search;
4. select the requested date when it is visible in the current calendar month;
5. scroll and save screenshots for later timeline extraction.

WeChat does not expose chat message text through normal UI XML on many devices,
so the exported artifact is screenshots plus a Markdown timeline template.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path


WECHAT_PACKAGE = "com.tencent.mm"
SCREENSHOT_REMOTE_DIR = "/sdcard/wechat_chat_export"


@dataclass(frozen=True)
class Point:
    x: int
    y: int


@dataclass(frozen=True)
class Coords:
    search_button: Point = Point(900, 170)
    search_input: Point = Point(260, 170)
    first_search_result: Point = Point(430, 320)
    group_more: Point = Point(1000, 170)
    chat_record_search: Point = Point(260, 2035)
    date_filter: Point = Point(540, 515)
    month_selector: Point = Point(300, 282)
    month_confirm: Point = Point(720, 2195)
    calendar_first_week_y: int = 575
    calendar_row_height: int = 137
    calendar_col_x: tuple[int, ...] = (143, 275, 405, 535, 670, 800, 930)
    scroll_start: Point = Point(520, 1900)
    scroll_end: Point = Point(520, 650)


COORDS = Coords()


class StepError(RuntimeError):
    pass


def run(
    args: list[str],
    *,
    timeout: int = 20,
    capture_output: bool = True,
    check: bool = True,
) -> subprocess.CompletedProcess:
    completed = subprocess.run(
        args,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        capture_output=capture_output,
    )
    if check and completed.returncode != 0:
        raise StepError(
            "command failed: %s\nstdout=%s\nstderr=%s"
            % (" ".join(args), completed.stdout, completed.stderr)
        )
    return completed


def adb_args(serial: str | None, *parts: str) -> list[str]:
    args = ["adb"]
    if serial:
        args.extend(["-s", serial])
    args.extend(parts)
    return args


def adb(serial: str | None, *parts: str, timeout: int = 20, check: bool = True) -> subprocess.CompletedProcess:
    return run(adb_args(serial, *parts), timeout=timeout, check=check)


def shell(serial: str | None, command: str, *, timeout: int = 20, check: bool = True) -> subprocess.CompletedProcess:
    return adb(serial, "shell", command, timeout=timeout, check=check)


def tap(serial: str | None, point: Point, *, wait: float = 0.4) -> None:
    shell(serial, f"input tap {point.x} {point.y}", timeout=10)
    time.sleep(wait)


def swipe(serial: str | None, start: Point, end: Point, *, duration_ms: int = 700, wait: float = 0.6) -> None:
    shell(serial, f"input swipe {start.x} {start.y} {end.x} {end.y} {duration_ms}", timeout=10)
    time.sleep(wait)


def select_device_serial(requested: str | None) -> str | None:
    if requested:
        return requested
    completed = adb(None, "devices", "-l", timeout=10)
    lines = [line for line in completed.stdout.splitlines() if "\tdevice" in line]
    if len(lines) == 1:
        return lines[0].split()[0]
    if not lines:
        raise StepError("no adb device is ready")
    raise StepError("multiple adb devices found; pass -Serial. devices=%s" % ", ".join(line.split()[0] for line in lines))


def ensure_wechat(serial: str | None) -> None:
    adb(serial, "shell", "monkey", "-p", WECHAT_PACKAGE, "-c", "android.intent.category.LAUNCHER", "1", timeout=10)
    time.sleep(1.2)


def safe_name(value: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|]+", "_", value.strip())
    text = re.sub(r"\s+", " ", text)
    return text or "wechat_group"


def connect_u2(serial: str | None):
    try:
        import uiautomator2 as u2  # type: ignore
    except Exception as exc:  # pragma: no cover - optional runtime dependency
        raise StepError("uiautomator2 is required for Chinese group-name input: pip install uiautomator2") from exc
    return u2.connect(serial) if serial else u2.connect()


def send_text(serial: str | None, text: str) -> None:
    device = connect_u2(serial)
    try:
        device.set_fastinput_ime(True)
        time.sleep(0.3)
        device.send_keys(text, clear=True)
    except Exception as exc:
        raise StepError("failed to input text through uiautomator2: %s" % exc) from exc
    finally:
        try:
            device.set_fastinput_ime(False)
        except Exception:
            pass
    time.sleep(0.8)


def search_and_open_group(serial: str | None, group_name: str) -> None:
    tap(serial, COORDS.search_button, wait=0.8)
    tap(serial, COORDS.search_input, wait=0.5)
    send_text(serial, group_name)
    time.sleep(1.2)
    tap(serial, COORDS.first_search_result, wait=1.2)


def open_date_search(serial: str | None) -> None:
    tap(serial, COORDS.group_more, wait=1.0)
    tap(serial, COORDS.chat_record_search, wait=1.0)
    tap(serial, COORDS.date_filter, wait=1.0)


def calendar_point(target: date) -> Point:
    # WeChat calendar is Sunday-first. The visible month must already match target.
    first = target.replace(day=1)
    # Python Monday=0..Sunday=6; convert to Sunday=0..Saturday=6.
    first_weekday = (first.weekday() + 1) % 7
    index = first_weekday + target.day - 1
    row = index // 7
    col = index % 7
    return Point(COORDS.calendar_col_x[col], COORDS.calendar_first_week_y + row * COORDS.calendar_row_height)


def select_visible_date(serial: str | None, target: date) -> None:
    point = calendar_point(target)
    tap(serial, point, wait=1.2)


def take_screenshot(serial: str | None, local_path: Path, remote_name: str, *, keep_remote: bool) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    remote_path = f"{SCREENSHOT_REMOTE_DIR}/{remote_name}"
    shell(serial, f"mkdir -p {SCREENSHOT_REMOTE_DIR}", timeout=10)
    shell(serial, f"screencap -p {remote_path}", timeout=15)
    adb(serial, "pull", remote_path, str(local_path), timeout=20)
    if not keep_remote:
        shell(serial, f"rm -f {remote_path}", timeout=10, check=False)


def capture_pages(serial: str | None, out_dir: Path, pages: int, *, keep_remote: bool) -> list[Path]:
    screenshots: list[Path] = []
    first = out_dir / "000_start.png"
    take_screenshot(serial, first, "000_start.png", keep_remote=keep_remote)
    screenshots.append(first)
    for index in range(1, pages + 1):
        swipe(serial, COORDS.scroll_start, COORDS.scroll_end, duration_ms=700, wait=0.8)
        path = out_dir / f"{index:03d}.png"
        take_screenshot(serial, path, f"{index:03d}.png", keep_remote=keep_remote)
        screenshots.append(path)
    return screenshots


def write_manifest(out_dir: Path, args: argparse.Namespace, serial: str | None, screenshots: list[Path]) -> None:
    payload = {
        "group_name": args.group_name,
        "date": args.date,
        "pages": args.pages,
        "serial": serial,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "screenshots": [path.name for path in screenshots],
        "notes": [
            "WeChat chat text may not be available in UI XML; screenshots are the source of truth.",
            "Use timeline.md to manually or OCR-assist the final summary.",
        ],
    }
    (out_dir / "manifest.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_timeline_template(out_dir: Path, args: argparse.Namespace, screenshots: list[Path]) -> None:
    lines = [
        f"# 微信聊天记录整理：{args.group_name} / {args.date}",
        "",
        "## 截图清单",
        "",
    ]
    lines.extend(f"- `{path.name}`" for path in screenshots)
    lines.extend(
        [
            "",
            "## 时间线",
            "",
            "| 时间 | 人物 | 发言/动作 | 来源截图 |",
            "| --- | --- | --- | --- |",
            "|  |  |  |  |",
            "",
            "## 摘要",
            "",
            "- ",
            "",
            "## 使用说明",
            "",
            "微信在部分安卓设备上不暴露聊天文本节点，本脚本先稳定采集日期定位后的连续截图。",
            "后续可以基于这些截图人工整理，或接入 OCR 后自动填充上面的时间线表格。",
        ]
    )
    (out_dir / "timeline.md").write_text("\n".join(lines), encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture WeChat group chat screenshots for one date.")
    parser.add_argument("--group-name", required=True, help="WeChat group name.")
    parser.add_argument("--date", required=True, help="Target date in YYYY-MM-DD.")
    parser.add_argument("--pages", type=int, default=12, help="Number of scroll screenshots after the first screen.")
    parser.add_argument("--out-dir", default="exports/wechat", help="Output root directory.")
    parser.add_argument("--serial", default="", help="ADB device serial.")
    parser.add_argument("--skip-navigation", action="store_true", help="Skip WeChat navigation and only capture current screen + scrolls.")
    parser.add_argument("--no-search", action="store_true", help="Assume the target group is already open; still open info/date search.")
    parser.add_argument("--keep-on-device", action="store_true", help="Keep screenshots under /sdcard/wechat_chat_export.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    target = date.fromisoformat(args.date)
    serial = select_device_serial(args.serial or None)
    out_dir = Path(args.out_dir) / safe_name(args.group_name) / target.isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"device: {serial or '<default>'}")
    print(f"output: {out_dir}")

    if not args.skip_navigation:
        ensure_wechat(serial)
        if not args.no_search:
            print("search group...")
            search_and_open_group(serial, args.group_name)
        print("open chat record date search...")
        open_date_search(serial)
        print("select visible date...")
        select_visible_date(serial, target)
        time.sleep(1.0)
    else:
        print("skip navigation; capture current WeChat screen")

    print("capture screenshots...")
    screenshots = capture_pages(serial, out_dir, max(args.pages, 0), keep_remote=bool(args.keep_on_device))
    write_manifest(out_dir, args, serial, screenshots)
    write_timeline_template(out_dir, args, screenshots)
    print(f"done: {len(screenshots)} screenshots")
    print(f"timeline: {out_dir / 'timeline.md'}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except StepError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2)
