# 架构说明

更完整的端到端流程见 [PROJECT_FLOW.md](PROJECT_FLOW.md)。本文只说明代码分层、核心抽象和扩展边界。

## 分层

| 层 | 位置 | 责任 |
| --- | --- | --- |
| 入口层 | `app.py`, `scripts/run.ps1`, `jobs/` | CLI、scheduler、supervisor、定时任务包装 |
| 运行时配置层 | `services/runtime_config.py`, `data_source_links`, `app_config` | 保存和加载任务源入口、OpenAPI 身份和 App 采集保护参数 |
| 工作流层 | `workflows/` | 编排 fetch、initial_check、detail_crawl、excel-detail、link-detail |
| 数据源层 | `sources/` | 读取腾讯文档、Excel、未来 API，输出 `SourceRecord` |
| 任务框架层 | `storage/` | 任务提交、执行、结果、写回记录和 MySQL 表 |
| App 适配层 | `crawlers/` | 链路识别、App 包名、deep link、采集计划、专属解析 |
| 手机执行层 | `mobile/` | ADB、设备会话、App 重启恢复、截图、XML、OCR、滑动、页面状态 |
| 写回层 | `sinks/`, `services/writeback.py` | 把标准结果写回腾讯文档、Excel 或未来目标 |
| 外部集成层 | `integrations/` | 腾讯文档等第三方 API 客户端 |

## 核心抽象

| 抽象 | 位置 | 说明 |
| --- | --- | --- |
| `RuntimeConfigItem` | `services/runtime_config.py` | MySQL 中的任务源配置项 |
| `SourceRecord` | `domain/records.py` | 数据源输出的统一记录 |
| `CrawlResult` | `domain/records.py` | App 采集后的统一结果 |
| `AppLinkProfile` | `crawlers/base.py` | 判断链接归属、目标包名和 direct link |
| `CapturePlan` | `crawlers/base.py` | 声明截图页数、OCR、滑动和等待策略 |
| `AppCrawlerAdapter` | `crawlers/base.py` | App 专属点击、解析和结果字段 |
| `WritebackPlan` | `services/writeback.py` | 写回目标、行定位和写回数据 |

## 工作流入口

| 入口 | 文件 | 用途 |
| --- | --- | --- |
| `fetch` | `workflows/tencent_docs_fetch.py` | 从腾讯文档读取候选链接并提交任务 |
| `check` | `workflows/initial_check.py` | 初检帖子是否存在并提取账号 |
| `detail` | `workflows/detail_crawl.py` | 批量详情采集 |
| `excel-detail` | `workflows/local_excel_detail.py` | 本地 Excel 直接详情采集 |
| `link-detail` | `workflows/single_link_detail.py` | 单条链接一次性详情测试 |

## 任务身份

任务由 `source_type + crawl_object_key + task_type` 唯一确定。

```text
crawl_object_key = url:<sha1(normalized_url)>
```

单链接测试为了每次都能独立执行，会使用：

```text
crawl_object_key = single_link:<sha1(run_token + url)>
```

行号、文件路径、sheet 名、输出路径属于定位信息，放入 `source_locator_json`，不参与普通批量任务身份。

## 手机采集边界

`mobile/` 提供通用执行能力，不放具体 App 业务。

```text
mobile/device_session.py   连接设备、打开链接、维护会话、必要时 force-stop 目标 App
mobile/capture_engine.py   ADB 截图、XML、OCR、滑动
mobile/record_capture.py   按 CapturePlan 执行页面采集
mobile/crawler.py          选择 App Adapter 并拼装结果
mobile/parsers.py          通用金融社区文本解析
```

`mobile/` 只处理通用执行问题，例如设备连接、App 打开、白屏/系统弹窗后的重启恢复、截图和页面状态识别。某个 App 需要点击哪里、截几页、解析哪些专属字段，仍然放到 `crawlers/<app>.py` 的 `AppCrawlerAdapter`，避免新增 App 时影响已有链路。

App 差异放在 `crawlers/`：

```text
crawlers/alipay.py         支付宝链路和解析
crawlers/antfortune.py     蚂蚁财富链路和解析
crawlers/tenpay.py         财付通链路、明细点击和调仓解析
crawlers/registry.py       App 注册表
```

## 扩展方式

新增 App：

1. 新增 `crawlers/<app>.py`。
2. 实现 `AppLinkProfile` 和 `AppCrawlerAdapter`。
3. 在 `crawlers/registry.py` 注册。
4. App 专属字段写入 `CrawlResult.metrics["app_metrics"]`。

新增数据源：

1. 新增 `sources/<source>.py`。
2. 输出 `SourceRecord`。
3. 调用 `upsert_source_record_submissions()` 进入任务框架。

新增写回目标：

1. 新增 `sinks/<sink>.py`。
2. 如需第三方 API，新增 `integrations/<sink>/`。
3. 通过 `services/writeback.py` 接入 workflow。
