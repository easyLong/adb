# ops_platform 需求采集接入链路

本文记录 ADB 项目从微信群持续采集消息、识别需求候选，并写入 `ops_platform` 的当前生产链路。

## 当前主链路

```text
ops_platform 群配置
-> ADB 打开微信群并按日期/页面采集截图
-> OCR 原始文本入库
-> 规则切分为稳定群消息
-> 增量模型识别新需求
-> 写入 ops_platform 候选需求和证据链
-> 管理端人工审核后进入正式需求
```

关键原则：

```text
截图和 OCR 原始结果尽量保留
最终群消息用 fingerprint 幂等去重
需求识别只消费 active 群消息
增量识别只处理水位后的新消息
人工 confirmed/rejected 的候选不被自动覆盖
```

## 元数据来源

ADB 从 `ops_platform` 读取群配置：

```text
ops_platform.group_contact_mappings
ops_platform.customers
```

主要字段：

```text
group_id           群 ID
group_name         微信群名称，用于 ADB 搜索并打开群
customer_code      客户编码，关联 customers
contact_name       对接人
business_platform  平台
collect_enabled    是否启用采集
status             active / inactive
```

ADB 会将这些信息带入采集 run、消息和需求候选，作为后续匹配客户、对接人、平台的依据。

## crawler_app 表职责

### `wechat_capture_runs`

记录一次微信群截图采集。

```text
source_key
source_name
target_date
screenshot_dir
screenshot_count
status
finished_at
```

常见状态：

```text
success      成功采集截图
no_messages  选中日期后没有可跳转的聊天记录
error        ADB、微信导航或截图异常
```

### `wechat_ocr_observations`

保存每张截图的原始 OCR 行。

用途：

```text
追溯 OCR 识别结果
排查消息切分错误
重复跑时保持幂等 upsert
```

这张表不是下游需求识别的直接输入。

### `wechat_message_observations`

保存最终可消费的规范化群消息。

关键字段：

```text
message_fingerprint    稳定消息指纹
source_key
source_name
message_date
inferred_message_time
sender_name
message_text
normalized_message_text
parser_type            ocr / model
status                 active / superseded
first_seen_run_id
latest_seen_run_id
```

下游统一只读：

```sql
WHERE message_type = 'text'
  AND status = 'active'
```

`message_fingerprint` 用于避免重复跑、跨截图重叠、同一消息多次出现导致重复 active 数据。

### `wechat_demand_intake_offsets`

记录每个群的需求识别水位。

```text
source_key
source_name
last_observation_id
last_message_time
last_intake_run_at
```

增量识别每次只处理：

```sql
wechat_message_observations.id > last_observation_id
```

如果模型失败，不推进水位。

### `wechat_demand_intake_runs`

记录一次增量需求识别任务。

```text
source_key
source_name
from_observation_id
to_observation_id
context_count
new_message_count
candidate_count
raw_model_json
status
finished_at
```

用于审计“这次模型看了哪些新消息、带了多少上下文、输出了几个候选”。

## ops_platform 写入表

### `demand_intake_candidates`

AI 识别出的待审核需求候选。

新链路的数据特征：

```text
source_app = crawler
external_capture_run_id LIKE 'intake:%'
status = pending
```

字段来源：

```text
source_chat_name       群名
external_source_key    群 source_key
external_chat_id       群 ID/source_key
business_category      模型识别
secondary_category     模型识别
tertiary_category      模型识别
business_name          模型识别
demand_title           模型识别
demand_content         模型总结
confidence             模型置信度
status                 pending
```

### `demand_candidate_evidence`

候选需求的证据链。

每条证据来自一条 active 群消息：

```json
{
  "source": "wechat_message_observations",
  "observation_id": 99,
  "source_run_id": 21,
  "message_fingerprint": "...",
  "parser_type": "ocr"
}
```

同一个候选重跑时，如果候选仍未人工确认或驳回，会替换为本次模型选中的证据集合。

## 运行命令

查看当前启用群：

```powershell
.\scripts\run.ps1 -Task wechat-groups-list
```

采集指定日期的群截图：

```powershell
.\scripts\run.ps1 -Task wechat-groups-capture -ReportDate 2026-06-17
```

测试时只跑前 1 个群：

```powershell
.\scripts\run.ps1 -Task wechat-groups-capture -ReportDate 2026-06-17 -WechatLimit 1
```

将截图 OCR 并写入 active 群消息：

```powershell
.\scripts\run.ps1 -Task wechat-messages-parse -ReportDate 2026-06-17
```

处理指定采集 run：

```powershell
.\scripts\run.ps1 -Task wechat-messages-parse -WechatCaptureRunId 21
```

增量识别新需求：

```powershell
.\scripts\run.ps1 -Task wechat-demand-intake -WechatIntakeMode incremental
```

生产小时级同步入口：

```powershell
.\scripts\run.ps1 -Task wechat-hourly-sync
```

指定固定华为手机：

```powershell
.\scripts\run.ps1 -Task wechat-hourly-sync -WechatSerial APH0219701010623
```

`wechat-hourly-sync` 会串联执行：

```text
wechat-groups-capture
-> wechat-messages-parse
-> wechat-demand-intake -WechatIntakeMode incremental
```

测试时只处理前 1 个群，并带 20 条历史上下文：

```powershell
.\scripts\run.ps1 -Task wechat-demand-intake -WechatIntakeMode incremental -WechatLimit 1 -WechatContextSize 20
```

保留的 batch 模式用于人工回放某个 capture run：

```powershell
.\scripts\run.ps1 -Task wechat-demand-intake -WechatCaptureRunId 21
```

## 小时级调度

建议生产配置：

```text
DEVICE_POOL_ENABLED=true
DEVICE_LOCK_WAIT_SECONDS=3600
DEVICE_LOCK_POLL_SECONDS=5
WECHAT_DEVICE_SERIAL=APH0219701010623
WECHAT_SYNC_PAGES=12
WECHAT_SYNC_PARSE_MODE=ocr
WECHAT_SYNC_CONTEXT_SIZE=30
WECHAT_SCHEDULER_ENABLED=true
WECHAT_SCHEDULER_START_TIME=08:00
WECHAT_SCHEDULER_END_TIME=19:00
WECHAT_SCHEDULER_INTERVAL_MINUTES=60
WECHAT_SCHEDULER_WORKDAYS=1,2,3,4,5
```

通过运行时配置启用：

```powershell
.\scripts\run.ps1 -Task config `
  -ConfigSet WECHAT_DEVICE_SERIAL=APH0219701010623 `
  -ConfigSet WECHAT_SCHEDULER_ENABLED=true `
  -ConfigSet WECHAT_SCHEDULER_START_TIME=08:00 `
  -ConfigSet WECHAT_SCHEDULER_END_TIME=19:00 `
  -ConfigSet WECHAT_SCHEDULER_INTERVAL_MINUTES=60 `
  -ConfigSet WECHAT_SCHEDULER_WORKDAYS=1,2,3,4,5
```

单独启动微信采集调度进程：

```powershell
.\scripts\run.ps1 -Task scheduler -SchedulerRoles wechat
```

也可以用队列 worker 一键启动，`workers-start` 会额外启动一个 `wechat` 角色进程：

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

调度时间窗是闭区间。以上配置会在工作日执行：

```text
08:00, 09:00, 10:00, 11:00, 12:00, 13:00,
14:00, 15:00, 16:00, 17:00, 18:00, 19:00
```

微信群采集会先获取全局设备锁。若同一台手机正在被 v2 crawl、profile 或其他 ADB 采集任务占用，`wechat-hourly-sync` 会等待锁释放后再操作手机，避免不同任务同时点击、滑动、截图导致数据污染。

## 增量识别逻辑

增量识别不是按天重复识别，而是按群持续推进水位：

```text
读取 offset
-> 取 offset 后的新 active 消息
-> 带上前 N 条 active 消息作为上下文
-> 模型判断新消息中是否出现新需求
-> 输出候选和 evidence_orders
-> 写入 ops_platform
-> 成功后推进 offset
```

模型输入中消息会标记：

```text
scope = context  历史上下文，只用于判断连续性
scope = new      水位后的新消息，可触发新需求
```

模型输出候选必须至少引用一条 `new` 消息，否则会被丢弃，避免只凭历史上下文重复创建需求。

## 去重和状态口径

消息去重：

```text
wechat_message_observations.message_fingerprint
```

候选去重：

```text
external_candidate_id
```

当前候选 ID 主要基于：

```text
source_key
目标日期/识别窗口
模型选中的证据消息集合
首条证据消息
```

展示当前 AI 新需求建议时推荐筛选：

```sql
SELECT *
FROM ops_platform.demand_intake_candidates
WHERE source_app = 'crawler'
  AND status = 'pending'
  AND external_capture_run_id LIKE 'intake:%'
ORDER BY updated_at DESC;
```

旧链路或测试数据通常表现为：

```text
external_capture_run_id IS NULL
external_capture_run_id LIKE 'capture:%'
external_capture_run_id LIKE 'capture:test:%'
```

这些不应作为当前生产候选展示。

## 常用排查 SQL

查看每个群的识别水位：

```sql
SELECT source_name, last_observation_id, last_message_time, last_intake_run_at
FROM crawler_app.wechat_demand_intake_offsets
ORDER BY updated_at DESC;
```

查看最近增量识别运行：

```sql
SELECT id, source_name, from_observation_id, to_observation_id,
       context_count, new_message_count, candidate_count, status, finished_at
FROM crawler_app.wechat_demand_intake_runs
ORDER BY id DESC
LIMIT 20;
```

查看 active 群消息数量：

```sql
SELECT source_name, message_date, COUNT(*) AS cnt,
       COUNT(DISTINCT message_fingerprint) AS fingerprint_cnt
FROM crawler_app.wechat_message_observations
WHERE status = 'active'
  AND message_type = 'text'
GROUP BY source_name, message_date
ORDER BY message_date DESC, source_name;
```

查看当前 AI 候选：

```sql
SELECT external_capture_run_id, source_chat_name, demand_title,
       business_category, secondary_category, confidence, status, updated_at
FROM ops_platform.demand_intake_candidates
WHERE source_app = 'crawler'
  AND status = 'pending'
  AND external_capture_run_id LIKE 'intake:%'
ORDER BY updated_at DESC;
```

查看某个候选的证据链：

```sql
SELECT evidence_order, message_time, sender_name, message_text, evidence_reason
FROM ops_platform.demand_candidate_evidence
WHERE candidate_id = '<candidate_id>'
ORDER BY evidence_order;
```

## 当前注意事项

1. `wechat-messages-parse` 默认使用 OCR，不默认使用模型视觉识别。
2. `wechat-demand-intake` 的增量模式才是持续生产推荐模式。
3. 第二次立刻跑增量模式没有新消息时会 `skipped`，这是防重复的正常表现。
4. 如果要回放历史消息，需要重置 `wechat_demand_intake_offsets`，或使用 batch 模式单独测试。
5. 管理端展示当前 AI 候选时，应过滤 `external_capture_run_id LIKE 'intake:%'`。
