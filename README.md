# 金融 App 帖子采集工作台

这是一个基于 Windows + Android 手机 + ADB 的金融 App 帖子采集项目。项目当前主要采集支付宝、蚂蚁财富、财付通/腾讯理财通帖子信息，并支持从腾讯文档读取待采集链接、写回采集结果。

项目已经从最初的“支付宝链路脚本”演进为更通用的采集框架：数据来源、App 链路、手机采集、结果写回都做了分层，后续新增本地 Excel、其他在线文档、更多 App 时，原则上不需要改动已有链路。

## 当前能力

- 支持 `alipay`、`antfortune`、`tenpay` 三类 App 链路识别和打开。
- 支持腾讯文档作为数据源，读取帖子链接和发布时间。
- 支持本地 Excel 作为可替换的数据源/写回目标适配器。
- 支持初检帖子是否存在，并提取发帖账号。
- 支持批量采集账号、正文、阅读数、评论数、截图。
- 财付通链路支持进入“去查看明细/调仓明细”，提取买入基金名称和金额。
- 支持结果写回腾讯文档，包含阅读数、评论数、状态、截图图片或截图路径。
- 支持 MySQL 兼容业务表 `posts`，同时双写框架表 `crawl_tasks`、`crawl_results`、`crawl_writebacks`。
- 当前 `posts` 仍是默认主业务路径，`crawl_*` 表用于逐步迁移到通用采集框架。
- 截图优先使用 `adb exec-out screencap -p`，比 `adb shell screencap + adb pull` 更快。

## 目录结构

```text
apps/
  finance_crawler/          当前金融帖子采集应用
docs/                       项目文档、环境变量模板、建表 SQL
scripts/                    项目级运行脚本和单链接测试脚本
requirements.txt            Python 依赖
```

`apps/finance_crawler` 内部结构：

```text
app.py                      调度入口，支持单任务、常驻调度、supervisor
config.py                   环境变量和默认配置
domain/                     通用记录对象和 source/crawler/sink 接口
crawlers/                   App Profile 和 App 专属 Adapter
mobile/                     手机采集执行层
mobile/capture_engine.py    ADB、deep link、截图、XML、OCR 底层能力
mobile/device_session.py    设备连接缓存、唤醒/锁屏检查、链接打开
mobile/page_status.py       页面可用/删除/错误状态判断
mobile/post_capture.py      按 CapturePlan 执行截图、XML、OCR、滑动采集
mobile/crawler.py           Adapter 调度、结果拼装
mobile/parsers.py           通用金融社区帖子解析规则
sources/                    数据源适配，例如腾讯文档
sinks/                      结果写回适配，例如腾讯文档、本地 Excel
workflows/                  fetch/check/batch 等业务编排
integrations/               腾讯文档等外部系统底层 API
storage/                    采集仓储边界、MySQL 兼容表和框架表写入
services/                   告警、报表、框架事件记录
utils/                      链路识别、表格解析、设备健康、限流等工具
```

## 快速开始

安装依赖：

```powershell
pip install -r requirements.txt
```

检查手机连接：

```powershell
adb devices
```

初始化数据库：

```powershell
.\scripts\run.ps1 -Task db
```

从腾讯文档拉取待采集链接：

```powershell
.\scripts\run.ps1 -Task fetch
```

执行初检：

```powershell
.\scripts\run.ps1 -Task check
```

执行批量采集：

```powershell
.\scripts\run.ps1 -Task batch
```

本地 Excel 直接批跑，不入库、不跑初检：

```powershell
$env:EXCEL_BATCH_INPUT_PATH = "D:\demo\5月20日.xlsx"
$env:EXCEL_BATCH_OUTPUT_PATH = "D:\demo\5月20日_batch_output.xlsx"
$env:EXCEL_BATCH_SOURCE_FILTER = "alipay,antfortune"
$env:EXCEL_BATCH_ALIPAY_LIMIT = "50"
$env:EXCEL_BATCH_ANTFORTUNE_LIMIT = "50"
.\scripts\run.ps1 -Task excel-batch
```

启动常驻调度：

```powershell
.\scripts\run.ps1 -Task scheduler
```

启动带崩溃恢复的 supervisor：

```powershell
.\scripts\run.ps1 -Task supervisor
```

单链接测试：

```powershell
python .\scripts\crawl_one_link.py "https://www.tencentwm.com/h5/v6/pages/discussion/main/detail/index?subject_id=202604232026170116723608&sharefm=app"
```

## Windows 和手机的角色

Windows 负责调度、读写腾讯文档、读写 MySQL、解析链接、发 ADB 命令、保存截图/XML/OCR 记录、运行 OCR 和报表。

手机负责真实打开 App、承载登录态、渲染帖子页面、提供 UI XML 和屏幕截图。所有 App 交互都发生在手机上，Windows 只是通过 ADB/uiautomator2 驱动手机。

## 输出文件位置

- `apps/finance_crawler/captures/post_<id>_<time>/`：全链路调试采集目录，包含 `page_*.png`、`page_*.xml`、`ui_records.jsonl`、`ocr_records.jsonl`，财付通还会有 `tenpay_trade_*` 文件。
- `apps/finance_crawler/screenshots/`：用于业务写回的首屏截图。
- `apps/finance_crawler/logs/`：运行日志和告警记录。
- `apps/finance_crawler/reports/`：日报输出。
- `apps/finance_crawler/exports/latest_candidates.json`：最近一次从数据源解析出的候选链接快照。

## 配置和凭证

运行脚本默认会尝试读取：

- `D:\password\tengxun.txt`
- `D:\password\mysql.txt`

也可以直接设置环境变量。模板见 [docs/env.example.ps1](docs/env.example.ps1)。

常用配置：

- `TENCENT_DOC_URL` / `TENCENT_DOC_FILE_ID` / `TENCENT_DOC_SHEET_ID`
- `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DATABASE`
- `ADB_PATH` / `DEVICE_SERIAL`
- `FETCH_LIMIT` / `BATCH_LIMIT`
- `SCROLL_TIMES` / `BATCH_MAX_CAPTURE_PAGES`
- `TENPAY_PACKAGE`
- `WRITEBACK_SINK_TYPE`
- `WRITEBACK_EXCEL_PATH` / `WRITEBACK_EXCEL_SAVE_AS` / `WRITEBACK_EXCEL_SHEET_NAME`
- `EXCEL_BATCH_INPUT_PATH` / `EXCEL_BATCH_OUTPUT_PATH` / `EXCEL_BATCH_*_LIMIT`
- `USE_FRAMEWORK_TASKS_FOR_WORKFLOWS`

默认 MySQL 库名仍是 `alipay_crawler`，这是为了兼容历史本地数据；应用目录已经改为 `finance_crawler`。

## 文档索引

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：整体架构、分层边界、扩展方式。
- [docs/FINANCE_CRAWLER.md](docs/FINANCE_CRAWLER.md)：当前金融帖子采集应用的详细说明。
- MySQL 当前表作用见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 的“MySQL 表分层”章节。
- [docs/PROJECT_PROCESS.md](docs/PROJECT_PROCESS.md)：项目演进过程和后续优化顺序。
- [docs/OPERATIONS.md](docs/OPERATIONS.md)：运行、测试、排障命令。
- [docs/init.sql](docs/init.sql)：MySQL 建表 SQL。

## 扩展原则

新增 App 优先新增 `apps/finance_crawler/crawlers/<app>.py`，在 `crawlers/registry.py` 注册 Profile/Adapter，不要把 App 特例写进通用采集流程。

新增数据源优先新增 `sources/<source>.py`，复用 `domain.SourceRecord` 和 `utils/tabular_links.py`；当前已提供 `sources/excel.py` 作为本地 Excel 示例。

新增写回目标优先新增 `sinks/<sink>.py` 和必要的 `integrations/<sink>/` 底层客户端，不要让 workflow 直接调用第三方 API；当前已提供 `sinks/excel.py` 作为本地 Excel 示例。
