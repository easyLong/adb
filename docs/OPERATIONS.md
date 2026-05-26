# 运行和排障

## 安装依赖

```powershell
pip install -r requirements.txt
```

## 环境变量

默认运行脚本会从本机读取：

```text
D:\password\tengxun.txt
D:\password\mysql.txt
```

环境变量模板见 [env.example.ps1](env.example.ps1)。

## ADB 检查

```powershell
.\platform-tools\adb.exe devices
```

正确结果：

```text
List of devices attached
XXXXXXXXXXXXX    device
```

异常结果：

```text
XXXXXXXXXXXXX    unauthorized
```

说明手机上还没有点 USB 调试授权。

如果列表为空，优先检查数据线、驱动、USB 模式和手机是否解锁。

## 应用命令

使用统一脚本：

```powershell
.\scripts\run.ps1 -App alipay_crawler -Task db
.\scripts\run.ps1 -App alipay_crawler -Task fetch
.\scripts\run.ps1 -App alipay_crawler -Task check
.\scripts\run.ps1 -App alipay_crawler -Task batch
.\scripts\run.ps1 -App alipay_crawler -Task report
.\scripts\run.ps1 -App alipay_crawler -Task scheduler
.\scripts\run.ps1 -App alipay_crawler -Task supervisor
```

直接运行模块：

```powershell
python -m apps.alipay_crawler.app --once fetch
python -m apps.alipay_crawler.app --once check
python -m apps.alipay_crawler.app --once batch
python -m apps.alipay_crawler.app
python -m apps.alipay_crawler.app --supervise
```

## 可靠性配置

生产常驻建议使用 supervisor 模式：

```powershell
.\scripts\run.ps1 -Task supervisor
```

该模式会由父进程看护调度器，调度器异常退出后自动重启。任务开始前会检查 ADB 设备；
设备断开、未授权、离线或多设备未指定 `DEVICE_SERIAL` 时，本轮 `check`/`batch` 会中止并告警，
不会把每条帖子误记为重试失败。

告警默认写入：

```text
apps/alipay_crawler/logs/alerts.jsonl
```

配置 `ALERT_WEBHOOK_URL` 后，任务失败、设备断连、写回失败、报告失败、调度器崩溃等事件会同时 POST 到该地址。

可用这些环境变量控制单轮自动化强度：

```powershell
$env:MAX_POSTS_PER_RUN = "50"
$env:CRAWL_MAX_TASK_SECONDS = "1800"
$env:CRAWL_MAX_CONSECUTIVE_ERRORS = "5"
$env:CRAWL_ACTIVE_START = "08:00"
$env:CRAWL_ACTIVE_END = "23:00"
```

写回腾讯文档前会重新读取当前表格快照，用 URL 校验目标行号；若同一 URL 出现多行，会跳过写回以避免写错行。
批处理写回会合并为 batchUpdate 请求，`TENCENT_DOC_BATCH_UPDATE_SIZE` 控制每次提交的单元格数量。

批处理采集默认先抓首屏；只有阅读数或评论数没有解析出来时才继续滚动，最多抓
`BATCH_MAX_CAPTURE_PAGES` 屏，默认 3 屏。
批处理两条之间的等待由 `BATCH_POST_DELAY_MIN` / `BATCH_POST_DELAY_MAX` 单独控制；
滚动后的等待由 `BATCH_SCROLL_WAIT` 控制。

设备健康检查有短缓存，`DEVICE_HEALTH_CACHE_SECONDS` 默认 8 秒；唤醒和锁屏检测按
`DEVICE_PREPARE_INTERVAL_SECONDS` 周期执行，避免每条帖子重复跑多次 ADB 检查。

批量写回 O/P/Q 三列会合并成同一行请求，减少 Tencent Docs batchUpdate 的请求数量。
第一屏截图 `page_000.png` 会写入截图列，默认 R 列：

```powershell
$env:TENCENT_DOC_COL_SCREENSHOT = "17"
```

默认会优先调用腾讯文档开放平台上传图片接口，把截图作为文档资源插入到截图列，不需要配置
`SCREENSHOT_PUBLIC_BASE_URL`。如果上传或插入失败，会自动降级写入本机路径。
如果已经把 `apps/alipay_crawler/captures/` 暴露成可访问的静态文件服务，也可以配置
`SCREENSHOT_PUBLIC_BASE_URL`，降级写回值会变成在线链接。

```powershell
$env:TENCENT_DOC_UPLOAD_SCREENSHOTS = "true"
$env:TENCENT_DOC_IMAGE_INSERT_WIDTH = "160"
$env:TENCENT_DOC_IMAGE_INSERT_HEIGHT = "300"
$env:TENCENT_DOC_IMAGE_UPLOAD_DELAY = "0.25"
```

WebView 包裹内容如果无法从控件树读取，程序会对截图启用 OCR 兜底。先安装 Python 依赖：

```powershell
pip install -r requirements.txt
```

OCR 只使用 `rapidocr-onnxruntime`，不需要安装额外的 OCR 主程序或语言包。
可通过下面的开关控制是否启用截图 OCR：

```powershell
$env:BATCH_ENABLE_OCR = "true"
```

## 推荐测试顺序

1. 初始化数据库：

```powershell
.\scripts\run.ps1 -Task db
```

2. 拉取腾讯文档候选：

```powershell
.\scripts\run.ps1 -Task fetch
```

3. 手机保持解锁，跑初检：

```powershell
.\scripts\run.ps1 -Task check
```

4. 跑阅读数和评论数：

```powershell
.\scripts\run.ps1 -Task batch
```

5. 生成报告：

```powershell
.\scripts\run.ps1 -Task report
```

## 常见问题

### 支付宝没有打开详情页

确认：

- 手机未锁屏。
- ADB 显示 `device`。
- 手机已安装支付宝；测试蚂蚁财富链路时还需要安装蚂蚁财富。
- 手动打开分享链接时能进入详情页。

### 蚂蚁财富链接落到了浏览器

常见原因：

- 直接打开了 `https://think.klv5qu.com/...` 外链，系统先分给浏览器。
- 设备未安装蚂蚁财富。
- 分享链接参数不完整，无法改写成 `afwealth://platformapi/startapp?...`。

当前程序会自动把 `think.klv5qu.com` 这类分享链接改写成 `afwealth://...` 深链后再打开。

### 腾讯文档读不到数据

确认：

- `TENCENT_DOC_ACCESS_TOKEN` 未过期。
- `TENCENT_DOC_CLIENT_ID`、`TENCENT_DOC_OPEN_ID` 正确。
- `TENCENT_DOC_FILE_ID` 和 `TENCENT_DOC_SHEET_ID` 对应当前测试文档。

### 腾讯文档写回慢

当前策略：

- 初检结果批量写回 L 列。
- 批处理把 O/P/Q 三列合并成同行 batchUpdate。
- 首屏截图先上传成腾讯文档资源，再批量插入 R 列。
- 写回前用 URL 校验当前行号，避免行号漂移写错。

不要整行写回，除非确实需要整行格式。

### 阅读数不准

查看最近一次控件采集文件：

```text
apps/alipay_crawler/captures/post_xxx/ui_records.jsonl
```

如果控件树读不到 WebView 内容，再查看 OCR 结果：

```text
apps/alipay_crawler/captures/post_xxx/ocr_records.jsonl
```

确认阅读数字格式后再调整：

```text
apps/alipay_crawler/alipay/crawler.py
```

重点函数：`parse_numbers_with_presence()`。

如果是蚂蚁财富帖子，优先确认最近一次采集结果里是否已经进入了帖子详情页，而不是落到浏览器落地页或“该小程序已暂停服务”页。

### 账号提取不准

同样查看 `ui_records.jsonl`。目前规则优先取 `头像` 后面的第一个有效文本。

重点函数：`extract_account_name()`。
