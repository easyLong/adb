# 支付宝/蚂蚁财富爬虫应用

位置：`apps/alipay_crawler`

## 目标

从腾讯文档读取支付宝或蚂蚁财富帖子链接，按发帖时间和任务状态自动执行：

1. 初检帖子是否存在，并把发帖账号或不存在标记写回文档。
2. 批量抓取阅读数、评论数和首屏截图。
3. 将结果写回腾讯文档，并把截图上传为腾讯文档图片资源。
4. 将任务状态、采集结果、截图路径和错误信息落到 MySQL。
5. 发生设备断连、写回失败、任务异常等问题时记录告警。

## 数据流

```text
Tencent Docs
  |
  v
integrations/qq_docs.py
  |
  +--> utils/link_source.py -> source_app=alipay/antfortune/unknown
  |
  v
storage/db.py
  |
  +--> jobs/checker.py -> alipay/crawler.py -> L 列账号或 N
  |
  +--> jobs/batch.py   -> alipay/crawler.py -> O/P/Q/R 列阅读、评论、状态、截图
  |
  v
services/report.py
```

## 腾讯文档列约定

默认测试文档：

```text
https://docs.qq.com/sheet/DY1hCSG96TkVySmp1?tab=BB08J2
```

默认列：

| 列 | 下标 | 含义 |
| --- | ---: | --- |
| J | 9 | 发帖时间 |
| L | 11 | 发帖账号 / 初检结果 |
| N | 13 | 帖子链接 |
| O | 14 | 阅读数 |
| P | 15 | 评论数 |
| Q | 16 | 批处理状态 |
| R | 17 | 首屏截图 |

列下标均为 0-based，可通过 `TENCENT_DOC_COL_*` 环境变量覆盖。

## 任务说明

### fetch

职责：

- 读取腾讯文档。
- 按文档顺序筛选超过 `POST_ELIGIBLE_HOURS` 的帖子。
- 识别链接来源并写入 `posts.source_app`。
- 新增或更新 MySQL `posts` 表。

测试时可设置 `FETCH_LIMIT=10`；生产全量可设置为 `0`。

### check

职责：

- 通过 ADB 打开帖子链接。
- 判断帖子是否存在。
- 存在时把账号写回 L 列。
- 不存在时把 L 列写为 `N` 并标黄。
- 技术异常不写回文档，等待后续重试。

任务开始前会检查 ADB 设备状态。设备断开、未授权、离线或多设备未指定 `DEVICE_SERIAL` 时，本轮任务会中止并告警，不会把帖子误标为失败。

### batch

职责：

- 只处理初检成功的帖子。
- 默认先抓首屏；阅读数或评论数缺失时继续滚动，最多 `BATCH_MAX_CAPTURE_PAGES` 屏。
- 通过控件树和 RapidOCR 解析阅读数、评论数。
- 写回 O/P/Q 三列，并把首屏截图上传后插入 R 列。
- 写回前用帖子 URL 重新校验当前文档行，避免行号漂移导致写错。

截图写回优先使用腾讯文档图片上传接口。上传或插入失败时会降级写入路径或公开链接，便于排障。

### report

职责：

- 按日期汇总任务结果。
- 输出到 `apps/alipay_crawler/reports/`。

### supervisor

职责：

- 看护常驻调度器。
- 调度器异常退出时自动重启。
- 重启和崩溃信息会进入告警日志。

生产常驻建议使用：

```powershell
.\scripts\run.ps1 -Task supervisor
```

## OCR

OCR 只使用 `rapidocr-onnxruntime`，不依赖系统级 OCR 程序。默认启用：

```powershell
$env:BATCH_ENABLE_OCR = "true"
$env:OCR_MIN_CONFIDENCE = "30"
```

OCR 输出会保存到采集目录下的 `ocr_records.jsonl`。控件树输出保存到 `ui_records.jsonl`。

## 深链分流

当前在 `fetch` 入库时识别帖子来源：

| 来源 | 典型链接 |
| --- | --- |
| `alipay` | `ur.alipay.com`、`alipays://...`、`alipay://...` |
| `antfortune` | `think.klv5qu.com`、`afwealth://...` |
| `unknown` | 暂未识别的其他分享链接 |

实际打开前会解析或改写 deep link：

- 支付宝链路优先解析成 `alipays://...`。
- 蚂蚁财富分享链接改写成 `afwealth://platformapi/startapp?...`。

## 关键模块

| 模块 | 职责 |
| --- | --- |
| `app.py` | 调度入口，支持单任务和 supervisor |
| `config.py` | 环境变量和默认配置 |
| `integrations/qq_docs.py` | 腾讯文档读取、校验、写回、截图上传 |
| `jobs/checker.py` | 初检任务 |
| `jobs/batch.py` | 批量采集任务 |
| `alipay/capture_engine.py` | deep link、ADB、截图、控件树、OCR |
| `alipay/crawler.py` | 页面状态、账号、阅读数和评论数解析 |
| `services/alerts.py` | 告警 |
| `services/report.py` | 报告 |
| `storage/db.py` | MySQL 表初始化和读写 |
| `utils/device_health.py` | ADB 设备健康检查 |
| `utils/rate_limiter.py` | 运行窗口、耗时预算和节流 |
| `utils/url_resolver.py` | 批量 deep link 解析 |

## 常用命令

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task batch
.\scripts\run.ps1 -Task report
.\scripts\run.ps1 -Task scheduler
.\scripts\run.ps1 -Task supervisor
```

直接运行：

```powershell
python -m apps.alipay_crawler.app --once fetch
python -m apps.alipay_crawler.app --once check
python -m apps.alipay_crawler.app --once batch
python -m apps.alipay_crawler.app --supervise
```
