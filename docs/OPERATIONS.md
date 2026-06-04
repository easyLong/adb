# 运维手册

## 1. 环境检查

```powershell
adb devices -l
python -m unittest tests.test_mobile_parsing tests.test_report
.\scripts\run.ps1 -Task config
```

如果手机容易熄屏：

```powershell
adb shell input keyevent WAKEUP
adb shell svc power stayon true
```

## 2. 启动调度

前台运行：

```powershell
.\scripts\run.ps1 -Task scheduler
```

守护运行：

```powershell
.\scripts\run.ps1 -Task supervisor
```

scheduler 启动后会：

- 加载运行时配置。
- 注册通用任务和大 V 任务。
- 如果配置了大 V 文档和每日准备时间，会先检查当天行是否存在。
- 如果配置了大 V 抓取间隔，会跑一次 `profile_metrics`。

## 3. 大 V 每日流程

每日自动流程：

```text
00:10 profile-daily-rows
每 60 分钟 profile-metrics
```

手动补今天行：

```powershell
.\scripts\run.ps1 -Task profile-daily-rows
```

手动补指定日期：

```powershell
.\scripts\run.ps1 -Task profile-daily-rows -ReportDate 2026-06-04
```

手动完整抓取：

```powershell
.\scripts\run.ps1 -Task profile-metrics
```

拆分执行：

```powershell
.\scripts\run.ps1 -Task profile-sync
.\scripts\run.ps1 -Task profile-crawl
.\scripts\run.ps1 -Task profile-writeback
```

线上文档检查要点：

- 当天应有理财通和蚂蚁两批账号。
- 日期列不要带时分秒，应为 `YYYY-MM-DD`。
- E 列是粉丝数。
- F 列是增粉数。
- 前一天没有数据时，F 列写 `0`。

## 4. 主页帖子阅读数

```powershell
.\scripts\run.ps1 -Task profile-post-reads -ReportDate 2026-06-04
```

如果需要限制数量：

```powershell
.\scripts\run.ps1 -Task profile-post-reads -ReportDate 2026-06-04 -ConfigSet PROFILE_POST_READ_CRAWL_LIMIT=5
```

## 5. 需求 1 文章详情

完整执行：

```powershell
.\scripts\run.ps1 -Task article-details
```

拆分执行：

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

## 6. K 列链接阅读数

示例：

```powershell
.\scripts\run.ps1 -Task doc-link-reads `
  -TencentDocUrl "https://docs.qq.com/sheet/DV1ZuSnBjdGpVY1Fi?tab=57a89q" `
  -ReportDate 0602
```

常见结果：

- `read_count_not_found`：详情页没有阅读数字，或内容已不可见。
- `blank_page`：H5 空白页，任务会重试。
- `retryable_error_page`：网络不给力/重试按钮，任务会点重试或重开。

## 7. 日志和抓图

日志：

```text
apps/finance_crawler/logs/
```

抓图和解析材料：

```text
apps/finance_crawler/captures/
```

常用文件：

```text
page_000.png
page_000.xml
ui_records.jsonl
ocr_records.jsonl
```

查看最新日志：

```powershell
Get-ChildItem apps\finance_crawler\logs | Sort-Object LastWriteTime -Descending | Select-Object -First 5
Get-Content apps\finance_crawler\logs\<log-file>.err.log -Tail 80
```

## 8. 常见故障

### ADB 无设备

```powershell
adb devices -l
adb kill-server
adb start-server
```

确认手机开启 USB 调试，并允许当前电脑。

### 页面被身份认证拦截

表现：

```text
profile page is blocked by identity verification
```

处理：

- 手动在手机 App 内完成认证。
- 重新运行对应任务。
- 如果页面长期不可访问，保留 blocked 状态，不会无限重试。

### 内容不存在

表现：

```text
内容不见了，先去看看其他的吧
```

这种页面没有可抓取数据，应保留空值或错误记录。

### OCR 漏识别

先查看对应 `captures` 目录中的截图。如果截图上数字可见但 OCR 读错，可以在 `mobile/parsers.py` 增加解析兼容，并补测试。

### 写回失败

看 `profile_metric_writebacks`、`crawl_writebacks` 或日志中的 Tencent Docs batchUpdate 错误。

常见原因：

- token 失效。
- sheet/range 配置错误。
- 单次写回 payload 过长。

## 9. 提交前检查

```powershell
git status --short
python -m unittest tests.test_mobile_parsing tests.test_report
```

确认不要提交敏感配置：

- `.env`
- token 缓存
- 大量截图和日志

这些应由 `.gitignore` 排除。
