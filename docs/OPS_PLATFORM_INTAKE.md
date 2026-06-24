# ops_platform 需求采集接入链路

本文记录 ADB 项目从微信群持续采集消息、识别需求候选，并写入 `ops_platform` 的当前生产链路。

## 一眼看懂

当前微信链路已经收敛成两层：

```text
事实层
ops_platform 群配置
-> ADB 打开微信群并按日期/页面采集截图
-> OCR 原始行入库
-> 规整成稳定群消息

业务层
active 群消息
-> 增量取新消息 + 少量历史上下文
-> 模型识别需求候选
-> 写入 ops_platform 候选需求和证据链
```

可以把它理解成：

- `wechat_capture_runs`、`wechat_ocr_observations`、`wechat_message_observations` 负责“尽量完整保留群消息事实”
- `demand_intake_candidates`、`demand_candidate_evidence` 负责“把事实消息变成可审核的需求候选”

当前生产推荐口径：

- 微信消息解析默认使用 `ocr`，不默认使用模型视觉识别
- 需求识别只消费 `wechat_message_observations.status = 'active'`
- 持续生产优先跑 `incremental`，不是每天全量回扫
- 人工已 `confirmed/rejected` 的候选不应被自动覆盖

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

## 表级数据流

```text
ops_platform.group_contact_mappings + ops_platform.customers
-> crawler_app.wechat_capture_runs
-> crawler_app.wechat_ocr_observations
-> crawler_app.wechat_message_observations
-> crawler_app.wechat_demand_intake_offsets / crawler_app.wechat_demand_intake_runs
-> ops_platform.demand_intake_candidates
-> ops_platform.demand_candidate_evidence
```

每一层的职责是：

| 层 | 表 | 作用 |
| --- | --- | --- |
| 群元数据 | `ops_platform.group_contact_mappings`, `ops_platform.customers` | 定义“采谁”，并提供客户、对接人、平台等上下文 |
| 采集运行 | `crawler_app.wechat_capture_runs` | 记录一次截图采集 run，包含群、日期、截图目录、状态 |
| OCR 原始事实 | `crawler_app.wechat_ocr_observations` | 保存每张截图的 OCR 原始行，主要用于追溯和排错 |
| 稳定消息流 | `crawler_app.wechat_message_observations` | 保存最终可消费的文本消息，按 `message_fingerprint` 幂等去重 |
| 增量水位 | `crawler_app.wechat_demand_intake_offsets` | 记录每个群上次识别推进到哪一条消息 |
| 识别运行 | `crawler_app.wechat_demand_intake_runs` | 记录一次增量识别看了哪些消息、带了多少上下文、产出多少候选 |
| 候选需求 | `ops_platform.demand_intake_candidates` | AI 识别出的待审核需求候选 |
| 候选证据 | `ops_platform.demand_candidate_evidence` | 候选需求关联的原始群消息证据链 |

## 运行入口

日常生产只需要记住一条命令：

```powershell
.\scripts\run.ps1 -Task wechat-hourly-sync
```

它会串联执行：

```text
wechat-groups-capture
-> wechat-messages-parse
-> wechat-demand-intake -WechatIntakeMode incremental
```

拆开单独跑时：

- `wechat-groups-capture`：只采截图
- `wechat-messages-parse`：把截图转成 `active` 群消息
- `wechat-demand-intake -WechatIntakeMode incremental`：只处理水位之后的新消息

生产排查时，建议按这个顺序看：

1. `wechat_capture_runs` 有没有成功采到截图
2. `wechat_message_observations` 有没有生成 `active` 文本消息
3. `wechat_demand_intake_offsets` 有没有推进
4. `ops_platform.demand_intake_candidates` 有没有产出新的 `pending` 候选

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

## 如何配置“采集谁”

当前“采集谁”不再从 `crawler_app.wechat_chats` 维护，而是直接读取：

```text
ops_platform.group_contact_mappings
ops_platform.customers
```

系统实际筛选条件是：

```sql
mapping.status = 'active'
AND mapping.collect_enabled = 1
AND mapping.deleted_at IS NULL
AND customer.deleted_at IS NULL
```

也就是说，只有“有效 + 启用采集”的群映射，才会进入 `wechat-groups-list` 和 `wechat-hourly-sync`。

### 新增一个群的最小操作

新增群时，至少要保证两件事：

1. `customers` 里已经有对应的 `customer_code`
2. `group_contact_mappings` 里插入一条启用采集的群映射

最小必填字段建议是：

```text
group_key
group_name
customer_code
contact_name
business_platform
status
collect_enabled
```

推荐值：

```text
status = active
collect_enabled = 1
```

### 字段怎么理解

| 字段 | 含义 | 配置建议 |
| --- | --- | --- |
| `group_key` | 群稳定主键 | 推荐使用稳定 ID；如果没有稳定 ID，至少保证同一个群长期不变 |
| `group_name` | 微信里真实可搜索的群名称 | ADB 进微信后就是靠它搜群，尽量和微信显示完全一致 |
| `customer_code` | 客户编码 | 必须能在 `customers` 表里关联到有效客户 |
| `contact_name` | 对接人 | 后续会写进候选需求上下文；同群多对接人可以多行配置 |
| `business_platform` | 平台 | 比如基金平台、业务线、渠道；会进入候选需求元数据 |
| `status` | 配置状态 | 生产建议固定 `active` |
| `collect_enabled` | 是否参与微信采集 | 打开为 `1`，暂停采集就改成 `0` |

### 最常见的新增方式

如果一个群只服务一个客户、一个平台，直接加一行：

```sql
INSERT INTO ops_platform.group_contact_mappings (
    group_key,
    group_name,
    customer_code,
    contact_name,
    business_platform,
    status,
    collect_enabled
) VALUES (
    'wx_group_demo_001',
    '向量-汇添富沟通小分队',
    'HTF001',
    '张三',
    '微信',
    'active',
    1
);
```

### 已有基金信息，只新增一个群

如果 `customers` 里已经有这只基金，只需要新增 `group_contact_mappings`，不需要再插 `customers`。

先确认基金已经存在：

```sql
SELECT id, customer_code, customer_name, status, deleted_at
FROM ops_platform.customers
WHERE customer_code = 'HTF001';
```

确认存在且未删除后，直接插入群映射：

```sql
INSERT INTO ops_platform.group_contact_mappings (
    id,
    group_key,
    group_name,
    contact_name,
    business_platform,
    collect_enabled,
    status,
    remark,
    deleted_at,
    customer_code
) VALUES (
    UUID(),
    'wx_group_htf_new_001',
    '汇添富设计需求响应群',
    '张三',
    '微信',
    1,
    'active',
    '已有基金信息，新增微信群采集配置',
    NULL,
    'HTF001'
);
```

如果这个新群也有多个对接人，就继续追加多行，保持：

```text
group_key 相同
group_name 相同
customer_code 相同
```

只修改 `contact_name`。

### 一个群多个对接人，怎么配

同一个群如果只是多个对接人、但客户和平台相同，可以插入多行，`group_key` 和 `group_name` 保持一致，`contact_name` 不同。

系统读取时会按：

```text
group_key + group_name + customer_code + customer_name + business_platform
```

做聚合，并把多个 `contact_name` 用逗号拼起来。

也就是说：

- 同群 + 同客户 + 同平台 + 多对接人：会合并成一条采集来源
- 同群 + 不同客户，或同群 + 不同平台：会变成多条采集来源

这一点很重要，新增群时如果配了多客户/多平台，要明确这是有意为之，不然同一个群会被系统当成多个来源上下文。

### 新增后怎么验证

先查元数据是否生效：

```sql
SELECT
    mapping.group_key,
    mapping.group_name,
    mapping.customer_code,
    customer.customer_name,
    mapping.contact_name,
    mapping.business_platform,
    mapping.status,
    mapping.collect_enabled
FROM ops_platform.group_contact_mappings mapping
JOIN ops_platform.customers customer
  ON customer.customer_code = mapping.customer_code
 AND customer.deleted_at IS NULL
WHERE mapping.group_name = '向量-汇添富沟通小分队'
  AND mapping.deleted_at IS NULL;
```

再用项目命令确认会不会被采集进来：

```powershell
.\scripts\run.ps1 -Task wechat-groups-list
```

如果列表里没有出现，优先检查：

- `group_name` 是否和微信里真实群名一致
- `customer_code` 是否能关联到 `customers`
- `status` 是否为 `active`
- `collect_enabled` 是否为 `1`
- 是否被误删，`deleted_at` 不为空

### 临时停用一个群

不建议删数据，建议直接停用：

```sql
UPDATE ops_platform.group_contact_mappings
SET collect_enabled = 0
WHERE group_key = 'wx_group_demo_001';
```

重新启用：

```sql
UPDATE ops_platform.group_contact_mappings
SET collect_enabled = 1,
    status = 'active'
WHERE group_key = 'wx_group_demo_001';
```

### 改群名怎么处理

如果只是微信群改名了，优先更新 `group_name`，不要新建一条新群配置；这样历史 `source_key/group_key` 还能连续。

只有在“这个群在业务上已经不是原来的群”时，才建议换新的 `group_key`。

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
