# 业务流程说明

更完整的项目全景见 [PROJECT_FLOW.md](PROJECT_FLOW.md)。本文聚焦业务流程、采集字段、MySQL 表和关键配置。

## 采集目标

| 字段 | 来源 |
| --- | --- |
| 发帖账号 | App 页面 UI/XML/OCR |
| 帖子正文 | App 页面 |
| 阅读数 | App 页面 |
| 评论数 | App 页面 |
| 首屏截图 | ADB 截图 |
| 财付通买入基金名称和金额 | 财付通调仓明细页 |
| 状态和错误 | workflow、任务执行、写回结果 |

## 支持链路

| app_type | 典型链接 | 打开方式 | 专属能力 |
| --- | --- | --- | --- |
| `alipay` | `ur.alipay.com`, `alipays://` | 短链解析或 deep link 打开支付宝 | 账号、正文、阅读、评论 |
| `antfortune` | `think.klv5qu.com`, `afwealth://` | 分享链接改写为 `afwealth://platformapi/startapp?...` | 账号、正文、阅读、评论 |
| `tenpay` | `www.tencentwm.com`, `tenpay://` | 指定 `TENPAY_PACKAGE` 打开 | 进入调仓明细，解析买入基金和金额 |

## 标准在线流程

```text
fetch
  -> 读取腾讯文档候选链接
  -> 提交 initial_check / detail_crawl

check
  -> 执行 initial_check
  -> 判断帖子存在性
  -> 提取账号
  -> 写回账号或 N

detail
  -> 执行 detail_crawl
  -> 打开 App
  -> 截图、XML、OCR、滑动
  -> 解析正文、阅读、评论、截图和 App 专属指标
  -> 写回结果
```

## 本地 Excel 流程

```text
excel-detail
  -> 读取本地 Excel 链接
  -> 直接打开 App 详情页采集
  -> 写回输出 Excel
  -> 记录任务提交和执行结果
```

## 任务调度

| task_type | 生成规则 | 默认执行时间 |
| --- | --- | --- |
| `initial_check` | 导入时未晚于次日详情采集窗口 | `source_time + INITIAL_CHECK_DELAY_HOURS` |
| `detail_crawl` | 每条有效链接都会生成 | `source_time` 次日 `DETAIL_TIME` |

## MySQL 表

| 表 | 作用 |
| --- | --- |
| `crawl_sources` | 数据源注册，例如腾讯文档、Excel |
| `crawler_apps` | App 注册，例如 alipay、antfortune、tenpay |
| `crawl_task_submissions` | 任务提交和总体状态 |
| `crawl_task_executions` | 每次执行尝试、结果摘要、错误和写回状态 |
| `crawl_results` | 标准化采集结果，App 专属数据放在 `metrics_json` |
| `crawl_writebacks` | 写回目标、定位、状态和错误 |
| `crawl_jobs` | 一次 job 运行记录 |
| `task_log` | 调度日志 |

## 腾讯文档列

列索引为 0-based，可通过环境变量覆盖。

| 默认列 | 索引 | 配置 | 含义 |
| --- | --- | --- | --- |
| J | 9 | `TENCENT_DOC_COL_POST_TIME` | 发帖时间 |
| L | 11 | `TENCENT_DOC_COL_ACCOUNT_NAME` | 账号/初检结果 |
| N | 13 | `TENCENT_DOC_COL_URL` | 帖子链接 |
| O | 14 | `TENCENT_DOC_COL_READ_COUNT` | 阅读数 |
| P | 15 | `TENCENT_DOC_COL_COMMENT_COUNT` | 评论数 |
| Q | 16 | `TENCENT_DOC_COL_DETAIL_STATUS` | 详情采集状态 |
| R | 17 | `TENCENT_DOC_COL_SCREENSHOT` | 首屏截图 |

写回前会用 URL 校验目标行号。同一 URL 出现多行时会跳过写回，避免写错行。

## 常用配置

| 配置 | 说明 |
| --- | --- |
| `FETCH_INTERVAL_MINUTES` | 拉取数据源间隔 |
| `CHECK_INTERVAL_MINUTES` | 初检间隔 |
| `INITIAL_CHECK_DELAY_HOURS` | 初检相对 `source_time` 的延迟小时数 |
| `DETAIL_TIME` | 每日详情采集时间 |
| `FETCH_LIMIT` | 单次 fetch 数量 |
| `CHECK_LIMIT` | 单次初检任务数量 |
| `DETAIL_LIMIT` | 单次详情采集数量，0 表示不限制 |
| `DETAIL_MAX_RETRIES` | 详情任务最大执行次数 |
| `DETAIL_MAX_CAPTURE_PAGES` | 详情采集最多主帖截图页数 |
| `DETAIL_ENABLE_OCR` | 是否启用 OCR |
| `DETAIL_REQUIRES_CHECK_SUCCESS` | 详情采集是否等待初检成功 |
| `MAX_RECORDS_PER_RUN` | 单次任务最多处理记录数，0 表示不限制 |
| `EXCEL_DETAIL_INPUT_PATH` | 本地 Excel 输入文件 |
| `EXCEL_DETAIL_OUTPUT_PATH` | 本地 Excel 输出文件 |
| `WRITEBACK_SINK_TYPE` | 写回目标，支持 `tencent_docs`、`excel` |
| `TENPAY_PACKAGE` | 财付通/腾讯理财通包名 |
