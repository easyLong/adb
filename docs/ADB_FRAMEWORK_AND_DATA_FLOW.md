# ADB 项目框架与数据链路

本文是 `adb` 项目的当前总览入口，用来明确项目边界、代码分层、任务流转和数据落库/写回链路。

## 0. 一页总图

### 0.1 运行边界

```text
Windows Python Scheduler
  -> MySQL 任务框架
  -> ADB / uiautomator2
  -> Android 手机 App
  -> UI/XML/OCR/截图采集
  -> MySQL 结果表
  -> 腾讯文档 / Excel 写回
```

本项目的核心是“Android App 采集”。桌面微信、浏览器 DOM、Windows UI Automation 不进入本仓库。

### 0.2 主数据链路

```text
数据源入口
  data_source_links / app_config
        |
        v
数据源读取
  sources/tencent_docs.py
  sources/excel.py
        |
        v
统一候选记录
  SourceRecord
        |
        v
任务提交
  crawl_sources
  crawl_task_submissions
        |
        v
任务执行
  crawl_task_executions
        |
        v
手机采集
  mobile/device_session.py
  mobile/capture_engine.py
  mobile/record_capture.py
  crawlers/<app>.py
        |
        v
标准结果
  CrawlResult / crawl_results
        |
        v
业务写回
  services/writeback.py
  sinks/tencent_docs.py
  sinks/excel.py
  crawl_writebacks
```

### 0.3 三条 workflow

```text
腾讯文档在线链路:
  config -> fetch -> check -> detail -> report

本地 Excel 链路:
  config -ExcelInputPath -> excel-detail -> 输出 Excel + JSONL

单链接测试链路:
  link-detail -SingleLink -> 单次采集 -> JSON 摘要 + MySQL 结果
```

### 0.4 排查时先看哪些表

| 问题 | 优先看 |
| --- | --- |
| 数据源有没有配置 | `data_source_links`, `app_config` |
| fetch 有没有生成任务 | `crawl_sources`, `crawl_task_submissions` |
| 任务为什么没跑 | `crawl_task_submissions.status`, `scheduled_at`, `attempts`, `max_attempts` |
| 这次执行发生了什么 | `crawl_task_executions` |
| 标准采集结果是什么 | `crawl_results` |
| 写回有没有成功 | `crawl_writebacks`, `crawl_task_executions.writeback_status` |
| 调度是否正常 | `task_log`, `crawl_jobs`, `apps/finance_crawler/logs/` |

## 1. 项目边界

`adb` 项目只负责 Android App 侧采集：

- 通过 Windows 调度 Python 任务。
- 通过 ADB / uiautomator2 控制 Android 手机。
- 打开 App 链接、采集 UI/XML/OCR/截图。
- 将采集结果写入 MySQL，并按配置写回腾讯文档或 Excel。

不放入本项目的内容：

- Windows 桌面微信模拟点击。
- Windows UI Automation。
- Playwright / 浏览器 DOM 采集。
- 桌面 WebView 专用采集。

这些内容放到同级项目 `C:\Code\desktop-browser-crawler`。

## 2. 顶层运行入口

```text
scripts/run.ps1
  -> python -m apps.finance_crawler.app
      -> --once db
      -> --once config
      -> --once fetch
      -> --once check
      -> --once detail
      -> --once excel-detail
      -> --once link-detail
      -> --once report
      -> scheduler / supervisor
```

主要入口文件：

| 文件 | 职责 |
| --- | --- |
| `apps/finance_crawler/app.py` | CLI、scheduler、supervisor 入口 |
| `scripts/run.ps1` | PowerShell 统一运行入口 |
| `start_supervisor.cmd` | Windows 下启动 supervisor |
| `backfill_detail_by_date.cmd` | 按日期回补详情任务的辅助入口 |

## 3. 代码分层

```text
apps/finance_crawler/
  app.py                     CLI / scheduler / supervisor
  config.py                  环境变量、默认配置、运行目录
  domain/                    SourceRecord / CrawlResult / WritebackResult 等领域模型
  workflows/                 fetch/check/detail/excel-detail/link-detail 业务编排
  jobs/                      scheduler 调用的 job 封装
  sources/                   数据源读取：腾讯文档、Excel
  sinks/                     写回目标：腾讯文档、Excel
  services/                  运行配置、写回编排、备注、报表、告警
  storage/                   MySQL 表初始化、任务队列、结果和写回记录
  crawlers/                  App Profile、链接识别、App 专属 Adapter
  mobile/                    ADB、设备会话、页面状态、截图/XML/OCR/滚动采集
  integrations/              第三方 API 客户端，例如腾讯文档 OpenAPI
  utils/                     URL、链接来源、限流、日志、设备健康等工具
```

分层原则：

- `workflows/` 只做业务编排，不直接写第三方 API 细节。
- `mobile/` 只做 Android 设备通用能力，不写具体 App 的业务解析。
- `crawlers/` 存放 App 差异，例如包名、链接识别、采集计划、专属字段解析。
- `services/writeback.py` 屏蔽腾讯文档和 Excel 的写回差异。
- `storage/` 是任务框架和结果事实的落库边界。

## 4. 核心数据模型

| 模型 | 文件 | 含义 |
| --- | --- | --- |
| `SourceRecord` | `domain/records.py` | 数据源中的一条待采集记录 |
| `CrawlResult` | `domain/records.py` | App 采集后的标准结果 |
| `WritebackResult` | `domain/records.py` | 写回目标后的结果 |
| `AppLinkProfile` | `crawlers/base.py` | 一个 App 的链接识别、包名、ready 关键字 |
| `CapturePlan` | `crawlers/base.py` | 一个 App 的截图页数、OCR、滚动策略 |
| `AppCrawlerAdapter` | `crawlers/base.py` | App 专属解析和采集前置动作 |
| `WritebackPlan` | `services/writeback.py` | 写回目标、行定位、写回字段 |

## 5. 数据源到任务提交

### 5.1 在线腾讯文档

```text
data_source_links(TENCENT_DOC_URL)
  -> load_runtime_config()
  -> sources/tencent_docs.py
  -> SourceRecord
  -> workflows/tencent_docs_fetch.py
  -> upsert_source_record_submissions()
  -> crawl_sources
  -> crawl_task_submissions(initial_check/detail_crawl)
```

关键点：

- 腾讯文档 URL、扫描模式、OpenAPI 身份等运行配置来自 MySQL。
- `TencentDocsSource.fetch_records()` 输出统一 `SourceRecord`。
- `resolve_source_app()` 根据链接识别 `alipay` / `antfortune` / `tenpay` / `unknown`。
- `upsert_source_record_submissions()` 将一条业务记录拆成任务提交。

### 5.2 本地 Excel

```text
data_source_links(EXCEL_DETAIL_INPUT_PATH)
  -> load_runtime_config()
  -> workflows/local_excel_detail.py
  -> 读取 Excel 行
  -> detect_link_source()
  -> upsert_excel_row_submission()
  -> crawl_task_submissions(detail_crawl)
```

关键点：

- Excel 模式直接做详情采集，不单独跑初检。
- 输出文件默认是输入文件同目录的 `_detail_output.xlsx`。
- 运行结束后会自动停用 `EXCEL_DETAIL_INPUT_PATH`。

### 5.3 单链接测试

```text
data_source_links(SINGLE_TEST_LINK) 或 CLI --single-link
  -> workflows/single_link_detail.py
  -> upsert_task_submission(source_type=single_link)
  -> crawl_task_submissions(detail_crawl)
```

关键点：

- 每次运行都会生成带 `run_token` 的唯一任务对象，避免复用旧结果。
- 单链接没有业务写回目标，只落库和打印 JSON 摘要。
- 运行结束后会自动停用 `SINGLE_TEST_LINK`。

## 6. 任务调度与状态流转

任务表以 `crawl_task_submissions` 为中心：

```text
crawl_task_submissions
  status: pending / running / success / not_found / failed_retryable / failed_final / cancelled
  task_type: initial_check / detail_crawl
  app_type: alipay / antfortune / tenpay / unknown
  original_url / canonical_url
  source_locator_json
  scheduled_at
  attempts / max_attempts
```

每次真实执行会写入 `crawl_task_executions`：

```text
start_task_execution()
  -> crawl_task_executions(status=running, attempt_no=N)
  -> workflow 执行
  -> finish_task_execution()
  -> 更新 execution
  -> 回写 submission.latest_execution_id / status / result_summary_json
```

调度规则：

| 任务 | 生成规则 | 执行规则 |
| --- | --- | --- |
| `initial_check` | 通常由 fetch 生成 | `scheduled_at <= now`，由 check job 消费 |
| `detail_crawl` | 每条有效链接都会生成 | `scheduled_at <= now`，由 detail job 轮询消费 |

补偿规则：

- `recover_stale_running_submissions()` 将超时 running 任务恢复为可重试或终态。
- `finalize_exhausted_submissions()` 将重试耗尽的任务标记为 `failed_final`。
- `finalize_detail_submissions_blocked_by_initial_check()` 将初检已 not_found 的详情任务同步关闭。

## 7. 初检链路

```text
jobs/checker.py
  -> workflows/initial_check.py
  -> get_pending_initial_check_records()
  -> start_task_execution()
  -> resolve_short_url()
  -> open_url()
  -> check_record_exists_and_account()
  -> record_crawl_result()
  -> WritebackService.prepare_initial_check()
  -> sink.write_initial_check_results()
  -> finish_task_execution()
```

初检目标：

- 判断内容是否存在。
- 提取账号名。
- 将成功账号或不存在状态写回业务目标。

异常恢复：

- 页面空白、加载失败、未知状态等临时错误会触发 `restart_app_for_url()`。
- 设备不可用会告警并终止当前 job。

## 8. 详情采集链路

```text
jobs/detail.py
  -> workflows/detail_crawl.py
  -> get_pending_detail_records()
  -> start_task_execution()
  -> resolve_short_url()
  -> open_url()
  -> scrape_record_content()
      -> detect_page_status()
      -> get_app_adapter()
      -> adapter.before_main_capture()
      -> capture_record_pages()
      -> adapter.extract_content/account/counts()
      -> generic parsers fallback
  -> record_crawl_result()
  -> WritebackService.prepare_detail()
  -> sink.write_detail_results()
  -> finish_task_execution()
  -> generate_report()
```

详情结果包含：

- `account_name`
- `content`
- `read_count`
- `comment_count`
- `screenshot_path`
- `capture_pages`
- `ocr_attempted`
- `app_metrics`

App 专属扩展放在 `crawlers/`：

| App | 文件 | 专属逻辑 |
| --- | --- | --- |
| 支付宝 | `crawlers/alipay.py` | 链接识别、包名、ready 关键字 |
| 蚂蚁财富 | `crawlers/antfortune.py` | 分享链接改写为 `afwealth://...` |
| 财付通/腾讯理财通 | `crawlers/tenpay.py` | 调仓明细点击、OCR 解析买入基金和金额 |

## 9. Android 设备执行链路

```text
mobile/device_session.py
  -> assert_device_ready()
  -> connect_uiautomator()
  -> open_app_link()
  -> restart_app_for_url()

mobile/capture_engine.py
  -> adb exec-out screencap -p
  -> dump_hierarchy()
  -> collect_ui_records()
  -> try_ocr()
  -> scroll_forward()

mobile/record_capture.py
  -> 按 CapturePlan 循环截图/XML/OCR/滚动

mobile/crawler.py
  -> 通用 App 采集包装
  -> 调用 App Adapter
  -> 返回标准 dict 结果
```

设备恢复重点：

- 优先用 `adb exec-out screencap -p` 截图。
- 滚动优先走 `adb shell input swipe`，再 fallback 到 uiautomator2。
- 识别到输入注入权限问题时不会硬失败，会安全停止滚动。
- 白屏或临时错误会 force-stop 目标 App 后重新打开链接。

## 10. 写回链路

```text
workflow result
  -> record_crawl_result()
  -> crawl_results
  -> default_writeback_service()
      -> TencentDocsWritebackService 或 ExcelWritebackService
  -> WritebackPlan
  -> sink 写回
  -> crawl_writebacks
  -> update_task_execution_writeback()
```

腾讯文档写回：

- `sinks/tencent_docs.py`
- `integrations/tencent_docs/client.py`
- `integrations/tencent_docs/write_requests.py`
- 写回前会按 URL 校验目标行，避免写错行。

Excel 写回：

- `sinks/excel.py`
- 根据源行号写回账号、阅读、评论、状态、耗时、错误等列。

## 11. 报表链路

```text
detail job 结束
  -> generate_report()
  -> 查询 crawl_task_submissions / crawl_task_executions
  -> 汇总总量、成功、失败、阅读量
  -> 保存本地 report 文件
  -> 写入腾讯文档日报 sheet
```

相关配置：

- `REPORT_TIME`
- `READ_COUNT_THRESHOLD`
- `REPORT_TOP_N`
- `TENCENT_DOC_REPORT_SHEET_TITLE`

## 12. MySQL 表职责

| 表 | 职责 |
| --- | --- |
| `data_source_links` | 数据源入口：腾讯文档、本地 Excel、单链接 |
| `app_config` | 应用级运行配置：OpenAPI、采集保护、报表配置等 |
| `crawl_sources` | 数据源注册 |
| `crawler_apps` | 支持的 App 注册 |
| `crawl_task_submissions` | 任务提交、排队、最终状态 |
| `crawl_task_executions` | 每次执行尝试、结果、写回状态 |
| `crawl_results` | 标准化采集结果 |
| `crawl_writebacks` | 写回目标、定位、状态和错误 |
| `crawl_jobs` | job 运行记录 |
| `task_log` | 调度日志 |

## 13. 运行命令速查

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task config
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
.\scripts\run.ps1 -Task excel-detail
.\scripts\run.ps1 -Task link-detail -SingleLink "https://..."
.\scripts\run.ps1 -Task report
.\scripts\run.ps1 -Task scheduler
.\scripts\run.ps1 -Task supervisor
```

## 14. 新增 App 的标准位置

新增 Android App 时只改 ADB 项目内这些位置：

1. `crawlers/constants.py` 增加 `SOURCE_xxx`。
2. 新增 `crawlers/<app>.py`，实现 `AppLinkProfile`，必要时实现 `AppCrawlerAdapter`。
3. `crawlers/registry.py` 注册 Profile 和 Adapter。
4. 如需新增环境配置，放入 `config.py` 和 `services/runtime_config.py`。
5. App 专属结果写入 `CrawlResult.metrics["app_metrics"]`。
6. 补充 `tests/` 中的链接识别、解析和异常恢复测试。

不要在本项目中加入 Windows 桌面自动化代码；桌面和浏览器爬虫放到 `desktop-browser-crawler`。
