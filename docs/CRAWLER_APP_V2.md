# crawler_app v2 Design

`crawler_app` is the new document-driven crawler database and code path. The
project now defaults to this database, so legacy workflow tables and v2 tables
can live in the same empty database while the old database remains untouched.

For day-to-day setup and auto-run operations, see
[`V2_AUTO_RUN_OPERATION.md`](V2_AUTO_RUN_OPERATION.md).

## Database

The new database name defaults to `crawler_app`.

Credentials are shared with the previous deployment:

```powershell
$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "..."
$env:MYSQL_DATABASE = "crawler_app"
```

Initialize the full project database, including legacy workflow tables and v2
tables:

```powershell
.\scripts\run.ps1 -Task db
```

Initialize only the v2 tables:

```powershell
.\scripts\run.ps1 -Task crawler-app-db
```

This does not create or modify the old database.

## Core Data Flow

```text
Tencent Docs URL
-> document source adapter
-> document sheet snapshot
-> header title resolution
-> normalized source rows
-> crawl task submissions
-> generic task runner
-> app execution
-> writeback plans
-> auditable corrections
```

This flow is the post/link document path. It is used when each spreadsheet row
contains a concrete `post_url`, such as initial check, detail, read count,
comment count, screenshot, and remark writeback.

Homepage/KOL work uses the DB-first KOL path because the crawl target and
actions are different:

```text
kol_daily_db_pipeline
-> profile_metric_sources
-> profile_metric_runs
-> kol_daily_metrics
```

The current default KOL daily path is `kol_daily_db_pipeline`. It runs at
`KOL_DAILY_CRAWL_TIME`, initializes `kol_daily_metrics`, syncs Tenpay external
read counts for the configured lookback window, opens each `homepage_url` for
today's profile metrics, and stores the result in MySQL. It is not a
`document_trigger_configs` job.

## Online Document Commands

The v2 path now supports the online-document post workflows used most often:

- `initial_check`: open the post link in the target App and write back the
  account nickname, or `N` when the page is clearly not found.
- `detail`: open the post link in the target App and write back account
  nickname, read count, comment count, screenshot path, and remarks when those
  fields are present in the resolved sheet header.
- `read_count`: a narrower read-count-only workflow.

Submit source rows into `task_submissions`:

```powershell
.\scripts\run.ps1 -Task v2-read-count-submit -TencentDocUrl "https://docs.qq.com/sheet/..." -ReportDate 2026-06-04
.\scripts\run.ps1 -Task v2-initial-check-submit -TencentDocUrl "https://docs.qq.com/sheet/..." -ReportDate 2026-06-04
.\scripts\run.ps1 -Task v2-detail-submit -TencentDocUrl "https://docs.qq.com/sheet/..." -ReportDate 2026-06-04
```

Crawl pending tasks and create `task_executions` plus `writeback_plans`:

```powershell
.\scripts\run.ps1 -Task v2-read-count-crawl
.\scripts\run.ps1 -Task v2-initial-check-crawl
.\scripts\run.ps1 -Task v2-detail-crawl
```

Apply pending writeback plans:

```powershell
.\scripts\run.ps1 -Task v2-read-count-writeback
.\scripts\run.ps1 -Task v2-initial-check-writeback
.\scripts\run.ps1 -Task v2-detail-writeback
```

Run the whole chain in one command:

```powershell
.\scripts\run.ps1 -Task v2-read-count -TencentDocUrl "https://docs.qq.com/sheet/..." -ReportDate 2026-06-04
.\scripts\run.ps1 -Task v2-initial-check -TencentDocUrl "https://docs.qq.com/sheet/..." -ReportDate 2026-06-04
.\scripts\run.ps1 -Task v2-detail -TencentDocUrl "https://docs.qq.com/sheet/..." -ReportDate 2026-06-04
```

## Business Fields

Business fields are fixed, but their columns may move between sheets.

Input fields:

- `post_url`
- `account_name`
- `post_time`

Output fields:

- `read_count`
- `comment_count`
- `like_count`
- `article_title`
- `screenshot`
- `remark`
- `check_result`

The first v2 rule is: business logic uses field names, never hard-coded column
indexes. Each submission stores the column mapping produced from the sheet
header so later writeback and correction can target the same resolved fields.

## Tables

- `documents`: online document identity.
- `document_sheets`: one sheet/tab, usually one business date.
- `column_mappings`: resolved field-to-column mapping for a header hash.
- `source_rows`: normalized spreadsheet rows.
- `task_submissions`: submitted tasks with stable dedupe keys.
- `capture_action_profiles`: configurable mapping from app/task/field combo to
  ADB capture actions.
- `derived_records`: optional records discovered while crawling, such as a
  homepage link related to the current post.
- `document_task_configs`: business configuration for what a given online
  document should do.
- `task_executions`: execution attempts.
- `writeback_plans`: field-level pending/applied writebacks.
- `corrections`: auditable temporary data fixes.

## Execution Boundary

Crawler execution is task-type driven:

- `crawler_app.tasks.handlers` registers one handler per task type.
- A handler provides `runtime`, `crawl`, `writeback_values`, and `metrics`.
- `runtime` prepares the ADB execution environment. This project only runs
  Android App crawlers through ADB/uiautomator2.
- The ADB runtime resolves one ready phone before a batch starts. The selected
  device may be connected by USB or WiFi, and the execution summary records the
  chosen serial, transport, model, product, and device name.
- `crawler_app.workflows.execution.crawl_pending_tasks` owns the common loop:
  claim pending submissions, start executions, call the handler, store results,
  and create field-level writeback plans.
- Business workflows such as `read_count` keep their submit/writeback commands,
  but no longer duplicate the execution loop.

## ADB Device Boundary

Device selection is centralized in `utils.device_health`:

- `list_adb_devices()` parses `adb devices -l` into structured `AdbDevice`
  records.
- `prepare_adb_device()` selects and verifies one ready device, including boot
  completion.
- USB devices are identified from normal ADB serials with `transport_id`.
- WiFi devices are identified from `ip:port` serials.
- If `DEVICE_SERIAL` is set, that exact device is required.
- If `DEVICE_SERIAL` is empty, the runtime auto-selects a single ready device;
  multiple ready devices still require `DEVICE_SERIAL` to avoid writing data
  from the wrong phone.
- WiFi reconnect uses configured `DEVICE_SERIAL`, the last ready WiFi serial,
  and `adb mdns services` candidates when auto reconnect is enabled.

Crawler code should call `current_serial()` or use the prepared session instead
of choosing devices directly.

## Data Source Boundary

Crawler intake reads source documents through `DocumentSource`:

- `DocumentSource.load_sheet(...)` returns a `DocumentSheetSnapshot`.
- `TencentDocsSource` is the current online document adapter.
- Intake code consumes only the snapshot: rows, sheet identity, document
  identity, start row, and business date.
- Field recognition, source-row normalization, task submission, and writeback
  planning do not call Tencent Docs APIs directly.

This keeps online document changes isolated from the ADB task chain.

## Document Task Config

Business should not have to remember whether a document is for initial check,
detail, or read-count collection. Store that intent in `document_task_configs`.
One config belongs to the base online document, not to one daily sheet.

Each row answers:

```text
Which online document?
Which task_type?
Which business fields should be written back?
Which sheet/date selection mode?
Is this config active?
```

For Tencent Docs, the identity is split like this:

```text
doc_url  = base document URL, for example https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm
file_id  = DYm1aSG9nb3NHVWZm
sheet_id = optional fallback tab only
```

If the business gives a URL with `?tab=...`, v2 stores the base `doc_url` and
keeps that `sheet_id` only as a fallback. Normal daily runs should pass
`-ReportDate`, and v2 will choose the matching date sheet inside the same
base document.

Supported `sheet_selector_json` modes:

| Mode | Meaning |
| --- | --- |
| `date_sheet` | Select the sheet matching `-ReportDate`, such as `0604`, `06-04`, or `2026-06-04`. |
| `fixed_sheet` | Select one fixed `sheet_id`. |
| `linked_tab` | Use the `tab=` sheet from the configured URL as fallback. |
| `sheet_title` | Select a sheet by exact title. |
| `sheet_title_contains` | Select a sheet by title keyword. |
| `sheet_group` | Select from a configured list of sheet IDs. |

Create or update a config:

```powershell
.\scripts\run.ps1 -Task v2-doc-config-set `
  -DocumentConfigKey "redsoil_daily_initial_check" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm?tab=qlmz7y" `
  -DocumentTaskType "initial_check" `
  -DocumentFields "account_name" `
  -DocumentSheetMode "date_sheet" `
  -DocumentDescription "Redsoil daily initial check"
```

Create a config for a fixed functional sheet in the same base document:

```powershell
.\scripts\run.ps1 -Task v2-doc-config-set `
  -DocumentConfigKey "redsoil_fixed_detail" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentTaskType "detail" `
  -DocumentFields "account_name,read_count,screenshot" `
  -DocumentSheetMode "fixed_sheet" `
  -DocumentSheetId "qlmz7y"
```

List active configs:

```powershell
.\scripts\run.ps1 -Task v2-doc-config-list
```

Check one config without submitting tasks:

```powershell
.\scripts\run.ps1 -Task v2-doc-config-check -DocumentConfigKey "redsoil_daily_initial_check"
```

Submit tasks from a config:

```powershell
.\scripts\run.ps1 -Task v2-doc-config-submit -DocumentConfigKey "redsoil_daily_initial_check"
```

## Submit Trigger Config

`document_trigger_configs` is the v2 submit-worker configuration. It is the
recommended shape for recurring online documents because one document/sheet
selection can fan out to multiple task types.

The boundary is:

```text
submit worker
-> read configured online document/sheet
-> resolve columns by header title
-> normalize rows
-> keep the first row for each URL
-> create/update task_submissions for each active binding

crawl workers and writeback workers run independently from their own queues
```

Tables:

- `document_trigger_configs`: one trigger source, including base URL, sheet
  selector, status, due time, and per-config scan interval.
- `document_trigger_bindings`: one or more task bindings for a trigger, such
  as `initial_check` and `detail`.
- `submit_runs`: audit log for each manual or scheduled submit pass.

Create a daily initial-check trigger for the common case: same base URL and
date-like sheets. Initial check is time-sensitive, so it must target today's
sheet.

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_initial_check" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentSheetMode "date_sheet" `
  -SubmitTargetDateOffsetDays 0 `
  -SubmitScanIntervalSeconds 600 `
  -DocumentDescription "Redsoil daily sheets"

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_initial_check" `
  -DocumentTaskType "initial_check" `
  -DocumentFields "account_name"
```

Create a separate daily detail trigger for yesterday's sheet:

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_detail" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentSheetMode "date_sheet" `
  -SubmitTargetDateOffsetDays -1 `
  -SubmitScanIntervalSeconds 600

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_detail" `
  -DocumentTaskType "detail" `
  -DocumentFields "account_name,read_count,screenshot"
```

Create separate fixed-function triggers when the same base URL has different
sheets doing different jobs:

```powershell
.\scripts\run.ps1 -Task v2-trigger-set `
  -DocumentConfigKey "redsoil_fixed_initial_check" `
  -TencentDocUrl "https://docs.qq.com/sheet/DYm1aSG9nb3NHVWZm" `
  -DocumentSheetMode "fixed_sheet" `
  -DocumentSheetId "qlmz7y"

.\scripts\run.ps1 -Task v2-trigger-bind `
  -DocumentConfigKey "redsoil_fixed_initial_check" `
  -DocumentTaskType "initial_check" `
  -DocumentFields "account_name"
```

Manual submit for one configured trigger:

```powershell
.\scripts\run.ps1 -Task v2-trigger-submit -DocumentConfigKey "redsoil_daily" -ReportDate 0604
```

For scheduled `date_sheet` triggers, `-SubmitTargetDateOffsetDays` controls the
automatic date selection. Use `0` for today's initial check and `-1` for
yesterday's detail run. Initial-check bindings are rejected unless the offset is
`0`.

Run one due submit-worker pass:

```powershell
.\scripts\run.ps1 -Task v2-submit-worker-once
```

Run it as part of the normal long-running scheduler:

```powershell
.\scripts\run.ps1 -Task config -ConfigSet SUBMIT_WORKER_INTERVAL_SECONDS=300
.\scripts\run.ps1 -Task scheduler
```

`SUBMIT_WORKER_INTERVAL_SECONDS` controls how often the scheduler checks for
due trigger configs. Each trigger still controls its own document scan cadence
through `scan_interval_seconds`.

URL handling rule:

- `doc_url` is stored as the base Tencent Docs URL without `?tab=...`.
- The `tab=` value is only a fallback for `linked_tab` configs.
- Date-driven documents should use `date_sheet`.
- Fixed functional sheets should use `fixed_sheet`, `sheet_title`, or
  `sheet_title_contains`.

`date_sheet` trigger behavior:

- A trigger submit pass selects every sheet whose title contains the target
  date token, such as `0605`, `06-05`, `20260605`, or `2026-06-05`.
- Each matched sheet is submitted independently, so duplicate URL filtering
  stays scoped to one sheet.
- One `submit_runs` row records the whole pass. For multiple sheets,
  `submit_runs.sheet_id` is `multi:<count>`, `sheet_title` is a compact summary,
  and `summary_json.sheets` contains the per-sheet `sheet_id`, `sheet_title`,
  row counts, duplicate counts, and submitted task counts.

Duplicate URL rule:

- For one sheet submit pass, only the first row for a URL creates tasks.
- Later rows with the same URL are counted as skipped duplicates.
- Task dedupe uses document, sheet, URL, and task type; it does not depend on
  row position.

Submit tasks for a specific daily sheet in the configured base document:

```powershell
.\scripts\run.ps1 -Task v2-doc-config-submit `
  -DocumentConfigKey "redsoil_daily_initial_check" `
  -ReportDate 2026-06-04
```

Run submit, crawl, and writeback from a config:

```powershell
.\scripts\run.ps1 -Task v2-doc-config-run -DocumentConfigKey "redsoil_daily_initial_check"
```

`DocumentFields` is a comma-separated list of fixed business field names, such
as `account_name`, `read_count`, `comment_count`, `screenshot`, `remark`, or
`check_result`. v2 stores those fields in each submission as
`requested_fields`, and the generic runner only creates writeback plans for
those fields.

`remark` is always allowed through writeback filtering, even when it is not
listed in `DocumentFields`. This keeps task-level error notes visible by
default. For detail tasks, a clearly missing or deleted post writes
`account_name=N`, `read_count=N`, and `remark=<reason>` when those columns are
available in the current sheet header.

Remarks describe the current run outcome. A successful crawl writes
`remark=成功`; a clearly missing page writes the missing-page reason; a final
failed crawl writes `remark=失败：<reason>`. Transient failures that will still
be retried do not write back a failure remark.

Config validation now runs when a config is saved and again before a configured
submission starts. It checks:

- `task_type` exists in the v2 task handler registry.
- `DocumentFields` uses known fixed business field names.
- The requested fields are supported by the selected task type.
- The sheet selector mode is valid and has its required value, such as
  `sheet_id`, `title`, `keyword`, or `sheet_ids`.
- `status` is either `active` or `disabled`.

## Strategy Boundary

Each business crawler strategy owns its business-specific conversion rules:

- `crawl_*_task`: turns a normalized submission into an ADB App crawl.
- `plan_capture_for_task`: turns task type, app type, and required fields into
  an ADB capture action plan.
- `*_metrics`: extracts durable execution metrics.
- `*_writeback_values`: maps crawl results to fixed business field names.

The writeback executor only applies already-planned field values to the target
document. It does not decide whether a failed read-count crawl should write
`N`, a remark, or some other business value.

## Capture Action Boundary

Different apps and metrics can require different crawl actions. The v2 path
keeps that decision out of the generic task runner:

- `mobile.action_plan.FieldCapturePlan` describes ADB actions such as
  `open_link`, `ui_controls`, `screenshot`, `ocr`, `tap_retry`, `scroll`, and
  `click_detail`.
- `crawler_app.capture.planner.plan_capture_for_task(...)` builds the default
  plan by merging the action requirements for each `app_type + metric`.
- `task_type` chooses the business handler and the requested field set; it is
  not the smallest action-planning unit.
- Simple fields such as `read_count` can use UI controls plus screenshot, with
  OCR enabled by config or app policy.
- Fields such as `comment_count` may require scrolling.
- App-specific paths such as Tenpay can force OCR or detail-click behavior
  without changing the generic task runner.
- Mobile crawlers execute the plan and return both crawl results and the plan
  snapshot for debugging.

The built-in default planner resolves evidence requirements with:

```text
app_type + metric -> required actions
requested metrics -> merged action set -> one shared capture -> field results
```

For example, `tenpay + (article_title, comment_count, like_count, screenshot)`
uses one first-screen capture with `open_link, ui_controls, screenshot, ocr`;
it does not need the scroll action. The OCR snapshot is shared, then split into
`article_title`, `comment_count`, `like_count`, and `screenshot` field results.

The database table `capture_action_profiles` remains available as an explicit
override. At execution time v2 resolves the configured profile with:

```text
app_type + task_type + requested_fields
```

`app_type` comes from each post URL in the selected sheet, not from the online
document URL. If no exact app profile exists, v2 can fall back to `unknown`; if
no profile matches the requested field combo, it falls back to the code default
plan.

| Column | Meaning |
| --- | --- |
| `app_type` | One concrete App type, such as `alipay`, `antfortune`, `tenpay`, or `unknown`. Do not combine multiple apps in one row. |
| `task_type` | Task number/type, such as `read_count`, `article_detail`, or `detail`. |
| `field_combo` | Comma-separated crawl fields, such as `read_count` or `comment_count,screenshot`. |
| `action_combo` | Comma-separated ADB actions, such as `open_link,ui_controls,screenshot,ocr`. |

This corresponds to the business-facing A/B/C/D layout:

```text
A: app_type
B: task_type
C: field_combo
D: action_combo
```

`field_names_json`, `action_names_json`, and `capture_config_json` keep the
machine-readable details. Hash columns are used for stable unique keys while
keeping the combo text readable.

## Task Status Flow

`task_submissions` is the durable queue:

- `pending` / `retry`: eligible for the generic task runner.
- `running`: currently claimed by an execution.
- `success`, `not_found`, `skipped`: terminal states and not automatically
  retried.
- `failed`: no attempts remain.

When an execution returns an error, the submission becomes `retry` while
`attempts < max_attempts`; it becomes `failed` only after attempts are exhausted.
Submitting the same dedupe key again revives `failed` tasks back to `pending`
and clears their previous execution pointer/error, but terminal success and
not-found results stay terminal.

## Temporary Corrections

Manual data fixes should also go through v2 field mappings and writeback plans.
Use `v2-correction-plan` to create an audit row in `corrections` and a pending
field-level `writeback_plans` row:

```powershell
.\scripts\run.ps1 -Task v2-correction-plan `
  -CorrectionDocumentId 1 `
  -CorrectionSheetId "qlmz7y" `
  -CorrectionRowIndex 2 `
  -CorrectionField "read_count" `
  -CorrectionValue "123" `
  -CorrectionReason "manual business correction" `
  -CorrectionOperator "ops"
```

Apply pending correction writebacks:

```powershell
.\scripts\run.ps1 -Task v2-correction-writeback
```

Plan and apply in one command:

```powershell
.\scripts\run.ps1 -Task v2-correction-apply `
  -CorrectionDocumentId 1 `
  -CorrectionSheetId "qlmz7y" `
  -CorrectionRowIndex 2 `
  -CorrectionField "read_count" `
  -CorrectionValue "123" `
  -CorrectionReason "manual business correction"
```

For normal business use, prefer targeting through a document config instead of
raw database IDs. For a daily document, use config key plus date plus row:

```powershell
.\scripts\run.ps1 -Task v2-correction-plan `
  -DocumentConfigKey "redsoil_daily_detail" `
  -ReportDate 2026-06-04 `
  -CorrectionRowIndex 2 `
  -CorrectionField "read_count" `
  -CorrectionValue "123" `
  -CorrectionReason "manual business correction"
```

Or target the row by post URL:

```powershell
.\scripts\run.ps1 -Task v2-correction-plan `
  -DocumentConfigKey "redsoil_daily_detail" `
  -ReportDate 2026-06-04 `
  -CorrectionPostUrl "https://ur.alipay.com/..." `
  -CorrectionField "read_count" `
  -CorrectionValue "123" `
  -CorrectionReason "manual business correction"
```

The correction command requires an existing `source_rows` record because it
uses that row's latest `column_mapping_id`. If the requested field is not
mapped in the sheet header, v2 refuses the correction before writing anything.
When the writeback executor succeeds, skips, or errors, it updates the matching
`corrections.status`.

## Next Migration Steps

1. Continue splitting mobile app adapters from field extraction logic. The
   read-count chain now separates capture record loading
   (`mobile.capture_records`), field extraction (`mobile.read_count_parser`),
   and ADB orchestration (`mobile.read_count_crawler`).
2. Migrate `article-details` by adding an `article_detail` task handler and
   reusing the generic task runner.
3. Add a correction preview/list command for pending `manual_correction`
   writeback plans before applying them.
