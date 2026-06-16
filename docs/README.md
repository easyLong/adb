# ADB Crawler 文档

本项目只做 Android App / ADB 采集。桌面、浏览器、Windows UI Automation 采集不放在本项目里。

## 现行文档

建议按下面顺序阅读：

1. [ARCHITECTURE.md](ARCHITECTURE.md)  
   最新架构总览，包含总览图、项目边界、两条核心链路、数据库表分层、调度方式。
2. [PROJECT_FLOW.md](PROJECT_FLOW.md)  
   日常数据流转。说明业务给在线文档后，系统如何提交、采集、回填。
3. [SCRIPTS.md](SCRIPTS.md)  
   今日详情定时提交见 [REDSOIL_DETAIL_SCHEDULE.md](REDSOIL_DETAIL_SCHEDULE.md)，
   用于每天 16:00 自动提交当天 `redsoil_detail`。
   命令手册。说明 `scripts/run.ps1` 支持的任务和常用跑法。
4. [RUNTIME_CONFIG.md](RUNTIME_CONFIG.md)  
   运行配置。说明 MySQL 配置、腾讯文档 OpenAPI、定时任务和采集行为配置。
5. [ACTION_TEMPLATES.md](ACTION_TEMPLATES.md)
   App 采集动作模板经验库。说明哪个 App 采集哪种数据、采用什么动作、UI/OCR/跳转/滚动等技术手段，以及什么证据算成功。
6. [CRAWLER_APP_V2.md](CRAWLER_APP_V2.md)
   v2 文档任务设计细节，主要面向帖子链接型任务。
7. [V2_AUTO_RUN_OPERATION.md](V2_AUTO_RUN_OPERATION.md)
   v2 文档触发器操作手册。
8. [init.sql](init.sql)
   数据库初始化 SQL。

架构图见 [ARCHITECTURE.md](ARCHITECTURE.md)，完整源图见 [assets/adb-crawler-architecture.mmd](assets/adb-crawler-architecture.mmd)。

当前架构有三条主线：

```text
帖子/链接型任务
  -> document_trigger
  -> task_submissions / task_executions
  -> writeback_plans

主页型任务
  -> profile_trigger
  -> profile_metric_sources / profile_metric_runs
  -> profile_metric_writebacks

内部数据报告
  -> 读取线上日期 sheet 当前数据
  -> 按产品统计
  -> 写回日报 sheet
```

常驻入口：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

## 历史参考

下面文档保留用于查旧业务、旧命令或迁移背景，日常配置优先看上面的现行文档：

| 文档 | 用途 |
| --- | --- |
| [FINANCE_CRAWLER.md](FINANCE_CRAWLER.md) | 旧金融爬虫业务链路说明 |
| [OPERATIONS.md](OPERATIONS.md) | 旧运维说明 |
| [ADB_FRAMEWORK_AND_DATA_FLOW.md](ADB_FRAMEWORK_AND_DATA_FLOW.md) | 早期 ADB 框架和数据流说明 |
| [env.example.ps1](env.example.ps1) | 环境变量示例 |
