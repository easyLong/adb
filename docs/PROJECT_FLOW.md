# 项目流程

本文按日常使用视角说明数据如何流转，以及人工需要配置什么。

## 1. 总览

```text
业务在线文档
  -> 触发器配置
  -> worker 定时扫描
  -> MySQL 任务/来源/运行记录
  -> ADB 手机采集
  -> MySQL 结果记录
  -> 腾讯文档回填
```

现在有两类触发器：

| 触发器 | 处理对象 | 适用场景 |
| --- | --- | --- |
| `document_trigger` | 帖子链接 `post_url` | 初检、详情、阅读数、截图、评论数 |
| `profile_trigger` | 主页链接 `homepage_url` | 大 V 主页粉丝数、主页帖子阅读数、持仓等复杂主页动作 |

## 2. 人工需要配置什么

### 通用配置

首次或配置变化后：

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task crawler-app-db
.\scripts\run.ps1 -Task config
```

腾讯文档 OpenAPI 凭证在 MySQL `app_config`：

```text
TENCENT_DOC_CLIENT_ID
TENCENT_DOC_OPEN_ID
TENCENT_DOC_ACCESS_TOKEN
TENCENT_DOC_CLIENT_SECRET
TENCENT_DOC_TOKEN_URL
```

### 帖子/链接型任务

配置 `document_trigger_configs` 和 `document_trigger_bindings`。

常用命令：

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

### 主页型任务

配置 `profile_trigger_configs` 和 `profile_action_profiles`。

当前默认 KOL 主页触发器会自动生成：

```text
config_key: kol_daily_metrics_wpvy0d
doc: DYnhxS2VHZHBqR0V5 / wpvy0d
row_adapter: kol_daily_profile
source_name: kol_daily_crawl
task_type: profile_daily_metrics
fields: fans_count,growth_count,read_count
schedule_time: 08:00
```

查看：

```powershell
.\scripts\run.ps1 -Task profile-trigger-list
```

手动跑：

```powershell
.\scripts\run.ps1 -Task profile-trigger-run
.\scripts\run.ps1 -Task profile-trigger-run -ReportDate 2026-06-06
```

## 3. document 链路

适合普通在线文档：一行一个帖子链接。

```text
submit_worker
  -> 扫描到期 document_trigger_configs
  -> 根据 sheet_selector 选择 sheet
  -> 读取表头并按字段名解析列
  -> 提取 post_url 等 source_rows
  -> 按 URL 去重
  -> 写入 task_submissions
  -> 记录 submit_runs

v2_crawl_worker
  -> 扫 task_submissions
  -> 只启动 status=pending/retry 且 attempts < max_attempts 的任务
  -> 根据 task_type 选择 handler
  -> 根据 app_type + fields 选择 capture_action_profiles
  -> ADB 打开 App 页面并采集
  -> 写入 task_executions
  -> 生成 writeback_plans

v2_writeback_worker
  -> 扫 writeback_plans
  -> 重新读取当前 sheet
  -> 按 URL 定位当前行
  -> 腾讯文档 batchUpdate
  -> 更新 writeback 状态
```

常用任务类型：

| task_type | 说明 |
| --- | --- |
| `initial_check` | 初检，回填账号昵称；明确找不到页面写 `N` |
| `detail` | 详情，回填账号昵称、阅读数、截图、评论数、备注 |
| `read_count` | 只回填阅读数 |

字段靠表头识别，列偏移不是问题；但表头必须能匹配业务字段。

队列安全规则：

| 规则 | 目的 |
| --- | --- |
| `attempts < max_attempts` 才能启动采集 | 超限旧任务不再卡住整轮 worker |
| 任务间隔由 `READ_COUNT_POST_DELAY_MIN/MAX`、`DETAIL_POST_DELAY_MIN/MAX` 等控制 | 降低 App 风控和页面未渲染风险 |
| 截图必须上传后再写回 | 避免把本地路径写进腾讯文档 |
| 回填按 URL 重新定位当前行 | 避免人工插行、删行后写错位置 |
| URL 重复时只写第一个，后续标记重复 | 避免同一个链接多行被误写 |

## 4. profile 链路

适合主页型业务：一行一个主页链接。

```text
profile_trigger
  -> 读取 profile_trigger_configs
  -> 选择 row_adapter
  -> 读取在线文档当天行
  -> 写入 profile_metric_sources
  -> 记录 profile_trigger_runs

profile crawl
  -> 根据 profile_metric_sources 打开主页
  -> 采集粉丝数
  -> 如果需要，进入精确粉丝页
  -> 查找最近帖子
  -> 点击帖子详情采集阅读数
  -> 按 aggregation_policy 聚合
  -> 写入 profile_metric_runs

profile writeback
  -> 重新读取目标 sheet
  -> 按 日期 + 主页链接 定位唯一行
  -> 回填粉丝数、增粉数、阅读数
  -> 更新 profile_metric_writebacks
```

默认动作模板：

| action_profile_key | App | 说明 |
| --- | --- | --- |
| `alipay_profile_daily_metrics_v1` | 支付宝 | 粉丝数精确化，最近 3 条帖子阅读数取最大 |
| `antfortune_profile_daily_metrics_v1` | 蚂蚁财富 | 粉丝数精确化，最近 3 条帖子阅读数取最大 |
| `tenpay_profile_daily_metrics_v1` | 理财通 | App 重启清状态，UI/OCR 采集，三列计数器识别，粉丝详情页精确化，账号锚点校验 |
| `unknown_profile_daily_metrics_v1` | 兜底 | 基础主页采集 |

## 5. KOL 每日数据库主链路

主结果表：

```text
kol_daily_metrics
```

基础资料表：

```text
kol_base_profiles
```

主字段：

| 字段 | 说明 |
| --- | --- |
| `metric_date` | 日期 |
| `kol_name` | 大 V 名称 |
| `platform` | 平台 |
| `fans_count` | 粉丝数 |
| `growth_count` | 增粉数 |
| `read_count` | 阅读数 |

每天 `KOL_DAILY_CRAWL_TIME`：

```text
kol_daily_db_pipeline
  -> 从 kol_base_profiles 生成今天 kol_daily_metrics 空行
  -> 读取 7 个理财通外部文档，更新 T-1 到 T-5 read_count
  -> 从 kol_daily_metrics JOIN kol_base_profiles 生成今日主页采集来源
  -> ADB 采集今日 fans_count / growth_count
  -> 写回 kol_daily_metrics
```

这条主链路是串行的，前一步失败会在结果里体现，后续排查以数据库为准。腾讯文档只作为理财通外部阅读数来源，不作为 KOL 主结果表。

手动跑今日：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline
```

手动跑历史日期：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline -ReportDate 2026-06-22
```

查看页面：

```powershell
.\scripts\run.ps1 -Task kol-metrics-web -WebHost 0.0.0.0 -WebPort 8091
```

兼容旧链路仍保留：

```text
kol_daily_snapshot         旧腾讯文档快照写回
profile-trigger-run        旧 profile trigger 手动采集
kol-tenpay-external-reads  旧理财通阅读数腾讯文档写回
```

## 6. 常驻运行

启动：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

当前常驻任务：

| 任务 | 频率 |
| --- | --- |
| `v2_submit_worker` | 300 秒 |
| `v2_crawl_worker` | 30 秒 |
| `v2_writeback_worker` | 30 秒 |
| `kol_daily_db_pipeline` | `KOL_DAILY_CRAWL_TIME`，默认 08:00 |
| `heartbeat` | 30 分钟 |

## 7. 排查顺序

1. 看设备：

```powershell
adb devices -l
```

2. 看常驻进程：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'finance_crawler|run\.ps1' } |
  Select-Object ProcessId,Name,CommandLine
```

3. 看日志：

```powershell
Get-ChildItem apps\finance_crawler\logs |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name,LastWriteTime,Length
```

4. 看触发配置：

```powershell
.\scripts\run.ps1 -Task v2-trigger-list
.\scripts\run.ps1 -Task profile-trigger-list
```

5. 看数据库状态：

```text
document 链路：
  submit_runs
  task_submissions
  task_executions
  writeback_plans

profile 链路：
  profile_trigger_runs
  profile_metric_sources
  profile_metric_runs
  profile_metric_writebacks

KOL 数据库主链路：
  kol_base_profiles
  kol_daily_metrics
  profile_metric_sources
  profile_metric_runs
```
