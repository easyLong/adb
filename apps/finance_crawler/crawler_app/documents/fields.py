"""Fixed business fields used by document-driven crawling."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class FieldRole(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


@dataclass(frozen=True, slots=True)
class BusinessField:
    name: str
    label: str
    aliases: tuple[str, ...]
    role: FieldRole
    required: bool = False
    create_if_missing: bool = False


POST_URL = "post_url"
ACCOUNT_NAME = "account_name"
POST_TIME = "post_time"
READ_COUNT = "read_count"
COMMENT_COUNT = "comment_count"
SCREENSHOT = "screenshot"
REMARK = "remark"
CHECK_RESULT = "check_result"


DEFAULT_BUSINESS_FIELDS: tuple[BusinessField, ...] = (
    BusinessField(
        name=POST_URL,
        label="\u5e16\u5b50\u94fe\u63a5",
        aliases=(
            "\u5e16\u5b50\u94fe\u63a5",
            "\u539f\u6587\u94fe\u63a5",
            "\u6587\u7ae0\u94fe\u63a5",
            "\u5185\u5bb9\u94fe\u63a5",
            "\u94fe\u63a5",
            "url",
        ),
        role=FieldRole.INPUT,
        required=True,
    ),
    BusinessField(
        name=ACCOUNT_NAME,
        label="\u53d1\u5e16\u8d26\u53f7\u6635\u79f0",
        aliases=(
            "\u53d1\u5e16\u8d26\u53f7\u6635\u79f0",
            "\u53d1\u5e03\u8d26\u53f7",
            "\u8d26\u53f7\u6635\u79f0",
            "\u8d26\u53f7\u540d\u79f0",
            "\u8d26\u53f7",
            "\u6635\u79f0",
            "\u4f5c\u8005",
        ),
        role=FieldRole.INPUT,
        required=False,
    ),
    BusinessField(
        name=POST_TIME,
        label="\u53d1\u5e16\u65f6\u95f4",
        aliases=(
            "\u53d1\u5e16\u65f6\u95f4",
            "\u53d1\u5e03\u65f6\u95f4",
            "\u53d1\u6587\u65f6\u95f4",
            "\u65f6\u95f4",
            "\u65e5\u671f",
        ),
        role=FieldRole.INPUT,
        required=False,
    ),
    BusinessField(
        name=READ_COUNT,
        label="\u9605\u8bfb\u6570",
        aliases=(
            "\u9605\u8bfb\u6570",
            "\u9605\u8bfb\u91cf",
            "\u9605\u8bfb",
            "\u6d4f\u89c8\u6570",
            "\u6d4f\u89c8\u91cf",
        ),
        role=FieldRole.OUTPUT,
        create_if_missing=True,
    ),
    BusinessField(
        name=COMMENT_COUNT,
        label="\u8bc4\u8bba\u6570",
        aliases=("\u8bc4\u8bba\u6570", "\u8bc4\u8bba\u91cf"),
        role=FieldRole.OUTPUT,
        create_if_missing=True,
    ),
    BusinessField(
        name=SCREENSHOT,
        label="\u622a\u56fe",
        aliases=("\u622a\u56fe", "\u622a\u56fe\u94fe\u63a5", "\u56fe\u7247"),
        role=FieldRole.OUTPUT,
        create_if_missing=True,
    ),
    BusinessField(
        name=REMARK,
        label="\u5907\u6ce8",
        aliases=("\u5907\u6ce8", "\u5f02\u5e38\u5907\u6ce8", "\u8bf4\u660e", "\u72b6\u6001"),
        role=FieldRole.OUTPUT,
        create_if_missing=True,
    ),
    BusinessField(
        name=CHECK_RESULT,
        label="\u68c0\u67e5\u7ed3\u679c",
        aliases=(
            "\u68c0\u67e5\u7ed3\u679c",
            "\u68c0\u67e5\u72b6\u6001",
            "\u662f\u5426\u627e\u5230",
            "\u521d\u68c0",
            "check",
        ),
        role=FieldRole.OUTPUT,
        create_if_missing=True,
    ),
)


def default_field_by_name() -> dict[str, BusinessField]:
    return {field.name: field for field in DEFAULT_BUSINESS_FIELDS}
