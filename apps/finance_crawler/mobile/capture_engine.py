import argparse
import hashlib
import json
import shlex
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

from apps.finance_crawler.config import Config
from apps.finance_crawler.crawlers.registry import (
    build_direct_app_link,
    is_reasonable_app_url,
    readiness_keywords_for_url,
    supported_schemes,
    target_package_for_url,
)

DEFAULT_CACHE_PATH = Config.CACHE_FILE
_SCHEME_CACHE_LOCK = threading.Lock()
_RAPIDOCR_ENGINE: Any = None
_RAPIDOCR_UNAVAILABLE = False


def run_adb(args: List[str], serial: Optional[str] = None, timeout: int = 20) -> str:
    cmd = [Config.ADB_PATH or "adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
        encoding="utf-8",
        errors="replace",
    )
    return result.stdout.strip()


def run_adb_bytes(args: List[str], serial: Optional[str] = None, timeout: int = 20) -> bytes:
    cmd = [Config.ADB_PATH or "adb"]
    if serial:
        cmd += ["-s", serial]
    cmd += args
    result = subprocess.run(
        cmd,
        check=True,
        capture_output=True,
        timeout=timeout,
    )
    return result.stdout


def screenshot_exec_out(image_path: Path, serial: Optional[str] = None, timeout: int = 15) -> None:
    image_path.parent.mkdir(parents=True, exist_ok=True)
    data = run_adb_bytes(["exec-out", "screencap", "-p"], serial=serial, timeout=timeout)
    png_header = b"\x89PNG\r\n\x1a\n"
    start = data.find(png_header)
    if start < 0:
        raise RuntimeError("adb exec-out screencap did not return PNG data")
    image_path.write_bytes(data[start:])


def save_screenshot(device, image_path: Path, serial: Optional[str] = None) -> str:
    try:
        screenshot_exec_out(image_path, serial=serial)
        return "adb_exec_out"
    except Exception as exc:
        print(f"adb exec-out screenshot failed; fallback to uiautomator2: {exc}", file=sys.stderr)
        device.screenshot(str(image_path))
        return "uiautomator2"


def is_package_installed(package_name: str, serial: Optional[str] = None) -> bool:
    try:
        return bool(run_adb(["shell", "pm", "path", package_name], serial=serial, timeout=8))
    except Exception:
        return False


def load_scheme_cache(cache_path: Path = DEFAULT_CACHE_PATH) -> Dict[str, str]:
    if not cache_path.exists():
        return {}
    try:
        return json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_scheme_cache(cache: Dict[str, str], cache_path: Path = DEFAULT_CACHE_PATH) -> None:
    cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


def build_direct_app_deep_link(url: str) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None

    return build_direct_app_link(url)


def resolve_app_deep_link(url: str, timeout: int = 15, use_cache: bool = True) -> Optional[str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None

    direct_scheme = build_direct_app_deep_link(url)
    if direct_scheme:
        return direct_scheme

    if use_cache:
        with _SCHEME_CACHE_LOCK:
            cache = load_scheme_cache()
    else:
        cache = {}
    cached = cache.get(url)
    if cached:
        print("Resolved app scheme from cache.")
        return cached

    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Linux; Android 12) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36"
            )
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            final_url = response.geturl()
    except Exception as exc:
        print(f"Scheme resolve skipped: {exc}", file=sys.stderr)
        return None

    query = parse_qs(urlparse(final_url).query)
    schemes = query.get("scheme") or []
    if schemes and schemes[0].startswith(("alipays://", "alipay://")):
        if use_cache:
            with _SCHEME_CACHE_LOCK:
                cache = load_scheme_cache()
                cache[url] = schemes[0]
                save_scheme_cache(cache)
        return schemes[0]
    return None


def open_app_link(url: str, serial: Optional[str] = None) -> None:
    resolved_url = resolve_app_deep_link(url) or url
    if resolved_url != url:
        print("Resolved app scheme from share link.")

    args = [
        "shell",
        "am",
        "start",
        "-a",
        "android.intent.action.VIEW",
        "-d",
        shlex.quote(resolved_url),
    ]
    target_package = target_package_for_url(resolved_url)
    if target_package:
        if not is_package_installed(target_package, serial=serial):
            raise RuntimeError(f"target app package is not installed: {target_package}")
        args += ["-p", target_package]
    run_adb(args, serial=serial)


def ensure_reasonable_url(url: str) -> None:
    if not is_reasonable_app_url(url):
        schemes = ", ".join(sorted(supported_schemes()))
        raise ValueError(f"URL scheme must be one of: {schemes}")


def connect_uiautomator(serial: Optional[str]):
    try:
        import uiautomator2 as u2
    except ImportError as exc:
        raise RuntimeError("Missing dependency: pip install uiautomator2") from exc

    return u2.connect(serial) if serial else u2.connect()


def set_device_awake(serial: Optional[str]) -> None:
    try:
        run_adb(["shell", "input", "keyevent", "WAKEUP"], serial=serial, timeout=5)
        run_adb(["shell", "svc", "power", "stayon", "true"], serial=serial, timeout=5)
    except Exception as exc:
        print(f"Wake/stay-awake skipped: {exc}", file=sys.stderr)


def is_lockscreen_showing(serial: Optional[str]) -> bool:
    try:
        output = run_adb(["shell", "dumpsys", "window"], serial=serial, timeout=10)
    except Exception:
        return False
    return "mDreamingLockscreen=true" in output or "mShowingLockscreen=true" in output


def screenshot_stats(image_path: Path) -> Dict[str, Any]:
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return {"available": False}

    image = Image.open(image_path).convert("L")
    width, height = image.size
    content = image.crop((0, int(height * 0.16), width, int(height * 0.92)))
    stat = ImageStat.Stat(content)
    extrema = content.getextrema()
    mean = stat.mean[0]
    contrast = extrema[1] - extrema[0]
    return {
        "available": True,
        "mean": round(mean, 2),
        "contrast": contrast,
        "is_black": mean < 8,
        "is_blank_white": mean > 248 and contrast < 25,
    }


def wait_for_page_ready(
    device,
    output_dir: Path,
    min_wait: float,
    timeout: float,
    interval: float,
    serial: Optional[str] = None,
    on_retry=None,
    max_retries: int = 0,
    ready_keywords: tuple[str, ...] = (),
) -> Dict[str, Any]:
    time.sleep(min_wait)
    output_dir.mkdir(parents=True, exist_ok=True)
    checks = []
    attempts = []

    for attempt in range(max_retries + 1):
        started = time.perf_counter()
        last_xml = ""

        while True:
            elapsed = time.perf_counter() - started
            xml_text = device.dump_hierarchy(compressed=True)
            last_xml = xml_text
            check_path = output_dir / f"_ready_check_{attempt}.png"
            save_screenshot(device, check_path, serial=serial)
            stats = screenshot_stats(check_path)

            has_title = any(keyword in xml_text for keyword in ready_keywords) if ready_keywords else True
            has_webview = "WebView" in xml_text or "h5_web_content" in xml_text
            screen_usable = not stats.get("is_black") and not stats.get("is_blank_white")
            ready = has_title and has_webview and screen_usable

            check = {
                "attempt": attempt + 1,
                "elapsed": round(elapsed, 3),
                "has_title": has_title,
                "has_webview": has_webview,
                "screen": stats,
                "ready": ready,
            }
            checks.append(check)
            if ready:
                return {
                    "ready": True,
                    "elapsed": round(time.perf_counter() - started, 3),
                    "attempts": attempt + 1,
                    "checks": checks,
                }
            if elapsed >= timeout:
                attempts.append(
                    {
                        "attempt": attempt + 1,
                        "elapsed": round(elapsed, 3),
                        "last_xml_sample": last_xml[:1000],
                    }
                )
                if attempt < max_retries and on_retry:
                    print(f"Ready wait timed out; retrying open ({attempt + 1}/{max_retries}).")
                    on_retry()
                    time.sleep(min_wait)
                    break
                return {
                    "ready": False,
                    "elapsed": round(time.perf_counter() - started, 3),
                    "attempts": attempt + 1,
                    "checks": checks,
                    "timeouts": attempts,
                }
            time.sleep(interval)


def parse_bounds(bounds: Optional[str]) -> Optional[Dict[str, int]]:
    if not bounds:
        return None
    cleaned = bounds.replace("][", ",").replace("[", "").replace("]", "")
    parts = cleaned.split(",")
    if len(parts) != 4:
        return None
    try:
        left, top, right, bottom = [int(part) for part in parts]
    except ValueError:
        return None
    return {
        "left": left,
        "top": top,
        "right": right,
        "bottom": bottom,
        "width": right - left,
        "height": bottom - top,
    }


def node_to_record(node: ET.Element, page_index: int) -> Optional[Dict[str, Any]]:
    text = (node.attrib.get("text") or "").strip()
    desc = (node.attrib.get("content-desc") or "").strip()
    resource_id = node.attrib.get("resource-id") or ""

    if not text and not desc and not resource_id:
        return None

    return {
        "page_index": page_index,
        "text": text,
        "content_desc": desc,
        "resource_id": resource_id,
        "class": node.attrib.get("class") or "",
        "package": node.attrib.get("package") or "",
        "clickable": node.attrib.get("clickable") == "true",
        "enabled": node.attrib.get("enabled") == "true",
        "selected": node.attrib.get("selected") == "true",
        "bounds": parse_bounds(node.attrib.get("bounds")),
    }


def collect_ui_records(xml_text: str, page_index: int) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    records: List[Dict[str, Any]] = []
    for node in root.iter("node"):
        record = node_to_record(node, page_index)
        if record:
            records.append(record)
    return records


def stable_key(record: Dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "text": record.get("text"),
            "content_desc": record.get("content_desc"),
            "resource_id": record.get("resource_id"),
            "class": record.get("class"),
            "bounds": record.get("bounds"),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def save_text(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")


def append_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    with path.open("a", encoding="utf-8") as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + "\n")


def try_ocr(image_path: Path) -> Optional[List[Dict[str, Any]]]:
    global _RAPIDOCR_ENGINE, _RAPIDOCR_UNAVAILABLE
    if _RAPIDOCR_UNAVAILABLE:
        return None

    try:
        if _RAPIDOCR_ENGINE is None:
            from rapidocr_onnxruntime import RapidOCR

            _RAPIDOCR_ENGINE = RapidOCR()
        result, _ = _RAPIDOCR_ENGINE(str(image_path))
    except Exception as exc:
        _RAPIDOCR_UNAVAILABLE = True
        print(f"RapidOCR skipped for {image_path.name}: {exc}", file=sys.stderr)
        return None

    rows: List[Dict[str, Any]] = []
    for item in result or []:
        if len(item) < 3:
            continue
        points, text, confidence = item[0], str(item[1] or "").strip(), item[2]
        if not text:
            continue
        xs = [float(point[0]) for point in points]
        ys = [float(point[1]) for point in points]
        left = int(min(xs))
        top = int(min(ys))
        right = int(max(xs))
        bottom = int(max(ys))
        rows.append(
            {
                "text": text,
                "confidence": float(confidence) * 100.0,
                "bounds": {
                    "left": left,
                    "top": top,
                    "width": max(0, right - left),
                    "height": max(0, bottom - top),
                },
                "engine": "rapidocr",
            }
        )
    return rows


def scroll_forward(device, duration: float = 0.35) -> bool:
    try:
        scrollable = device(scrollable=True)
        if scrollable.exists:
            if scrollable.scroll.forward():
                return True
    except Exception:
        pass

    width, height = device.window_size()
    start_x = width // 2
    start_y = int(height * 0.78)
    end_y = int(height * 0.28)
    device.swipe(start_x, start_y, start_x, end_y, duration)
    return True


def current_screen_signature(xml_text: str) -> str:
    root = ET.fromstring(xml_text)
    visible_text = []
    for node in root.iter("node"):
        text = (node.attrib.get("text") or node.attrib.get("content-desc") or "").strip()
        if text:
            visible_text.append(text)
    sample = "\n".join(visible_text[:80])
    return hashlib.sha1(sample.encode("utf-8")).hexdigest()


def capture_pages(
    device,
    output_dir: Path,
    max_scrolls: int,
    wait_after_open: float,
    wait_after_scroll: float,
    enable_ocr: bool,
    dynamic_wait: bool,
    ready_timeout: float,
    ready_check_interval: float,
    serial: Optional[str] = None,
    retry_open=None,
    ready_retries: int = 0,
    ready_keywords: tuple[str, ...] = (),
) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    ui_jsonl = output_dir / "ui_records.jsonl"
    ocr_jsonl = output_dir / "ocr_records.jsonl"

    seen_record_keys: Set[str] = set()
    seen_screen_signatures: Set[str] = set()
    total_ui_records = 0
    total_ocr_records = 0

    readiness: Optional[Dict[str, Any]] = None
    if dynamic_wait:
        readiness = wait_for_page_ready(
            device=device,
            output_dir=output_dir,
            min_wait=wait_after_open,
            timeout=ready_timeout,
            interval=ready_check_interval,
            serial=serial,
            on_retry=retry_open,
            max_retries=ready_retries,
            ready_keywords=ready_keywords,
        )
        (output_dir / "readiness.json").write_text(
            json.dumps(readiness, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if readiness.get("ready"):
            print(f"Ready after {readiness.get('elapsed')}s.")
        else:
            print(f"Ready wait timed out after {readiness.get('elapsed')}s; capturing current screen.")
    else:
        time.sleep(wait_after_open)

    for page_index in range(max_scrolls + 1):
        xml_text = device.dump_hierarchy(compressed=False, pretty=True)
        signature = current_screen_signature(xml_text)

        xml_path = output_dir / f"page_{page_index:03d}.xml"
        screenshot_path = output_dir / f"page_{page_index:03d}.png"
        save_text(xml_path, xml_text)
        save_screenshot(device, screenshot_path, serial=serial)

        ui_records = collect_ui_records(xml_text, page_index)
        new_ui_records = []
        for record in ui_records:
            key = stable_key(record)
            if key in seen_record_keys:
                continue
            seen_record_keys.add(key)
            new_ui_records.append(record)
        append_jsonl(ui_jsonl, new_ui_records)
        total_ui_records += len(new_ui_records)

        if enable_ocr:
            ocr_records = try_ocr(screenshot_path)
            if ocr_records is None:
                print("OCR skipped: install rapidocr-onnxruntime.", file=sys.stderr)
                enable_ocr = False
            else:
                for row in ocr_records:
                    row["page_index"] = page_index
                    row["screenshot"] = screenshot_path.name
                append_jsonl(ocr_jsonl, ocr_records)
                total_ocr_records += len(ocr_records)

        print(
            f"page={page_index} ui_new={len(new_ui_records)} "
            f"ui_total={total_ui_records} screenshot={screenshot_path.name}"
        )

        if page_index >= max_scrolls:
            break
        if signature in seen_screen_signatures:
            print("Stop: screen content repeated.")
            break
        seen_screen_signatures.add(signature)

        moved = scroll_forward(device)
        if not moved:
            print("Stop: no more scrollable content.")
            break
        time.sleep(wait_after_scroll)

    return {
        "output_dir": str(output_dir),
        "ui_records": total_ui_records,
        "ocr_records": total_ocr_records,
        "ui_jsonl": str(ui_jsonl),
        "ocr_jsonl": str(ocr_jsonl) if ocr_jsonl.exists() else None,
        "readiness": readiness,
    }


def write_metadata(output_dir: Path, args: argparse.Namespace) -> None:
    metadata = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "url": args.url,
        "serial": args.serial,
        "max_scrolls": args.max_scrolls,
        "wait_after_open": args.wait_after_open,
        "wait_after_scroll": args.wait_after_scroll,
        "dynamic_wait": not args.no_dynamic_wait,
        "ready_timeout": args.ready_timeout,
        "ready_check_interval": args.ready_check_interval,
        "ocr": args.ocr,
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Open a finance app post link by ADB, capture UI hierarchy, screenshots, optional OCR, and scroll results."
    )
    parser.add_argument("url", help="Post URL or app deep link to open on the connected Android device.")
    parser.add_argument("--serial", help="ADB device serial when multiple devices are connected.")
    parser.add_argument("--output", default="captures", help="Base output directory.")
    parser.add_argument("--max-scrolls", type=int, default=8, help="How many times to scroll after first capture.")
    parser.add_argument("--wait-after-open", type=float, default=0.5, help="Minimum seconds to wait after opening link.")
    parser.add_argument("--wait-after-scroll", type=float, default=2.0, help="Seconds to wait after each scroll.")
    parser.add_argument("--ready-timeout", type=float, default=12.0, help="Max seconds to wait for app page content.")
    parser.add_argument("--ready-check-interval", type=float, default=0.6, help="Seconds between page readiness checks.")
    parser.add_argument("--ready-retries", type=int, default=1, help="How many times to reopen the link if readiness times out.")
    parser.add_argument("--ocr", action="store_true", help="Enable OCR from screenshots via RapidOCR.")
    parser.add_argument("--skip-open", action="store_true", help="Do not send intent; capture the current screen.")
    parser.add_argument("--no-dynamic-wait", action="store_true", help="Use fixed wait-after-open instead of readiness checks.")
    parser.add_argument("--no-stay-awake", action="store_true", help="Do not wake the device or enable stay-awake while plugged in.")
    parser.add_argument("--allow-locked", action="store_true", help="Continue even if the Android lockscreen is showing.")
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    ensure_reasonable_url(args.url)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output) / f"finance_{stamp}"
    output_dir.mkdir(parents=True, exist_ok=True)
    write_metadata(output_dir, args)

    print("Connecting uiautomator2...")
    device = connect_uiautomator(args.serial)

    if not args.no_stay_awake:
        set_device_awake(args.serial)
    if not args.allow_locked and is_lockscreen_showing(args.serial):
        raise RuntimeError("Device is locked. Unlock the phone, then run again.")

    def open_current_link() -> None:
        print("Opening link in target app...")
        open_app_link(args.url, serial=args.serial)

    if not args.skip_open:
        open_current_link()

    summary = capture_pages(
        device=device,
        output_dir=output_dir,
        max_scrolls=args.max_scrolls,
        wait_after_open=args.wait_after_open,
        wait_after_scroll=args.wait_after_scroll,
        enable_ocr=args.ocr,
        dynamic_wait=not args.no_dynamic_wait,
        ready_timeout=args.ready_timeout,
        ready_check_interval=args.ready_check_interval,
        serial=args.serial,
        retry_open=None if args.skip_open else open_current_link,
        ready_retries=args.ready_retries,
        ready_keywords=readiness_keywords_for_url(args.url),
    )

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
