# 运行时配置

项目根目录 `.env` 只负责 MySQL 连接。任务源、腾讯文档 OpenAPI 身份等运行时配置统一放在 MySQL，避免再依赖 `D:\password\*.txt`、`G:\passwd\*.txt` 这类本地密码文件。

## 配置分层

| 位置 | 保存内容 | 说明 |
| --- | --- | --- |
| `.env` | `MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE` | 只用于连接 MySQL，不提交 Git |
| `data_source_links` | `TENCENT_DOC_URL`、`EXCEL_DETAIL_INPUT_PATH`、`SINGLE_TEST_LINK` | 数据从哪里来 |
| `app_config` | `TENCENT_DOC_*`、`APP_OPEN_RECOVERY_RETRIES`、`APP_RESTART_WAIT`、`DETAIL_INTERVAL_MINUTES`、`TASK_RUNNING_TIMEOUT_MINUTES` | 应用级运行配置：腾讯文档 OpenAPI 身份、App 采集和调度保护参数 |

程序启动任务前会调用 `load_runtime_config()`，先从 `data_source_links` 读取任务入口，再从 `app_config` 读取腾讯文档 OpenAPI 和 App 采集保护配置，并覆盖到当前进程的 `Config`。

## 根目录 .env

参考 `.env.example`：

```dotenv
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your-mysql-password
MYSQL_DATABASE=finance_crawler
```

`.env` 已加入 `.gitignore`。不要把腾讯文档 key、token、OpenId 写进 `.env`。

## 查看配置

```powershell
.\scripts\run.ps1 -Task config
```

输出会分成三组：

| 分组 | 来源表 | 作用 |
| --- | --- | --- |
| 任务源配置 | `data_source_links` | 在线腾讯文档、本地 Excel、单条测试链接 |
| 腾讯文档 OpenAPI | `app_config` | 读写腾讯文档所需身份 |
| App 采集和调度保护 | `app_config` | 白屏、系统更新弹窗、App 卡死时的自动恢复参数，以及详情队列轮询间隔 |

`app_config.is_secret=1` 的值会在命令行里打码显示。

## 设置腾讯文档 OpenAPI

更新 key 或 token 时直接写 MySQL 配置表，推荐通过命令：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet "TENCENT_DOC_CLIENT_ID=你的client_id" `
  -ConfigSet "TENCENT_DOC_OPEN_ID=你的open_id" `
  -ConfigSet "TENCENT_DOC_ACCESS_TOKEN=你的access_token"
```

如果使用 `client_secret` 自动换 token：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet "TENCENT_DOC_CLIENT_ID=你的client_id" `
  -ConfigSet "TENCENT_DOC_OPEN_ID=你的open_id" `
  -ConfigSet "TENCENT_DOC_CLIENT_SECRET=你的client_secret"
```

也可以直接 SQL 更新：

```sql
INSERT INTO app_config (config_key, config_value, status, is_secret, description, updated_by)
VALUES
  ('TENCENT_DOC_ACCESS_TOKEN', '<new-token>', 'active', 1, 'Tencent Docs OpenAPI Access-Token', 'manual')
ON DUPLICATE KEY UPDATE
  config_value = VALUES(config_value),
  status = 'active',
  is_secret = VALUES(is_secret),
  updated_by = VALUES(updated_by);
```

## 设置任务源

在线腾讯文档：

```powershell
.\scripts\run.ps1 -Task config -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"
.\scripts\run.ps1 -Task supervisor
```

本地 Excel：

```powershell
.\scripts\run.ps1 -Task config -ExcelInputPath "D:\demo\input.xlsx"
.\scripts\run.ps1 -Task excel-detail
```

单条链接：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

本地 Excel 和单条链接执行完成后，程序会把对应数据源状态改成 `unavailable`，避免下次误跑。

## App 采集保护配置

当 App 因系统更新弹窗、白屏、页面卡死或短暂网络异常导致初检/详情采集失败时，系统会先按链接识别目标 App 包名，执行 `adb shell am force-stop <package>`，等待后重新打开同一链接再采集。真实删帖或内容不存在的 `not_found` 不会触发重启。

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet "APP_OPEN_RECOVERY_RETRIES=1" `
  -ConfigSet "APP_RESTART_WAIT=2.0" `
  -ConfigSet "DETAIL_INTERVAL_MINUTES=10" `
  -ConfigSet "TASK_RUNNING_TIMEOUT_MINUTES=360"
```

| 配置 | 默认值 | 说明 |
| --- | --- | --- |
| `APP_OPEN_RECOVERY_RETRIES` | `1` | 技术性异常时重启 App 并重新打开链接的次数 |
| `APP_RESTART_WAIT` | `2.0` | `force-stop` 后重新打开链接前等待秒数 |
| `DETAIL_INTERVAL_MINUTES` | `10` | 到期详情任务轮询间隔；每轮消费 `scheduled_at <= now` 的任务 |
| `TASK_RUNNING_TIMEOUT_MINUTES` | `360` | `running` 任务超过该时间无心跳时，视为异常中断并转回可重试或最终失败 |

## 腾讯文档 URL 解析

`TENCENT_DOC_URL` 只保存在 `data_source_links`。程序会自动从 URL 解析：

| 字段 | 来源 |
| --- | --- |
| `TENCENT_DOC_FILE_ID` | 从文档 URL 自动解析 |
| `TENCENT_DOC_SHEET_ID` | 从文档 URL 的 `tab` 自动解析 |

这两个派生值不需要手工写入配置表。

## 相关表

| 表 | 作用 |
| --- | --- |
| `data_source_links` | 保存数据源入口 |
| `app_config` | 保存应用级配置和敏感 OpenAPI 身份 |
| `crawl_sources` | 标准化后的来源 |
| `crawl_task_submissions` | 任务提交、去重、排队、重试 |
| `crawl_task_executions` | 每次执行记录 |
| `crawl_results` | 标准化采集结果 |
| `crawl_writebacks` | 写回目标和状态 |
