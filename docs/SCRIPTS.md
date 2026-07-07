# 脚本和任务索引

日常统一使用：

```powershell
.\scripts\run.ps1 -Task <task>
```

所有命令默认在项目根目录执行：

```powershell
cd c:\Code\adb
```

## 1. 基础命令

| Task | 作用 |
| --- | --- |
| `db` | 初始化或升级主数据库表，包括 legacy、document v2、profile 表 |
| `crawler-app-db` | 初始化或升级 `crawler_app` v2 表 |
| `config` | 查看或更新运行配置 |
| `scheduler` | 启动 scheduler |
| `supervisor` | 启动 supervisor，scheduler 崩溃后自动重启 |
| `workers-start` | 一键启动队列隔离 worker：submit-heartbeat、crawl、writeback、profile |
| `workers-status` | 查看队列隔离 worker 运行状态 |
| `workers-stop` | 停止队列隔离 worker |

查看配置：

```powershell
.\scripts\run.ps1 -Task config
```

更新配置：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet KEY=VALUE
```

启动常驻：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

## 2. document 链路命令

document 链路处理帖子/链接型任务，核心表是：

```text
document_trigger_configs
document_trigger_bindings
submit_runs
task_submissions
task_executions
writeback_plans
```

### 2.1 触发器配置

| Task | 作用 |
| --- | --- |
| `v2-trigger-set` | 创建或更新 document 触发器 |
| `v2-trigger-bind` | 给触发器绑定任务类型和字段 |
| `v2-trigger-list` | 查看触发器，包括 disabled 触发器 |
| `v2-trigger-submit` | 手动提交一个触发器 |
| `v2-submit-worker-once` | 手动跑一次 submit worker |

示例：

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey redsoil_detail `
  -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>" `
  -DocumentSheetMode fixed_sheet `
  -DocumentSheetId <sheetId> `
  -SubmitScanIntervalSeconds 600

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey redsoil_detail `
  -DocumentTaskType detail `
  -DocumentFields account_name,read_count,screenshot,remark
```

阅读数-only 示例：

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey redsoil_read_count `
  -TencentDocUrl "https://docs.qq.com/sheet/DV1ZuSnBjdGpVY1Fi" `
  -DocumentSheetMode date_sheet `
  -SubmitTargetDateOffsetDays -1 `
  -SubmitScanIntervalSeconds 600

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey redsoil_read_count `
  -DocumentTaskType read_count `
  -DocumentFields read_count
```

手动提交历史日期：

```powershell
.\scripts\run.ps1 -Task v2-trigger-submit `
  -DocumentConfigKey redsoil_read_count `
  -ReportDate 2026-06-05
```

### 2.2 一次性 document 工作流

| Task | 作用 |
| --- | --- |
| `v2-initial-check-submit` | 提交初检任务 |
| `v2-initial-check-crawl` | 采集初检任务 |
| `v2-initial-check-writeback` | 写回初检结果 |
| `v2-initial-check` | 初检提交、采集、写回一体执行 |
| `v2-detail-submit` | 提交详情任务 |
| `v2-detail-crawl` | 采集详情任务 |
| `v2-detail-writeback` | 写回详情结果 |
| `v2-detail` | 详情提交、采集、写回一体执行 |
| `v2-read-count-submit` | 提交阅读数任务 |
| `v2-read-count-crawl` | 采集阅读数任务 |
| `v2-read-count-writeback` | 写回阅读数结果 |
| `v2-read-count` | 阅读数提交、采集、写回一体执行 |

示例：

```powershell
.\scripts\run.ps1 -Task v2-detail `
  -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>" `
  -ReportDate 2026-06-04
```

### 2.3 worker

| Task | 作用 |
| --- | --- |
| `v2-crawl-worker-once` | 手动跑一次 document 采集 worker |
| `v2-writeback-worker-once` | 手动跑一次 document 写回 worker |

## 3. KOL 每日数据库主链路

日常优先使用 `kol-daily-db-pipeline`。这条链路以 `kol_daily_metrics` 为主结果表，串行执行：

```text
每日初始化 kol_daily_metrics
  -> 理财通外部阅读数 T-1 到 T-5 入库
  -> 今日主页粉丝数 / 增粉数 ADB 采集入库
```

| Task | 作用 |
| --- | --- |
| `kol-daily-db-pipeline` | 串行执行 KOL 每日数据库主链路 |
| `kol-tenpay-external-reads` | 单独补跑理财通外部阅读数；正常由 `kol-daily-db-pipeline` 串起来 |

启动常驻 worker 后，`profile` 角色会按 `KOL_DAILY_CRAWL_TIME` 注册：

```text
kol_daily_db_pipeline
```

手动跑今天：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline
```

手动跑指定日期：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline -ReportDate 2026-06-22
```

快捷脚本：

```powershell
.\scripts\kol-daily-db-pipeline.ps1
.\scripts\kol-daily-db-pipeline.ps1 -ReportDate 2026-06-22
```

调整阅读数回看天数：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS=5
```

## 4. KOL 结算表帖子指标

这条链路从 `crawler_app.kol_business_settlements` 读取 `post_url`，采集文章标题、评论数、点赞数和截图，再写回同一张表的 `article_title`、`comment_count`、`like_count`、`screenshot_url` 字段。

| Task | 作用 |
| --- | --- |
| `kol-settlement-metrics-submit` | 从结算表提交缺指标的帖子任务 |
| `kol-settlement-metrics-crawl` | 采集已提交的结算帖子指标任务 |
| `kol-settlement-metrics-writeback` | 将成功采集结果写回结算表 |
| `kol-settlement-metrics` | submit、crawl、writeback 一体执行 |

灰度跑一条：

```powershell
.\scripts\run.ps1 -Task kol-settlement-metrics -ReportDate 2026-07-02 -Limit 1
```

每天 23:00 自动跑一体流程：

```powershell
.\scripts\kol-settlement-metrics-schedule.ps1 -Action install
```

这个脚本通过 `$PSScriptRoot` 推导项目根目录，计划任务的 `WorkingDirectory`
不需要在命令里写死机器绝对路径。

查看计划任务状态：

```powershell
.\scripts\kol-settlement-metrics-schedule.ps1 -Action status
```

立即跑一次：

```powershell
.\scripts\kol-settlement-metrics-schedule.ps1 -Action run
```

删除计划任务：

```powershell
.\scripts\kol-settlement-metrics-schedule.ps1 -Action uninstall
```

## 5. 其它业务命令

| Task | 作用 |
| --- | --- |
| `doc-columns-check` | 检查在线文档表头字段识别结果 |
| `doc-link-reads` | 从文档链接列读取链接，回填阅读数列 |
| `article-sync` | 同步文章详情来源 |
| `article-crawl` | 采集文章详情 |
| `article-writeback` | 写回文章详情 |
| `article-details` | 文章详情一体流程 |
| `excel-detail` | 本地 Excel 详情采集 |
| `link-detail` | 单链接详情调试 |
| `report` | 生成报告 |

单链接调试：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

检查表头：

```powershell
.\scripts\run.ps1 -Task doc-columns-check `
  -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"
```

## 6. 常用排查

看设备：

```powershell
adb devices -l
```

看进程：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'finance_crawler|run\.ps1' } |
  Select-Object ProcessId,Name,CommandLine
```

看最新日志：

```powershell
Get-ChildItem apps\finance_crawler\logs |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name,LastWriteTime,Length

Get-Content apps\finance_crawler\logs\<date>.log -Tail 120
```

看 git 状态：

```powershell
git status --short
```

## 7. 今日详情定时提交脚本

`scripts/submit_redsoil_detail_today.ps1` 是一个单入口脚本，用于把当天
`redsoil_detail` 任务提交到 `task_submissions` 队列。它只负责提交任务，
后续仍然由常驻的 `crawl` 和 `writeback` worker 采集、回填。

默认行为：

- 目标日期：今天
- 定时提交时间：每天 16:00
- 提交范围：`redsoil_detail` 匹配到的当天日期 sheet
- 默认限制：每个匹配 sheet 最多 15 条，不是所有 sheet 合计 15 条
- 日志目录：`apps\finance_crawler\logs\scheduled_tasks`

注册每天 16:00 自动提交：

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action install
```

查看计划任务状态：

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action status
```

立即执行一次提交：

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action run
```

手动触发已注册的计划任务：

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action start
```

删除计划任务：

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action uninstall
```

修改每日触发时间，例如改成 16:30：

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action install -Time "16:30"
```

如果要提交全部当天详情任务，使用 `-Limit 0`：

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action install -Limit 0
```

## 6. 截图下载链接

`kol-settlement-metrics` 会把 `screenshot_url` 写成
`apps/finance_crawler/captures` 下截图文件的直接下载链接。

启动本机下载服务：

```powershell
.\scripts\run.ps1 -Task capture-file-server
```

默认链接格式：

```text
http://127.0.0.1:8765/captures/...png
```

如果要让其它机器访问节点上的截图，配置节点可访问地址：

```text
CAPTURE_FILE_SERVER_HOST=0.0.0.0
CAPTURE_FILE_SERVER_PORT=8765
CAPTURE_PUBLIC_BASE_URL=http://<node-host>:8765
```

## 7. KOL 结算表 IP 名称

`kol-settlement-metrics` 会复用帖子页面采集到的 `account_name`，并回写到
`kol_business_settlements.ip_name`。

这和文章标题、评论数、点赞数、截图共享同一次首屏 ADB 采集，不需要额外滚动或二次打开页面。
