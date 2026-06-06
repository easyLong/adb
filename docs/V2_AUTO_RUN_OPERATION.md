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
crawl/writeback worker 每 30 秒处理自己的队列
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

当前默认 KOL 主页触发器：

```text
config_key: kol_daily_metrics_wpvy0d
task_type: kol_daily_crawl
row_adapter: kol_daily_profile
requested_fields: fans_count,growth_count,read_count
target_date_offset_days: 0
schedule_time: 08:00
```

查看 profile trigger：

```powershell
.\scripts\run.ps1 -Task profile-trigger-list
```

手动跑今天：

```powershell
.\scripts\run.ps1 -Task profile-trigger-run
```

手动跑指定日期：

```powershell
.\scripts\run.ps1 -Task profile-trigger-run -ReportDate 2026-06-06
```

`kol-daily-crawl` 仍保留为兼容命令，但新链路优先使用 `profile-trigger-run`。

## 8. 先试跑一次

查看 document trigger：

```powershell
.\scripts\run.ps1 -Task v2-trigger-list
```

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
.\scripts\run.ps1 -Task scheduler
```

守护启动，进程异常退出后会自动拉起：

```powershell
.\scripts\run.ps1 -Task supervisor
```

启动后会注册：

```text
v2 submit worker       每 SUBMIT_WORKER_INTERVAL_SECONDS 秒检查 document trigger
v2 crawl worker        每 V2_CRAWL_WORKER_INTERVAL_SECONDS 秒处理采集队列
v2 writeback worker    每 V2_WRITEBACK_WORKER_INTERVAL_SECONDS 秒处理回填队列
kol_daily_snapshot     每天 22:00 生成明日 KOL 行
kol_daily_crawl        每天 08:00 通过 profile trigger 采集今日 KOL 主页数据
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

.\scripts\run.ps1 -Task supervisor
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
