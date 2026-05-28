# 数据源链接配置

`data_source_links` 只保留最关键的任务源入口，不放系统内部字段。

| source_key | data_source_link | status |
| --- | --- | --- |
| `TENCENT_DOC_URL` | 在线腾讯文档链接。调度器常驻后，会持续监测当天 sheet。 | `active` |
| `EXCEL_DETAIL_INPUT_PATH` | 本地 Excel 文件路径。手动执行一次 `excel-detail` 后跑完即结束。 | 跑完后 `unavailable` |
| `SINGLE_TEST_LINK` | 单条测试链接。手动执行一次 `link-detail` 后跑完即结束。 | 跑完后 `unavailable` |

其它信息不需要配置：

| 信息 | 处理方式 |
| --- | --- |
| 腾讯文档 `file_id` / `sheet_id` | 程序从 `TENCENT_DOC_URL` 自动解析，不写入配置表。 |
| 在线文档扫描规则 | 默认只扫描当天日期的 sheet。 |
| 在线文档读取范围 | 使用程序默认配置。 |
| 本地 Excel 输出路径 | 默认写回原文件。 |

## 在线文档

配置一次：

```powershell
.\scripts\run.ps1 -Task config -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"
```

启动常驻调度：

```powershell
.\scripts\run.ps1 -Task supervisor
```

也可以直接改数据库：

```sql
INSERT INTO data_source_links (source_key, data_source_link, status, updated_by)
VALUES ('TENCENT_DOC_URL', 'https://docs.qq.com/sheet/<fileId>?tab=<sheetId>', 'active', 'manual')
ON DUPLICATE KEY UPDATE
  data_source_link = VALUES(data_source_link),
  status = 'active',
  updated_by = VALUES(updated_by);
```

## 本地 Excel

配置文件路径：

```powershell
.\scripts\run.ps1 -Task config -ExcelInputPath "D:\demo\input.xlsx"
```

执行一次跑批：

```powershell
.\scripts\run.ps1 -Task excel-detail
```

也可以直接改数据库：

```sql
INSERT INTO data_source_links (source_key, data_source_link, status, updated_by)
VALUES ('EXCEL_DETAIL_INPUT_PATH', 'D:\\demo\\input.xlsx', 'active', 'manual')
ON DUPLICATE KEY UPDATE
  data_source_link = VALUES(data_source_link),
  status = 'active',
  updated_by = VALUES(updated_by);
```

## 单条链接

直接传链接测试一次：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

也可以先写入数据源表，再执行：

```sql
INSERT INTO data_source_links (source_key, data_source_link, status, updated_by)
VALUES ('SINGLE_TEST_LINK', 'https://ur.alipay.com/...', 'active', 'manual')
ON DUPLICATE KEY UPDATE
  data_source_link = VALUES(data_source_link),
  status = 'active',
  updated_by = VALUES(updated_by);
```

```powershell
.\scripts\run.ps1 -Task link-detail
```

执行完成后，程序会把 `SINGLE_TEST_LINK` 的 `status` 改为 `unavailable`。

## 任务表

配置表只负责入口。真正的任务仍然进入：

| 表 | 用途 |
| --- | --- |
| `crawl_task_submissions` | 任务提交管理表，负责唯一性、状态、重跑和调度。 |
| `crawl_task_executions` | 具体执行记录表，负责每次执行结果、耗时和错误。 |
