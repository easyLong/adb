# 项目流程

本文说明当前项目如何运行、数据如何流转，以及日常应该如何触发任务。

## 1. 总体流程

```text
数据源
  -> 运行时配置
  -> workflow 解析
  -> MySQL 保存任务和目标
  -> ADB 驱动手机采集
  -> MySQL 保存结果
  -> 写回腾讯文档 / Excel
```

当前数据源：

| 数据源 | 用途 |
| --- | --- |
| 腾讯文档 | 主要在线数据源和写回目标 |
| 本地 Excel | 临时批量详情采集 |
| 单条链接 | 调试 App 打开和解析 |

当前支持 App：

| app_type | App | 主要字段 |
| --- | --- | --- |
| `alipay` | 支付宝 | 帖子、主页粉丝数 |
| `antfortune` | 蚂蚁财富 | 帖子、主页粉丝数 |
| `tenpay` | 财付通/腾讯理财通 | 帖子、文章、主页粉丝数、调仓明细 |

## 2. Scheduler

启动：

```powershell
.\scripts\run.ps1 -Task scheduler
```

带守护：

```powershell
.\scripts\run.ps1 -Task supervisor
```

当前 scheduler 会注册：

| 任务 | 频率 |
| --- | --- |
| `fetch_docs` | `FETCH_INTERVAL_MINUTES` |
| `check` | `CHECK_INTERVAL_MINUTES`，如果开启 |
| `detail_crawl_due` | `DETAIL_INTERVAL_MINUTES` |
| `report` | 每天 `REPORT_TIME` |
| `profile_daily_rows` | 每天 `PROFILE_METRICS_DAILY_PREPARE_TIME`，为空则关闭 |
| `profile_metrics` | `PROFILE_METRICS_INTERVAL_MINUTES`，大于 0 且配置了文档才开启 |
| `heartbeat` | `HEARTBEAT_INTERVAL_MINUTES` |

scheduler 启动时也会执行一次幂等检查：

- 同步腾讯文档候选链接。
- 扫描到期详情任务。
- 如果开启大 V 调度，先确保当天行存在，再跑一次 `profile_metrics`。

## 3. 大 V 每日粉丝数

### 当前策略

模板范围：

```text
PROFILE_METRICS_TEMPLATE_RANGE=A2:H126
```

读取范围：

```text
PROFILE_METRICS_READ_RANGE=A1:H2000
```

每日准备时间：

```text
PROFILE_METRICS_DAILY_PREPARE_TIME=00:10
```

流程：

```text
profile-daily-rows
  -> 读取模板 A2:H126
  -> 检查当天已有主页链接
  -> 只追加缺失链接
  -> 新行日期写当天，E/F/G 清空
  -> profile-sync 入库
  -> profile-crawl 抓粉丝数
  -> profile-writeback 写回 E/F
```

幂等规则：

- 同一天同一个主页链接只会生成一次。
- 重复执行 `profile-daily-rows` 不会重复追加。
- 如果当天只存在理财通，模板里蚂蚁还不存在，会只补蚂蚁。

手动生成某天：

```powershell
.\scripts\run.ps1 -Task profile-daily-rows -ReportDate 2026-06-04
```

完整跑一次大 V 粉丝数：

```powershell
.\scripts\run.ps1 -Task profile-metrics
```

拆分执行：

```powershell
.\scripts\run.ps1 -Task profile-sync
.\scripts\run.ps1 -Task profile-crawl
.\scripts\run.ps1 -Task profile-writeback
```

### 增粉数

`profile_metric_runs.growth_count` 由数据库计算：

```text
今日粉丝数 - 前一日同一 target 的粉丝数
```

如果前一日没有成功数据，增粉数写 `0`。

## 4. 大 V 主页帖子阅读数

入口：

```powershell
.\scripts\run.ps1 -Task profile-post-reads -ReportDate 2026-06-04
```

流程：

```text
读取指定日期的大 V 主页目标
  -> 打开主页
  -> 根据时间文本识别对应日期帖子
  -> 点击详情页
  -> 提取阅读数
  -> 写入 profile_metric_runs.read_count
```

相关配置：

```text
PROFILE_POST_READ_MAX_SCROLLS
PROFILE_POST_READ_MAX_POSTS
PROFILE_POST_READ_CRAWL_LIMIT
```

## 5. 需求 1 文章详情

入口：

```powershell
.\scripts\run.ps1 -Task article-details
```

拆分：

```powershell
.\scripts\run.ps1 -Task article-sync
.\scripts\run.ps1 -Task article-crawl
.\scripts\run.ps1 -Task article-writeback
```

字段：

```text
文章标题
截图
评论数
点赞数
```

说明：页面本身不提供阅读数时，不抓阅读数。

## 6. K 列链接阅读数写回 M 列

入口：

```powershell
.\scripts\run.ps1 -Task doc-link-reads `
  -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>" `
  -ReportDate 0602
```

默认列：

```text
DOC_LINK_READS_LINK_COL=10    # K 列，0-based
DOC_LINK_READS_READ_COL=12    # M 列，0-based
DOC_LINK_READS_ONLY_EMPTY=True
```

Tencent Docs column-number settings are fallbacks. Runtime prefers row-1 titles when reading and writing sheet columns.

特性：

- 只处理 M 列为空的链接。
- 支持按 `0602` 或 `2026-06-02` 选择 sheet。
- 支持详情页空白、网络错误、重试按钮、force-stop 后重开。
- 写回采用批量分段，避免长任务最后一次失败导致全部丢失。

## 7. 通用详情采集

```powershell
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
```

`fetch` 从腾讯文档导入候选链接，生成初检和详情任务。`check` 判断帖子是否存在并提取账号。`detail` 到期后采集详情并写回。

## 8. 单链接调试

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

用于验证手机、App、短链、页面解析是否正常。

## 9. 捕获文件

采集材料保存在：

```text
apps/finance_crawler/captures/
apps/finance_crawler/screenshots/
apps/finance_crawler/logs/
```

常见文件：

```text
page_000.png
page_000.xml
ui_records.jsonl
ocr_records.jsonl
```
