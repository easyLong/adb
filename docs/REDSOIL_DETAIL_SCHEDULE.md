# Redsoil Detail Scheduled Submit

This guide describes the standalone Windows scheduled task for submitting
today's `redsoil_detail` jobs.

The script only submits rows into `crawler_app.task_submissions`. The actual
ADB crawl and Tencent Docs writeback still require the normal queue workers:

```powershell
.\scripts\run.ps1 -Task workers-start
.\scripts\run.ps1 -Task workers-status
```

## Script

Use one entrypoint:

```powershell
.\scripts\submit_redsoil_detail_today.ps1
```

Default behavior:

- Target date: today
- Scheduled time: 16:00 every day
- Trigger type: `scheduled_today_detail`
- Config key: `redsoil_detail`
- Limit: 15 rows per matched date sheet
- Logs: `apps\finance_crawler\logs\scheduled_tasks`

If today's document has two sheets, such as `0616-精选-制造` and
`0616-新兴产业-500`, the default `-Limit 15` can submit up to 30 tasks.

## Install

Register the Windows scheduled task on the machine that runs the project:

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action install
```

The task name is:

```text
ADB Redsoil Detail Today Submit
```

Change the scheduled time:

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action install -Time "16:30"
```

Submit all matched rows instead of 15 per sheet:

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action install -Limit 0
```

## Operate

Check registration and last run result:

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action status
```

Run the submit immediately, without waiting for the scheduled time:

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action run
```

Manually start the registered Windows scheduled task:

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action start
```

Remove the scheduled task:

```powershell
.\scripts\submit_redsoil_detail_today.ps1 -Action uninstall
```

## Verify

Check the script log:

```powershell
Get-Content apps\finance_crawler\logs\scheduled_tasks\redsoil_detail_today_yyyyMMdd.log -Tail 120
```

Check the latest submit runs:

```powershell
@'
from apps.finance_crawler.storage.db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            select r.id, c.config_key, r.trigger_type, r.sheet_id, r.sheet_title,
                   r.status, r.source_rows, r.submitted_tasks, r.skipped_rows,
                   r.started_at, r.finished_at, r.error
            from crawler_app.submit_runs r
            left join crawler_app.document_trigger_configs c on c.id = r.config_id
            where c.config_key = 'redsoil_detail'
              and r.trigger_type = 'scheduled_today_detail'
            order by r.id desc
            limit 10
        """)
        for row in cur.fetchall():
            print(row)
'@ | python -
```

Check queued detail tasks created or updated today:

```powershell
@'
from apps.finance_crawler.storage.db import get_conn

with get_conn() as conn:
    with conn.cursor() as cur:
        cur.execute("""
            select sheet_id, status, count(*) as cnt,
                   min(row_index) as min_row,
                   max(row_index) as max_row,
                   max(updated_at) as last_updated
            from crawler_app.task_submissions
            where task_type = 'detail'
              and date(updated_at) = curdate()
            group by sheet_id, status
            order by sheet_id, status
        """)
        for row in cur.fetchall():
            print(row)
'@ | python -
```
