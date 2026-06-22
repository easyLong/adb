# ADB v2 自动执行操作手册

这份手册用于把一个腾讯在线文档配置成常驻自动执行：

```text
在线文档
  -> submit worker 生成 task_submissions
  -> crawl worker 调用 ADB 手机采集
  -> writeback worker 回填腾讯文档
```

当前项目只做 Android App / ADB 爬虫。桌面、浏览器、UI Automation、mock 不在本项目链路里。

## 1. 启动前检查

进入项目根目录：

```powershell
cd c:\Code\adb
```

确认依赖和 ADB：

```powershell
pip install -r requirements.txt
adb devices -l
```

如果只连接一台手机，可以不配置 `DEVICE_SERIAL`。如果同时连接多台手机，必须指定一台：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet DEVICE_SERIAL=<adb-serial>
```

USB 设备通常是普通序列号，WiFi 设备通常是 `ip:port`。

## 2. 初始化数据库

新版本数据库是 `crawler_app`，账号密码沿用旧库。

`.env` 里只需要放 MySQL 连接信息，例如：

```powershell
$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "你的密码"
$env:MYSQL_DATABASE = "crawler_app"
```

初始化 v2 表：

```powershell
.\scripts\run.ps1 -Task crawler-app-db
```

完整初始化项目库：

```powershell
.\scripts\run.ps1 -Task db
```

## 3. 配置腾讯文档 OpenAPI

OpenAPI 凭证写入运行时配置表，不写死在代码里：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet TENCENT_DOC_CLIENT_ID=你的ClientId,TENCENT_DOC_OPEN_ID=你的OpenId,TENCENT_DOC_ACCESS_TOKEN=你的AccessToken
```

如果用 `CLIENT_SECRET` 自动换 token，也可以配置：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet TENCENT_DOC_CLIENT_SECRET=你的ClientSecret,TENCENT_DOC_TOKEN_URL=https://docs.qq.com/oauth/v2/token
```

查看当前配置：

```powershell
.\scripts\run.ps1 -Task config
```

## 4. 两类任务入口

项目现在分两条主链路：

| 链路 | 用途 | 核心触发器 |
| --- | --- | --- |
| 帖子/链接型任务 | 每行是帖子链接 `post_url`，采集昵称、阅读数、截图、评论数、备注等 | `document_trigger_configs` |
| 主页型任务 | 每行是主页链接 `homepage_url`，采集粉丝数、主页最近帖子阅读数、持仓等复杂动作 | `profile_trigger_configs` |

普通在线文档的初检、详情、阅读数任务走 `document_trigger`。KOL 每日主页采集走 `profile_trigger`，默认配置是 `kol_daily_metrics_wpvy0d`。

## 5. 配置常驻 worker 间隔

常驻 scheduler 会周期运行三个 v2 worker：

```text
submit worker    扫描到期 document trigger，生成 task_submissions
crawl worker     领取 pending/retry 任务，调用 ADB 手机采集
writeback worker 回填 pending writeback_plans
```

建议线上使用：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet SUBMIT_WORKER_INTERVAL_SECONDS=300,V2_CRAWL_WORKER_INTERVAL_SECONDS=30,V2_WRITEBACK_WORKER_INTERVAL_SECONDS=30,ENABLE_LEGACY_SCHEDULER_JOBS=false
```

含义：

```text
submit worker 每 5 分钟醒一次，看有没有到期 trigger
每个 document trigger 自己通常每 10 分钟真正扫描一次在线文档
crawl/writeback worker 每 30 秒处理自己的队列；submit、crawl、writeback、profile 是隔离队列
```

`ENABLE_LEGACY_SCHEDULER_JOBS=false` 表示 scheduler 不启动旧版 fetch/check/detail 链路，只跑 v2 和已启用的新任务。

## 6. 配置 document trigger

document trigger 由两张表组成：

```text
document_trigger_configs   配哪个在线文档、如何选择 sheet、多久扫描一次
document_trigger_bindings  配这个 trigger 要提交哪些任务和字段
```

### 场景 A：同一个基础 URL，每天一个日期 sheet

适合 sheet 名称类似 `0604`、`06-04`、`2026-06-04` 的在线文档。

初检只适合当天跑，目标日期偏移必须是 `0`：

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_initial_check" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentSheetMode "date_sheet" `
  -SubmitTargetDateOffsetDays 0 `
  -SubmitScanIntervalSeconds 600 `
  -DocumentDescription "红土每日初检"

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_initial_check" `
  -DocumentTaskType "initial_check" `
  -DocumentFields "account_name"
```

详情通常跑昨天的数据，单独建一个 trigger，目标日期偏移设为 `-1`：

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_detail" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentSheetMode "date_sheet" `
  -SubmitTargetDateOffsetDays -1 `
  -SubmitScanIntervalSeconds 600 `
  -DocumentDescription "红土昨日详情"

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_detail" `
  -DocumentTaskType "detail" `
  -DocumentFields "account_name,read_count,screenshot,remark"
```

如果某个文档只需要回填阅读数，单独建 `read_count` trigger，不要复用 `detail`：

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_read_count" `
  -TencentDocUrl "https://docs.qq.com/sheet/DV1ZuSnBjdGpVY1Fi" `
  -DocumentSheetMode "date_sheet" `
  -SubmitTargetDateOffsetDays -1 `
  -SubmitScanIntervalSeconds 600 `
  -DocumentDescription "红土阅读数-only，每天处理昨日日期sheet"

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_read_count" `
  -DocumentTaskType "read_count" `
  -DocumentFields "read_count"
```

`redsoil_read_count` 如果因为周末或缺少日期 sheet 被停用/报错，重新执行上面的 `v2-trigger-set` 会把 trigger 更新为 active。再手动提交指定日期：

```powershell
.\scripts\run.ps1 -Task v2-trigger-list
.\scripts\run.ps1 -Task v2-trigger-submit `
  -DocumentConfigKey "redsoil_read_count" `
  -ReportDate 2026-06-08
```

历史日期只需要把 `-ReportDate` 换成要处理的日期；例如 0605 用 `2026-06-05`，0608 用 `2026-06-08`。

`date_sheet` 可以匹配同一天多个 sheet。例如同一个文档里有 `0605-A` 和 `0605-B`，一次 submit 会把两个 sheet 都提交。`submit_runs.summary_json.sheets` 会记录每个 sheet 的提交明细。

### 场景 B：同一个基础 URL，不同固定 sheet 做不同任务

每个功能 sheet 建一个 trigger：

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_initial_check_sheet" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentSheetMode "fixed_sheet" `
  -DocumentSheetId "qlmz7y" `
  -SubmitScanIntervalSeconds 600

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_initial_check_sheet" `
  -DocumentTaskType "initial_check" `
  -DocumentFields "account_name"
```

也可以按标题选择：

```powershell
-DocumentSheetMode "sheet_title" -DocumentSheetTitle "0604-红土舆情监测"
```

或按标题关键字选择：

```powershell
-DocumentSheetMode "sheet_title_contains" -DocumentSheetKeyword "红土"
```

## 7. 配置 profile trigger

profile trigger 用于主页型任务，不走 `task_submissions` / `writeback_plans`，而是走：

```text
profile_trigger_configs
  -> profile_metric_sources
  -> profile_metric_runs
  -> profile_metric_writebacks
```

当前 KOL 每日任务优先走数据库主链路：

```text
kol_daily_db_pipeline
  -> kol_daily_metrics
  -> Tenpay external reads T-1 到 T-5
  -> profile_metric_sources / profile_metric_runs
```

KOL 每日主链路不是 `document_trigger`。详细说明见 [KOL_DAILY_DB_PIPELINE.md](KOL_DAILY_DB_PIPELINE.md)。

查看旧 profile trigger：

```powershell
.\scripts\run.ps1 -Task profile-trigger-list
```

手动跑今天的新主链路：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline
```

手动跑指定日期的新主链路：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline -ReportDate 2026-06-22
```

`profile-trigger-run` 和 `kol-daily-crawl` 仍保留为兼容命令，用于旧腾讯文档回填链路排查。

## 8. 先试跑一次

查看 document trigger：

```powershell
.\scripts\run.ps1 -Task v2-trigger-list
```

`v2-trigger-list` 会显示 active 和 disabled 的触发器。看到 `status=disabled` 或 `scan_status=error` 时，先看 `last_error`；如果是缺少日期 sheet，通常不是代码问题，只是对应日期没有人工建表。

手动提交某一天：

```powershell
.\scripts\run.ps1 -Task v2-trigger-submit `
  -DocumentConfigKey "redsoil_detail" `
  -ReportDate 2026-06-05
```

跑一次采集 worker：

```powershell
.\scripts\run.ps1 -Task v2-crawl-worker-once
```

跑一次回填 worker：

```powershell
.\scripts\run.ps1 -Task v2-writeback-worker-once
```

这三步正常后，再启动常驻。

## 9. 启动常驻

前台启动：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

守护启动，进程异常退出后会自动拉起：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

启动后会注册：

```text
v2 submit worker       每 SUBMIT_WORKER_INTERVAL_SECONDS 秒检查 document trigger
v2 crawl worker        每 V2_CRAWL_WORKER_INTERVAL_SECONDS 秒处理采集队列
v2 writeback worker    每 V2_WRITEBACK_WORKER_INTERVAL_SECONDS 秒处理回填队列
kol_daily_db_pipeline  每天 KOL_DAILY_CRAWL_TIME 串行执行 KOL 数据库主链路
```

## 10. 字段和 URL 规则

业务字段名称固定，但列位置可以移动。系统通过表头识别字段，不通过固定列号识别。

常用字段：

```text
post_url       帖子链接
homepage_url   主页链接
account_name   发帖账号昵称
post_time      发帖时间
read_count     阅读数
comment_count  评论数
screenshot     截图
remark         备注
check_result   初检结果
fans_count     粉丝数
growth_count   增粉数
```

帖子/链接型任务的重复 URL 规则：

```text
同一个 sheet 内，只提交第一个 URL
后续重复 URL 标记为跳过
任务去重不依赖 row_index
回填时按当前 sheet 的 URL 重新定位
只有 URL 唯一匹配时才写；重复时标记重复，不盲写
```

截图字段回填的是上传后的图片，不是本地路径。备注字段记录本次运行结果，例如成功、页面找不到、设备失败、最终失败原因等。

## 11. 最小上手命令

```powershell
cd c:\Code\adb

.\scripts\run.ps1 -Task crawler-app-db

.\scripts\run.ps1 -Task config `
  -ConfigSet TENCENT_DOC_CLIENT_ID=你的ClientId,TENCENT_DOC_OPEN_ID=你的OpenId,TENCENT_DOC_ACCESS_TOKEN=你的AccessToken,ENABLE_LEGACY_SCHEDULER_JOBS=false,SUBMIT_WORKER_INTERVAL_SECONDS=300,V2_CRAWL_WORKER_INTERVAL_SECONDS=30,V2_WRITEBACK_WORKER_INTERVAL_SECONDS=30

.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_initial_check" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentSheetMode "date_sheet" `
  -SubmitTargetDateOffsetDays 0 `
  -SubmitScanIntervalSeconds 600

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_initial_check" `
  -DocumentTaskType "initial_check" `
  -DocumentFields "account_name"

.\scripts\run.ps1 -Task v2-trigger-submit -DocumentConfigKey "redsoil_initial_check"
.\scripts\run.ps1 -Task v2-crawl-worker-once
.\scripts\run.ps1 -Task v2-writeback-worker-once

.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

## 12. 日常排查

查看运行进程：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'apps\.finance_crawler\.app' -or $_.CommandLine -match 'run\.ps1' } |
  Select-Object ProcessId, Name, CommandLine
```

查看最近日志：

```powershell
Get-ChildItem apps\finance_crawler\logs |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name, LastWriteTime, Length
```

查看配置：

```powershell
.\scripts\run.ps1 -Task config
.\scripts\run.ps1 -Task v2-trigger-list
.\scripts\run.ps1 -Task profile-trigger-list
```

单独跑 worker：

```powershell
.\scripts\run.ps1 -Task v2-submit-worker-once
.\scripts\run.ps1 -Task v2-crawl-worker-once
.\scripts\run.ps1 -Task v2-writeback-worker-once
```

如果 `pending` 一直不动，按这个顺序排查：

```powershell
.\scripts\run.ps1 -Task workers-status
adb devices -l
Get-Content apps\finance_crawler\logs\queue_workers\crawl.err.log -Tail 120
```

常见原因：

| 现象 | 处理 |
| --- | --- |
| `crawl` worker stopped | `.\scripts\run.ps1 -Task workers-start` |
| `adb devices -l` 为空 | 先恢复手机连接；worker 会在下一轮继续扫 |
| `task submission exceeded attempts` | 超过 `max_attempts` 的旧任务不应再卡队列；拉取最新代码并重启 worker |
| `read_count_not_found` | 页面确实没有阅读数或内容不可见，达到次数后会失败并写备注 |
| 腾讯文档截图显示本地路径 | 拉取最新代码并重启 writeback；新逻辑不再把本地路径当图片写回 |
