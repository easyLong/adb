# App 采集动作模板经验库

这份文档记录“哪个 App 采集哪种数据，用什么动作模板、什么技术手段、什么证据算成功”。它比单次跑通更重要，后续新增任务时优先查这里，再决定是否新增或调整 `capture_action_profiles` / `profile_action_profiles`。

## 1. 模板选择原则

动作模板不要只按任务名选，要按下面维度一起选：

```text
app_type + task_type + requested_fields
```

字段组合相同，不代表动作相同。比如支付宝、蚂蚁财富、理财通都可能采集 `fans_count`，但页面结构、是否需要 OCR、是否需要跳转详情页都不同。

## 2. Profile/KOL 主页型任务

主页型任务走 `profile_trigger_configs` -> `profile_metric_sources` -> `profile_metric_runs` -> `profile_metric_writebacks`。

| App | 任务 | 字段 | 动作模板 | 技术手段 | 成功证据 |
| --- | --- | --- | --- | --- | --- |
| `tenpay` | `profile_daily_metrics` | `fans_count` | `tenpay_profile_daily_metrics_v1` | force-stop 清状态；打开主页；UI + OCR 采集；按三列计数器几何关系识别粉丝列；首页过万近似值点击粉丝详情；OCR 读取 `TA的粉丝(xxxxx人)`；账号锚点校验 | 非过万：数字与“粉丝”标签同列且 `source=ui_home`；过万：`metrics.fans.source=exact_page`、`exact_required=true`、`exact_used=true`、`account_verified=true` |
| `tenpay` | `profile_daily_metrics` | `read_count` | `tenpay_profile_daily_metrics_v1` | 打开主页；扫描最近帖子；点击详情；UI + OCR 读取阅读数；最近 3 条聚合取最大 | `metrics.posts` 有详情截图和阅读数，`read_count` 写入 |
| `alipay` | `profile_daily_metrics` | `fans_count` | `alipay_profile_daily_metrics_v1` | 打开主页；优先 UI 控件读取；过万近似值进入精确粉丝页 | `fans_count` 成功，必要时记录 exact page |
| `antfortune` | `profile_daily_metrics` | `fans_count` | `antfortune_profile_daily_metrics_v1` | 打开主页；优先 UI 控件读取；过万近似值进入精确粉丝页 | `fans_count` 成功，必要时记录 exact page |
| `alipay` / `antfortune` | `profile_daily_metrics` | `read_count` | 对应 profile 模板 | 打开主页；扫描最近帖子；点击详情；读取阅读数；最近 3 条聚合取最大 | `read_count` 写入 |

### Tenpay 粉丝数已验证动作

本次验证通过的关键动作：

```text
reset_app
  -> open_profile
  -> capture_home(ui_controls + screenshot + ocr)
  -> detect_tenpay_counter_layout
  -> if fans is abbreviated: tap middle fans counter
  -> capture_exact_fans_page(ui_controls + screenshot + ocr)
  -> parse TA的粉丝(xxxxx人)
  -> verify expected account anchor
  -> record metrics.fans evidence
```

关键经验：

- 首页 `2.1万` 这种值只能作为候选值，不能直接当最终成功值。
- 粉丝过万时必须进入粉丝详情页读取精确整数。
- 理财通 OCR 可能把“粉丝”误识别成“关注”，也可能漏掉中间粉丝数字，因此需要通过主页三列计数器布局兜底：左列关注数，中列粉丝数，右列获评赞。
- 三列指标页不能用“粉丝标签附近最近数字”做宽松匹配。必须把 OCR bounds 统一成 `left/top/right/bottom`，只接受数字与“粉丝”标签同列或水平重叠的候选。
- 如果“粉丝”标签可见，但同列粉丝数字缺失，不要用左侧“关注”或右侧“获赞/已获评赞”兜底；本次结果应记为未检测到粉丝数，等待重采或人工复核。
- 如果 OCR 漏掉“粉丝”标签但三列数字完整，且页面存在理财通主页锚点，可以按布局取中列；如果三列数字不完整，不能按布局猜。
- 每个主页任务开始前要 force-stop 对应 App，避免上一条停在粉丝详情页，导致数字串到下一条账号。
- 如果初始页面已经是粉丝详情页，必须能证明它属于当前账号，否则不采信。

## 3. Document/帖子链接型任务

帖子/链接型任务走 `document_trigger_configs` -> `task_submissions` -> `task_executions` -> `writeback_plans`。

| App | 任务 | 字段 | 动作模板 | 技术手段 | 成功证据 |
| --- | --- | --- | --- | --- | --- |
| `alipay` | `initial_check` | `account_name` | `alipay + initial_check + account_name` | 打开链接；UI 控件读取昵称；截图留证 | 回填账号昵称；找不到页面写 `N` |
| `antfortune` | `initial_check` | `account_name` | `antfortune + initial_check + account_name` | 打开链接；UI 控件读取昵称；截图留证 | 回填账号昵称；找不到页面写 `N` |
| `alipay` | `detail` | `account_name,read_count,screenshot` | `alipay + detail` | 打开链接；UI 控件读取账号和阅读数；上传截图到腾讯文档 | 账号、阅读数、截图、备注写回 |
| `antfortune` | `detail` | `account_name,read_count,screenshot` | `antfortune + detail` | 打开链接；UI 控件读取账号和阅读数；上传截图到腾讯文档 | 账号、阅读数、截图、备注写回 |
| `antfortune` | `read_count` | `read_count` | `antfortune + read_count` | 默认直接打开帖子；等待页面渲染；UI 控件读取阅读数；遇到“网络不给力/稍后再试”时 force-stop、打开首页预热、滑动一屏、再打开帖子 | 成功读取 `read_count`；可重试风控页记录恢复证据；最终失败写备注 |
| `tenpay` | `read_count` | `read_count` | `tenpay + read_count` | 打开链接；UI + OCR 读取阅读数；必要时截图辅助解析 | 阅读数写回 |
| `tenpay` | `detail` | `trade_details` | `tenpay + detail` | 打开链接；截图；OCR；滚动；点击详情区域 | 详情字段写入执行结果 |

### Ant Fortune 阅读数已验证动作

日常不要每条都先打开首页预热，太慢，也容易把链路变复杂。当前建议：

```text
open_post_direct
  -> wait_page_status_ready
  -> capture_ui_controls
  -> parse_read_count
  -> if retryable_page:
       force_stop_app
       open_antfortune_home
       wait
       swipe_one_screen
       wait
       reopen_post
       capture_ui_controls
       parse_read_count
```

关键经验：

- “网络不给力”“稍后再试”“重试”“加载失败”属于可重试页面，不应立刻把帖子判成永久失败。
- 常规路径直接打开帖子；只有遇到可重试页面才执行重启 + 首页预热 + 滑动一屏 + 重开帖子。
- “内容不见了”“页面明确不存在”这类不可恢复状态应写失败/备注，不需要无限重试。
- 阅读数任务之间需要随机停顿，配置项是 `READ_COUNT_POST_DELAY_MIN/MAX`。

## 4. 技术手段分层

| 技术手段 | 适用场景 | 风险 | 质量门禁 |
| --- | --- | --- | --- |
| UI 控件 | 页面文字在 uiautomator XML 中可见 | WebView 或 Canvas 页面可能没有目标文本 | 页面状态必须匹配任务类型 |
| OCR | WebView、图片化文本、UI 控件缺失 | 误识别、漏识别、bounds 只有 width/height | 与页面锚点、字段标签、布局关系一起验证 |
| 跳转详情页 | 首页只展示近似值，详情页有精确值 | 点击错、上一页状态污染 | 点击前必须定位字段区域，点击后必须验证详情页语义 |
| 滚动 | 主页最近帖子、长详情页 | 滚过目标、重复候选 | 候选去重，遇到早于目标日期可停止 |
| App 重启 | 页面残留、网络异常、空白页 | 增加耗时 | 关键链路开始前或异常恢复时使用 |
| 截图上传 | 详情任务需要截图回填 | 本地路径不能当最终结果 | 必须走腾讯文档图片上传 |

## 5. 结果证据字段

Profile 粉丝链路会把证据写到 `profile_metric_runs.metrics_json.fans`：

```json
{
  "fans_count": 20665,
  "home_fans_count": 21000,
  "page_state": "fans_detail",
  "source": "exact_page",
  "exact_required": true,
  "exact_used": true,
  "account_verified": true,
  "quality_error": null
}
```

判断优先级：

- `source=exact_page` 且 `exact_used=true`：粉丝详情页精确值。
- `exact_required=true` 但 `exact_used=false`：不能写成功。
- `account_verified=false`：不能采信详情页数字，可能是上一条页面残留。
- `quality_error` 非空：按失败处理，不回填近似值。

## 6. 新增模板时的 checklist

新增 App 或字段组合时，必须回答：

1. 字段在 UI 控件里能不能直接拿到？
2. 是否需要 OCR？
3. 是否需要点击进入二级页面？
4. 是否需要滚动？
5. 是否存在近似值，需要精确化？
6. 页面成功锚点是什么？
7. 字段成功证据是什么？
8. 失败是否可重试，还是终态失败？
9. 写回前如何防止 URL/账号/行错配？

## 7. 主页型实测记录

### 2026-06-07 profile fans smoke test

测试范围只包含主页型，不包含用户给定的帖子链接。

| App | 场景 | 样本 | 结果 | 结论 |
| --- | --- | --- | --- | --- |
| `alipay` | 主页粉丝数，UI 可见 | 钱宝鼓鼓生活甜甜 | 成功，`fans_count=8`，`source=ui_home` | 支付宝主页粉丝数可以走 UI 控件直接采集 |
| `tenpay` | 主页普通粉丝数，三列指标 | 闻基起舞 | 修正：`17` 是关注数，`771` 才是粉丝数；当 OCR 漏掉 `771` 时应失败为 `profile fans count was not detected` | 理财通普通粉丝数必须按“关注 / 粉丝 / 已获评赞”列位取中列；粉丝列数字缺失时不能写相邻列 |
| `tenpay` | 过万粉丝精确化 | 拎壶冲 | 成功，`home_fans_count=21000`，详情页精确值 `20664`，`source=exact_page` | 首页近似值必须点击粉丝详情页读取精确整数 |
| `tenpay` | 慢加载主页 | 拎壶冲/夏小鱼 | 首屏只看到标题时失败；等待 20 秒可看到主页 | 模板已加入“标题页二次等待重采”：初始 8 秒，标题页再等 12 秒 |
| `antfortune` | 主页粉丝数 | 对门老李 | 失败，页面停在“打开支付宝登录 / 密码登录” | 当前设备蚂蚁财富登录态不可用；不是字段解析失败 |

本轮新增规则：

- 理财通首页只看到 `腾讯理财通` 标题时，不直接判失败，先等待并重采。
- 重采也必须包含 OCR，因为理财通主页主体内容常不出现在 UI XML 中。
- 蚂蚁财富主页采集前要先满足登录态；登录页应作为设备/账号环境问题处理。
- 对于“关注 / 粉丝 / 获赞”这类横向指标组，字段提取必须同时满足：页面锚点正确、字段标签正确、数字和标签几何对齐、相邻列不混用。缺少其中任一项时不回填近似值。
