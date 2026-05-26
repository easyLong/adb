"""Tencent Docs result sink adapter."""

from __future__ import annotations

from typing import Any

from apps.alipay_crawler.domain.records import CrawlResult, WritebackResult
from apps.alipay_crawler.integrations import qq_docs


class TencentDocsSink:
    """Write initial checks and batch crawl results back to Tencent Docs."""

    sink_type = "tencent_docs"

    def fetch_grid(self, range_a1: str | None = None) -> tuple[list[list[str]], int]:
        return qq_docs.fetch_grid(range_a1)

    def resolve_row_index_for_url(
        self,
        url: str,
        preferred_row_index: int | None = None,
        rows: list[list[str]] | None = None,
        start_row: int | None = None,
    ) -> int | None:
        return qq_docs.resolve_row_index_for_url(url, preferred_row_index, rows, start_row)

    def write_initial_check_results(self, rows: list[dict[str, Any]]) -> None:
        qq_docs.write_initial_check_results(rows)

    def write_batch_results(self, rows: list[dict[str, Any]]) -> None:
        qq_docs.write_back_rows(rows)

    def write_results(self, results: list[CrawlResult]) -> list[WritebackResult]:
        writebacks: list[dict[str, Any]] = []
        output: list[WritebackResult] = []
        for result in results:
            row_index = result.metrics.get("row_index")
            if not row_index:
                output.append(
                    WritebackResult(
                        sink_type=self.sink_type,
                        status="skipped",
                        task_id=result.task_id,
                        error="missing row_index",
                    )
                )
                continue

            writebacks.append(
                {
                    "row_index": row_index,
                    "read_count": result.metrics.get("read_count"),
                    "comment_count": result.metrics.get("comment_count"),
                    "batch_status": result.status,
                    "screenshot_path": result.screenshot_path,
                }
            )
            output.append(
                WritebackResult(
                    sink_type=self.sink_type,
                    status="pending",
                    task_id=result.task_id,
                    locator={"row_index": row_index},
                )
            )

        if writebacks:
            self.write_batch_results(writebacks)
            for item in output:
                if item.status == "pending":
                    item.status = "success"
        return output


_DEFAULT_SINK = TencentDocsSink()


def fetch_grid(range_a1: str | None = None) -> tuple[list[list[str]], int]:
    return _DEFAULT_SINK.fetch_grid(range_a1)


def resolve_row_index_for_url(
    url: str,
    preferred_row_index: int | None = None,
    rows: list[list[str]] | None = None,
    start_row: int | None = None,
) -> int | None:
    return _DEFAULT_SINK.resolve_row_index_for_url(url, preferred_row_index, rows, start_row)


def write_initial_check_results(rows: list[dict[str, Any]]) -> None:
    _DEFAULT_SINK.write_initial_check_results(rows)


def write_back_rows(rows: list[dict[str, Any]]) -> None:
    _DEFAULT_SINK.write_batch_results(rows)
