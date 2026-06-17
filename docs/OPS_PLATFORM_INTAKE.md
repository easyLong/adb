# ops_platform 需求采集接入链路

## 简化后的生产边界

ops_platform 负责维护业务主数据和 AI 候选结果，ADB 只负责采集与写入候选。

主链路：

1. ADB 从 `ops_platform.wechat_group_configs` 读取启用的微信群配置。
2. ADB 按 `sort_order` 顺序打开群，采集聊天记录和截图证据。
3. AI 根据聊天记录识别候选需求，并参考 `business_category_secondary_categories` 做业务大类/二级分类适配。
4. ADB 将候选需求写入 `ops_platform.demand_intake_candidates`，将原始聊天证据写入 `ops_platform.demand_candidate_evidence`。
5. 管理端人工确认后，再生成正式 `requirements`、`requirement_items`、`tasks`。

ADB 不直接写正式需求表。

## 元数据表

### `wechat_group_configs`

微信群采集配置表，是 ADB 的入口清单。

核心字段：

```text
group_id                   群 ID，可为空
group_name                 群名称
source_key                 稳定来源 key，由 group_id 或 group_name 生成
customer_id                基金/客户 ID，指向 customers.id
contact_context_config_id  对接人上下文，可为空，指向 contact_context_configs.id
business_platform          平台，可为空；为空时优先使用 contact_context_configs.business_platform
status                     active / inactive
collect_enabled            是否参与自动采集
sort_order                 采集顺序
```

### `customers`

基金详情表。当前项目里基金主数据复用 `customers`。

### `business_category_secondary_categories`

业务大类与二级分类映射表。AI 识别需求时应优先从这里读取合法分类关系。

## ADB 读取方式

```python
from apps.finance_crawler.crawler_app.storage.ops_platform import (
    list_wechat_group_configs_once,
)

groups = list_wechat_group_configs_once()
for group in groups:
    print(group.group_name, group.customer_name, group.contact_name, group.business_platform)
```

CLI 查看当前启用群：

```powershell
.\scripts\run.ps1 -Task wechat-groups-list
```

按配置顺序采集指定日期：

```powershell
.\scripts\run.ps1 -Task wechat-groups-capture -ReportDate 2026-06-17
```

测试时只跑前 1 个群、每个群只截首屏：

```powershell
.\scripts\run.ps1 -Task wechat-groups-capture -ReportDate 2026-06-17 -WechatLimit 1 -WechatPages 0
```

采集输出会写入：

```text
exports/wechat/<群名称>/<日期>/
exports/wechat/_batches/<日期>/<批次时间>/manifest.json
```

同时会在 `crawler_app.wechat_capture_runs` 记录采集批次，在 `crawler_app.wechat_message_observations` 为每张截图生成一条 observation。

## 写入候选

采集和识别完成后，用群配置补齐候选需求的基金、对接人和平台：

```python
from apps.finance_crawler.crawler_app.storage.ops_platform import (
    OpsDemandCandidate,
    OpsDemandEvidence,
    candidate_with_wechat_group_config,
    upsert_ops_demand_candidate_once,
)

candidate = candidate_with_wechat_group_config(
    OpsDemandCandidate(
        external_candidate_id="crawler:run-1:1",
        external_capture_run_id="capture:1",
        business_category="设计",
        secondary_category="banner新设计",
        tertiary_category="活动页头图",
        demand_title="活动 banner 设计",
        demand_content="需要设计一张活动 banner。",
        confidence=0.86,
        evidences=[
            OpsDemandEvidence(
                external_evidence_id="obs:1",
                evidence_order=1,
                sender_name="李四",
                message_text="帮忙做一版活动 banner",
                screenshot_path="exports/wechat/demo.png",
            )
        ],
    ),
    group_config,
)

candidate_id = upsert_ops_demand_candidate_once(candidate)
```

写入后会自动带上：

```text
external_source_key
external_chat_id
source_chat_name
raw_customer_name
raw_owner_name
raw_business_platform
matched_customer_id
matched_contact_context_id
matched_business_platform
match_confidence
match_reason
```

## 幂等规则

候选需求：

```text
source_app + external_candidate_id
```

证据链：

```text
candidate_id + external_evidence_id
```

重复跑会更新未处理候选；已人工确认或驳回的候选不会被自动识别结果覆盖。

## 兼容说明

`source_contact_contexts` 是上一版来源上下文绑定表，保留用于历史兼容。新的 ADB 采集主链路不再依赖它。
