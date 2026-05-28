# 运行时任务源配置

`data_source_links` 只保存最关键的任务源入口，不保存系统内部派生字段。程序启动具体任务前会调用 `load_runtime_config()`，把这些入口加载到 `Config`。

## 配置项

| source_key | data_source_link | status |
| --- | --- | --- |
| `TENCENT_DOC_URL` | 在线腾讯文档链接。调度器常驻后会持续扫描目标文档。 | 通常保持 `active` |
| `EXCEL_DETAIL_INPUT_PATH` | 本地 Excel 文件路径。手动执行一次 `excel-detail`。 | 跑完后 `unavailable` |
| `SINGLE_TEST_LINK` | 单条测试链接。手动执行一次 `link-detail`。 | 跑完后 `unavailable` |

程序会自动从 `TENCENT_DOC_URL` 解析 `file_id` 和 `sheet_id`，不需要手动写入配置表。

## 查看配置

```powershell
.\scripts\run.ps1 -Task config
```

## 在线腾讯文档

```powershell
.\scripts\run.ps1 -Task config -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"
.\scripts\run.ps1 -Task supervisor
```

补扫指定日期：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet "TENCENT_DOC_SCAN_MODE=date" -ConfigSet "TENCENT_DOC_SCAN_DATE=2026-05-27"
.\scripts\run.ps1 -Task fetch
```

常见扫描模式：

| 模式 | 说明 |
| --- | --- |
| `single` | 只读 URL 中指定 sheet |
| `today` | 只读当天日期 sheet |
| `date` | 只读 `TENCENT_DOC_SCAN_DATE` 指定日期 sheet |
| `filter` | 按 `TENCENT_DOC_SHEET_TITLE_FILTER` 过滤 |
| `all` | 扫描全部 sheet |

## 本地 Excel

```powershell
.\scripts\run.ps1 -Task config -ExcelInputPath "D:\demo\input.xlsx"
.\scripts\run.ps1 -Task excel-detail
```

执行完成后，程序会把 `EXCEL_DETAIL_INPUT_PATH` 的 `status` 改为 `unavailable`。

## 单条链接

直接传链接测试一次：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

也可以先写入配置，再执行：

```powershell
.\scripts\run.ps1 -Task config -SingleLink "https://ur.alipay.com/..."
.\scripts\run.ps1 -Task link-detail
```

执行完成后，程序会把 `SINGLE_TEST_LINK` 的 `status` 改为 `unavailable`。

## SQL 示例

```sql
INSERT INTO data_source_links (source_key, data_source_link, status, updated_by)
VALUES ('TENCENT_DOC_URL', 'https://docs.qq.com/sheet/<fileId>?tab=<sheetId>', 'active', 'manual')
ON DUPLICATE KEY UPDATE
  data_source_link = VALUES(data_source_link),
  status = 'active',
  updated_by = VALUES(updated_by);
```

## 与任务表的关系

`data_source_links` 只负责入口配置。真正的任务仍然进入：

| 表 | 用途 |
| --- | --- |
| `crawl_task_submissions` | 任务提交、唯一性、状态、调度和重跑 |
| `crawl_task_executions` | 具体执行记录、耗时、错误和结果摘要 |
| `crawl_results` | 标准化采集结果 |
| `crawl_writebacks` | 写回目标和写回状态 |
