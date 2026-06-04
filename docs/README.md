# ADB 项目文档入口

推荐阅读顺序：

1. [ADB_FRAMEWORK_AND_DATA_FLOW.md](ADB_FRAMEWORK_AND_DATA_FLOW.md)
   当前主入口，说明项目边界、整体框架、数据链路、任务状态、写回链路和排查入口。

2. [SCRIPTS.md](SCRIPTS.md)
   日常命令、`scripts/run.ps1` 任务清单、`.cmd` 辅助脚本和维护脚本说明。

3. [OPERATIONS.md](OPERATIONS.md)
   部署、运行、测试、排障命令说明。

4. [RUNTIME_CONFIG.md](RUNTIME_CONFIG.md)
   MySQL 运行配置、数据源入口和应用配置说明。

5. [PROJECT_FLOW.md](PROJECT_FLOW.md)
   早期端到端流程说明，作为补充材料保留。

6. [ARCHITECTURE.md](ARCHITECTURE.md)
   分层架构和扩展原则说明，作为补充材料保留。

7. [FINANCE_CRAWLER.md](FINANCE_CRAWLER.md)
   业务字段、支持链接、表格列和常用配置说明。

8. [init.sql](init.sql)
   MySQL 初始化 SQL。

项目边界：

- 本仓库只做 Android App / ADB 采集。
- Windows 桌面、浏览器、Windows UI Automation 采集放到同级项目 `C:\Code\desktop-browser-crawler`。
