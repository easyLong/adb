# 多 App 爬虫工作台

本项目是一个可扩展的爬虫工作台，不把某一个爬虫应用直接放在项目根目录。

当前已有应用：

| App | 位置 | 作用 |
| --- | --- | --- |
| `alipay_crawler` | `apps/alipay_crawler` | 从腾讯文档读取支付宝/蚂蚁财富帖子链接，按链接来源自动分流到支付宝或蚂蚁财富，完成初检、阅读数/评论数抓取和文档写回 |

未来新增 `xxx_crawler`、`yyy_crawler` 时，放到 `apps/` 下与 `alipay_crawler` 平级。

## 目录结构

```text
apps/
  alipay_crawler/          支付宝/蚂蚁财富爬虫应用
docs/                      项目文档
scripts/                   项目级运行脚本
archive/                   历史验证脚本、旧文档、截图
platform-tools/            ADB 工具
requirements.txt           Python 依赖
```

每个 App 推荐保持类似结构：

```text
apps/<app_name>/
  app.py                   调度入口，支持 --once
  config.py                该 App 的配置
  integrations/            外部系统接入
  jobs/                    定时任务和批处理任务
  storage/                 数据库访问
  services/                业务服务
  utils/                   工具函数
```

## 快速开始

安装依赖：

```powershell
pip install -r requirements.txt
```

检查 ADB 设备：

```powershell
.\platform-tools\adb.exe devices
```

运行应用单个任务：

```powershell
.\scripts\run.ps1 -App alipay_crawler -Task fetch
.\scripts\run.ps1 -App alipay_crawler -Task check
.\scripts\run.ps1 -App alipay_crawler -Task batch
```

`alipay_crawler` 是默认 App，可简写：

```powershell
.\scripts\run.ps1 -Task fetch
```

启动应用常驻调度：

```powershell
.\scripts\run.ps1 -App alipay_crawler -Task scheduler
```

直接使用 Python 模块入口：

```powershell
python -m apps.alipay_crawler.app --once fetch
python -m apps.alipay_crawler.app
```

## 凭证

默认运行脚本读取本机凭证文件：

- `D:\password\tengxun.txt`
- `D:\password\mysql.txt`

不要把真实 token、数据库密码提交到项目里。环境变量模板见 [docs/env.example.ps1](docs/env.example.ps1)。

## 文档索引

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)：项目级多 App 架构。
- [docs/ALIPAY_CRAWLER.md](docs/ALIPAY_CRAWLER.md)：支付宝/蚂蚁财富爬虫应用流程。
- [docs/OPERATIONS.md](docs/OPERATIONS.md)：运行、测试、排障命令。

## 新增 App 约定

新增应用时复制 `apps/alipay_crawler` 的基础结构，保证有 `app.py` 入口：

```powershell
.\scripts\run.ps1 -App xxx_crawler -Task fetch
python -m apps.xxx_crawler.app --once fetch
```
