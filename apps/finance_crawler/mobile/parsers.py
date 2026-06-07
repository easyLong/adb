"""Generic parsers for finance community post screens."""

from __future__ import annotations

import re
from typing import Any


def extract_account_name(texts: list[str]) -> str:
    ignore_exact = {
        "\u5173\u6ce8",
        "\u5df2\u5173\u6ce8",
        "\u8bc4\u8bba",
        "\u9605\u8bfb",
        "\u70b9\u8d5e",
        "\u5206\u4eab",
        "\u6536\u85cf",
        "\u56de\u590d",
        "\u6253\u5f00",
        "\u5c55\u5f00",
        "\u67e5\u770b\u66f4\u591a",
        "\u5934\u50cf",
        "\u8fd4\u56de",
        "\u66f4\u591a",
        "\u5317\u4eac",
        "\u4e0a\u6d77",
        "\u5929\u6d25",
        "\u91cd\u5e86",
        "\u6c5f\u897f",
        "\u798f\u5efa",
        "\u5e7f\u4e1c",
        "\u6c5f\u82cf",
        "\u6d59\u6c5f",
        "\u5c71\u4e1c",
        "\u56db\u5ddd",
        "\u6cb3\u5357",
        "\u6cb3\u5317",
        "\u6e56\u5357",
        "\u6e56\u5317",
    }
    ignore_contains = {
        "\u652f\u4ed8\u5b9d",
        "\u8682\u8681\u8d22\u5bcc",
        "\u7406\u8d22",
        "\u57fa\u91d1",
        "\u9605\u8bfb",
        "\u8bc4\u8bba",
        "\u70b9\u8d5e",
        "\u5173\u6ce8",
        "\u5f00\u542f\u62a4\u773c\u6a21\u5f0f",
        "NFC",
        "\u84dd\u7259",
        "\u624b\u673a\u4fe1\u53f7",
        "\u6b63\u5728\u5145\u7535",
        "\u5185\u5bb9\u4e0d\u89c1\u4e86",
        "\u5148\u53bb\u770b\u770b\u5176\u4ed6\u7684\u5427",
        "\u5185\u5bb9\u4e0d\u5b58\u5728",
        "\u5df2\u88ab\u5220\u9664",
        "\u65e0\u6cd5\u67e5\u770b\u8be5\u5185\u5bb9",
        "\u7f51\u7edc\u4e0d\u7ed9\u529b",
        "\u52a0\u8f7d\u5931\u8d25",
        "\u8bf7\u6c42\u8d85\u65f6",
        "\u632f\u94c3\u5668",
        "Android \u7cfb\u7edf\u901a\u77e5",
        "\u7cfb\u7edf\u901a\u77e5",
        "\u624b\u673a\u7ba1\u5bb6\u901a\u77e5",
        "\u65e0\u7ebf\u8c03\u8bd5",
        "\u6ca1\u6709 SIM \u5361",
        "WLAN",
        "\u5df2\u5b8c\u6210\u767e\u5206\u4e4b",
        "\u6b63\u5728\u5145\u7535",
    }

    relative_time_pattern = re.compile(
        r"^(?:刚刚|\d+\s*(?:秒|分钟|小时|天|周|个月|年)前|昨天|前天|\d{1,2}[-/]\d{1,2}(?:\s*\d{1,2}:\d{2})?)$"
    )

    def usable(text: str, *, allow_numeric: bool = False) -> bool:
        cleaned = text.strip()
        if not cleaned or len(cleaned) > 30:
            return False
        if cleaned in ignore_exact:
            return False
        if any(word in cleaned for word in ignore_contains):
            return False
        if re.fullmatch(r"\d{1,2}:\d{2}", cleaned):
            return False
        if re.search(r"https?://|ur\.alipay\.com|\d{4}-\d{2}-\d{2}", cleaned):
            return False
        if relative_time_pattern.fullmatch(cleaned):
            return False
        if re.search(r"^\d+$", cleaned) and not allow_numeric:
            return False
        return True

    for index, text in enumerate(texts[:40]):
        if text.strip() != "\u5934\u50cf":
            continue
        for candidate in texts[index + 1 : index + 6]:
            if usable(candidate, allow_numeric=True):
                return candidate.strip()

    for text in texts[:40]:
        cleaned = text.strip()
        if usable(cleaned):
            return cleaned
    return ""


def extract_post_content(texts: list[str]) -> str:
    content_parts: list[str] = []
    for text in current_post_scope_texts(texts):
        cleaned = (text or "").strip()
        if not cleaned:
            continue
        if is_post_content_stop(cleaned):
            break
        if is_post_content_noise(cleaned):
            continue
        # The first long/business text after author metadata is the post body.
        if len(cleaned) < 8 and not content_parts:
            continue
        content_parts.append(cleaned)
    return "\n".join(dict.fromkeys(content_parts))


def parse_numbers_with_presence(texts: list[str]) -> tuple[int, int, bool, bool]:
    scoped_texts = current_post_scope_texts(texts)
    read_count = 0
    comment_count = 0
    read_found = False

    no_comments = any(
        "\u6682\u65e0\u8bc4\u8bba" in text
        or "\u70b9\u51fb\u62a2\u9996\u8bc4" in text
        or "\u8bf4\u8bf4\u4f60\u7684\u60f3\u6cd5" in text
        for text in scoped_texts
    )
    comment_found = no_comments
    number = r"(?P<num>\d+(?:[,.]\d+)*(?:\.\d+)?\s*[\u4e07wWkK\u5343]?)"
    for text in number_candidates(scoped_texts):
        compact = normalize_count_text(text)
        for pattern in (
            rf"{number}(?:\u6b21)?(?:\u9605\u8bfb|\u6d4f\u89c8|\u67e5\u770b|\u9605)",
            rf"(?:\u9605\u8bfb|\u6d4f\u89c8|\u67e5\u770b|\u9605)(?:\u91cf|\u6570)?{number}",
        ):
            match = re.search(pattern, compact)
            if match:
                prefix = compact[: match.start()]
                if any(word in prefix for word in ("\u8bc4\u8bba", "\u56de\u590d", "\u7559\u8a00")):
                    continue
                read_found = True
                read_count = max(read_count, parse_count_token(match.group("num")))
        for pattern in (
            rf"{number}(?:\u6761)?(?:\u8bc4\u8bba|\u56de\u590d|\u7559\u8a00|\u8bc4)",
            rf"(?:\u8bc4\u8bba|\u56de\u590d|\u7559\u8a00|\u8bc4)(?:\u6570|\u91cf)?{number}",
        ):
            match = re.search(pattern, compact)
            if match:
                prefix = compact[: match.start()]
                suffix = compact[match.end() :]
                if any(word in prefix for word in ("\u9605\u8bfb", "\u6d4f\u89c8", "\u67e5\u770b")):
                    continue
                if any(word in suffix for word in ("\u9605\u8bfb", "\u6d4f\u89c8", "\u67e5\u770b")):
                    continue
                if (
                    match.start() == 0
                    and match.end() == len(compact)
                    and re.search(r"[\u4e07wWkK\u5343]", match.group("num"))
                ):
                    continue
                comment_found = True
                comment_count = max(comment_count, parse_count_token(match.group("num")))
    if no_comments:
        comment_count = 0
    return read_count, comment_count, read_found, comment_found


def parse_numbers(texts: list[str]) -> tuple[int, int]:
    read_count, comment_count, _, _ = parse_numbers_with_presence(texts)
    return read_count, comment_count


def extract_article_title(texts: list[str], content: str | None = None) -> str:
    """Best-effort article title from visible text, falling back to content."""

    for text in current_post_scope_texts(texts):
        cleaned = (text or "").strip()
        if not cleaned or is_post_content_noise(cleaned) or is_post_content_stop(cleaned):
            continue
        if _looks_like_metadata(cleaned):
            continue
        if 4 <= len(cleaned) <= 80:
            return cleaned

    for line in (content or "").splitlines():
        cleaned = line.strip()
        if 4 <= len(cleaned) <= 80:
            return cleaned
    return ""


def parse_like_count(texts: list[str]) -> tuple[int, bool]:
    like_count = 0
    like_found = False
    number = r"(?P<num>\d+(?:[,.]\d+)*(?:\.\d+)?\s*[\u4e07wWkK\u5343]?)"
    for text in number_candidates(current_post_scope_texts(texts)):
        compact = normalize_count_text(text)
        for pattern in (
            rf"{number}(?:\u4e2a)?(?:\u70b9\u8d5e|\u8d5e)",
            rf"(?:\u70b9\u8d5e|\u8d5e)(?:\u6570|\u91cf)?{number}",
        ):
            match = re.search(pattern, compact)
            if not match:
                continue
            prefix = compact[: match.start()]
            suffix = compact[match.end() :]
            if any(word in prefix for word in ("\u8bc4\u8bba", "\u56de\u590d", "\u9605\u8bfb", "\u6d4f\u89c8")):
                continue
            if any(word in suffix for word in ("\u8bc4\u8bba", "\u56de\u590d", "\u9605\u8bfb", "\u6d4f\u89c8")):
                continue
            like_found = True
            like_count = max(like_count, parse_count_token(match.group("num")))
    return like_count, like_found


def parse_count_token(raw: str) -> int:
    text = re.sub(r"\s+", "", raw.replace(",", "")).lower()
    match = re.fullmatch(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>[\u4e07wk\u5343]?)", text)
    if not match:
        return 0

    value = float(match.group("num"))
    unit = match.group("unit")
    if unit in {"\u4e07", "w"}:
        value *= 10000
    elif unit == "k":
        value *= 1000
    elif unit == "\u5343":
        value *= 1000
    return int(value)


def extract_profile_fans_count(records: list[dict[str, Any]]) -> int | None:
    """Extract the number displayed above the profile "fans" label."""

    numeric: list[tuple[int, dict[str, Any], dict[str, int]]] = []
    labels: list[tuple[dict[str, Any], dict[str, int]]] = []
    fans_label = "\u7c89\u4e1d"

    for record in records:
        text = str(record.get("text") or "").strip()
        bounds = _profile_counter_bounds(record.get("bounds") or {})
        if not text or not isinstance(bounds, dict):
            continue
        inline_match = re.search(
            r"(?P<num>\d+(?:\.\d+)?(?:[\u4e07wWkK])?)(?=\u7c89\u4e1d)",
            re.sub(r"\s+", "", text),
        )
        if inline_match:
            return _parse_profile_counter(inline_match.group("num"))
        if fans_label in text:
            labels.append((record, bounds))
        parsed = _parse_profile_counter(text)
        if parsed is not None:
            numeric.append((parsed, record, bounds))

    for _, label_bounds in labels:
        label_x = (int(label_bounds.get("left", 0)) + int(label_bounds.get("right", 0))) / 2
        label_left = int(label_bounds.get("left", 0))
        label_right = int(label_bounds.get("right", 0))
        label_width = max(label_right - label_left, 1)
        label_top = int(label_bounds.get("top", 0))
        candidates: list[tuple[float, int]] = []
        for value, _, number_bounds in numeric:
            number_left = int(number_bounds.get("left", 0))
            number_right = int(number_bounds.get("right", 0))
            number_x = (int(number_bounds.get("left", 0)) + int(number_bounds.get("right", 0))) / 2
            number_bottom = int(number_bounds.get("bottom", 0))
            number_top = int(number_bounds.get("top", 0))
            horizontal_overlap = min(label_right, number_right) - max(label_left, number_left)
            max_center_distance = max(90, int(label_width * 1.15))
            if (
                number_bottom <= label_top + 20
                and (horizontal_overlap > 0 or abs(number_x - label_x) <= max_center_distance)
                and 350 <= number_top <= 1400
            ):
                distance = abs(number_x - label_x) + abs(label_top - number_bottom)
                candidates.append((distance, value))
        if candidates:
            candidates.sort(key=lambda item: item[0])
            return candidates[0][1]

    if any(_profile_counter_has_position(label_bounds) for _, label_bounds in labels):
        return None

    ordered: list[tuple[int, int, str]] = []
    for record in records:
        text = str(record.get("text") or "").strip()
        bounds = record.get("bounds") or {}
        if text and isinstance(bounds, dict):
            ordered.append((int(bounds.get("top", 0)), int(bounds.get("left", 0)), text))
    ordered.sort()
    for index, (_, _, text) in enumerate(ordered):
        if fans_label not in text:
            continue
        for previous in range(index - 1, max(index - 5, -1), -1):
            parsed = _parse_profile_counter(ordered[previous][2])
            if parsed is not None:
                return parsed
    return None


def _profile_counter_has_position(bounds: dict[str, Any]) -> bool:
    return (
        int(bounds.get("right", 0)) > int(bounds.get("left", 0))
        and int(bounds.get("bottom", 0)) > int(bounds.get("top", 0))
    )


def _profile_counter_bounds(bounds: dict[str, Any]) -> dict[str, int]:
    left = int(bounds.get("left", 0) or 0)
    top = int(bounds.get("top", 0) or 0)
    width = int(bounds.get("width", 0) or 0)
    height = int(bounds.get("height", 0) or 0)
    right = int(bounds.get("right", left + width) or 0)
    bottom = int(bounds.get("bottom", top + height) or 0)
    if right <= left and width > 0:
        right = left + width
    if bottom <= top and height > 0:
        bottom = top + height
    return {"left": left, "top": top, "right": right, "bottom": bottom}


def _parse_profile_counter(text: str) -> int | None:
    cleaned = re.sub(r"\s+", "", text.replace(",", "")).lower()
    match = re.fullmatch(r"(?P<num>\d+(?:\.\d+)?)(?P<unit>[\u4e07wk]?)", cleaned)
    if not match:
        return None
    num_text = match.group("num")
    if "." in num_text and not match.group("unit"):
        integer, decimal = num_text.split(".", 1)
        if len(decimal) == 3:
            num_text = integer + decimal
    value = float(num_text)
    unit = match.group("unit")
    if unit in {"\u4e07", "w"}:
        value *= 10000
    elif unit == "k":
        value *= 1000
    return int(round(value))


def number_candidates(texts: list[str]) -> list[str]:
    candidates: list[str] = []
    cleaned = [item.strip() for item in texts if item and item.strip()]
    candidates.extend(cleaned)
    for index in range(max(len(cleaned) - 1, 0)):
        candidates.append("".join(cleaned[index : index + 2]))
    return candidates


def current_post_scope_texts(texts: list[str]) -> list[str]:
    scope: list[str] = []
    after_latest_count: int | None = None
    for text in texts:
        cleaned = (text or "").strip()
        if not cleaned:
            continue
        scope.append(cleaned)
        if (
            "\u6682\u65e0\u8bc4\u8bba" in cleaned
            or "\u70b9\u51fb\u62a2\u9996\u8bc4" in cleaned
            or "\u8bf4\u8bf4\u4f60\u7684\u60f3\u6cd5" in cleaned
        ):
            break
        if after_latest_count is not None:
            after_latest_count += 1
            if after_latest_count >= 4:
                break
        if cleaned == "\u6700\u65b0":
            after_latest_count = 0
    return scope


def is_post_content_stop(text: str) -> bool:
    if text in {"\u53d1\u8868\u89c2\u70b9", "\u53d1\u8868\u89c2\u70b9.", "\u53d1\u8868\u8bc4\u8bba"}:
        return True
    if text in {
        "\u8bc4\u8bba",
        "\u8f6c\u53d1",
        "\u70ed\u5ea6",
        "\u6700\u65b0",
        "\u70b9\u8d5e",
        "\u8fd4\u56de",
        "\u66f4\u591a",
    }:
        return True
    return any(
        text.startswith(prefix)
        for prefix in (
            "\u6765\u81ea\u4ee5\u4e0b\u8ba8\u8bba\u533a",
            "\u98ce\u9669\u63d0\u793a",
            "\u6682\u65e0\u8bc4\u8bba",
            "\u70b9\u51fb\u62a2\u9996\u8bc4",
            "\u8bf4\u8bf4\u4f60\u7684\u60f3\u6cd5",
        )
    )


def is_post_content_noise(text: str) -> bool:
    if any(
        keyword in text
        for keyword in (
            "\u5185\u5bb9\u4e0d\u89c1\u4e86",
            "\u5148\u53bb\u770b\u770b\u5176\u4ed6\u7684\u5427",
            "\u5185\u5bb9\u4e0d\u5b58\u5728",
            "\u5df2\u88ab\u5220\u9664",
            "\u65e0\u6cd5\u67e5\u770b\u8be5\u5185\u5bb9",
            "\u7f51\u7edc\u4e0d\u7ed9\u529b",
            "\u52a0\u8f7d\u5931\u8d25",
            "\u8bf7\u6c42\u8d85\u65f6",
        )
    ):
        return True
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}", text):
        return True
    if text in {
        "\u5934\u50cf",
        "\u5173\u6ce8",
        "\u5df2\u5173\u6ce8",
        "\u9605\u8bfb",
        "\u6d4f\u89c8",
        "\u67e5\u770b",
        "\u8bc4\u8bba",
        "\u8f6c\u53d1",
        "\u70b9\u8d5e",
    }:
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d{1,2}[-/]\d{1,2}\s*\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d+", text):
        return True
    if re.search(r"(?:\u9605\u8bfb|\u6d4f\u89c8|\u67e5\u770b)\s*\d", text):
        return True
    if re.search(r"(?:\u8bc4\u8bba|\u56de\u590d|\u7559\u8a00)\s*\d", text):
        return True
    if re.search(r"\d+(?:[,.]\d+)*(?:\.\d+)?\s*[\u4e07wWkK\u5343]?\s*(?:\u9605\u8bfb|\u8bc4\u8bba|\u56de\u590d)", text):
        return True
    if len(text) <= 3 and text in {
        "\u5317\u4eac",
        "\u4e0a\u6d77",
        "\u5929\u6d25",
        "\u91cd\u5e86",
        "\u798f\u5efa",
        "\u5e7f\u4e1c",
        "\u6c5f\u82cf",
        "\u6d59\u6c5f",
        "\u5c71\u4e1c",
        "\u56db\u5ddd",
        "\u6cb3\u5357",
        "\u6cb3\u5317",
        "\u6e56\u5357",
        "\u6e56\u5317",
    }:
        return True
    return False


def _looks_like_metadata(text: str) -> bool:
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}\s*\d{1,2}:\d{2}(?::\d{2})?", text):
        return True
    if re.fullmatch(r"\d{1,2}[-/]\d{1,2}\s*\d{1,2}:\d{2}", text):
        return True
    if re.fullmatch(r"\d{1,2}:\d{2}", text):
        return True
    if re.search(r"(?:\u9605\u8bfb|\u8bc4\u8bba|\u70b9\u8d5e|\u56de\u590d)\s*\d", text):
        return True
    return False


def normalize_count_text(text: str) -> str:
    compact = re.sub(r"\s+", "", text)
    labels = "\u9605\u8bfb|\u6d4f\u89c8|\u67e5\u770b|\u8bc4\u8bba|\u56de\u590d|\u7559\u8a00"
    return re.sub(
        rf"(?:\d{{1,2}}[-/]\d{{1,2}})?(?P<time>\d{{1,2}}:\d{{2}})(?P<num>\d+(?:[,.]\d+)*(?:\.\d+)?\s*[\u4e07WwKk\u5343]?)(?=(?:{labels}))",
        lambda match: match.group("num"),
        compact,
    )
