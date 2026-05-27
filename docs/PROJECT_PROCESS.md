# 项目过程梳理

## 目标演进

这个项目最初是为了通过 ADB 自动打开金融 App 帖子链接，采集发帖账号、阅读数、评论数和截图，并把结果写回腾讯文档。随着链路增加，目标已经从“跑通某一个 App”演进为“可扩展的金融 App 帖子采集工作台”。

当前支持的 App 链路：

| 链路 | 典型链接 | 打开方式 | 专属能力 |
| --- | --- | --- | --- |
| 支付宝 | `ur.alipay.com`、`alipays://...` | 短链解析成 `alipays://...` 后打开支付宝 | 通用账号、正文、阅读数、评论数采集 |
| 蚂蚁财富 | `think.klv5qu.com`、`afwealth://...` | 分享链接改写成 `afwealth://platformapi/startapp?...` | 通用账号、正文、阅读数、评论数采集 |
| 财付通/腾讯理财通 | `tencentwm.com`、`tenpay://...`、`tencentwm://...` | 指定包名 `com.tencent.fortuneplat` 打开 | 额外点击“去查看明细/调仓明细”，解析买入基金和金额 |

## 当前全链路

```text
数据源
  Tencent Docs / 后续 Excel / 后续 API
  |
  v
表格候选解析
  utils/tabular_links.py
  - 判断 URL 是否支持
  - 解析发帖时间
  - 生成候选帖子
  |
  v
入库与任务
  workflows/tencent_docs_fetch.py
  storage/crawl_repository.py
  storage/db.py + storage/framework_db.py
  - 写入兼容业务表 posts
  - 同步写入框架表 crawl_tasks
  |
  v
App 链路分发
  crawlers/registry.py
  - 识别 source_app
  - 匹配 AppLinkProfile
  - 选择 AppCrawlerAdapter
  |
  v
手机执行
  mobile/capture_engine.py
  mobile/device_session.py
  mobile/page_status.py
  mobile/post_capture.py
  mobile/crawler.py
  - ADB 打开 deep link
  - 按 App CapturePlan 决定截图页数、OCR、滑动等待和停止条件
  - 截图使用 adb exec-out screencap
  - 采集 UI XML 和 OCR
  - 调用 App 专属 Adapter 钩子
  |
  v
结果落库
  posts 兼容更新
  crawl_results / crawl_writebacks 框架记录
  |
  v
写回目标
  sinks/tencent_docs.py
  integrations/tencent_docs/client.py
  integrations/tencent_docs/rows.py
  integrations/tencent_docs/write_requests.py
  integrations/tencent_docs/screenshots.py
  integrations/tencent_docs/writeback.py
  integrations/qq_docs.py 兼容旧入口
  - URL 校验行号
  - 批量写回阅读数/评论数/状态
  - 上传并插入首屏截图
```

## 当前分层边界

| 层 | 主要文件 | 稳定职责 |
| --- | --- | --- |
| 数据源层 | `sources/*` | 从腾讯文档、Excel、API 等来源读取候选记录，输出统一 `SourceRecord` |
| 表格解析层 | `utils/tabular_links.py` | 把二维表格行解析成 URL、发帖时间、来源 App，不绑定腾讯文档 |
| App Profile 层 | `crawlers/*` | 声明 App 的 scheme、host、包名、ready 关键词、链接改写规则 |
| App Adapter 层 | `crawlers/*` | 承载 App 专属点击、解析、特殊指标输出 |
| 手机采集层 | `mobile/*` | 负责设备会话、页面状态、截图、XML、OCR、滑动采集和通用编排 |
| 工作流层 | `workflows/*` | 编排 fetch/check/batch，不写具体 App 特例 |
| 写回层 | `sinks/*` | 把采集结果写回目标系统，不关心手机采集细节 |
| 写回服务层 | `services/writeback.py` | 为 workflow 准备写回计划、定位行号并批量提交写回 |
| 集成层 | `integrations/*` | 第三方系统底层 API，例如腾讯文档 OpenAPI client |
| 存储层 | `storage/*` | 面向 workflow 的仓储边界、MySQL 兼容业务表和框架表双写 |

## 重构原则

1. 新增 App 时，优先只新增 `crawlers/<app>.py` 并在 `crawlers/registry.py` 注册。
2. 新增数据源时，优先只新增 `sources/<source>.py`，复用 `utils/tabular_links.py` 解析表格候选。
3. 新增写回目标时，优先只新增 `sinks/<sink>.py`，不要改手机采集层。
4. `mobile/crawler.py` 只保留 Adapter 调度和结果拼装，不能继续堆具体 App 的 if/else。
5. App Adapter 必须 best-effort，专属解析失败不能拖垮通用采集。
6. 兼容旧 `posts` 表，但新结果同步写入 `crawl_results` 和 `crawl_writebacks`。
7. 默认 MySQL 库名仍是 `alipay_crawler`，这是历史兼容，不代表应用边界仍是支付宝。

## 已完成的关键优化

- 应用目录从 `apps/alipay_crawler` 改为 `apps/finance_crawler`。
- 手机内部目录从 `alipay/` 改为 `mobile/`。
- 新增 `crawlers/` App Profile / Adapter 层。
- 财付通“去查看明细/调仓明细/买入基金金额”逻辑迁出公共采集层。
- 批处理指标从写死 `tenpay_*` 改为合并 `app_metrics`。
- 表格候选解析从腾讯文档集成层迁到 `utils/tabular_links.py`。
- 腾讯文档 OpenAPI 鉴权、读表、批量更新、图片上传抽到 `integrations/tencent_docs/client.py`。
- 腾讯文档单元格更新、截图链接、截图图片插入 request 构造抽到 `integrations/tencent_docs/write_requests.py`。
- 腾讯文档行号定位抽到 `integrations/tencent_docs/rows.py`，source/sink 不再依赖旧 `qq_docs.py` 门面。
- 腾讯文档截图上传/降级路径抽到 `integrations/tencent_docs/screenshots.py`，批量写回编排抽到 `integrations/tencent_docs/writeback.py`。
- 新增 `sources/excel.py` 和 `sinks/excel.py`，开始验证腾讯文档可以被本地 Excel 替换。
- 新增 `mobile/parsers.py`，把通用金融社区帖子账号、正文、阅读数、评论数解析规则从手机采集流程中拆出。
- 新增 `CapturePlan`，把主帖截几屏、是否 OCR、滑动等待、停止条件、明细页滑动次数变成 App 可声明策略。
- 新增 `mobile/post_capture.py`，把截图、XML、OCR、滑动循环从 `mobile/crawler.py` 拆出。
- 新增 `mobile/device_session.py`，把设备连接缓存、ADB 路径准备、唤醒/锁屏检查、链接打开从 `mobile/crawler.py` 拆出。
- 新增 `mobile/page_status.py`，把页面可用/删除/错误判断从 `mobile/crawler.py` 拆出。
- 新增 `storage/crawl_repository.py`，让 `check` / `batch` workflow 不再直接调用旧 `posts` 表读写函数。
- 新增 `services/writeback.py`，让 `check` / `batch` workflow 不再直接实例化 `TencentDocsSink`、读取表格快照或解析行号。
- `crawl_results` 增加 `workflow` 字段，并新增基于 `crawl_tasks` / `crawl_results` 的可选待处理查询路径。
- `crawler_apps` 框架表从 App Profile 自动同步，不再在 DB 层硬编码。
- `source_app='unknown'` 会按 URL 重新识别，避免历史 unknown 数据走错链路。
- 截图优先使用 `adb exec-out screencap -p`，避免 `adb shell screencap + pull` 的慢路径。

## 后续优化顺序

1. 把 Excel Source/Sink 接入独立 workflow 或命令行，形成完整 Excel 全链路。
2. 灰度打开 `USE_FRAMEWORK_TASKS_FOR_WORKFLOWS`，把业务查询逐步从 `posts` 迁移到 `crawl_tasks` / `crawl_results`。
3. 给 App Adapter 和 `mobile/parsers.py` 增加轻量单元测试，尤其是财付通 OCR 明细解析。
4. 把真实手机链路测试整理成固定脚本，覆盖支付宝、蚂蚁财富、财付通各 1 条样例。
5. 继续压缩 `integrations/qq_docs.py`，只保留确实需要对外兼容的旧函数。
