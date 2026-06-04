# Finance Crawler

Windows + Android + ADB 驱动的金融 App 数据采集工作台。

项目通过腾讯文档、Excel 或单条链接读取待采集对象，使用真实 Android 手机打开支付宝、蚂蚁财富、财付通/腾讯理财通页面，采集帖子详情、阅读数、评论数、点赞数、截图、大 V 主页粉丝数等数据，并写回腾讯文档或本地 Excel，同时将任务、结果和写回状态沉淀到 MySQL。

整体框架和数据链路见 [docs/ADB_FRAMEWORK_AND_DATA_FLOW.md](docs/ADB_FRAMEWORK_AND_DATA_FLOW.md)；脚本和常用命令见 [docs/SCRIPTS.md](docs/SCRIPTS.md)。

## 当前能力

- 支持 `alipay`、`antfortune`、`tenpay` 三类 App 链接识别和打开。
- 支持腾讯文档 OpenAPI 读取和写回。
- 支持通用帖子详情采集：账号、正文、阅读数、评论数、截图。
- 支持需求 1 文章详情：文章标题、截图、评论数、点赞数。
- 支持大 V 主页统计：粉丝数、增粉数、主页当日帖子阅读数。
- 支持腾讯文档 K 列链接读取详情页阅读数并写回 M 列。
- 支持每日自动从模板行生成当天大 V 统计行，避免手动复制日期块。
- 支持 MySQL 运行时配置，不需要频繁改代码或 `.env`。
- 支持 scheduler/supervisor 周期调度。

## 核心思路

```text
腾讯文档 / Excel / 单链接
  -> workflow 解析任务
  -> MySQL 保存任务、目标、结果
  -> ADB 驱动手机打开 App
  -> UI 节点 / OCR / 截图解析
  -> 写回腾讯文档或 Excel
```

在线文档现在只作为输入和输出适配器。大 V 主页、文章详情等长期数据会先进入数据库，写回文档只是最后一步适配。

## 快速开始

```powershell
pip install -r requirements.txt
adb devices
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task config
```

运行 scheduler：

```powershell
.\scripts\run.ps1 -Task scheduler
```

使用 supervisor 守护 scheduler：

```powershell
.\scripts\run.ps1 -Task supervisor
```

## 常用任务速查

```powershell
# 通用腾讯文档候选链接
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
```

单链接详情测试：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

大 V 每日行生成，默认按配置日期或今天：

```powershell
.\scripts\run.ps1 -Task profile-daily-rows
.\scripts\run.ps1 -Task profile-daily-rows -ReportDate 2026-06-04
```

大 V 粉丝数同步、抓取、写回：

```powershell
.\scripts\run.ps1 -Task profile-sync
.\scripts\run.ps1 -Task profile-crawl
.\scripts\run.ps1 -Task profile-writeback
.\scripts\run.ps1 -Task profile-metrics
```

大 V 主页帖子阅读数：

```powershell
.\scripts\run.ps1 -Task profile-post-reads -ReportDate 2026-06-04
```

需求 1 文章详情：

```powershell
.\scripts\run.ps1 -Task article-details
```

K 列链接阅读数写回 M 列：

```powershell
.\scripts\run.ps1 -Task doc-link-reads -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>" -ReportDate 0602
```

报告：

```powershell
.\scripts\run.ps1 -Task report -ReportDate 2026-06-04
```

更多任务、列偏移 tab、`.cmd` 辅助脚本和维护脚本见 [docs/SCRIPTS.md](docs/SCRIPTS.md)。

## 当前重要配置

配置优先从 MySQL 读取，`.env` 只保留 MySQL 连接信息。

```text
PROFILE_METRICS_DOC_URL              大 V 统计腾讯文档
PROFILE_METRICS_READ_RANGE           大 V 统计读取范围，建议 A1:H2000
PROFILE_METRICS_TEMPLATE_RANGE       每日复制模板范围，当前 A2:H126
PROFILE_METRICS_DAILY_PREPARE_TIME   每日生成当天行时间，当前 00:10
PROFILE_METRICS_INTERVAL_MINUTES     大 V 粉丝数抓取调度间隔，当前 60
DOC_LINK_READS_READ_RANGE            K->M 阅读数读取范围
ARTICLE_DETAILS_DOC_URL              需求 1 文章详情文档
ARTICLE_DETAILS_READ_RANGE           需求 1 读取范围
```

## 目录结构

```text
apps/finance_crawler/
  app.py                    CLI、scheduler、supervisor 入口
  config.py                 默认配置和环境变量读取
  workflows/                业务流程编排
  storage/                  MySQL 读写
  mobile/                   ADB、uiautomator2、截图、OCR、页面采集
  crawlers/                 App 链接识别和 App 专属解析
  integrations/tencent_docs 腾讯文档 OpenAPI 客户端
  services/                 运行时配置、报告、写回、告警
  utils/                    链接、限流、设备健康等工具
scripts/
  run.ps1                   常用任务入口
docs/
  README.md                 文档入口
  ADB_FRAMEWORK_AND_DATA_FLOW.md 当前整体框架和数据链路
  ARCHITECTURE.md           架构分层
  PROJECT_FLOW.md           端到端流程
  FINANCE_CRAWLER.md        业务说明
  RUNTIME_CONFIG.md         运行时配置
  OPERATIONS.md             运维手册
  SCRIPTS.md                脚本和任务索引
  init.sql                  MySQL 建表 SQL
```

## 文档入口

- [docs/README.md](docs/README.md)
- [docs/ADB_FRAMEWORK_AND_DATA_FLOW.md](docs/ADB_FRAMEWORK_AND_DATA_FLOW.md)
- [docs/SCRIPTS.md](docs/SCRIPTS.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/PROJECT_FLOW.md](docs/PROJECT_FLOW.md)
- [docs/FINANCE_CRAWLER.md](docs/FINANCE_CRAWLER.md)
- [docs/RUNTIME_CONFIG.md](docs/RUNTIME_CONFIG.md)
- [docs/OPERATIONS.md](docs/OPERATIONS.md)
