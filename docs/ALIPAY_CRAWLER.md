# 支付宝/蚂蚁财富爬虫应用

位置：`apps/alipay_crawler`

## 目标

从腾讯文档读取支付宝或蚂蚁财富帖子链接，按发帖时间触发任务：

1. 初检帖子是否存在。
2. 次日批量抓取阅读数和评论数。
3. 把结果写回腾讯文档。
4. 把任务状态和抓取结果落到 MySQL。

## 数据流

```text
腾讯文档
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
  +--> jobs/batch.py   -> alipay/crawler.py -> O/P/Q 列阅读、评论、状态
  |
  v
services/report.py
```

## 腾讯文档列约定

当前测试文档：

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

## 来源识别

当前会在 `fetch` 入库时识别帖子来源，并写入 `posts.source_app`：

| 来源 | 典型链接 |
| --- | --- |
| `alipay` | `ur.alipay.com`、`alipays://...`、`alipay://...` |
| `antfortune` | `think.klv5qu.com`、`afwealth://...` |
| `unknown` | 暂未识别的其他分享链接 |

识别逻辑位置：`apps/alipay_crawler/utils/link_source.py`

## post_time 生成规则

`post_time` 由 sheet 名称和 J 列拼接：

1. 从 sheet 名称提取日期。
   - `0522-精选-制造` -> `2026-05-22`
   - 年份默认 `2026`
2. 从 J 列提取最早时间。
   - `10:30-11:30` -> `10:30:00`
   - `10:30` -> `10:30:00`
3. 拼接为最终发帖时间：
   - `2026-05-22 10:30:00`

## 深链分流

两条链路在任务层共用 `open_url()`、`check_post_exists_and_account()`、`scrape_post_content()`，
但在唤起前会先做 deep link 分流：

- 支付宝链路：优先把外部分享链接解析成 `alipays://...`
- 蚂蚁财富链路：把 `think.klv5qu.com` 分享链接改写成 `afwealth://platformapi/startapp?...`

实际处理位置：`apps/alipay_crawler/alipay/capture_engine.py`

## 任务拆分

### fetch

默认每 5 分钟执行一次。

职责：

- 读取腾讯文档。
- 按文档顺序筛选超过 2 小时的帖子。
- 按链接域名/scheme 区分 `alipay` 和 `antfortune`。
- 写入或更新 MySQL `posts` 表。

测试时 `FETCH_LIMIT=10`，生产全量可设置为 `0`。

### check

默认每 10 分钟执行一次。

触发条件：

```text
当前时间 > post_time + 2小时
```

职责：

- 用 ADB 打开对应来源的链接。
- 支付宝链接走支付宝链路，`think.klv5qu.com` / `afwealth://` 走蚂蚁财富链路。
- 判断帖子是否存在。
- 存在：L 列写发帖账号。
- 不存在：L 列写 `N`，并把 L 单元格标黄。
- 技术异常：不写回文档，等待下次重试。

### batch

默认每天 10:00 执行。

职责：

- 只处理初检成功的帖子。
- 抓取阅读数和评论数。
- 写回 O/P/Q 列。
- 更新 MySQL 抓取结果。
- 结果会保留 `source_app`，便于后续按链路排查和统计。

`BATCH_LIMIT=0` 表示全量。

### report

默认每天 11:30 执行。

职责：

- 按日期汇总任务结果。
- 输出到 `apps/alipay_crawler/reports/`。

## 模块说明

| 模块 | 职责 |
| --- | --- |
| `app.py` | 调度入口 |
| `config.py` | 配置 |
| `integrations/qq_docs.py` | 腾讯文档读取、筛选、写回 |
| `utils/link_source.py` | 链接来源识别 |
| `storage/db.py` | MySQL 表初始化和任务读写 |
| `jobs/checker.py` | 初检 |
| `jobs/batch.py` | 次日批量抓取 |
| `alipay/capture_engine.py` | ADB/uiautomator2 底层能力 |
| `alipay/crawler.py` | 支付宝/蚂蚁财富页面状态、账号、阅读数、评论数解析 |
| `services/report.py` | 报告 |

## 运行命令

```powershell
.\scripts\run.ps1 -App alipay_crawler -Task db
.\scripts\run.ps1 -App alipay_crawler -Task fetch
.\scripts\run.ps1 -App alipay_crawler -Task check
.\scripts\run.ps1 -App alipay_crawler -Task batch
.\scripts\run.ps1 -App alipay_crawler -Task report
.\scripts\run.ps1 -App alipay_crawler -Task scheduler
```

直接运行：

```powershell
python -m apps.alipay_crawler.app --once fetch
python -m apps.alipay_crawler.app --once check
python -m apps.alipay_crawler.app --once batch
python -m apps.alipay_crawler.app
```
