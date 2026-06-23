# Finance Crawler

本项目只做 Android App / ADB 采集。桌面、浏览器、Windows UI Automation 采集不放在本项目里。

系统的核心职责是：从在线文档或数据库生成采集任务，用真实 Android 手机打开支付宝、蚂蚁财富、腾讯理财通等 App，采集帖子详情、阅读数、截图、主页粉丝数等数据，并把任务过程和结果沉淀到 MySQL。

## 当前主线

```text
帖子/链接型任务
  -> document_trigger
  -> task_submissions / task_executions
  -> writeback_plans
  -> 腾讯文档回填

主页/KOL 型任务
  -> kol_daily_metrics 数据库每日表
  -> 理财通外部阅读数同步
  -> ADB 主页粉丝数 / 增粉数采集
  -> 数据库结果查看和导出
```

KOL 每日数据现在优先走数据库主链路，腾讯文档不再作为主结果表。需要查看或导出时，启动本地 Web 页面。

## 快速开始

```powershell
cd C:\Code\adb
pip install -r requirements.txt
adb devices
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task config
```

启动队列隔离 worker：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

停止：

```powershell
.\scripts\run.ps1 -Task workers-stop
```

## 常用命令

帖子/链接型任务：

```powershell
.\scripts\run.ps1 -Task v2-trigger-list
.\scripts\run.ps1 -Task v2-submit-worker-once
.\scripts\run.ps1 -Task v2-crawl-worker-once
.\scripts\run.ps1 -Task v2-writeback-worker-once
```

单链接调试：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

KOL 数据库主链路：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline
.\scripts\run.ps1 -Task kol-daily-db-pipeline -ReportDate 2026-06-22
```

专用 wrapper：

```powershell
.\scripts\kol-daily-db-pipeline.ps1
.\scripts\kol-daily-db-pipeline.ps1 -ReportDate 2026-06-22 -StartWeb
```

KOL 数据查看页面：

```powershell
.\scripts\run.ps1 -Task kol-metrics-web -WebHost 0.0.0.0 -WebPort 8091
```

打开：

```text
http://127.0.0.1:8091/
http://<LAN-IP>:8091/
```

If another device on the LAN cannot open the page, allow inbound TCP 8091 in Windows Firewall.

## 文档入口

- [docs/README.md](docs/README.md)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [docs/PROJECT_FLOW.md](docs/PROJECT_FLOW.md)
- [docs/SCRIPTS.md](docs/SCRIPTS.md)
- [docs/KOL_DAILY_DB_PIPELINE.md](docs/KOL_DAILY_DB_PIPELINE.md)
- [docs/KOL_TENPAY_EXTERNAL_READS.md](docs/KOL_TENPAY_EXTERNAL_READS.md)
- [docs/RUNTIME_CONFIG.md](docs/RUNTIME_CONFIG.md)
- [docs/ACTION_TEMPLATES.md](docs/ACTION_TEMPLATES.md)
- [docs/CRAWLER_APP_V2.md](docs/CRAWLER_APP_V2.md)

## 目录

```text
apps/finance_crawler/
  app.py                    CLI、scheduler、supervisor 入口
  config.py                 默认配置和环境变量读取
  workflows/                业务流程编排
  crawler_app/              v2 数据库、任务、KOL 每日表和 Web 页面
  mobile/                   ADB、uiautomator2、截图、OCR、页面采集
  crawlers/                 App 链接识别和 App 专属解析
  integrations/tencent_docs 腾讯文档 OpenAPI 客户端
  services/                 运行时配置、告警、报告
scripts/
  run.ps1                   统一任务入口
  kol-daily-db-pipeline.ps1 KOL 数据库主链路快捷入口
docs/
  README.md                 文档入口
  SCRIPTS.md                脚本和任务索引
  PROJECT_FLOW.md           端到端流程
  RUNTIME_CONFIG.md         运行时配置
```
