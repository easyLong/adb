# 架构说明

## 总体原则

项目是多 App 爬虫工作台，根目录只放项目级内容：

```text
apps/             各个爬虫应用
docs/             项目文档
scripts/          项目级脚本
archive/          历史材料
platform-tools/   ADB 工具
```

单个爬虫应用必须放在 `apps/<app_name>/` 下，例如：

```text
apps/
  alipay_crawler/
  xxx_crawler/
  yyy_crawler/
```

## App 内部结构

每个 App 推荐使用同一套目录习惯：

```text
apps/<app_name>/
  app.py
  config.py
  integrations/
  jobs/
  storage/
  services/
  utils/
```

职责说明：

| 目录/文件 | 职责 |
| --- | --- |
| `app.py` | 调度入口，提供 `--once` 单任务运行 |
| `config.py` | 读取环境变量和默认配置 |
| `integrations/` | 腾讯文档、第三方 API 等外部系统 |
| `jobs/` | 定时任务、批处理任务 |
| `storage/` | MySQL、缓存、持久化访问 |
| `services/` | 报告、聚合等业务服务 |
| `utils/` | 日志、通用工具 |

## 当前 App

`apps/alipay_crawler`

支付宝/蚂蚁财富帖子数据采集应用。详见 [ALIPAY_CRAWLER.md](ALIPAY_CRAWLER.md)。

当前实现保持单 App 内聚，但在 App 内已经区分两条执行链路：

- `alipay`：`ur.alipay.com`、`alipays://`、`alipay://`
- `antfortune`：`think.klv5qu.com`、`afwealth://`

两条链路共用同一套任务调度、ADB/uiautomator2 抓取、MySQL 落库和腾讯文档写回框架，只在链接识别和 App 唤起阶段分流。

## 当前分层

以 `apps/alipay_crawler` 为例，当前大致分成 6 层：

| 层 | 位置 | 作用 |
| --- | --- | --- |
| 入口调度层 | `app.py` | `fetch/check/batch/report/scheduler` |
| 配置层 | `config.py` | MySQL、腾讯文档、ADB、设备、目录配置 |
| 集成层 | `integrations/qq_docs.py` | 读取/筛选/写回腾讯文档 |
| 来源识别层 | `utils/link_source.py` | 将链接识别为 `alipay` / `antfortune` / `unknown` |
| 执行引擎层 | `alipay/capture_engine.py` | deep link 转换、ADB 打开、页面采集 |
| 任务与存储层 | `jobs/*`, `storage/db.py`, `services/report.py` | 初检、批处理、落库、日报 |

## 统一运行入口

项目级脚本 [scripts/run.ps1](../scripts/run.ps1) 通过 `-App` 选择应用：

```powershell
.\scripts\run.ps1 -App alipay_crawler -Task fetch
.\scripts\run.ps1 -App xxx_crawler -Task fetch
```

约定每个 App 都暴露：

```powershell
python -m apps.<app_name>.app --once <task>
python -m apps.<app_name>.app
```

## 什么时候抽公共包

目前先保持 App 内聚。只有当多个 App 真的复用同一类能力时，再抽项目级公共包，例如：

```text
crawler_core/
  scheduler/
  adb/
  docs/
  storage/
```

不要为了“看起来通用”提前抽象。
