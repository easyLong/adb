# 金融 App 帖子采集工作台

这是一个基于 Windows + Android 手机 + ADB 的金融 App 帖子采集项目。当前支持支付宝、蚂蚁财富、财付通/腾讯理财通链路，支持从腾讯文档或本地 Excel 获取待采集链接，并把采集结果写回业务目标。

## 当前主流程

```text
腾讯文档 / Excel / API
  -> SourceRecord
  -> crawl_task_submissions
  -> initial_check / detail_crawl
  -> crawl_task_executions
  -> 手机 ADB 打开 App
  -> App Adapter 采集和解析
  -> crawl_results
  -> Sink 写回
  -> crawl_writebacks
```

更完整的整体流程、数据流和项目框架见 [docs/PROJECT_FLOW.md](docs/PROJECT_FLOW.md)。

## 当前能力

- 支持 `alipay`、`antfortune`、`tenpay` 三类 App 链路识别和打开。
- 支持腾讯文档和本地 Excel 作为数据源。
- 支持初检任务：判断帖子是否存在并提取账号。
- 支持详情采集：账号、正文、阅读数、评论数、截图和 App 专属指标。
- 财付通链路支持进入调仓明细，提取买入基金名称和金额。
- MySQL 使用框架任务表：`crawl_task_submissions`、`crawl_task_executions`、`crawl_results`、`crawl_writebacks`。
- 截图优先使用 `adb exec-out screencap -p`。

## 快速开始

```powershell
pip install -r requirements.txt
adb devices
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
```

本地 Excel 直接详情采集：

```powershell
$env:EXCEL_DETAIL_INPUT_PATH = "D:\demo\input.xlsx"
$env:EXCEL_DETAIL_OUTPUT_PATH = "D:\demo\output.xlsx"
$env:EXCEL_DETAIL_SOURCE_FILTER = "alipay,antfortune,tenpay"
.\scripts\run.ps1 -Task excel-detail
```

单链接测试：

```powershell
python .\scripts\crawl_one_link.py "https://www.tencentwm.com/h5/v6/pages/discussion/main/detail/index?subject_id=202604232026170116723608&sharefm=app"
```

## 目录结构

```text
apps/finance_crawler/       采集应用主目录
docs/                       项目文档、环境变量模板、建表 SQL
scripts/                    运行脚本和单链接测试脚本
requirements.txt            Python 依赖
```

核心代码目录：

```text
app.py                      CLI、scheduler、supervisor 入口
config.py                   环境变量和默认配置
domain/                     领域模型
jobs/                       定时任务薄封装
workflows/                  fetch / check / detail / excel-detail 业务编排
sources/                    数据源适配器
sinks/                      写回目标适配器
crawlers/                   App Profile 和专属 Adapter
mobile/                     ADB、截图、XML、OCR、页面采集
integrations/               第三方 API 客户端
storage/                    MySQL 框架表读写
services/                   写回、报告、告警等服务
utils/                      链路、表格、限流、设备健康等工具
```

## 文档入口

- [docs/PROJECT_FLOW.md](docs/PROJECT_FLOW.md)：当前整体项目流程、数据流程、项目框架。
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：分层架构和扩展原则。
- [docs/FINANCE_CRAWLER.md](docs/FINANCE_CRAWLER.md)：业务流程、字段、MySQL 表说明。
- [docs/OPERATIONS.md](docs/OPERATIONS.md)：运行、测试、排障命令。
- [docs/env.example.ps1](docs/env.example.ps1)：环境变量模板。
- [docs/init.sql](docs/init.sql)：MySQL 建表 SQL。
