# 运行和排障

## 准备

```powershell
pip install -r requirements.txt
adb devices
```

`adb devices` 状态必须是 `device`。如果是 `unauthorized`，需要在手机上允许 USB 调试；如果没有设备，检查数据线、驱动、USB 模式和手机解锁状态。

## 常用命令

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task config
.\scripts\run.ps1 -Task fetch
.\scripts\run.ps1 -Task check
.\scripts\run.ps1 -Task detail
.\scripts\run.ps1 -Task excel-detail
.\scripts\run.ps1 -Task report
.\scripts\run.ps1 -Task scheduler
.\scripts\run.ps1 -Task supervisor
```

## 数据源链接表

部署到新的 Windows 服务器后，先初始化数据库，再把业务输入写入数据源链接表：

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task config -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"
```

临时本地 Excel 跑批：

```powershell
.\scripts\run.ps1 -Task config -ExcelInputPath "D:\demo\input.xlsx"
.\scripts\run.ps1 -Task excel-detail
```

单条链接测试：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

数据源入口保存在 MySQL 表 `data_source_links` 中，任务启动时会自动加载并覆盖环境变量。
在线文档数据源保持 `active` 长期监测；本地 Excel 和单条测试链接跑完后会自动改为 `unavailable`。
任务提交和执行仍使用现有的 `crawl_task_submissions`、`crawl_task_executions` 表。在线腾讯文档默认只提交当天 sheet 的任务；本地 Excel 跑批不要求日期，链接行会直接提交并执行。

Python 模块入口：

```powershell
python -m apps.finance_crawler.app --once fetch
python -m apps.finance_crawler.app --once check
python -m apps.finance_crawler.app --once detail
python -m apps.finance_crawler.app --once excel-detail
python -m apps.finance_crawler.app --once report
```

## 推荐测试顺序

1. 初始化数据库。

```powershell
.\scripts\run.ps1 -Task db
```

2. 拉取候选链接。

```powershell
.\scripts\run.ps1 -Task fetch
```

3. 手机保持解锁，跑初检。

```powershell
.\scripts\run.ps1 -Task check
```

4. 跑详情采集。

```powershell
.\scripts\run.ps1 -Task detail
```

5. 生成报告。

```powershell
.\scripts\run.ps1 -Task report
```

## 本地 Excel 直接详情采集

```powershell
$env:EXCEL_DETAIL_INPUT_PATH = "D:\demo\input.xlsx"
$env:EXCEL_DETAIL_OUTPUT_PATH = "D:\demo\output.xlsx"
$env:EXCEL_DETAIL_SOURCE_FILTER = "alipay,antfortune,tenpay"
$env:EXCEL_DETAIL_ALIPAY_LIMIT = "50"
$env:EXCEL_DETAIL_ANTFORTUNE_LIMIT = "50"
$env:EXCEL_DETAIL_TENPAY_LIMIT = "0"
.\scripts\run.ps1 -Task excel-detail
```

## 单链接测试

```powershell
python .\scripts\crawl_one_link.py "<url>"
python .\scripts\crawl_one_link.py "<url>" --skip-check
python .\scripts\crawl_one_link.py "<url>" --record-id 10001
```

## 常驻运行

生产建议使用 supervisor：

```powershell
.\scripts\run.ps1 -Task supervisor
```

supervisor 会看护调度器，异常退出后自动重启。设备断开、未授权、离线或多设备未指定 `DEVICE_SERIAL` 时，本轮 `check` / `detail` 会中止并告警。

## 截图和 OCR

截图优先走：

```powershell
adb exec-out screencap -p
```

调试文件：

```text
apps/finance_crawler/captures/record_<id>_<time>/
```

常看文件：

| 文件 | 用途 |
| --- | --- |
| `page_000.png` | 首屏截图 |
| `page_000.xml` | UI XML |
| `ui_records.jsonl` | 控件文本 |
| `ocr_records.jsonl` | OCR 文本 |
| `tenpay_trade_*.png/jsonl` | 财付通明细页截图和 OCR |

OCR 开关：

```powershell
$env:DETAIL_ENABLE_OCR = "true"
```

## 常见问题

### App 没有打开详情页

- 手机保持解锁。
- `adb devices` 必须是 `device`。
- 确认目标 App 已安装并登录。
- 手动打开分享链接确认能进入详情页。

### 腾讯文档读不到数据

- 检查 `TENCENT_DOC_ACCESS_TOKEN` 是否过期。
- 检查 `TENCENT_DOC_CLIENT_ID`、`TENCENT_DOC_OPEN_ID`。
- 检查 `TENCENT_DOC_FILE_ID` 和 `TENCENT_DOC_SHEET_ID`。
- 检查 `TENCENT_DOC_READ_RANGE` 是否覆盖目标行。

### 阅读数或评论数不准

先看：

```text
apps/finance_crawler/captures/record_<id>_<time>/ui_records.jsonl
apps/finance_crawler/captures/record_<id>_<time>/ocr_records.jsonl
```

再调整：

```text
apps/finance_crawler/mobile/parsers.py
```

### 财付通明细没抓到

检查采集目录里是否有 `tenpay_trade_*` 文件。如果没有，通常是没有识别到“去查看明细/查看明细”；如果有截图但没结果，优先看对应 OCR JSONL。
