# 架构说明

本项目正在从“脚本直接读写在线文档”演进为“数据库驱动任务，在线文档只是适配器”的架构。

## 分层

| 层级 | 位置 | 职责 |
| --- | --- | --- |
| 入口层 | `app.py`, `scripts/run.ps1` | CLI、scheduler、supervisor、一次性任务 |
| 配置层 | `config.py`, `services/runtime_config.py` | 默认配置、环境变量、MySQL 运行时配置 |
| 工作流层 | `workflows/` | 业务流程编排，例如详情、文章、大 V、K->M 阅读数 |
| 数据源层 | `sources/` | 腾讯文档、Excel 等来源解析 |
| 数据中心层 | `storage/` | MySQL 任务、目标、结果、写回状态 |
| 手机执行层 | `mobile/` | ADB、uiautomator2、截图、XML、OCR、滑动、页面状态 |
| App 适配层 | `crawlers/` | 支付宝、蚂蚁财富、理财通链接识别和专属解析 |
| 外部集成层 | `integrations/` | 腾讯文档 OpenAPI 等第三方 API |
| 写回层 | `sinks/`, `services/writeback.py` | 标准任务写回腾讯文档或 Excel |

## 关键模块

```text
apps/finance_crawler/app.py
  CLI 入口、scheduler 注册、supervisor 子进程守护。

apps/finance_crawler/workflows/profile_metrics.py
  大 V 主页粉丝数、增粉数、每日行生成、在线文档写回。

apps/finance_crawler/workflows/profile_post_reads.py
  打开大 V 主页，识别指定日期帖子，点击详情页抓阅读数。

apps/finance_crawler/workflows/article_details.py
  需求 1 文章详情，抓文章标题、截图、评论数、点赞数。

apps/finance_crawler/workflows/docs_link_reads.py
  读取腾讯文档 K 列链接，抓详情页阅读数，写回 M 列。

apps/finance_crawler/mobile/
  设备连接、打开链接、截图、XML、OCR、滚动、状态识别。

apps/finance_crawler/storage/
  通用任务框架表、大 V 统计表、文章详情表。
```

## App 适配边界

`mobile/` 只负责通用手机操作，不承载具体业务。

```text
mobile/device_session.py   设备连接、session、打开 URL、重置连接
mobile/capture_engine.py   截图、UI 节点、OCR、滑动、页面 ready 检查
mobile/crawler.py          通用详情采集包装
mobile/parsers.py          通用文本解析器
```

App 差异放在 `crawlers/`：

```text
crawlers/alipay.py         支付宝链接和页面差异
crawlers/antfortune.py     蚂蚁财富 deep link 和页面差异
crawlers/tenpay.py         理财通帖子、调仓详情等页面差异
crawlers/registry.py       链接识别和 App adapter 注册
```

## 数据库中心化

大 V 和文章详情已经按数据库中心化实现：

```text
腾讯文档行
  -> source 表保存目标和定位信息
  -> run 表保存采集结果
  -> writeback 表保存写回状态
  -> 腾讯文档只按 locator 更新单元格
```

这样可以做到：

- 文档行可以迁移或追加，任务身份仍然稳定。
- 同一主页可以每天生成一条统计任务。
- 增粉数可以基于数据库中的前一日结果计算。
- 写回失败可以单独重放。

## 当前主要业务流

### 通用帖子详情

```text
tencent_docs_fetch
  -> initial_check
  -> detail_crawl
  -> crawl_results
  -> writeback
```

### 大 V 粉丝数

```text
profile-daily-rows
  -> 从模板范围生成当天行
  -> profile-sync 入库
  -> profile-crawl 手机抓粉丝数
  -> profile-writeback 写 E/F 列
```

### 主页帖子阅读数

```text
profile-post-reads
  -> 打开主页
  -> 查找指定日期帖子
  -> 同一天最多取配置数量
  -> 点击详情页抓阅读数
  -> 写入 profile_metric_runs.read_count
```

### 需求 1 文章详情

```text
article-sync
  -> article-crawl
  -> article-writeback
```

### K 列链接阅读数

```text
doc-link-reads
  -> 读取 K 列链接
  -> 手机打开详情页
  -> UI/OCR 提取阅读数
  -> 写回 M 列
```

## 扩展原则

- 新增 App：在 `crawlers/` 添加 profile/adapter，再到 `registry.py` 注册。
- 新增长期业务：优先新增 `workflow + storage`，让腾讯文档只做适配器。
- 新增临时文档任务：可以先做 workflow，稳定后再沉淀到 storage。
- 新增写回目标：优先通过 `sinks/` 或 `integrations/` 封装。
