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

现在有两条主链路：

| 链路 | 处理对象 | 适用场景 |
| --- | --- | --- |
| `document_trigger` | 帖子链接 `post_url` | 初检、详情、阅读数、截图、评论数 |
| `kol_daily_db_pipeline` | KOL 基础表中的主页链接 `homepage_url` | 大 V 粉丝数、增粉数、理财通阅读数 |

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

### KOL / 主页型任务

当前 KOL 主结果只看数据库表 `kol_daily_metrics`，日常只使用数据库主链路：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline
```

需要单独补跑理财通外部阅读数时使用：

```powershell
.\scripts\run.ps1 -Task kol-tenpay-external-reads
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
| `detail` | 详情，回填账号昵称、文章标题、阅读数、评论数、点赞数、截图、备注 |
| `read_count` | 只回填阅读数 |

字段靠表头识别，列偏移不是问题；但表头必须能匹配业务字段。`detail`
当前支持的常用写回字段包括 `account_name`、`article_title`、`read_count`、
`comment_count`、`like_count`、`screenshot`、`remark`。

动作选择规则：

```text
task_type -> 选择字段集合和业务 handler
app_type + metric -> 对应证据动作
多个 metric -> 合并动作集合 -> 共享一次采集 -> 分字段产出 writeback_plans
```

例如 `tenpay + (article_title, comment_count, like_count, screenshot)` 只需首屏
`open_link + ui_controls + screenshot + ocr`，不会为了评论/点赞额外滚动。

队列安全规则：

| 规则 | 目的 |
| --- | --- |
| `attempts < max_attempts` 才能启动采集 | 超限旧任务不再卡住整轮 worker |
| 任务间隔由 `READ_COUNT_POST_DELAY_MIN/MAX`、`DETAIL_POST_DELAY_MIN/MAX` 等控制 | 降低 App 风控和页面未渲染风险 |
| 截图必须上传后再写回 | 避免把本地路径写进腾讯文档 |
| 回填按 URL 重新定位当前行 | 避免人工插行、删行后写错位置 |
| URL 重复时只写第一个，后续标记重复 | 避免同一个链接多行被误写 |

## 4. KOL / 主页链路

适合主页型业务：一行一个主页链接。

```text
kol_daily_db_pipeline
  -> 根据 kol_base_profiles 初始化当天 kol_daily_metrics
  -> 同步 T-1 到 T-5 理财通阅读数
  -> 为当天主页采集生成 profile_metric_sources
  -> 根据 profile_metric_sources 打开主页
  -> 采集粉丝数
  -> 如果需要，进入精确粉丝页
  -> 写入 profile_metric_runs
  -> 汇总写入 kol_daily_metrics
```

默认动作模板：

| action_profile_key | App | 说明 |
| --- | --- | --- |
| `alipay_profile_daily_metrics_v1` | 支付宝 | 粉丝数精确化，最近 3 条帖子阅读数取最大 |
| `antfortune_profile_daily_metrics_v1` | 蚂蚁财富 | 粉丝数精确化，最近 3 条帖子阅读数取最大 |
| `tenpay_profile_daily_metrics_v1` | 理财通 | App 重启清状态，UI/OCR 采集，三列计数器识别，粉丝详情页精确化，账号锚点校验 |
| `unknown_profile_daily_metrics_v1` | 兜底 | 基础主页采集 |

## 5. KOL 结算表帖子指标链路

适合结算表业务：从 `crawler_app.kol_business_settlements` 读取帖子链接，采集后写回同一张表。

```text
kol_business_settlements
  -> SELECT settlement_date, post_url
  -> task_submissions(task_type=kol_settlement_post_metrics)
  -> ADB 打开 post_url
  -> app_type + metric 合并动作
  -> task_executions / field_capture_observations
  -> UPDATE kol_business_settlements
```

业务唯一键：

```text
settlement_date + normalized_post_url
```

提交任务时，`dedupe_key` 也按这个业务唯一键生成：

```text
kol_business_settlements + settlement_date + normalized_post_url + kol_settlement_post_metrics
```

因此每天重复跑 submit 时：

- 已成功且指标齐全的行不会再次提交。
- 同一天同一帖子不会生成重复任务；`sharefm`、`lctsessionkey` 等分享参数不同但 `subject_id` 相同，也按同一帖子处理。
- 失败任务可以被下次 submit 重新激活，继续补跑。
- 同一帖子对应多条结算记录时，writeback 会把同一份采集结果补写到所有缺失或占位的结算行。

以下值视为缺失，会触发 submit 或允许 writeback 覆盖：

```text
机器识别、识别失败、N、NULL、-、--
```

写回字段：

| metric | 回写列 |
| --- | --- |
| `account_name` | `ip_name` |
| `article_title` | `article_title` |
| `comment_count` | `comment_count` |
| `like_count` | `like_count` |
| `screenshot` | `screenshot_url` |

常用命令：

```powershell
.\scripts\run.ps1 -Task kol-settlement-metrics-submit -ReportDate 2026-07-02 -Limit 1
.\scripts\run.ps1 -Task kol-settlement-metrics-crawl -Limit 1
.\scripts\run.ps1 -Task kol-settlement-metrics-writeback -Limit 1
```

一体化执行：

```powershell
.\scripts\run.ps1 -Task kol-settlement-metrics -ReportDate 2026-07-02 -Limit 1
```

## 6. KOL 每日数据库主链路

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

## 7. 常驻运行

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

## 8. 排查顺序

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
```

5. 看数据库状态：

```text
document 链路：
  submit_runs
  task_submissions
  task_executions
  writeback_plans
  field_capture_observations
  derived_records

KOL 数据库主链路：
  kol_base_profiles
  kol_daily_metrics
  profile_metric_sources
  profile_metric_runs
  profile_metric_sources
  profile_metric_runs
```

## 9. 截图下载链接

KOL 结算表帖子指标链路会把 `screenshot_url` 写成可下载链接，链接对应
`apps/finance_crawler/captures` 下的截图文件。

启动下载服务：

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
