# KOL 每日数据库主链路

任务名：`kol-daily-db-pipeline`

这条链路以 MySQL 为主结果表，只更新数据库。腾讯文档只作为外部阅读数来源，不作为主结果载体。

当前 KOL / 主页型任务只保留两个任务入口：

| Task | 作用 |
| --- | --- |
| `kol-daily-db-pipeline` | 串行执行每日主链路 |
| `kol-tenpay-external-reads` | 单独补跑理财通外部阅读数；正常由主链路串起来 |

## 串行顺序

```text
每日初始化
  -> 阅读数任务
  -> 粉丝数 / 增粉数任务
```

### 1. 每日初始化

从 `kol_base_profiles` 生成当天 `kol_daily_metrics` 空行。

唯一键：

```text
metric_date + kol_name + platform
```

初始化只补行，不覆盖已有的粉丝数、增粉数、阅读数。

### 2. 阅读数任务

读取 7 个理财通外部腾讯文档，默认处理最近 5 个已结束日期：

```text
T-1、T-2、T-3、T-4、T-5
```

匹配：

```text
日期 + 账号名称 + 平台=理财通
```

只更新：

```text
kol_daily_metrics.read_count
```

不写回目标腾讯文档。

### 3. 粉丝数 / 增粉数任务

从 `kol_daily_metrics JOIN kol_base_profiles` 生成主页采集任务。

采集成功后写入：

```text
profile_metric_runs
kol_daily_metrics.fans_count
kol_daily_metrics.growth_count
```

不写回目标腾讯文档。

## 手动运行

跑今天的串行主链路：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline
```

跑指定日期的初始化和粉丝任务，阅读数会处理该日期前 5 天：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline -ReportDate 2026-06-22
```

快捷脚本：

```powershell
.\scripts\kol-daily-db-pipeline.ps1
.\scripts\kol-daily-db-pipeline.ps1 -ReportDate 2026-06-22
```

只查看命令，不实际执行：

```powershell
.\scripts\kol-daily-db-pipeline.ps1 -ReportDate 2026-06-22 -DryRun
```

## 常驻运行

启动常驻 worker 后，`profile` 调度角色会注册：

```text
kol_daily_db_pipeline daily at KOL_DAILY_CRAWL_TIME
```

默认时间来自：

```text
KOL_DAILY_CRAWL_TIME
```

阅读数回看天数来自：

```text
KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS
```

## 账号名称一致性备注

主页 ADB 采集粉丝数时，会同时用页面 UI/OCR 文本识别账号名称，并和
`kol_base_profiles.kol_name` / `profile_targets.account_name` 中的配置名称做校验。

如果页面名称和配置名称不一致，不覆盖基础资料，只在每日主结果表写备注：

```text
kol_daily_metrics.remark = 账号名称不一致：配置「旧名」，页面「新名」
```

详细证据同时保存在：

```text
profile_metric_runs.metrics_json.fans.detected_account_name
profile_metric_runs.metrics_json.fans.expected_account_name
profile_metric_runs.metrics_json.fans.nickname_mismatch
kol_daily_metrics.source_payload_json
```
