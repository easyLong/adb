# 项目总览：流程、数据流和框架

本文档只描述当前项目的运行方式。

## 1. 项目定位

这是一个金融 App 帖子采集工作台。Windows 负责调度、读写数据源、驱动手机、保存结果；Android 手机负责真实打开 App 页面、承载登录态、渲染帖子和明细页面。

当前支持三类 App 链路：

| app_type | App | 主要采集内容 |
| --- | --- | --- |
| `alipay` | 支付宝 | 账号、正文、阅读数、评论数、截图 |
| `antfortune` | 蚂蚁财富 | 账号、正文、阅读数、评论数、截图 |
| `tenpay` | 财付通/腾讯理财通 | 账号、评论数、调仓明细里的买入基金名称和金额、截图 |

当前支持两类数据源：

| source_type | 来源 | 说明 |
| --- | --- | --- |
| `tencent_docs` | 腾讯文档 | 在线表格读取链接、写回采集结果 |
| `excel` | 本地 Excel | 本地 `.xlsx` 读取链接、写回输出文件 |

## 2. 整体项目流程

### 标准在线流程

```text
腾讯文档
  -> fetch 读取待采集链接
  -> 生成 initial_check 和 detail_crawl 任务
  -> check 执行初检
  -> detail 执行详情采集
  -> 写回腾讯文档
  -> 记录 MySQL 结果和写回状态
```

对应命令：

```powershell
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
```

### 本地 Excel 直接采集流程

```text
本地 Excel
  -> excel-detail 读取链接
  -> 手机打开 App 详情页
  -> 采集 App 页面内容
  -> 写回输出 Excel
  -> 记录 MySQL 任务和执行状态
```

对应命令：

```powershell
$env:EXCEL_DETAIL_INPUT_PATH = "D:\demo\input.xlsx"
$env:EXCEL_DETAIL_OUTPUT_PATH = "D:\demo\output.xlsx"
.\scripts\run.ps1 -Task excel-detail
```

### 单链接测试流程

```text
单个链接
  -> 自动识别 App 类型
  -> 打开手机 App
  -> 可选初检
  -> 详情采集
  -> 输出 JSON 结果
```

对应命令：

```powershell
python .\scripts\crawl_one_link.py "<url>"
```

## 3. 任务生成规则

同一个采集对象用 URL 生成稳定键：

```text
crawl_object_key = url:<sha1(normalized_url)>
```

任务唯一性由三部分决定：

```text
source_type + crawl_object_key + task_type
```

这意味着同一个 URL 即使在腾讯文档行号变化、Excel 文件移动、sheet 名变化时，也不会被当成新的采集对象。行号、文件路径、sheet 名只放在 `source_locator_json` 里，用于写回定位。

默认任务类型：

| task_type | 触发时机 | 做什么 |
| --- | --- | --- |
| `initial_check` | `source_time + INITIAL_CHECK_DELAY_HOURS` | 轻量判断帖子是否存在，并提取账号 |
| `detail_crawl` | `source_time` 次日 `DETAIL_TIME` | 采集正文、阅读、评论、截图和 App 专属指标 |

如果导入任务时已经晚于次日详情采集时间，系统会跳过初检，只生成详情任务。

## 4. 数据流转

### 从数据源到任务

```text
sources/tencent_docs.py 或 sources/excel.py
  -> SourceRecord
  -> storage/framework_db.py
  -> crawl_sources
  -> crawl_task_submissions
```

`SourceRecord` 是所有数据源的统一输入模型：

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

### 从任务到手机采集

```text
crawl_task_submissions
  -> get_pending_check_submissions / get_pending_detail_submissions
  -> workflows/initial_check.py 或 workflows/detail_crawl.py
  -> start_task_execution()
  -> mobile/device_session.py 打开链接
  -> mobile/crawler.py 选择 App Adapter
  -> mobile/record_capture.py 截图、XML、OCR、滑动
```

手机侧采集会保存调试材料：

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

### 从采集结果到写回

```text
App 页面采集结果
  -> CrawlResult
  -> crawl_results
  -> services/writeback.py
  -> sinks/tencent_docs.py 或 sinks/excel.py
  -> crawl_writebacks
```

`CrawlResult.metrics` 用于承载不同 App 的差异字段。例如财付通调仓明细会放入 `app_metrics.tenpay_trade_details` 和 `app_metrics.tenpay_summary`。

## 5. 项目框架

```text
apps/finance_crawler/
  app.py                    CLI、scheduler、supervisor 入口
  config.py                 环境变量和默认配置
  domain/                   SourceRecord、CrawlResult、任务类型等领域模型
  jobs/                     定时任务薄封装
  workflows/                fetch、check、detail、excel-detail 业务编排
  sources/                  数据源适配器
  sinks/                    写回目标适配器
  crawlers/                 App Profile、CapturePlan、AppCrawlerAdapter
  mobile/                   ADB、设备会话、截图、XML、OCR、页面状态
  integrations/             腾讯文档等第三方底层 API
  storage/                  MySQL 框架表、任务队列、结果记录
  services/                 写回服务、报告、告警、框架事件
  utils/                    链路识别、表格解析、限流、设备健康
scripts/
  run.ps1                   常用任务运行入口
  crawl_one_link.py         单链接手机采集测试
  fill_antfortune_xlsx.py   蚂蚁财富 Excel 辅助脚本
docs/
  PROJECT_FLOW.md           当前整体流程、数据流和框架
  ARCHITECTURE.md           分层架构和扩展原则
  FINANCE_CRAWLER.md        业务流程、字段和 MySQL 表说明
  OPERATIONS.md             运行、测试和排障
  env.example.ps1           环境变量模板
  init.sql                  MySQL 建表 SQL
```

## 6. 核心分层职责

| 层 | 代码位置 | 职责边界 |
| --- | --- | --- |
| 数据源层 | `sources/` | 只负责读取外部数据并输出 `SourceRecord` |
| 业务编排层 | `workflows/` | 决定 fetch、初检、详情、Excel 直采如何串起来 |
| 任务框架层 | `storage/framework_db.py` | 负责任务提交、执行、状态、结果、写回记录 |
| App 适配层 | `crawlers/` | 定义每个 App 怎么打开、截多少、点哪里、解析什么 |
| 手机执行层 | `mobile/` | 提供 ADB、截图、XML、OCR、滑动等通用能力 |
| 写回层 | `sinks/` | 把标准结果写回腾讯文档、Excel 或未来其它目标 |
| 外部 API 层 | `integrations/` | 封装腾讯文档等第三方接口细节 |

## 7. MySQL 表职责

| 表 | 作用 |
| --- | --- |
| `crawl_sources` | 记录数据源，如腾讯文档、Excel |
| `crawler_apps` | 记录支持的 App 类型和包名 |
| `crawl_task_submissions` | 任务提交表，一行代表一个待执行业务任务 |
| `crawl_task_executions` | 任务执行表，一行代表一次实际执行尝试 |
| `crawl_results` | 标准化采集结果表 |
| `crawl_writebacks` | 写回结果表 |
| `crawl_jobs` | 一次 job 运行记录 |
| `task_log` | 调度日志 |

## 8. 新增能力的推荐方式

### 新增一个 App

1. 在 `apps/finance_crawler/crawlers/<app>.py` 实现 App Profile 和 Adapter。
2. 在 `apps/finance_crawler/crawlers/registry.py` 注册。
3. 如果有专属字段，写入 `CrawlResult.metrics["app_metrics"]`。
4. 不修改通用 workflow，除非新增了全新的业务阶段。

### 新增一个数据源

1. 在 `apps/finance_crawler/sources/<source>.py` 实现读取逻辑。
2. 输出统一的 `SourceRecord`。
3. 复用 `upsert_source_record_submissions()` 生成任务。

### 新增一个写回目标

1. 在 `apps/finance_crawler/sinks/<sink>.py` 实现写回适配器。
2. 如需第三方 API，在 `integrations/<sink>/` 封装底层客户端。
3. 通过 `services/writeback.py` 接入 workflow。

## 9. 当前常用命令

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
.\scripts\run.ps1 -Task excel-detail
.\scripts\run.ps1 -Task report
.\scripts\run.ps1 -Task scheduler
.\scripts\run.ps1 -Task supervisor
```

模块入口：

```powershell
python -m apps.finance_crawler.app --once fetch
python -m apps.finance_crawler.app --once check
python -m apps.finance_crawler.app --once detail
python -m apps.finance_crawler.app --once excel-detail
python -m apps.finance_crawler.app --once report
```
