# ADB Crawler 文档索引

本项目只负责 Android App / ADB 采集、任务调度和数据库沉淀。

## 当前主线

```text
帖子/链接型任务
  -> document_trigger
  -> task_submissions / task_executions
  -> writeback_plans
  -> 腾讯文档回填

KOL / 主页型任务
  -> kol_daily_metrics 数据库每日表
  -> 理财通外部阅读数同步
  -> ADB 主页粉丝数 / 增粉数采集

微信群消息 / ops_platform 需求识别
  -> ops_platform 群配置
  -> wechat_capture_runs / wechat_message_observations
  -> wechat_demand_intake_offsets / wechat_demand_intake_runs
  -> ops_platform.demand_intake_candidates / demand_candidate_evidence

内部数据报告
  -> 读取线上日期 sheet 当前数据
  -> 按产品统计
  -> 写回日报 sheet
```

## 必读

| 文档 | 用途 |
| --- | --- |
| [ARCHITECTURE.md](ARCHITECTURE.md) | 架构总览、项目边界、核心链路、数据库表分层、调度方式 |
| [PROJECT_FLOW.md](PROJECT_FLOW.md) | 端到端数据流：在线文档提交、采集、回填、KOL 主链路、常驻运行 |
| [SCRIPTS.md](SCRIPTS.md) | `scripts/run.ps1` 任务索引和常用命令 |
| [RUNTIME_CONFIG.md](RUNTIME_CONFIG.md) | MySQL、腾讯文档、scheduler、worker、KOL、微信群等运行配置 |

架构图：

| 资源 | 用途 |
| --- | --- |
| [assets/adb-crawler-architecture.mmd](assets/adb-crawler-architecture.mmd) | Mermaid 源图 |

## 业务链路

| 文档 | 用途 |
| --- | --- |
| [CRAWLER_APP_V2.md](CRAWLER_APP_V2.md) | v2 文档任务设计细节，主要面向帖子链接型任务 |
| [V2_AUTO_RUN_OPERATION.md](V2_AUTO_RUN_OPERATION.md) | v2 document trigger 操作手册 |
| [REDSOIL_DETAIL_SCHEDULE.md](REDSOIL_DETAIL_SCHEDULE.md) | 每天 16:00 自动提交当天 `redsoil_detail` 的配置和排查 |
| [KOL_DAILY_DB_PIPELINE.md](KOL_DAILY_DB_PIPELINE.md) | KOL 每日数据库主链路：初始化、阅读数同步、主页粉丝数/增粉数采集、查看入口 |
| [KOL_TENPAY_EXTERNAL_READS.md](KOL_TENPAY_EXTERNAL_READS.md) | 理财通外部阅读数同步逻辑 |
| [OPS_PLATFORM_INTAKE.md](OPS_PLATFORM_INTAKE.md) | 微信群消息采集和需求识别生产链路 |
| [WECHAT_CHAT_EXPORT.md](WECHAT_CHAT_EXPORT.md) | 微信聊天截图导出和调试命令 |
| [ACTION_TEMPLATES.md](ACTION_TEMPLATES.md) | App 采集动作模板经验库 |

## 运维和配置

| 文档 | 用途 |
| --- | --- |
| [MULTI_DEVICE_POOL.md](MULTI_DEVICE_POOL.md) | 多设备池配置、调度和排查 |
| [GITHUB_OPERATIONS.md](GITHUB_OPERATIONS.md) | GitHub 提交、推送和协作操作记录 |
| [init.sql](init.sql) | 数据库初始化 SQL |
| [env.example.ps1](env.example.ps1) | 环境变量示例 |

## 日常入口

启动常驻 worker：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

手动跑 KOL 主链路：

```powershell
.\scripts\run.ps1 -Task kol-daily-db-pipeline
```

## 历史参考

下面文档保留用于查旧业务、旧命令或迁移背景。日常配置优先看上面的现行文档。

| 文档 | 用途 |
| --- | --- |
| [FINANCE_CRAWLER.md](FINANCE_CRAWLER.md) | 旧金融爬虫业务链路说明 |
| [OPERATIONS.md](OPERATIONS.md) | 旧运维说明 |
| [ADB_FRAMEWORK_AND_DATA_FLOW.md](ADB_FRAMEWORK_AND_DATA_FLOW.md) | 早期 ADB 框架和数据流说明 |
