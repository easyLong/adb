"""Initial check job.

Triggered after current time is greater than post_time + 2 hours.
It only checks whether the post has content and writes the account name back.
If no content is found, it writes "N" to the account column and marks it yellow.
"""

from __future__ import annotations

import random
import time

from apps.alipay_crawler.alipay.crawler import (
    check_post_exists_and_account,
    open_url,
    resolve_short_url,
)
from apps.alipay_crawler.config import Config
from apps.alipay_crawler.integrations.qq_docs import write_initial_check_result
from apps.alipay_crawler.storage.db import (
    get_pending_check_posts,
    log_task,
    update_check_result,
)
from apps.alipay_crawler.utils.logger import get_logger

logger = get_logger("checker")


def run_check() -> list[dict]:
    start_time = time.time()
    posts = get_pending_check_posts()
    total = len(posts)
    logger.info("初检开始，待检查 %s 条", total)

    if not posts:
        log_task("check", "success", "no pending posts", 0)
        return []

    results: list[dict] = []
    success_count = 0
    not_found_count = 0
    error_count = 0

    for idx, post in enumerate(posts, start=1):
        post_id = post["id"]
        url = post["url"]
        row_index = post.get("doc_row_index")
        logger.info("[%s/%s] 初检 id=%s row=%s %s", idx, total, post_id, row_index, url)

        try:
            deep_link = resolve_short_url(url)
            open_url(deep_link)
            result = check_post_exists_and_account(post_id)
        except RuntimeError as exc:
            result = {
                "status": "error",
                "exists": False,
                "account_name": None,
                "error": str(exc),
            }
        except Exception as exc:
            logger.exception("初检异常 id=%s", post_id)
            result = {
                "status": "error",
                "exists": False,
                "account_name": None,
                "error": str(exc),
            }

        update_check_result(
            post_id,
            result["status"],
            result.get("error"),
            result.get("account_name"),
        )

        if row_index and result["status"] in {"success", "not_found"}:
            try:
                write_initial_check_result(
                    row_index=row_index,
                    exists=result["status"] == "success",
                    account_name=result.get("account_name"),
                )
            except Exception as exc:
                logger.warning("初检写回腾讯文档失败 id=%s row=%s: %s", post_id, row_index, exc)
        elif result["status"] == "error":
            logger.warning("技术错误不写回腾讯文档，等待下次重试 id=%s", post_id)

        if result["status"] == "success":
            success_count += 1
        elif result["status"] == "not_found":
            not_found_count += 1
        else:
            error_count += 1

        result_with_post = dict(result)
        result_with_post.update({"id": post_id, "url": url, "row_index": row_index})
        results.append(result_with_post)
        time.sleep(random.uniform(Config.POST_DELAY_MIN, Config.POST_DELAY_MAX))

    duration = time.time() - start_time
    msg = (
        f"total={total}, success={success_count}, "
        f"not_found={not_found_count}, error={error_count}, duration={duration:.1f}s"
    )
    logger.info("初检完成: %s", msg)
    log_task("check", "success", msg, duration)
    return results


if __name__ == "__main__":
    run_check()
