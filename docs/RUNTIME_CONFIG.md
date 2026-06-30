# 运行时配置

项目配置分两类：

| 类型 | 位置 | 说明 |
| --- | --- | --- |
| MySQL 连接 | 根目录 `.env` 或系统环境变量 | 只存数据库连接信息 |
| 业务运行配置 | MySQL `app_config` / `data_source_links` | 文档 URL、OpenAPI token、调度时间、worker 间隔、采集策略 |

查看配置：

```powershell
.\scripts\run.ps1 -Task config
```

更新单个配置：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet KEY=VALUE
```

一次更新多个配置：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet KOL_DAILY_CRAWL_TIME=08:00
```

## MySQL

`.env` 示例：

```powershell
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=...
MYSQL_DATABASE=crawler_app
MYSQL_APP_DATABASE=crawler_app
```

当前新版本默认库是 `crawler_app`。

## 腾讯文档 OpenAPI

| Key | 说明 |
| --- | --- |
| `TENCENT_DOC_CLIENT_ID` | OpenAPI Client-Id |
| `TENCENT_DOC_OPEN_ID` | OpenAPI Open-Id |
| `TENCENT_DOC_ACCESS_TOKEN` | Access-Token |
| `TENCENT_DOC_CLIENT_SECRET` | 可选，用于换 token |
| `TENCENT_DOC_TOKEN_URL` | token 地址 |

这些配置放在运行时配置表里，不放在 `.env`。

## 数据源

| Key | 说明 |
| --- | --- |
| `TENCENT_DOC_URL` | 通用文档 URL，旧链路和部分调试命令使用 |
| `ARTICLE_DETAILS_DOC_URL` | 文章详情文档 |
| `EXCEL_DETAIL_INPUT_PATH` | 本地 Excel 输入路径 |
| `SINGLE_TEST_LINK` | 单链接调试 |

普通帖子/链接型在线文档不建议只依赖 `TENCENT_DOC_URL`，应通过 `document_trigger_configs` 配置基础 URL、sheet 选择规则和任务绑定。

## Scheduler 和 Worker

| Key | 当前建议 | 说明 |
| --- | --- | --- |
| `ENABLE_LEGACY_SCHEDULER_JOBS` | `false` | 是否启用旧版 fetch/check/detail/report 周期任务 |
| `SUBMIT_WORKER_INTERVAL_SECONDS` | `300` | document submit worker 唤醒间隔，建议 5 分钟 |
| `V2_CRAWL_WORKER_INTERVAL_SECONDS` | `30` | document crawl worker 唤醒间隔 |
| `V2_WRITEBACK_WORKER_INTERVAL_SECONDS` | `30` | document writeback worker 唤醒间隔 |
| `SCHEDULER_ROLES` | `all` | scheduler 注册哪些角色；可用 `submit,crawl,writeback,profile,wechat,heartbeat` 拆成多个常驻进程 |
| `HEARTBEAT_INTERVAL_MINUTES` | `30` | scheduler 心跳 |
| `TASK_RUNNING_TIMEOUT_MINUTES` | `360` | running 任务超时回收 |
| `DEVICE_POOL_ENABLED` | `true` | 启用设备池和全局设备锁 |
| `DEVICE_LOCK_WAIT_SECONDS` | `3600` | 手机被占用时最多等待多久 |
| `DEVICE_LOCK_POLL_SECONDS` | `5` | 等待锁时多久检查一次 |

每个 document trigger 还有自己的 `scan_interval_seconds`，用于控制同一个在线文档多久真正扫描一次。当前建议 trigger 级别设置为 `600` 秒。

常驻进程每次任务开始前会重新加载运行时配置。

如果 ADB 采集耗时较长，建议用一键队列隔离常驻进程，避免 crawl 阻塞 submit/writeback：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
.\scripts\run.ps1 -Task workers-stop
```

`workers-start` 会启动 5 个隐藏常驻进程：
```text
submit-heartbeat -> SCHEDULER_ROLES=submit,heartbeat
crawl            -> SCHEDULER_ROLES=crawl
writeback        -> SCHEDULER_ROLES=writeback
profile          -> SCHEDULER_ROLES=profile
wechat           -> SCHEDULER_ROLES=wechat
```

PID 和 stdout/stderr 日志在 `apps/finance_crawler/logs/queue_workers/`。

这是队列隔离，不是多线程。`submit` 和 `writeback` 不会被采集任务阻塞；`crawl` 负责 document 采集队列；`profile` 负责 KOL/profile 队列；`wechat` 负责微信群小时级采集和需求识别。

## 全局设备锁

所有需要操作手机屏幕的采集任务都应先获取设备锁。锁按 `adb_serial` 串行化，同一台手机同一时间只允许一个任务操作。

当前覆盖的入口：

```text
v2 initial_check / detail / read_count
legacy check / detail
excel-detail
link-detail
doc-link-reads
article-crawl / article-details
kol-daily-db-pipeline
wechat-groups-capture / wechat-hourly-sync
```

锁状态可通过设备池查看：

```powershell
.\scripts\run.ps1 -Task device-pool-status
```

`running_leases` 中有数据时，说明手机正在被某个采集任务占用。后续任务会等待：

```text
DEVICE_LOCK_WAIT_SECONDS
```

如果等待超时，会失败并保留错误信息；不会并发抢同一台手机。

## 微信群小时级采集

| Key | 当前建议 | 说明 |
| --- | --- | --- |
| `WECHAT_DEVICE_SERIAL` | `APH0219701010623` | 固定用于微信群采集的 ADB 设备 |
| `WECHAT_SYNC_PAGES` | `12` | 每个群每次采集截图页数 |
| `WECHAT_SYNC_OUT_DIR` | `exports/wechat` | 截图输出目录 |
| `WECHAT_SYNC_LIMIT` | `0` | 每次最多跑几个群；0 表示不限制 |
| `WECHAT_SYNC_PARSE_MODE` | `ocr` | 消息解析方式，默认 OCR |
| `WECHAT_SYNC_CONTEXT_SIZE` | `30` | 增量需求识别带入的历史上下文消息数 |
| `WECHAT_SCHEDULER_ENABLED` | `false` | 是否启用 scheduler 自动跑微信群同步 |
| `WECHAT_SCHEDULER_START_TIME` | `08:00` | 工作日开始时间 |
| `WECHAT_SCHEDULER_END_TIME` | `19:00` | 工作日结束时间，包含这一轮 |
| `WECHAT_SCHEDULER_INTERVAL_MINUTES` | `60` | 时间窗内触发间隔 |
| `WECHAT_SCHEDULER_WORKDAYS` | `1,2,3,4,5` | ISO 周几，1=周一，7=周日 |

手动跑完整链路：

```powershell
.\scripts\run.ps1 -Task wechat-hourly-sync
```

启用工作日 08:00~19:00 每小时调度：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet WECHAT_SCHEDULER_ENABLED=true `
  -ConfigSet WECHAT_DEVICE_SERIAL=APH0219701010623

.\scripts\run.ps1 -Task scheduler -SchedulerRoles wechat
```

## KOL 每日数据库主链路

| Key | 默认 | 说明 |
| --- | --- | --- |
| `KOL_DAILY_CRAWL_TIME` | `08:00` | 每天触发 KOL 数据库主链路 |
| `KOL_DAILY_CRAWL_LIMIT` | `0` | 单次主页采集限制；`0` 表示不限制 |
| `KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS` | `5` | 每次主链路同步最近几个已结束日期的理财通阅读数，默认 T-1 到 T-5 |

`KOL_DAILY_CRAWL_TIME` 触发的是数据库主链路：

```text
kol_daily_db_pipeline
  -> ensure kol_daily_metrics rows
  -> sync Tenpay external reads to kol_daily_metrics.read_count
  -> crawl profile metrics to kol_daily_metrics.fans_count / growth_count
```

不是 document trigger，也不是旧的腾讯文档写回链路。

查看结果页面：

```powershell
cd ..\easy-viewer
.\scripts\start_viewer.ps1
```

## Profile 主页动作模板

主页动作模板存在 `profile_action_profiles`。
动作细节和已验证经验见 [ACTION_TEMPLATES.md](ACTION_TEMPLATES.md)。

| action_profile_key | 说明 |
| --- | --- |
| `alipay_profile_daily_metrics_v1` | 支付宝主页粉丝数、最近 3 条帖子阅读数取最大 |
| `antfortune_profile_daily_metrics_v1` | 蚂蚁财富主页粉丝数、最近 3 条帖子阅读数取最大 |
| `tenpay_profile_daily_metrics_v1` | 理财通主页采集，带 App 重启、UI/OCR、三列计数器识别、粉丝详情页精确化和账号锚点校验 |
| `unknown_profile_daily_metrics_v1` | 兜底模板 |

profile trigger 的 `action_profile_key` 可以为空。为空时，系统按每行主页链接识别 `app_type`，自动选择对应 action profile。

## Document 采集动作模板

帖子/链接型动作模板存在 `capture_action_profiles`。

执行时按以下维度选择动作：

```text
app_type + task_type + requested_fields
```

例如：

```text
alipay + detail + account_name,read_count,screenshot
```

每个 App 单独配置，不要把 `alipay,antfortune` 写成一个组合。因为不同 App 的页面结构、等待、点击、OCR 策略可能不同。

## 设备和 App 恢复

| Key | 说明 |
| --- | --- |
| `ADB_PATH` | ADB 路径，默认优先使用 `platform-tools/adb.exe` |
| `DEVICE_SERIAL` | 指定设备序列号，空则自动选择可用设备 |
| `DEVICE_POOL_HOST_ID` | 多机器设备池隔离标识，空则使用电脑名 |
| `DEVICE_FAILURE_COOLDOWN_SECONDS` | 普通采集失败后，该设备/App 短冷却时间 |
| `DEVICE_CHECK_TIMEOUT` | 设备检测超时 |
| `DEVICE_HEALTH_CACHE_SECONDS` | 设备健康检查缓存时间 |
| `DEVICE_AUTO_RECONNECT` | 是否自动重连 |
| `DEVICE_RECONNECT_RETRIES` | 自动重连次数 |
| `APP_OPEN_RECOVERY_RETRIES` | 页面异常时 App 恢复重试次数 |
| `APP_RESTART_WAIT` | force-stop 后等待时间 |
| `PAGE_LOAD_WAIT` | 打开页面后的基础等待 |
| `PAGE_STATUS_READY_TIMEOUT` | 打开帖子后等待页面进入可判断状态的最长时间 |
| `PAGE_STATUS_READY_INTERVAL` | 页面状态轮询间隔 |
| `DETAIL_SCROLL_WAIT` | 滑动后的等待 |
| `READ_COUNT_POST_DELAY_MIN` / `READ_COUNT_POST_DELAY_MAX` | 阅读数任务之间的随机停顿 |
| `DOC_LINK_READS_RETRYABLE_COOLDOWN_SECONDS` | 遇到稍后再试、网络不给力等可重试页面后的冷却时间 |
| `ANTFORTUNE_READ_COUNT_RECOVER_ON_RETRYABLE` | 蚂蚁财富阅读数遇到可重试页面时是否重启 App 并重新打开帖子 |
| `ANTFORTUNE_READ_COUNT_WARMUP_ENABLED` | 蚂蚁财富阅读数恢复流程是否启用首页预热 |
| `ANTFORTUNE_READ_COUNT_WARMUP_BEFORE_OPEN` | 是否每次打开帖子前都预热；日常建议 `false`，只在风控时恢复 |
| `ANTFORTUNE_READ_COUNT_WARMUP_WAIT_SECONDS` | 蚂蚁财富首页预热等待时间 |
| `ANTFORTUNE_READ_COUNT_WARMUP_SWIPE_COUNT` | 蚂蚁财富首页预热滑动次数 |
| `ANTFORTUNE_READ_COUNT_WARMUP_AFTER_SWIPE_SECONDS` | 预热滑动后的等待时间 |

项目只抽象执行设备，不抽象桌面或浏览器。执行设备统一是 ADB 手机，只是连接方式可能是 USB、WiFi 或后续其他 ADB 适配方式。

## KOL 底层 Profile 配置

这些配置只服务当前 KOL DB pipeline 的底层主页采集模块，旧的独立 profile CLI 和 scheduler 入口已移除：

| Key | 说明 |
| --- | --- |
| `PROFILE_METRICS_CRAWL_LIMIT` | KOL 主页粉丝采集单轮限制，`0` 表示不限制 |
| `PROFILE_POST_READ_MAX_SCROLLS` | 主页帖子阅读数最多滚动页数 |
| `PROFILE_POST_READ_MAX_POSTS` | 主页帖子阅读数最多取几条帖子 |
| `PROFILE_POST_READ_CRAWL_LIMIT` | 主页帖子阅读数单轮限制 |

## 注意事项

- 启用 `KOL_DAILY_CRAWL_TIME` 后，scheduler 会注册 `kol_daily_db_pipeline`。
- 新 KOL 主链路只更新数据库；查看和下载走 `easy-viewer` 的 `/kol-metrics` 页面。
- `KOL_TENPAY_EXTERNAL_READS_LOOKBACK_DAYS=5` 表示阅读数每天更新 T-1 到 T-5。
- `KOL_DAILY_CRAWL_TIME=08:00` 才采集今日主页粉丝数和增粉数。
- 文档字段优先按表头 title 识别，列号配置只是兜底。
- 写回图片时要走腾讯文档图片上传，不应把本地路径当最终截图结果。
