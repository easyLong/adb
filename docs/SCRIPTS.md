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

## 3. profile 链路命令

profile 链路处理主页型任务，核心表是：

```text
profile_trigger_configs
profile_action_profiles
profile_trigger_runs
profile_metric_sources
profile_metric_runs
profile_metric_writebacks
```

| Task | 作用 |
| --- | --- |
| `profile-trigger-list` | 查看 profile 触发器 |
| `profile-trigger-run` | 手动跑 profile 触发器，默认跑 `kol_daily_metrics_wpvy0d` |
| `kol-daily-crawl` | 兼容命令，内部走默认 profile trigger |

示例：

```powershell
.\scripts\run.ps1 -Task profile-trigger-list
.\scripts\run.ps1 -Task profile-trigger-run
.\scripts\run.ps1 -Task profile-trigger-run -ReportDate 2026-06-06
```

## 4. KOL 每日表命令

| Task | 作用 |
| --- | --- |
| `kol-daily-snapshot` | 同步 KOL 基础数据，生成指定日期快照，并写入目标表 |
| `kol-daily-writeback` | 只把 `kol_daily_snapshots` 写回目标表 |
| `kol-daily-crawl` | 跑当天或指定日期主页采集，兼容入口 |

每天 22:00 默认跑 `kol-daily-snapshot`，生成明日行。

每天 08:00 默认跑 `kol-daily-crawl`，实际走：

```text
profile_trigger_configs.kol_daily_metrics_wpvy0d
```

手动生成某天行：

```powershell
.\scripts\run.ps1 -Task kol-daily-snapshot -ReportDate 2026-06-07
```

手动采集某天主页指标：

```powershell
.\scripts\run.ps1 -Task profile-trigger-run -ReportDate 2026-06-06
```

## 5. 旧 profile 拆分命令

这些命令仍保留，用于旧版主页统计链路排查：

| Task | 作用 |
| --- | --- |
| `profile-sync` | 从旧主页统计文档同步来源行 |
| `profile-daily-rows` | 根据模板生成每日行 |
| `profile-create-tasks` | 从 active profile targets 创建 DB-only 任务 |
| `profile-crawl` | 抓主页粉丝数 |
| `profile-writeback` | 写回粉丝数、增粉数 |
| `profile-metrics` | 同步、采集、写回一体流程 |
| `profile-post-reads` | 抓主页指定日期帖子阅读数 |

新 KOL 主页自动化优先使用 `profile-trigger-*`。

## 6. 其它业务命令

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

## 7. 常用排查

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
