# 脚本和任务索引

本项目日常优先使用 `scripts/run.ps1`。根目录的 `.cmd` 文件适合双击或放到计划任务里；`scripts/*.py` 多数是一次性排查、修复或本地文件处理工具。

## 1. 基本约定

所有命令默认在项目根目录执行：

```powershell
cd c:\Code\adb
```

先确认环境：

```powershell
pip install -r requirements.txt
adb devices -l
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task config
```

`.env` 只放 MySQL 连接信息。业务配置优先写入 MySQL 运行时配置，可用下面命令查看和更新：

```powershell
.\scripts\run.ps1 -Task config
.\scripts\run.ps1 -Task config -ConfigSet KEY=VALUE
```

## 2. 主入口 `scripts/run.ps1`

`run.ps1` 是所有常用任务的统一入口。

通用参数：

| 参数 | 用途 |
| --- | --- |
| `-Task` | 选择任务。 |
| `-TencentDocUrl` | 设置或临时使用腾讯文档 URL。 |
| `-ReportDate` | 业务日期，支持 `YYYY-MM-DD`，部分任务也支持 `MMDD`。 |
| `-ConfigSet KEY=VALUE` | 临时写入运行时配置。 |
| `-TencentDocScanMode` | 通用 fetch 扫描模式：`single`、`today`、`date`、`filter`、`all`。 |
| `-TencentDocScanDate` | fetch 按日期扫描，格式 `YYYY-MM-DD`。 |
| `-TencentDocSheetTitleFilter` | fetch 按 sheet 标题过滤。 |
| `-DetailSourceDates` | detail 只处理指定来源日期，多个日期用逗号分隔。 |
| `-SingleLink` | 单链接调试或配置。 |
| `-ExcelInputPath` | 本地 Excel 详情采集输入文件。 |

任务清单：

| Task | 作用 | 常用场景 |
| --- | --- | --- |
| `db` | 初始化或升级 MySQL 表结构。 | 首次部署、表结构变更后。 |
| `config` | 查看/更新运行时配置。 | 改文档 URL、token、范围、限速。 |
| `fetch` | 从腾讯文档同步候选链接到任务库。 | 新 tab、新日期、新表格导入。 |
| `check` | 初检链接是否存在并写回账号或 `N`。 | 跑 check、补跑失败项。 |
| `detail` | 抓取已通过初检的详情阅读数、评论数、截图等。 | 通用详情采集。 |
| `excel-detail` | 从本地 Excel 读取任务并写回本地 Excel。 | 离线表格采集。 |
| `link-detail` | 单链接详情调试。 | 排查一个链接的解析结果。 |
| `report` | 生成日报。 | 每日汇总或手工补报告。 |
| `profile-sync` | 同步大 V 主页统计源数据。 | 大 V 拆分流程第一步。 |
| `profile-daily-rows` | 生成大 V 当日模板行。 | 每天建当天行、补历史日期。 |
| `profile-create-tasks` | 从大 V 源数据创建抓取任务。 | 拆分排查。 |
| `profile-crawl` | 抓取大 V 粉丝数等指标。 | 拆分排查。 |
| `profile-writeback` | 写回大 V 指标。 | 写回失败后补写。 |
| `profile-metrics` | 大 V 同步、建任务、抓取、写回一体流程。 | 日常大 V 主页统计。 |
| `profile-post-reads` | 抓大 V 主页当日帖子阅读数。 | 主页帖子阅读数补采。 |
| `article-sync` | 同步文章详情源数据。 | 文章详情拆分流程第一步。 |
| `article-crawl` | 抓取文章详情。 | 拆分排查。 |
| `article-writeback` | 写回文章详情。 | 写回失败后补写。 |
| `article-details` | 文章详情同步、抓取、写回一体流程。 | 需求 1 文章详情。 |
| `doc-link-reads` | 从文档链接列打开详情页，回填阅读数列。 | K->M 或类似阅读数回填。 |
| `scheduler` | 启动周期调度。 | 常驻运行。 |
| `supervisor` | 守护 scheduler，崩溃后自动重启。 | 生产/长时间运行。 |

## 3. 常用跑法

### 3.1 普通腾讯文档 tab 初检

```powershell
.\scripts\run.ps1 -Task config -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"
.\scripts\run.ps1 -Task fetch -TencentDocScanMode single
$env:CRAWL_MAX_CONSECUTIVE_ERRORS='0'
.\scripts\run.ps1 -Task check
```

完成标志：

```text
checked records: 0
```

### 3.2 链接列右移的 tab

有些表格列会右移，例如：

```text
K 列：发帖时间
O 列：帖子链接
```

这种要临时指定列号。列号从 0 开始，所以 K 是 `10`，O 是 `14`。

```powershell
.\scripts\run.ps1 -Task config -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>"

$env:TENCENT_DOC_COL_POST_TIME='10'
$env:TENCENT_DOC_COL_URL='14'
.\scripts\run.ps1 -Task fetch -TencentDocScanMode single

$env:TENCENT_DOC_COL_URL='14'
$env:CRAWL_MAX_CONSECUTIVE_ERRORS='0'
.\scripts\run.ps1 -Task check

$env:TENCENT_DOC_COL_URL='14'
$env:CRAWL_MAX_CONSECUTIVE_ERRORS='0'
.\scripts\run.ps1 -Task check
```

最后一次看到 `checked records: 0` 表示队列清空。

### 3.3 按日期补导入并跑详情

```powershell
.\scripts\run.ps1 -Task fetch -TencentDocScanMode date -TencentDocScanDate 2026-05-26
.\scripts\run.ps1 -Task detail -DetailSourceDates 2026-05-26
```

多个日期可以用根目录脚本：

```powershell
.\backfill_detail_by_date.cmd 2026-05-26 2026-05-27
```

### 3.4 K 列链接阅读数回填 M 列

```powershell
.\scripts\run.ps1 -Task doc-link-reads `
  -TencentDocUrl "https://docs.qq.com/sheet/<fileId>?tab=<sheetId>" `
  -ReportDate 0602
```

如果明确找不到页面，当前逻辑写回 `N`；技术失败会保留错误状态，避免把临时网络问题误写成 `N`。

### 3.5 单链接排查

通过主流程：

```powershell
.\scripts\run.ps1 -Task link-detail -SingleLink "https://ur.alipay.com/..."
```

通过脚本直接看 JSON：

```powershell
python .\scripts\crawl_one_link.py "https://ur.alipay.com/..."
python .\scripts\crawl_one_link.py "https://ur.alipay.com/..." --skip-check
```

### 3.6 大 V 主页统计

```powershell
.\scripts\run.ps1 -Task profile-daily-rows -ReportDate 2026-06-04
.\scripts\run.ps1 -Task profile-metrics
```

拆分排查：

```powershell
.\scripts\run.ps1 -Task profile-sync
.\scripts\run.ps1 -Task profile-create-tasks -ReportDate 2026-06-04
.\scripts\run.ps1 -Task profile-crawl
.\scripts\run.ps1 -Task profile-writeback
```

### 3.7 文章详情

一体流程：

```powershell
.\scripts\run.ps1 -Task article-details
```

拆分排查：

```powershell
.\scripts\run.ps1 -Task article-sync
.\scripts\run.ps1 -Task article-crawl
.\scripts\run.ps1 -Task article-writeback
```

## 4. 根目录 `.cmd` 脚本

| 脚本 | 作用 | 示例 |
| --- | --- | --- |
| `start_supervisor.cmd` | 双击启动 supervisor，适合常驻任务。 | `.\start_supervisor.cmd` |
| `backfill_detail_by_date.cmd` | 按一个或多个日期导入并跑详情。 | `.\backfill_detail_by_date.cmd 2026-05-26 2026-05-27` |

两个脚本都支持 `--dry-run`：

```powershell
.\start_supervisor.cmd --dry-run
.\backfill_detail_by_date.cmd --dry-run 2026-05-26
```

## 5. 维护脚本

### `scripts/crawl_one_link.py`

直接打开一个链接并打印初检/详情 JSON。用于定位 App 打开、UI 节点、OCR 或解析问题。

```powershell
python .\scripts\crawl_one_link.py "https://ur.alipay.com/..."
```

### `scripts/fill_antfortune_xlsx.py`

读取本地蚂蚁财富 Excel，通过手机采集并写出一个新 Excel。表头需要包含：

```text
发帖账号
链接
阅读数
评论数
```

示例：

```powershell
python .\scripts\fill_antfortune_xlsx.py ".\input.xlsx" --limit 10
python .\scripts\fill_antfortune_xlsx.py ".\input.xlsx" --sheet Sheet1 --save-as ".\output.xlsx" --force
```

### `scripts/repair_initial_check_link.py`

重跑单条初检并可写回腾讯文档。用于修正某一行误判。

```powershell
python .\scripts\repair_initial_check_link.py "https://ur.alipay.com/..." --dry-run
python .\scripts\repair_initial_check_link.py "https://ur.alipay.com/..."
```

### `scripts/replay_tencent_docs_writebacks.py`

不重新打开手机，只把 MySQL 中失败的腾讯文档详情写回重放一次。适合 token 限流、batchUpdate 临时失败后的补写。

```powershell
python .\scripts\replay_tencent_docs_writebacks.py --dry-run
python .\scripts\replay_tencent_docs_writebacks.py --error-like "Requests Use Up" --batch-size 5
```

## 6. 排查命令

查看是否有任务进程：

```powershell
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match 'apps\.finance_crawler\.app' -or $_.CommandLine -match 'run\.ps1' } |
  Select-Object ProcessId, Name, CommandLine
```

查看最新日志：

```powershell
Get-ChildItem apps\finance_crawler\logs |
  Sort-Object LastWriteTime -Descending |
  Select-Object -First 5 Name, LastWriteTime, Length

Get-Content apps\finance_crawler\logs\<date>.log -Tail 120
```

查看最近 git 变更：

```powershell
git status --short
```

