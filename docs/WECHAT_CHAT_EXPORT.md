# 微信聊天记录导出命令

这个命令用于把“打开微信群 -> 查找聊天记录 -> 选择日期 -> 连续截图”的流程自动化。微信在部分安卓设备上不暴露聊天文本节点，所以当前版本先稳定导出截图和整理模板，后续可以再接 OCR 自动生成文字版时间线。

## 前置条件

1. 手机已通过 ADB 连接，并且微信已登录。
2. 电脑可以执行 `adb devices -l`。
3. 如需通过群名自动搜索进入群聊，需要安装 `uiautomator2`：

```powershell
pip install uiautomator2
```

## 常用命令

在项目根目录执行：

```powershell
.\scripts\wechat-chat-export.ps1 `
  -GroupName "创金设计需求响应群" `
  -Date "2026-06-12" `
  -Pages 12
```

如果已经手动打开了目标群聊，可以跳过群名搜索：

```powershell
.\scripts\wechat-chat-export.ps1 `
  -GroupName "创金设计需求响应群" `
  -Date "2026-06-12" `
  -Pages 12 `
  -NoSearch
```

如果已经手动进入了目标日期的聊天记录结果页，只想截图和翻页：

```powershell
.\scripts\wechat-chat-export.ps1 `
  -GroupName "创金设计需求响应群" `
  -Date "2026-06-12" `
  -Pages 12 `
  -SkipNavigation
```

如果连接了多台手机，指定设备：

```powershell
.\scripts\wechat-chat-export.ps1 `
  -Serial "<adb-serial>" `
  -GroupName "创金设计需求响应群" `
  -Date "2026-06-12" `
  -Pages 12
```

## 输出文件

默认输出到：

```text
exports/wechat/<群名>/<日期>/
```

目录里会包含：

```text
000_start.png
001.png
002.png
...
manifest.json
timeline.md
```

`timeline.md` 是后续整理用的文字模板，格式是：

```markdown
| 时间 | 人物 | 发言/动作 | 来源截图 |
| --- | --- | --- | --- |
```

## 当前限制

1. 坐标按当前 1080x2340 安卓手机和微信布局调过；换分辨率或微信版本后，可能需要调整 `scripts/wechat_chat_export.py` 里的 `Coords`。
2. 日期选择目前默认目标日期所在月份已经展示在微信日期选择页里。跨月份时，可以先手动选到目标日期结果页，再用 `-SkipNavigation` 截图。
3. 当前不会自动 OCR 识别聊天文字，只负责稳定留存截图和生成整理模板。
