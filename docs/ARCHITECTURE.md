# 架构说明

## 总体原则

项目是多 App 爬虫工作台，根目录只放项目级内容：

```text
apps/             各个爬虫应用
docs/             项目文档
scripts/          项目级脚本
archive/          历史材料
platform-tools/   ADB 工具
```

单个爬虫应用必须放在 `apps/<app_name>/` 下，例如：

```text
apps/
  alipay_crawler/
  xxx_crawler/
  yyy_crawler/
```

## App 内部结构

每个 App 推荐使用同一套目录习惯：

```text
apps/<app_name>/
  app.py
  config.py
  domain/
  sources/
  sinks/
  workflows/
  integrations/
  jobs/
  storage/
  services/
  utils/
```

职责说明：

| 目录/文件 | 职责 |
| --- | --- |
| `app.py` | 调度入口，提供 `--once` 单任务运行 |
| `config.py` | 读取环境变量和默认配置 |
| `domain/` | 通用领域对象和接口，例如 `SourceRecord`、`CrawlResult`、`LinkSource`、`AppCrawler`、`ResultSink` |
| `sources/` | 数据源适配，把腾讯文档、Excel、API 转成 `SourceRecord` 或候选数据 |
| `sinks/` | 写回适配，把采集结果写回腾讯文档、Excel 或其他系统 |
| `workflows/` | 业务编排层，组合 source、crawler、storage、sink，例如 fetch、initial check、batch crawl |
| `integrations/` | 腾讯文档、Excel、第三方 API 的底层客户端能力 |
| `jobs/` | 定时任务入口和兼容包装，尽量保持很薄 |
| `storage/` | MySQL、缓存、持久化访问；框架表与业务表分开 |
| `services/` | 报告、告警、框架事件记录等业务服务 |
| `utils/` | 日志、通用工具 |

## 新的核心边界

当前重构目标是把“数据从哪里来”“用哪个 App 抓”“结果写到哪里去”拆成三个可替换角色：

```text
LinkSource
  -> SourceRecord
  -> AppCrawler
  -> CrawlResult
  -> ResultSink / Business Workflow
```

| 角色 | 稳定职责 | 当前例子 |
| --- | --- | --- |
| `LinkSource` | 提供待爬链接和源记录定位信息 | 腾讯文档、本地 Excel |
| `AppCrawler` | 打开对应 App，采集账号、阅读数、评论数等结果 | 支付宝、蚂蚁财富 |
| `ResultSink` | 把结果写回目标系统 | 腾讯文档写回、Excel 回填 |
| `Business Workflow` | 组合 source、crawler、storage、sink，处理业务规则 | 初检、批处理、日报 |

注意：腾讯文档可以同时是 `LinkSource` 和 `ResultSink`，但“写回腾讯文档第几列、插入截图、标黄”等属于业务写回，不属于核心爬虫能力。这样以后把腾讯文档换成本地 Excel 或其他在线表格时，不需要改 App 抓取层。

## 当前 App

`apps/alipay_crawler`

支付宝/蚂蚁财富帖子数据采集应用。详见 [ALIPAY_CRAWLER.md](ALIPAY_CRAWLER.md)。

当前实现保持单 App 内聚，但在 App 内已经区分多条执行链路：

- `alipay`：`ur.alipay.com`、`alipays://`、`alipay://`
- `antfortune`：`think.klv5qu.com`、`afwealth://`
- `tenpay`：`tencentwm.com`、`tenpay://`、`tencentwm://`

这些链路共用同一套任务调度、ADB/uiautomator2 抓取、MySQL 落库和腾讯文档写回框架，只在链接识别和 App 唤起阶段分流。

## 当前分层

以 `apps/alipay_crawler` 为例，当前大致分成以下层次：

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 入口调度层 | `app.py` | `fetch/check/batch/report/scheduler/supervisor` |
| 配置层 | `config.py` | MySQL、腾讯文档、ADB、设备、目录配置 |
| 领域层 | `domain/*` | 定义通用输入、输出和 source/crawler/sink 接口 |
| 数据源层 | `sources/*` | 从腾讯文档、Excel 等来源读取候选链接 |
| 写回层 | `sinks/*` | 把初检和批处理结果写回腾讯文档、Excel 等目标 |
| 工作流层 | `workflows/*` | 承载业务编排，例如腾讯文档 fetch 入库、初检、批处理 |
| 集成层 | `integrations/qq_docs.py` | 腾讯文档底层 API、单元格请求、图片上传等兼容能力 |
| 来源识别层 | `utils/link_source.py` | 将链接识别为 `alipay` / `antfortune` / `unknown` |
| 执行引擎层 | `alipay/capture_engine.py` | deep link 转换、ADB 打开、页面采集、RapidOCR |
| 任务与存储层 | `jobs/*`, `storage/db.py`, `services/*` | 初检、批处理、落库、日报、告警 |
| 运行保护层 | `utils/device_health.py`, `utils/rate_limiter.py`, `utils/url_resolver.py` | 设备健康检查、任务预算、批量链接解析 |

`services/framework_events.py` 负责尽力写入 `crawl_results` 和 `crawl_writebacks`。workflow 只传业务结果和 metrics，不直接关心框架表写入细节；即使框架表写入失败，也不会中断旧 `posts` 兼容链路。

## MySQL 表分层

数据库分成“框架表”和“业务表”两类：

| 类型 | 表 | 职责 |
| --- | --- | --- |
| 框架表 | `crawl_sources` | 数据来源注册，例如腾讯文档、Excel、API |
| 框架表 | `crawler_apps` | 可用 App 注册，例如支付宝、蚂蚁财富 |
| 框架表 | `crawl_jobs` | 一轮 fetch/check/batch 等任务的运行记录 |
| 框架表 | `crawl_tasks` | 通用待爬任务，保存来源定位、App 类型、URL 和任务状态 |
| 框架表 | `crawl_results` | 通用采集结果，初检和批处理都会写入，差异字段放在 `metrics_json` |
| 框架表 | `crawl_writebacks` | 写回目标系统的结果记录，包含成功、失败和跳过 |
| 业务表 | `posts` | 当前支付宝/蚂蚁财富帖子业务的兼容表 |
| 业务表 | `task_log` | 当前调度日志表，后续可逐步迁移到 `crawl_jobs` |

第一阶段保留 `posts`，并在 `upsert_post()` 时同步写入 `crawl_tasks`。第二阶段已经新增 `sources/`、`sinks/`、`workflows/`，让新入口不再直接依赖 `integrations/qq_docs.py`。第三阶段已经把 `checker.py`、`batch.py` 收敛为薄入口，初检和批处理主体逻辑移动到 `workflows/`。第四阶段开始双写结果：旧 `posts` 继续更新，新 `crawl_results` / `crawl_writebacks` 同步记录采集结果和写回状态。后续迁移节奏是：继续把旧 `qq_docs.py` 拆成更小的底层客户端模块，并逐步让业务查询从 `posts` 迁移到框架表。

## 统一运行入口

项目级脚本 [scripts/run.ps1](../scripts/run.ps1) 通过 `-App` 选择应用：

```powershell
.\scripts\run.ps1 -App alipay_crawler -Task fetch
.\scripts\run.ps1 -App xxx_crawler -Task fetch
```

约定每个 App 都暴露：

```powershell
python -m apps.<app_name>.app --once <task>
python -m apps.<app_name>.app
```

## 什么时候抽公共包

目前先保持 App 内聚。只有当多个 App 真的复用同一类能力时，再抽项目级公共包，例如：

```text
crawler_core/
  scheduler/
  adb/
  docs/
  storage/
```

不要为了“看起来通用”提前抽象。
