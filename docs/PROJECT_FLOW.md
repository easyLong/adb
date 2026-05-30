# 项目总览：流程、数据流和框架

本文档描述当前项目的整体运行方式、数据流转和代码框架。

## 1. 项目定位

这是一个金融 App 帖子采集工作台。Windows 负责调度、读取数据源、驱动手机、保存结果和写回业务目标；Android 手机负责真实打开 App 页面、承载登录态、渲染帖子和明细页。

当前支持三类 App：

| app_type | App | 主要采集内容 |
| --- | --- | --- |
| `alipay` | 支付宝 | 账号、正文、阅读数、评论数、截图 |
| `antfortune` | 蚂蚁财富 | 账号、正文、阅读数、评论数、截图 |
| `tenpay` | 财付通/腾讯理财通 | 账号、评论数、调仓明细里的买入基金名称和金额、截图 |

当前支持三类任务源：

| source_key / source_type | 来源 | 使用方式 |
| --- | --- | --- |
| `TENCENT_DOC_URL` / `tencent_docs` | 腾讯文档 | 长期在线监控，读取链接并写回结果 |
| `EXCEL_DETAIL_INPUT_PATH` / `excel` | 本地 Excel | 手动执行一次 `excel-detail` |
| `SINGLE_TEST_LINK` / `single_link` | 单条链接 | 手动执行一次 `link-detail`，用于临时测试 |

任务源入口保存在 MySQL `data_source_links` 表。腾讯文档 OpenAPI 身份、App 采集保护等应用级配置保存在 MySQL `app_config` 表。应用启动任务前会加载运行时配置，并覆盖对应的 `Config` 值。

## 2. 总体流程

### 在线腾讯文档流程

```text
data_source_links(TENCENT_DOC_URL)
  -> load_runtime_config()
  -> fetch 读取腾讯文档候选链接
  -> 生成 initial_check / detail_crawl 任务
  -> check 执行初检
  -> detail 执行详情采集
  -> 写回腾讯文档
  -> 记录 MySQL 结果和写回状态
```

常用命令：

```powershell
.\scripts\run.ps1 -Task config -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
```

### 本地 Excel 详情采集流程

```text
data_source_links(EXCEL_DETAIL_INPUT_PATH)
  -> load_runtime_config()
  -> excel-detail 读取本地 Excel 链接
  -> 手机打开 App 详情页
  -> 采集 App 页面内容
  -> 写回输出 Excel
  -> 记录 MySQL 任务和执行状态
  -> 自动停用 EXCEL_DETAIL_INPUT_PATH
```

常用命令：

```powershell
.\scripts\run.ps1 -Task config -ExcelInputPath "D:\demo\input.xlsx"
.\scripts\run.ps1 -Task excel-detail
```

### 单链接详情测试流程

```text
data_source_links(SINGLE_TEST_LINK) 或 CLI 参数 --single-link
  -> link-detail
  -> 生成 single_link 详情任务
  -> 手机打开 App 详情页
  -> 采集并输出 JSON 摘要
  -> 写入 crawl_results
  -> 自动停用 SINGLE_TEST_LINK
```

常用命令：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

## 3. 数据流转

### 3.1 运行时配置

```text
data_source_links
  -> app_config
  -> services/runtime_config.py
  -> Config
  -> workflows / sources / sinks
```

目前运行时配置分两类。

`data_source_links` 只保存任务源入口：

| source_key | 含义 | 状态 |
| --- | --- | --- |
| `TENCENT_DOC_URL` | 在线腾讯文档链接 | 常驻 `active` |
| `EXCEL_DETAIL_INPUT_PATH` | 本地 Excel 输入文件 | 跑完后 `unavailable` |
| `SINGLE_TEST_LINK` | 单条测试链接 | 跑完后 `unavailable` |

`app_config` 保存应用级配置：

| config_key | 含义 |
| --- | --- |
| `TENCENT_DOC_CLIENT_ID` / `TENCENT_DOC_OPEN_ID` / `TENCENT_DOC_ACCESS_TOKEN` | 腾讯文档 OpenAPI 身份 |
| `TENCENT_DOC_CLIENT_SECRET` / `TENCENT_DOC_TOKEN_URL` | token 自动换取配置 |
| `APP_OPEN_RECOVERY_RETRIES` / `APP_RESTART_WAIT` | App 白屏、系统更新弹窗、卡死时的重启恢复策略 |

### 3.2 从数据源到任务

```text
sources/tencent_docs.py 或 sources/excel.py
  -> SourceRecord
  -> upsert_source_record_submissions()
  -> crawl_sources
  -> crawl_task_submissions
```

`SourceRecord` 是数据源输出的统一记录：

| 字段 | 含义 |
| --- | --- |
| `record_id` | 数据源内部记录 ID |
| `source_type` | 来源类型，如 `tencent_docs`、`excel` |
| `source_name` | 来源名称，如文档 ID、Excel 路径 |
| `url` | 原始链接 |
| `app_type` | 识别出的 App 类型 |
| `source_time` | 来源中的发帖/发布时间 |
| `locator` | 写回定位信息 |
| `raw` | 原始行数据快照 |

任务唯一性：

```text
source_type + crawl_object_key + task_type
```

其中：

```text
crawl_object_key = url:<sha1(normalized_url)>
```

行号、文件路径、sheet 名只放在 `source_locator_json`，不参与任务身份。

### 3.3 从任务到手机采集

```text
crawl_task_submissions
  -> get_pending_check_submissions / get_pending_detail_submissions
  -> workflows/initial_check.py 或 workflows/detail_crawl.py
  -> start_task_execution()
  -> mobile/device_session.py 打开链接
  -> 技术性异常时 force-stop 目标 App 并重新打开链接
  -> mobile/crawler.py 选择 App Adapter
  -> mobile/record_capture.py 截图、XML、OCR、滑动
```

采集调试材料保存在：

```text
apps/finance_crawler/captures/record_<id>_<time>/
  page_000.png
  page_000.xml
  ui_records.jsonl
  ocr_records.jsonl
  tenpay_trade_*.png/jsonl
```

截图优先使用：

```powershell
adb exec-out screencap -p
```

### 3.4 从采集结果到写回

```text
App 页面采集结果
  -> CrawlResult
  -> crawl_results
  -> services/writeback.py
  -> sinks/tencent_docs.py 或 sinks/excel.py
  -> crawl_writebacks
```

写回时会生成业务备注：

```text
services/remarks.py
  -> detail_remark()
  -> 腾讯文档/Excel 备注列
```

`CrawlResult.metrics` 用于承载 App 差异字段。例如财付通调仓明细会进入 `app_metrics.tenpay_trade_details` 和 `app_metrics.tenpay_summary`。

## 4. 任务调度规则

| task_type | 调度时间 | 作用 |
| --- | --- | --- |
| `initial_check` | `source_time + INITIAL_CHECK_DELAY_HOURS` | 判断帖子是否存在，提取账号 |
| `detail_crawl` | `source_time` 日期次日 `DETAIL_TIME` | 采集详情内容、阅读、评论、截图和 App 专属指标 |

如果导入时已经晚于详情采集窗口，会跳过 `initial_check`，只生成 `detail_crawl`。

## 5. 腾讯文档扫描规则

默认只扫描腾讯文档中标题日期等于当天的工作表。历史补扫不要写 `.env`，使用 `run.ps1` 的临时启动参数。

| 配置 | 说明 |
| --- | --- |
| `-TencentDocScanMode single` | 只读取 URL 中指定的单个 sheet |
| `-TencentDocScanMode today` | 读取当天日期 sheet |
| `-TencentDocScanMode date -TencentDocScanDate YYYY-MM-DD` | 读取指定日期 sheet |
| `-TencentDocScanMode filter -TencentDocSheetTitleFilter 关键词` | 按标题关键词过滤 sheet |
| `-TencentDocScanMode all` | 扫描全部 sheet |

## 6. 项目框架

```text
apps/finance_crawler/
  app.py                    CLI、scheduler、supervisor 入口
  config.py                 环境变量和默认配置
  domain/                   SourceRecord、CrawlResult、任务类型等领域模型
  jobs/                     定时任务薄封装
  workflows/                fetch、check、detail、excel-detail、link-detail 业务编排
  sources/                  数据源适配器
  sinks/                    写回目标适配器
  crawlers/                 App Profile、CapturePlan、AppCrawlerAdapter
  mobile/                   ADB、设备会话、截图、XML、OCR、页面状态
  integrations/             腾讯文档等第三方底层 API
  storage/                  MySQL 框架表、任务队列、结果记录
  services/                 运行时配置、备注、写回、报告、告警
  utils/                    链路识别、表格解析、限流、设备健康
scripts/
  run.ps1                   常用任务运行入口
  crawl_one_link.py         单链接手机采集测试脚本
  repair_initial_check_link.py 单条初检结果复核和写回修复脚本
  replay_tencent_docs_writebacks.py 腾讯文档写回失败重放脚本
  fill_antfortune_xlsx.py   蚂蚁财富 Excel 辅助脚本
docs/
  PROJECT_FLOW.md           当前整体流程、数据流和框架
  ARCHITECTURE.md           分层架构和扩展原则
  FINANCE_CRAWLER.md        业务流程、字段和 MySQL 表说明
  RUNTIME_CONFIG.md         运行时任务源配置
  OPERATIONS.md             运行、测试和排障
  env.example.ps1           旧 PowerShell 示例；新部署优先使用根目录 .env
  init.sql                  MySQL 建表 SQL
```

## 7. MySQL 表职责

| 表 | 作用 |
| --- | --- |
| `data_source_links` | 任务源入口配置：腾讯文档、本地 Excel、单链接 |
| `crawl_sources` | 数据源注册，例如腾讯文档、Excel |
| `crawler_apps` | App 注册，例如 alipay、antfortune、tenpay |
| `crawl_task_submissions` | 任务提交和总体状态 |
| `crawl_task_executions` | 每次执行尝试、结果摘要、错误和写回状态 |
| `crawl_results` | 标准化采集结果，App 专属数据放在 `metrics_json` |
| `crawl_writebacks` | 写回目标、定位、状态和错误 |
| `crawl_jobs` | 一次 job 运行记录 |
| `task_log` | 调度日志 |

## 8. 当前常用命令

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task config
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
.\scripts\run.ps1 -Task excel-detail
.\scripts\run.ps1 -Task link-detail
.\scripts\run.ps1 -Task report
.\scripts\run.ps1 -Task scheduler
.\scripts\run.ps1 -Task supervisor
```
