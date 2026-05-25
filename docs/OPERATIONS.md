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

## 支付宝应用命令

使用统一脚本：

```powershell
.\scripts\run.ps1 -App alipay_crawler -Task db
.\scripts\run.ps1 -App alipay_crawler -Task fetch
.\scripts\run.ps1 -App alipay_crawler -Task check
.\scripts\run.ps1 -App alipay_crawler -Task batch
.\scripts\run.ps1 -App alipay_crawler -Task report
.\scripts\run.ps1 -App alipay_crawler -Task scheduler
```

直接运行模块：

```powershell
python -m apps.alipay_crawler.app --once fetch
python -m apps.alipay_crawler.app --once check
python -m apps.alipay_crawler.app --once batch
python -m apps.alipay_crawler.app
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
- 手机已安装支付宝。
- 手动打开分享链接时能进入详情页。

### 腾讯文档读不到数据

确认：

- `TENCENT_DOC_ACCESS_TOKEN` 未过期。
- `TENCENT_DOC_CLIENT_ID`、`TENCENT_DOC_OPEN_ID` 正确。
- `TENCENT_DOC_FILE_ID` 和 `TENCENT_DOC_SHEET_ID` 对应当前测试文档。

### 腾讯文档写回慢

当前策略：

- 初检不存在时只更新 L 单元格。
- 批处理只写 O/P/Q 三个单元格。

不要再整行写回，除非确实需要整行格式。

### 阅读数不准

查看最近一次控件采集文件：

```text
apps/alipay_crawler/captures/post_xxx/ui_records.jsonl
```

确认控件文本里的阅读数字格式，再调整：

```text
apps/alipay_crawler/alipay/crawler.py
```

重点函数：`parse_numbers()`。

### 账号提取不准

同样查看 `ui_records.jsonl`。目前规则优先取 `头像` 后面的第一个有效文本。

重点函数：`extract_account_name()`。
