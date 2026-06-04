# 运行时配置

项目配置分两类：

| 类型 | 位置 | 用途 |
| --- | --- | --- |
| MySQL 连接 | 根目录 `.env` 或环境变量 | 连接数据库 |
| 业务运行配置 | MySQL `data_source_links` / `app_config` | 文档 URL、调度频率、列范围、任务开关 |

启动任务时会调用 `load_runtime_config()`，把 MySQL 配置覆盖到 `Config`。

## 修改配置

查看配置：

```powershell
.\scripts\run.ps1 -Task config
```

更新配置：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet KEY=VALUE
```

一次更新多个：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet PROFILE_METRICS_READ_RANGE=A1:H2000 `
  -ConfigSet PROFILE_METRICS_TEMPLATE_RANGE=A2:H126 `
  -ConfigSet PROFILE_METRICS_DAILY_PREPARE_TIME=00:10
```

## 数据源配置

| Key | 说明 |
| --- | --- |
| `TENCENT_DOC_URL` | 通用帖子详情腾讯文档 |
| `PROFILE_METRICS_DOC_URL` | 大 V 主页统计腾讯文档 |
| `ARTICLE_DETAILS_DOC_URL` | 需求 1 文章详情腾讯文档 |
| `EXCEL_DETAIL_INPUT_PATH` | 本地 Excel 详情采集输入文件 |
| `SINGLE_TEST_LINK` | 单链接详情测试 |

## 腾讯文档 OpenAPI

| Key | 说明 |
| --- | --- |
| `TENCENT_DOC_CLIENT_ID` | OpenAPI Client-Id |
| `TENCENT_DOC_OPEN_ID` | OpenAPI Open-Id |
| `TENCENT_DOC_ACCESS_TOKEN` | Access-Token |
| `TENCENT_DOC_CLIENT_SECRET` | 可选，用于换 token |
| `TENCENT_DOC_TOKEN_URL` | token 地址 |

## 通用调度

| Key | 默认 | 说明 |
| --- | --- | --- |
| `FETCH_INTERVAL_MINUTES` | `5` | 腾讯文档候选链接扫描间隔 |
| `CHECK_INTERVAL_MINUTES` | `10` | 初检任务扫描间隔 |
| `DETAIL_INTERVAL_MINUTES` | `10` | 到期详情任务扫描间隔 |
| `REPORT_TIME` | `11:30` | 每日报告时间 |
| `HEARTBEAT_INTERVAL_MINUTES` | `30` | scheduler 心跳 |

## 大 V 粉丝数

| Key | 当前建议 | 说明 |
| --- | --- | --- |
| `PROFILE_METRICS_DOC_URL` | 业务文档 URL | 大 V 统计文档 |
| `PROFILE_METRICS_READ_RANGE` | `A1:H2000` | 同步和写回使用的读取范围 |
| `PROFILE_METRICS_TEMPLATE_RANGE` | `A2:H126` | 每日生成行的模板范围 |
| `PROFILE_METRICS_DAILY_PREPARE_TIME` | `00:10` | 每天生成当天行的时间；为空则关闭 |
| `PROFILE_METRICS_INTERVAL_MINUTES` | `60` | 周期抓粉丝数间隔；0 关闭 |
| `PROFILE_METRICS_CRAWL_LIMIT` | `0` | 单轮限制，0 表示不限制 |
| `PROFILE_METRICS_TARGET_DATE` | 空 | 固定目标日期；日常调度应留空 |
| `PROFILE_METRICS_WRITEBACK_ENABLED` | `True` | 是否写回腾讯文档 E/F 列 |

日常建议：

```text
PROFILE_METRICS_TEMPLATE_RANGE=A2:H126
PROFILE_METRICS_READ_RANGE=A1:H2000
PROFILE_METRICS_DAILY_PREPARE_TIME=00:10
PROFILE_METRICS_INTERVAL_MINUTES=60
PROFILE_METRICS_TARGET_DATE=
```

## 大 V 主页帖子阅读数

| Key | 说明 |
| --- | --- |
| `PROFILE_POST_READ_CRAWL_LIMIT` | 单轮抓取限制，0 不限制 |
| `PROFILE_POST_READ_MAX_SCROLLS` | 主页最多滚动页数 |
| `PROFILE_POST_READ_MAX_POSTS` | 同一日期最多取多少帖子 |

## 需求 1 文章详情

| Key | 说明 |
| --- | --- |
| `ARTICLE_DETAILS_DOC_URL` | 文章详情腾讯文档 |
| `ARTICLE_DETAILS_READ_RANGE` | 读取范围，默认 `A1:O2000` |
| `ARTICLE_DETAILS_CRAWL_LIMIT` | 单轮抓取限制 |
| `ARTICLE_DETAILS_WRITEBACK_ENABLED` | 是否写回腾讯文档 |

## K 列链接阅读数

| Key | 默认 | 说明 |
| --- | --- | --- |
| `DOC_LINK_READS_READ_RANGE` | `A1:M2000` | 读取范围 |
| `DOC_LINK_READS_SHEET_TITLE` | 空 | 固定 sheet 标题，通常用 `--report-date` |
| `DOC_LINK_READS_CRAWL_LIMIT` | `0` | 单轮限制 |
| `DOC_LINK_READS_ONLY_EMPTY` | `True` | 只处理阅读数空值 |
| `DOC_LINK_READS_LINK_COL` | `10` | 链接列，K 列 |
| `DOC_LINK_READS_READ_COL` | `12` | 阅读数列，M 列 |
| `DOC_LINK_READS_ENABLE_OCR` | `True` | 是否启用 OCR |
| `DOC_LINK_READS_OPEN_RETRIES` | `2` | 打开失败重试次数 |

## 手机和恢复

| Key | 说明 |
| --- | --- |
| `ADB_PATH` | ADB 路径，默认优先使用 `platform-tools/adb.exe` |
| `DEVICE_SERIAL` | 指定设备序列号，空则自动选 |
| `APP_OPEN_RECOVERY_RETRIES` | 页面白屏、卡死等恢复重试次数 |
| `APP_RESTART_WAIT` | force-stop 后等待秒数 |
| `TASK_RUNNING_TIMEOUT_MINUTES` | running 任务超时回收 |

## 注意事项

- 日常不要固定 `PROFILE_METRICS_TARGET_DATE`，否则 scheduler 会一直抓同一天。
- `PROFILE_METRICS_DAILY_PREPARE_TIME` 只生成行，不直接抓取；抓取由 `PROFILE_METRICS_INTERVAL_MINUTES` 控制。
- 每日行生成按主页链接去重，重复执行不会重复追加。
- 如果手动改了模板范围，确认范围内只包含模板账号行，不要包含历史日期块。
