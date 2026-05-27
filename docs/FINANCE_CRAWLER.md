# Finance Crawler 说明

`apps/finance_crawler` 是当前项目的核心应用，负责采集金融 App 帖子数据。虽然历史上从支付宝链路开始，但现在内部已经支持支付宝、蚂蚁财富、财付通/腾讯理财通三条链路。

## 目标

当前业务目标是：给定帖子链接，自动打开对应 App 页面，采集“谁在什么时间发帖/买了什么/买了多少/多少人阅读/多少人评论”等信息，并把结果回填到业务表格和数据库。

当前主要字段：

- 发帖账号。
- 帖子正文。
- 阅读数。
- 评论数。
- 首屏截图。
- 财付通调仓明细中的买入基金名称和金额。
- 采集状态和错误信息。

## 支持链路

| source_app | 典型链接 | 打开方式 | 采集特点 |
| --- | --- | --- | --- |
| `alipay` | `ur.alipay.com`、`alipays://`、`alipay://` | 短链优先解析为支付宝 deep link | 采集账号、正文、阅读数、评论数 |
| `antfortune` | `think.klv5qu.com`、`afwealth://` | 分享链接直接改写为蚂蚁财富 deep link | 采集账号、正文、阅读数、评论数 |
| `tenpay` | `www.tencentwm.com`、`tenpay://`、`tencentwm://` | 通过财付通/腾讯理财通包名打开 | 额外进入调仓明细，采集买入基金和金额 |

## 业务流程

### 1. Fetch

入口：

```powershell
.\scripts\run.ps1 -Task fetch
```

流程：

```text
Tencent Docs
  -> sources/tencent_docs.py
  -> utils/tabular_links.py
  -> storage/db.py
  -> posts + crawl_tasks
```

职责：

- 从腾讯文档读取候选行。
- 解析发布时间和帖子链接。
- 根据 `POST_ELIGIBLE_HOURS` 判断是否达到采集延迟。
- 根据 URL 自动识别 `source_app`。
- 写入旧业务表 `posts`，同时同步写入框架表 `crawl_tasks`。

### 2. Initial Check

入口：

```powershell
.\scripts\run.ps1 -Task check
```

流程：

```text
pending records
  -> storage/crawl_repository.py
  -> resolve short/deep link
  -> ADB open app
  -> mobile/device_session.py
  -> mobile/page_status.py
  -> 检查页面是否存在、提取账号
  -> posts + crawl_results
  -> sinks/tencent_docs.py
```

职责：

- 打开帖子页面。
- 判断帖子是否存在或已删除。
- 提取发帖账号。
- 写回腾讯文档账号列；不存在时写 `N` 并标记。

### 3. Batch Crawl

入口：

```powershell
.\scripts\run.ps1 -Task batch
```

流程：

```text
pending records
  -> storage/crawl_repository.py
  -> resolve short/deep link
  -> ADB open app
  -> mobile/device_session.py
  -> mobile/crawler.py
  -> mobile/post_capture.py
  -> crawlers/<app>.py adapter
  -> screenshots/captures
  -> posts + crawl_results
  -> sinks/tencent_docs.py
  -> crawl_writebacks
```

职责：

- 打开帖子页面。
- 采集首屏截图、UI XML、OCR 记录。
- 解析账号、正文、阅读数、评论数。
- 如果是财付通链路，进入“去查看明细/调仓明细”，解析买入基金名称和金额。
- 写回腾讯文档阅读数、评论数、状态和截图。
- 记录 `crawl_results` 和 `crawl_writebacks`。

## 腾讯文档列约定

列索引均为 0-based，可通过环境变量覆盖。

| 默认列 | 索引 | 配置 | 含义 |
| --- | --- | --- | --- |
| J | 9 | `TENCENT_DOC_COL_POST_TIME` | 发帖时间 |
| L | 11 | `TENCENT_DOC_COL_ACCOUNT_NAME` | 发帖账号/初检结果 |
| N | 13 | `TENCENT_DOC_COL_URL` | 帖子链接 |
| O | 14 | `TENCENT_DOC_COL_READ_COUNT` | 阅读数 |
| P | 15 | `TENCENT_DOC_COL_COMMENT_COUNT` | 评论数 |
| Q | 16 | `TENCENT_DOC_COL_BATCH_STATUS` | 批处理状态 |
| R | 17 | `TENCENT_DOC_COL_SCREENSHOT` | 首屏截图 |

写回前会按 URL 校验当前行号，避免腾讯文档插入/删除行后写错行。若同一个 URL 在表格中出现多次，会跳过不安全写回。

## 手机采集文件

全链路调试文件保存在：

```text
apps/finance_crawler/captures/post_<post_id>_<yyyymmdd_hhmmss>/
```

常见文件：

| 文件 | 含义 |
| --- | --- |
| `page_000.png` | 第 1 屏截图 |
| `page_000.xml` | 第 1 屏 UI XML |
| `ui_records.jsonl` | 从 UI XML 提取的控件记录 |
| `ocr_records.jsonl` | 从截图 OCR 提取的文字记录 |
| `tenpay_trade_entry.png` | 财付通明细入口截图 |
| `tenpay_trade_rebalance_*.png` | 财付通调仓明细截图 |
| `tenpay_trade_ocr_records.jsonl` | 财付通调仓明细 OCR 记录 |

业务写回用的首屏截图保存在：

```text
apps/finance_crawler/screenshots/
```

## 截图实现

当前截图优先走：

```powershell
adb exec-out screencap -p
```

这样直接把 PNG 数据从手机标准输出写到 Windows 本地文件，避免先在手机 `/sdcard` 写文件再 `adb pull`，速度更快。

如果 `exec-out` 失败，会降级使用 uiautomator2 的截图能力。

## 财付通专属采集

财付通特殊逻辑集中在：

```text
apps/finance_crawler/crawlers/tenpay.py
```

它不会污染通用采集流程。主要步骤：

1. 判断当前页面是否是腾讯理财通帖子。
2. 查找并点击“去查看明细/查看明细”。
3. 打开“调仓明细”。
4. 对明细页截图和 OCR。
5. 解析买入基金名称、日期、金额。
6. 将结果写入 `app_metrics`，例如 `tenpay_trade_details`、`tenpay_summary`。

## App 采集策略

不同 App 的页面结构、截图数量、OCR 需要和点击路径可能不同。当前通过 `CapturePlan` 和 `AppCrawlerAdapter` 解耦：

| 能力 | 归属 | 说明 |
| --- | --- | --- |
| 链接识别、包名、deep link 改写 | `AppLinkProfile` | 判断链接属于哪个 App，以及怎么打开 |
| 主帖截几屏、是否 OCR、滑动等待、停止条件 | `CapturePlan` | 每个 App 可以声明自己的采集策略 |
| 采集前点击、明细页进入、特殊字段解析 | `AppCrawlerAdapter` | 例如财付通进入调仓明细 |
| 截图、XML、OCR 文件落盘 | `mobile/post_capture.py` | 只执行策略，不写 App 特殊分支 |

因此新增 App 时，优先新增 `crawlers/<app>.py`，而不是修改手机采集主循环。

## 关键配置

采集控制：

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `SCROLL_TIMES` | `2` | 普通采集滑动次数 |
| `BATCH_MAX_CAPTURE_PAGES` | `3` | 批处理最多采集屏数 |
| `BATCH_ENABLE_OCR` | `true` | 是否启用 OCR |
| `OCR_MIN_CONFIDENCE` | `30.0` | OCR 最低置信度 |
| `PAGE_LOAD_WAIT` | `3.0` | 打开页面后的等待秒数 |
| `BATCH_SCROLL_WAIT` | `0.8` | 滑动后的等待秒数 |

设备配置：

| 配置 | 含义 |
| --- | --- |
| `ADB_PATH` | adb 可执行文件路径 |
| `DEVICE_SERIAL` | 多设备时指定设备序列号 |
| `TENPAY_PACKAGE` | 财付通/腾讯理财通包名，默认 `com.tencent.fortuneplat` |

调度配置：

| 配置 | 默认 | 含义 |
| --- | --- | --- |
| `FETCH_INTERVAL_MINUTES` | `5` | 拉取腾讯文档间隔 |
| `CHECK_INTERVAL_MINUTES` | `10` | 初检间隔 |
| `BATCH_TIME` | `10:00` | 每日批处理时间 |
| `REPORT_TIME` | `11:30` | 每日报告时间 |
| `FETCH_LIMIT` | `10` | 单次 fetch 导入数量，0 表示全部 |
| `BATCH_LIMIT` | `0` | 单次 batch 数量，0 表示全部 |

## 模块职责速查

| 模块 | 职责 |
| --- | --- |
| `app.py` | 调度入口，支持 `--once`、常驻调度、supervisor |
| `config.py` | 环境变量和默认配置 |
| `domain/records.py` | 通用输入、输出、写回结果对象 |
| `domain/interfaces.py` | LinkSource、AppCrawler、ResultSink 协议 |
| `crawlers/base.py` | App Profile / Adapter 抽象 |
| `crawlers/registry.py` | App Profile / Adapter 注册中心 |
| `mobile/capture_engine.py` | ADB、deep link、截图、XML、OCR |
| `mobile/device_session.py` | 设备连接缓存、唤醒/锁屏检查、链接打开 |
| `mobile/page_status.py` | 通用页面可用/删除/错误状态判断 |
| `mobile/crawler.py` | 通用页面状态、Adapter 调度、结果拼装 |
| `mobile/post_capture.py` | 按 `CapturePlan` 执行截图、XML、OCR、滑动采集 |
| `mobile/parsers.py` | 通用金融社区帖子账号、正文、阅读数、评论数解析 |
| `sources/tencent_docs.py` | 腾讯文档数据源 |
| `sources/excel.py` | 本地 Excel 数据源 |
| `sinks/tencent_docs.py` | 腾讯文档结果写回适配 |
| `sinks/excel.py` | 本地 Excel 结果写回适配 |
| `integrations/tencent_docs/client.py` | 腾讯文档 OpenAPI client |
| `integrations/tencent_docs/rows.py` | 腾讯文档行号定位 |
| `integrations/tencent_docs/write_requests.py` | 腾讯文档 request 构造 |
| `integrations/tencent_docs/screenshots.py` | 腾讯文档截图上传和降级 |
| `integrations/tencent_docs/writeback.py` | 腾讯文档批量写回编排 |
| `workflows/tencent_docs_fetch.py` | 数据源导入 workflow |
| `workflows/initial_check.py` | 初检 workflow |
| `workflows/batch_crawl.py` | 批量采集 workflow |
| `storage/crawl_repository.py` | workflow 面向的采集任务、结果、写回记录仓储边界 |
| `storage/db.py` | `posts` 兼容业务表读写 |
| `storage/framework_db.py` | `crawl_*` 框架表读写 |
| `services/framework_events.py` | 尽力记录 `crawl_results` 和 `crawl_writebacks` |

## 单链接测试

```powershell
python .\scripts\crawl_one_link.py "帖子链接"
```

跳过初检，直接批量采集：

```powershell
python .\scripts\crawl_one_link.py --skip-check "帖子链接"
```

## 新增链路建议

新增 App 链路只改 `crawlers/`，不要改 `mobile/crawler.py` 的通用流程。

新增数据源只改 `sources/`，不要让 App 采集层知道数据来自腾讯文档还是 Excel。

新增写回目标只改 `sinks/` 和 `integrations/`，不要让 workflow 直接拼第三方 API 请求。

`check` / `batch` 默认仍走 `posts` 兼容表，但 workflow 已经通过 `storage/crawl_repository.py` 访问任务、结果保存和写回记录入口。需要灰度验证框架表主路径时，可以打开 `USE_FRAMEWORK_TASKS_FOR_WORKFLOWS=true`，让待处理查询改用 `crawl_tasks` / `crawl_results`。

## MySQL 表使用现状

当前仍在使用老表，`posts` 还没有退场：

| 表 | 作用 |
| --- | --- |
| `posts` | 默认主业务表，保存帖子链接、来源 App、发帖时间、腾讯文档行号、初检/批量状态、账号、正文、阅读数、评论数、截图路径和写回状态。 |
| `task_log` | 调度任务日志表，记录 fetch/check/batch/report 的状态、摘要、耗时和错误。 |
| `crawl_sources` | 通用数据源注册表，记录腾讯文档、manual，未来可扩展 Excel/API。 |
| `crawler_apps` | App 注册表，记录支付宝、蚂蚁财富、财付通/腾讯理财通等 App 类型、展示名和包名。 |
| `crawl_tasks` | 通用采集任务表，导入链接时与 `posts` 双写，保存来源定位、App 类型、原始 URL、发帖时间和任务状态。 |
| `crawl_results` | 通用采集结果表，记录初检和批量采集结果，App 专属指标放在 `metrics_json`。 |
| `crawl_writebacks` | 写回结果表，记录写回腾讯文档/Excel 等目标的状态、定位和错误。 |
| `crawl_jobs` | 通用 job 表，已建表，设计用于记录一次任务运行，目前主流程使用较少。 |

默认运行链路仍是 `posts` 主路径，同时同步记录 `crawl_tasks`、`crawl_results`、`crawl_writebacks`，后续再逐步迁移。
