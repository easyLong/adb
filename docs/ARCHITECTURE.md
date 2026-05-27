# 项目架构

## 架构目标

项目的核心目标不是写死某一个 App 的爬虫，而是形成一个可扩展的“金融 App 帖子采集工作台”。当前已经支持支付宝、蚂蚁财富、财付通/腾讯理财通，后续可以继续接入新的数据源、新的金融 App 和新的写回目标。

设计上坚持三条边界：

- 数据从哪里来，由 `sources/` 负责。
- 用哪个 App 抓、怎么打开、怎么解析 App 特有内容，由 `crawlers/` 和 `mobile/` 负责。
- 结果写到哪里去，由 `sinks/` 和 `integrations/` 负责。

## 总体数据流

```text
外部数据源
  腾讯文档 / 后续 Excel / 后续 API
        |
        v
Source Adapter
  sources/tencent_docs.py
        |
        v
SourceRecord
  URL、source_app、发布时间、来源定位
        |
        v
存储入库
  posts 兼容业务表
  crawl_tasks 框架任务表
        |
        v
Workflow
  workflows/tencent_docs_fetch.py
  workflows/initial_check.py
  workflows/batch_crawl.py
        |
        v
Mobile Crawler
  mobile/capture_engine.py
  mobile/device_session.py
  mobile/page_status.py
  mobile/post_capture.py
  mobile/crawler.py
        |
        v
App Adapter
  crawlers/alipay.py
  crawlers/antfortune.py
  crawlers/tenpay.py
        |
        v
CrawlResult
  账号、正文、阅读数、评论数、截图、app_metrics
        |
        v
Result Sink
  sinks/tencent_docs.py
  integrations/tencent_docs/*
        |
        v
写回结果
  腾讯文档列更新 / 截图插图 / crawl_writebacks
```

## Windows 和手机分工

Windows 是控制端和数据处理端：

- 运行 Python 调度器和 workflow。
- 读取腾讯文档、写回腾讯文档。
- 读写 MySQL。
- 解析链接来源、转换 deep link。
- 通过 ADB/uiautomator2 控制手机。
- 保存截图、UI XML、OCR 记录和日志。
- 执行 RapidOCR、报表、告警。

手机是真实 App 执行端：

- 保存支付宝、蚂蚁财富、财付通等 App 的登录态。
- 接收 deep link 并打开目标页面。
- 渲染帖子内容和调仓明细。
- 提供屏幕截图和 UI XML。
- 执行点击、返回、滑动等真实 UI 操作。

因此，账号登录、风控状态、页面渲染都依赖手机；Windows 只负责自动化驱动和数据处理。

## 核心分层

| 层 | 位置 | 职责 |
| --- | --- | --- |
| 入口调度层 | `app.py`, `scripts/run.ps1` | 单任务、常驻调度、supervisor |
| 领域接口层 | `domain/*` | `SourceRecord`、`CrawlResult`、`WritebackResult`、`LinkSource`、`ResultSink` |
| 数据源层 | `sources/*` | 将腾讯文档、Excel、API 等来源转成统一候选记录 |
| App 适配层 | `crawlers/*` | 链接识别、目标包名、deep link 改写、App 专属解析 |
| 手机采集层 | `mobile/*` | 设备会话、页面状态、截图、XML、OCR、滑动采集、采集编排 |
| 通用解析层 | `mobile/parsers.py` | 金融社区帖子账号、正文、阅读数、评论数解析规则 |
| 工作流层 | `workflows/*` | fetch、initial check、batch crawl 的业务编排 |
| 写回层 | `sinks/*` | 将采集结果写回业务系统 |
| 外部集成层 | `integrations/*` | 腾讯文档 OpenAPI 等第三方系统底层能力 |
| 存储层 | `storage/*` | MySQL 兼容表和框架表读写 |
| 服务层 | `services/*` | 告警、报表、框架事件记录 |
| 工具层 | `utils/*` | 链路识别、表格解析、设备健康、限流、URL 批量解析 |

## 当前应用结构

```text
apps/finance_crawler/
  app.py
  config.py
  domain/
    records.py
    interfaces.py
  crawlers/
    base.py
    constants.py
    registry.py
    alipay.py
    antfortune.py
    tenpay.py
  mobile/
    capture_engine.py
    crawler.py
    device_session.py
    page_status.py
    post_capture.py
    parsers.py
  sources/
    tencent_docs.py
    excel.py
  sinks/
    tencent_docs.py
    excel.py
  integrations/
    qq_docs.py
    tencent_docs/
      client.py
      rows.py
      write_requests.py
      screenshots.py
      writeback.py
  workflows/
    tencent_docs_fetch.py
    initial_check.py
    batch_crawl.py
  jobs/
    checker.py
    batch.py
  storage/
    db.py
    framework_db.py
  services/
    alerts.py
    framework_events.py
    report.py
  utils/
    device_health.py
    link_source.py
    rate_limiter.py
    tabular_links.py
    url_resolver.py
```

## App 链路扩展机制

App 差异被拆成两类对象。

`AppLinkProfile` 负责“这个链接属于谁、要打开哪个包、是否需要改写 deep link、页面 ready 看什么关键词”。

`AppCrawlerAdapter` 负责“这个 App 怎么采集、采集前要不要点特殊入口、账号怎么解析、内容怎么解析、阅读/评论怎么解析、专属指标怎么输出”。

`CapturePlan` 负责“这个 App 的主帖采集要截几屏、是否启用 OCR、OCR 阈值、滑动等待多久、拿到核心字段后是否提前停止、明细页最多滑几次”。通用 `mobile/post_capture.py` 只执行计划，不再把不同 App 的截图页数、OCR 策略和明细页滚动次数写死在业务编排里。

手机采集层内部继续拆成四块：

| 文件 | 职责 |
| --- | --- |
| `mobile/device_session.py` | 设备连接缓存、ADB 路径准备、唤醒/锁屏检查、链接打开 |
| `mobile/page_status.py` | 根据 UI 文本判断页面成功、删除、错误或未知 |
| `mobile/post_capture.py` | 按 `CapturePlan` 执行截图、XML、OCR、滑动和停止判断 |
| `mobile/crawler.py` | 选择 Adapter、调用采集引擎、解析字段、拼装结果 |

当前 Profile/Adapter：

| source_app | 文件 | 说明 |
| --- | --- | --- |
| `alipay` | `crawlers/alipay.py` | 支付宝短链、`alipays://`、`alipay://` |
| `antfortune` | `crawlers/antfortune.py` | 蚂蚁财富分享链接，改写为 `afwealth://platformapi/startapp?...` |
| `tenpay` | `crawlers/tenpay.py` | 财付通/腾讯理财通链接，支持调仓明细买入基金解析 |

新增 App 的推荐步骤：

1. 在 `crawlers/constants.py` 增加 `SOURCE_xxx`。
2. 新增 `crawlers/<app>.py`，实现 `AppLinkProfile`。
3. 如果截图页数、OCR、点击、明细页滚动或字段解析不同，继续实现 `AppCrawlerAdapter` 和 `capture_plan()`。
4. 在 `crawlers/registry.py` 注册 Profile 和 Adapter。
5. 如需配置包名，在 `config.py` 增加环境变量。

通用的 `mobile/crawler.py` 和 `workflows/batch_crawl.py` 不应该写入新 App 的特殊分支。

## 数据源扩展机制

数据源的职责是把外部表格、文件或 API 转成 `SourceRecord`。

当前已有：

| source_type | 文件 | 说明 |
| --- | --- | --- |
| `tencent_docs` | `sources/tencent_docs.py` | 从腾讯文档读取候选链接 |
| `excel` | `sources/excel.py` | 从本地 `.xlsx` 读取候选链接 |

`utils/tabular_links.py` 承担表格类数据的公共解析能力，包括：

- 判断 URL 是否支持。
- 从表格标题和单元格解析发布时间。
- 根据发布时间筛选符合延迟条件的候选链接。
- 根据 URL 识别 `source_app`。

本地 Excel 数据源已经复用 `utils/tabular_links.py`，只把“如何读取 Excel 行”放进 `sources/excel.py`。默认列配置沿用腾讯文档的 0-based 列索引，也可以在实例化时传入 `post_time_col` 和 `url_col`。

## 写回扩展机制

写回目标通过 `ResultSink` 隔离。当前腾讯文档既是数据源，也可以是写回目标，但两者职责分开。

当前已有：

| sink_type | 文件 | 说明 |
| --- | --- | --- |
| `tencent_docs` | `sinks/tencent_docs.py` | 写回初检和批处理结果 |
| `excel` | `sinks/excel.py` | 写回账号、阅读数、评论数、状态和截图路径到本地 `.xlsx` |

腾讯文档底层能力进一步拆在 `integrations/tencent_docs/`：

| 文件 | 职责 |
| --- | --- |
| `client.py` | OpenAPI 鉴权、读表、图片上传、批量更新 |
| `rows.py` | URL 行号定位和写回前安全校验 |
| `write_requests.py` | 单元格、行更新、截图链接、图片插入 request 构造 |
| `screenshots.py` | 截图上传、插图失败后的路径/链接降级 |
| `writeback.py` | 初检和批处理批量写回编排 |

`integrations/qq_docs.py` 只保留旧函数兼容入口。新代码应优先依赖 `sources/`、`sinks/` 或 `integrations/tencent_docs/*`。

## MySQL 表分层

数据库分为框架表和当前业务兼容表。

框架表：

| 表 | 职责 |
| --- | --- |
| `crawl_sources` | 数据源注册 |
| `crawler_apps` | 可用 App 注册 |
| `crawl_jobs` | fetch/check/batch 等任务运行记录 |
| `crawl_tasks` | 通用待采集任务 |
| `crawl_results` | 通用采集结果，差异字段放入 `metrics_json` |
| `crawl_writebacks` | 写回目标系统的结果记录 |

业务兼容表：

| 表 | 职责 |
| --- | --- |
| `posts` | 当前金融帖子业务兼容表 |
| `task_log` | 历史调度日志表 |

默认库名仍是 `alipay_crawler`，这是历史兼容选择，不代表当前应用仍只服务支付宝。后续如果要改库名，应单独做数据迁移。

`check` / `batch` 默认仍从 `posts` 查询，保证现有业务链路稳定。现在已经新增 `USE_FRAMEWORK_TASKS_FOR_WORKFLOWS` 开关和基于 `crawl_tasks` / `crawl_results` 的待处理查询函数，后续可以灰度切换，让 `posts` 逐步退化为兼容层。

## 运行入口

项目级入口：

```powershell
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task batch
.\scripts\run.ps1 -Task scheduler
.\scripts\run.ps1 -Task supervisor
```

Python 模块入口：

```powershell
python -m apps.finance_crawler.app --once fetch
python -m apps.finance_crawler.app --once check
python -m apps.finance_crawler.app --once batch
python -m apps.finance_crawler.app
python -m apps.finance_crawler.app --supervise
```

## 当前后续优化方向

1. 给 `tabular_links.py`、`link_source.py`、`mobile/parsers.py`、`crawlers/tenpay.py` 增加稳定单元测试。
2. 灰度打开 `USE_FRAMEWORK_TASKS_FOR_WORKFLOWS`，逐步让业务查询从 `posts` 迁移到 `crawl_tasks` / `crawl_results`。
3. 把本地 Excel Source/Sink 接入一个独立 workflow 或命令行，形成完整 Excel 全链路。
4. 把真实手机链路测试整理成固定脚本，覆盖支付宝、蚂蚁财富、财付通各 1 条样例。
5. 继续压缩 `integrations/qq_docs.py`，只保留确实需要对外兼容的旧函数。
