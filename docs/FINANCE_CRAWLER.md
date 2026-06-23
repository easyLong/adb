# 业务说明

## 1. 项目目标

本项目用于金融社区内容和大 V 指标采集，当前覆盖：

- 支付宝 / 蚂蚁财富帖子详情。
- 财付通 / 腾讯理财通帖子详情。
- 大 V 主页粉丝数、增粉数。
- 大 V 主页指定日期帖子阅读数。
- 需求 1 文章标题、截图、评论、点赞。
- 腾讯文档指定链接列阅读数回填。

## 2. 通用帖子详情

主要字段：

| 字段 | 说明 |
| --- | --- |
| 账号 | 从页面 UI/OCR 识别 |
| 正文 | 当前帖子正文 |
| 阅读数 | 页面提供时抓取 |
| 评论数 | 页面提供时抓取 |
| 截图 | 保存到本地，按配置写回或上传 |
| App 专属数据 | 例如理财通调仓明细 |

流程：

```text
fetch -> check -> detail -> writeback
```

## 3. 大 V 主页统计

本节为历史链路说明。当前 KOL / 主页型任务主结果只看数据库表 `kol_daily_metrics`，日常入口见 [KOL_DAILY_DB_PIPELINE.md](KOL_DAILY_DB_PIPELINE.md)。

旧文档列约定：

| 列 | 含义 |
| --- | --- |
| A | 日期 |
| B | 大 V 名称 |
| C | 平台 |
| D | 主页链接 |
| E | 粉丝数 |
| F | 增粉数 |
| G | 阅读数 |
| H | 分组 |

模板范围：

```text
A2:H126
```

旧的 profile 拆分命令入口已移除。当前每日流程由 `kol-daily-db-pipeline` 串行完成：初始化当天数据、同步理财通阅读数、采集主页粉丝数和增粉数。

增粉数规则：

```text
如果前一日有成功粉丝数：今日粉丝数 - 前一日粉丝数
否则：0
```

## 4. 大 V 主页帖子阅读数

用于按日期统计主页动态帖子阅读数。

规则：

- 打开主页。
- 识别目标日期的动态。
- 同一天帖子数量由 `PROFILE_POST_READ_MAX_POSTS` 控制。
- 点击详情页读取阅读数。
- 汇总写入数据库的 `read_count`。

旧的独立主页帖子阅读数入口已移除。当前阅读数由 `kol-tenpay-external-reads` 合并到 `kol_daily_metrics`。

## 5. 需求 1 文章详情

文档列约定由 `article_details.py` 中常量控制：

```text
DATE_COL = 0
IP_COL = 1
PRODUCT_COL = 2
URL_COL = 8
TITLE_COL = 9
SCREENSHOT_COL = 10
READ_COL = 11
COMMENT_COL = 12
LIKE_COL = 13
```

当前写回字段：

- 文章标题
- 截图
- 评论数
- 点赞数

阅读数说明：

页面本身不提供阅读数时，不采集阅读数。

## 6. K 列链接阅读数

适用于按链接逐条打开详情页，然后将阅读数写回 M 列。

默认列：

```text
K 列：链接
M 列：阅读数
```

入口：

```powershell
.\scripts\run.ps1 -Task doc-link-reads -TencentDocUrl "<url>" -ReportDate 0602
```

## 7. 数据库表职责

| 表 | 职责 |
| --- | --- |
| `data_source_links` | 数据源入口，例如腾讯文档 URL |
| `app_config` | 运行时配置 |
| `crawl_sources` | 通用来源记录 |
| `crawl_task_submissions` | 通用任务提交 |
| `crawl_task_executions` | 通用任务执行记录 |
| `crawl_results` | 通用采集结果 |
| `crawl_writebacks` | 通用写回状态 |
| `profile_targets` | 大 V 主页目标 |
| `profile_metric_sources` | 每日大 V 统计来源行 |
| `profile_metric_runs` | 每日大 V 采集结果 |
| `profile_metric_writebacks` | 大 V 写回记录 |
| `article_targets` | 文章目标 |
| `article_detail_sources` | 文章详情来源行 |
| `article_detail_runs` | 文章详情采集结果 |
| `article_detail_writebacks` | 文章详情写回记录 |
| `task_log` | 调度日志 |

## 8. 当前限制

- 手机必须保持登录状态。
- 某些主页会被身份认证页面拦截，状态会记录为 blocked。
- 页面不存在或内容被删除时无法抓取阅读数。
- OCR 可能误识别数字，需要通过测试不断补解析规则。
- 腾讯文档 API 对 range 和 batchUpdate 有限制，长任务需要分批写回。
