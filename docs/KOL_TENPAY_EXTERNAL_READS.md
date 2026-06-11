# KOL 理财通外部阅读数回填任务

任务名：`kol-tenpay-external-reads`

这个任务不走 ADB，只通过腾讯文档 OpenAPI 做文档到文档的回填。

## 数据流

```text
7 个理财通账号数据源表
  -> 读取 日期 / 账号名称 / T-1日文章阅读数
  -> 目标表按 日期 + 大V名称 + 平台=理财通 匹配
  -> 写回目标表 I 列 阅读数
```

目标表默认：

```text
https://docs.qq.com/sheet/DYnhxS2VHZHBqR0V5?tab=wpvy0d
```

默认每天 `06:00` 由 `profile` worker 自动执行。

## 手动运行

回填当前目标表内所有能匹配到的日期：

```powershell
.\scripts\run.ps1 -Task kol-tenpay-external-reads
```

只回填某一天：

```powershell
.\scripts\run.ps1 -Task kol-tenpay-external-reads -ReportDate 2026-06-10
```

临时指定目标表：

```powershell
.\scripts\run.ps1 -Task kol-tenpay-external-reads `
  -TencentDocUrl "https://docs.qq.com/sheet/DYnhxS2VHZHBqR0V5?tab=wpvy0d"
```

## 常驻运行

启动队列隔离 worker：

```powershell
.\scripts\run.ps1 -Task workers-start
```

其中 `profile` worker 会注册：

```text
kol_tenpay_external_reads daily at 06:00
```

## 可配置项

这些配置可以通过 `app_config` 或环境变量覆盖：

```text
KOL_TENPAY_EXTERNAL_READS_SOURCE_DOC_URLS
KOL_TENPAY_EXTERNAL_READS_TARGET_DOC_URL
KOL_TENPAY_EXTERNAL_READS_SOURCE_RANGE
KOL_TENPAY_EXTERNAL_READS_TARGET_RANGE
KOL_TENPAY_EXTERNAL_READS_TIME
KOL_TENPAY_EXTERNAL_READS_TARGET_PLATFORM
KOL_TENPAY_EXTERNAL_READS_WRITEBACK_FONT_SIZE
```

更新执行时间示例：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet KOL_TENPAY_EXTERNAL_READS_TIME=06:00
```

更新源表列表时，多个链接用英文逗号分隔。
