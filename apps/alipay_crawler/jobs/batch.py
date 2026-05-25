"""Daily batch job: crawl eligible posts and write results back."""

from __future__ import annotations

import random
import time

from apps.alipay_crawler.alipay.crawler import open_url, resolve_short_url, scrape_post_content
from apps.alipay_crawler.config import Config
from apps.alipay_crawler.integrations.qq_docs import get_row_index_map, write_back_row
from apps.alipay_crawler.services.report import generate_report
from apps.alipay_crawler.storage.db import (
    get_pending_batch_posts,
    log_task,
    mark_written_back,
    update_batch_result,
)
from apps.alipay_crawler.utils.link_source import detect_link_source
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("batch")


def _empty_result(status: str = "error", error: str | None = None) -> dict:
    return {
        "status": status,
        "content": None,
        "read_count": 0,
        "comment_count": 0,
        "screenshot_path": None,
        "error": error,
    }


def run_batch(limit: int | None = None) -> list[dict]:
    start_time = time.time()
    task_limit = limit or Config.BATCH_LIMIT
    posts = get_pending_batch_posts(task_limit)
    total = len(posts)
    logger.info("批处理开始，待处理 %s 条，limit=%s", total, task_limit)

    if not posts:
        log_task("batch", "success", "no pending posts", time.time() - start_time)
        return []

    try:
        row_index_map = get_row_index_map()
    except Exception as exc:
        logger.warning("构建腾讯文档行号映射失败，本轮仍会落库但可能无法写回: %s", exc)
        row_index_map = {}

    results: list[dict] = []
    success_count = 0
    deleted_count = 0
    error_count = 0

    for idx, post in enumerate(posts, start=1):
        post_id = post["id"]
        url = post["url"]
        source_app = post.get("source_app") or detect_link_source(url)
        logger.info("[%s/%s] 抓取 source=%s id=%s %s", idx, total, source_app, post_id, url)

        try:
            deep_link = resolve_short_url(url)
            open_url(deep_link)
            result = scrape_post_content(post_id)
        except RuntimeError as exc:
            result = _empty_result("error", str(exc))
            logger.warning("抓取失败 id=%s: %s", post_id, exc)
        except Exception as exc:
            result = _empty_result("error", str(exc))
            logger.exception("抓取异常 id=%s", post_id)

        update_batch_result(
            post_id=post_id,
            status=result["status"],
            content=result.get("content"),
            read_count=result.get("read_count") or 0,
            comment_count=result.get("comment_count") or 0,
            screenshot_path=result.get("screenshot_path"),
            error=result.get("error"),
        )

        if result["status"] == "success":
            success_count += 1
        elif result["status"] == "deleted":
            deleted_count += 1
        else:
            error_count += 1

        row_index = post.get("doc_row_index") or row_index_map.get(url)
        if row_index:
            try:
                write_back_row(
                    row_index=row_index,
                    read_count=result.get("read_count") or 0,
                    comment_count=result.get("comment_count") or 0,
                    batch_status=result["status"],
                )
                mark_written_back(post_id)
            except Exception as exc:
                logger.warning("写回腾讯文档失败 id=%s row=%s: %s", post_id, row_index, exc)
        else:
            logger.warning("找不到腾讯文档行号，跳过写回 id=%s", post_id)

        result_with_post = dict(result)
        result_with_post.update(
            {"id": post_id, "url": url, "source_app": source_app, "row_index": row_index}
        )
        results.append(result_with_post)

        time.sleep(random.uniform(Config.POST_DELAY_MIN, Config.POST_DELAY_MAX))

    duration = time.time() - start_time
    msg = (
        f"total={total}, success={success_count}, deleted={deleted_count}, "
        f"error={error_count}, duration={duration:.1f}s"
    )
    logger.info("批处理完成: %s", msg)
    log_task("batch", "success", msg, duration)

    try:
        generate_report()
    except Exception as exc:
        logger.warning("报告生成失败: %s", exc)

    return results


if __name__ == "__main__":
    run_batch()
