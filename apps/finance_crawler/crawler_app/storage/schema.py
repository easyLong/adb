"""Schema for the document-driven crawler_app database."""

from __future__ import annotations

from apps.finance_crawler.storage.device_pool_schema import ensure_device_pool_tables


def ensure_crawler_app_tables(cursor) -> None:
    _rename_table_if_needed(cursor, "crawl_tasks", "task_submissions")
    _rename_table_if_needed(cursor, "crawl_task_runs", "task_executions")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS documents (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            source_type VARCHAR(64) NOT NULL,
            doc_url TEXT NOT NULL,
            file_id VARCHAR(128) NULL,
            title VARCHAR(255) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_documents_file (source_type, file_id),
            INDEX idx_documents_source (source_type),
            INDEX idx_documents_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_sheets (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            document_id BIGINT UNSIGNED NOT NULL,
            sheet_id VARCHAR(128) NOT NULL,
            sheet_title VARCHAR(255) NOT NULL,
            business_date DATE NULL,
            header_row_index INT NOT NULL DEFAULT 0,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_document_sheet (document_id, sheet_id),
            INDEX idx_document_sheets_date (business_date),
            INDEX idx_document_sheets_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS kol_base_profiles (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            kol_name VARCHAR(255) NOT NULL,
            platform VARCHAR(64) NOT NULL,
            homepage_url TEXT NULL,
            group_name VARCHAR(64) NULL,
            kol_type VARCHAR(64) NOT NULL DEFAULT 'other',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_kol_base_profile (kol_name, platform),
            INDEX idx_kol_base_profile_name (kol_name),
            INDEX idx_kol_base_profile_platform (platform),
            INDEX idx_kol_base_profile_type (kol_type),
            INDEX idx_kol_base_profile_group (group_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _drop_index_if_exists(cursor, "kol_base_profiles", "uk_kol_base_profile")
    _drop_column_if_exists(cursor, "kol_base_profiles", "source_date")
    _drop_column_if_exists(cursor, "kol_base_profiles", "source_file_id")
    _drop_column_if_exists(cursor, "kol_base_profiles", "mapping_sheet_id")
    _drop_column_if_exists(cursor, "kol_base_profiles", "info_sheet_id")
    _drop_column_if_exists(cursor, "kol_base_profiles", "source_row_index")
    _add_unique_index_if_missing(
        cursor,
        "kol_base_profiles",
        "uk_kol_base_profile",
        "kol_name, platform",
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS kol_daily_snapshots (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            kol_profile_id BIGINT UNSIGNED NULL,
            snapshot_date DATE NOT NULL,
            kol_name VARCHAR(255) NOT NULL,
            platform VARCHAR(64) NOT NULL,
            homepage_url TEXT NULL,
            group_name VARCHAR(64) NULL,
            kol_type VARCHAR(64) NOT NULL DEFAULT 'other',
            fans_count BIGINT NULL,
            growth_count BIGINT NULL,
            read_count BIGINT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_kol_daily_snapshot (snapshot_date, kol_name, platform),
            INDEX idx_kol_daily_profile (kol_profile_id),
            INDEX idx_kol_daily_date (snapshot_date),
            INDEX idx_kol_daily_platform (platform),
            INDEX idx_kol_daily_type (kol_type),
            INDEX idx_kol_daily_group (group_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS kol_daily_metrics (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            metric_date DATE NOT NULL,
            kol_name VARCHAR(255) NOT NULL,
            platform VARCHAR(64) NOT NULL,
            fans_count BIGINT NULL,
            growth_count BIGINT NULL,
            read_count BIGINT NULL,
            post_count_24h INT NULL,
            fans_source VARCHAR(64) NULL,
            growth_source VARCHAR(64) NULL,
            read_source VARCHAR(64) NULL,
            post_count_source VARCHAR(64) NULL,
            source_doc_url TEXT NULL,
            source_row_index INT NULL,
            source_payload_json LONGTEXT NULL,
            target_doc_url TEXT NULL,
            target_sheet_id VARCHAR(128) NULL,
            target_row_index INT NULL,
            writeback_status VARCHAR(32) NOT NULL DEFAULT 'pending',
            writeback_error TEXT NULL,
            last_writeback_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_kol_daily_metric (metric_date, kol_name, platform),
            INDEX idx_kol_daily_metrics_date (metric_date),
            INDEX idx_kol_daily_metrics_platform (platform),
            INDEX idx_kol_daily_metrics_writeback (writeback_status, metric_date),
            INDEX idx_kol_daily_metrics_target_row (target_sheet_id, target_row_index)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _drop_column_if_exists(cursor, "kol_daily_metrics", "homepage_url")
    _drop_column_if_exists(cursor, "kol_daily_metrics", "group_name")
    _drop_column_if_exists(cursor, "kol_daily_metrics", "kol_type")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_action_profiles (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            action_profile_key VARCHAR(128) NOT NULL,
            app_type VARCHAR(64) NOT NULL,
            task_type VARCHAR(64) NOT NULL,
            field_combo VARCHAR(512) NOT NULL,
            action_combo VARCHAR(512) NOT NULL,
            field_names_json LONGTEXT NOT NULL,
            action_names_json LONGTEXT NOT NULL,
            action_config_json LONGTEXT NULL,
            aggregation_policy_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            priority INT NOT NULL DEFAULT 0,
            description VARCHAR(255) NULL,
            updated_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_profile_action_key (action_profile_key),
            UNIQUE KEY uk_profile_action_profile (app_type, task_type, field_combo(191)),
            INDEX idx_profile_action_status (status),
            INDEX idx_profile_action_app_task (app_type, task_type),
            INDEX idx_profile_action_priority (priority)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _insert_default_profile_action_profiles(cursor)
    _ensure_profile_metric_tables(cursor)
    ensure_device_pool_tables(cursor)
    _ensure_wechat_demand_intake_tables(cursor)
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS column_mappings (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            document_id BIGINT UNSIGNED NOT NULL,
            sheet_id VARCHAR(128) NOT NULL,
            header_row_index INT NOT NULL DEFAULT 0,
            header_hash CHAR(64) NOT NULL,
            mapping_json LONGTEXT NOT NULL,
            resolution_json LONGTEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_column_mapping_header (document_id, sheet_id, header_hash),
            INDEX idx_column_mapping_sheet (document_id, sheet_id),
            INDEX idx_column_mapping_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS source_rows (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            document_id BIGINT UNSIGNED NOT NULL,
            sheet_id VARCHAR(128) NOT NULL,
            column_mapping_id BIGINT UNSIGNED NOT NULL,
            row_index INT NOT NULL,
            business_date DATE NULL,
            post_url VARCHAR(1000) NOT NULL,
            account_name VARCHAR(255) NULL,
            post_time VARCHAR(128) NULL,
            row_hash CHAR(64) NOT NULL,
            row_json LONGTEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_source_row_position (document_id, sheet_id, row_index),
            INDEX idx_source_rows_url (post_url(191)),
            INDEX idx_source_rows_hash (row_hash),
            INDEX idx_source_rows_date (business_date),
            INDEX idx_source_rows_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_task_configs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            config_key VARCHAR(128) NOT NULL,
            source_type VARCHAR(64) NOT NULL DEFAULT 'tencent_docs',
            doc_url TEXT NOT NULL,
            file_id VARCHAR(128) NULL,
            sheet_id VARCHAR(128) NULL,
            task_type VARCHAR(64) NOT NULL,
            field_names_json LONGTEXT NOT NULL,
            sheet_selector_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            priority INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            description VARCHAR(255) NULL,
            updated_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_document_task_config (config_key),
            INDEX idx_document_task_config_status (status),
            INDEX idx_document_task_config_file (source_type, file_id),
            INDEX idx_document_task_config_task (task_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_trigger_configs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            config_key VARCHAR(128) NOT NULL,
            source_type VARCHAR(64) NOT NULL DEFAULT 'tencent_docs',
            doc_url TEXT NOT NULL,
            file_id VARCHAR(128) NULL,
            sheet_selector_json LONGTEXT NULL,
            submit_policy_json LONGTEXT NULL,
            scan_interval_seconds INT NOT NULL DEFAULT 300,
            next_scan_at DATETIME NULL,
            last_scan_at DATETIME NULL,
            scan_status VARCHAR(32) NOT NULL DEFAULT 'idle',
            locked_by VARCHAR(128) NULL,
            locked_until DATETIME NULL,
            last_error TEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            description VARCHAR(255) NULL,
            updated_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_document_trigger_config (config_key),
            INDEX idx_document_trigger_due (status, next_scan_at),
            INDEX idx_document_trigger_file (source_type, file_id),
            INDEX idx_document_trigger_lock (locked_until)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS document_trigger_bindings (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            config_id BIGINT UNSIGNED NOT NULL,
            task_type VARCHAR(64) NOT NULL,
            field_names_json LONGTEXT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            priority INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            description VARCHAR(255) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_document_trigger_binding (config_id, task_type),
            INDEX idx_document_trigger_binding_config (config_id, status),
            INDEX idx_document_trigger_binding_task (task_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS submit_runs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            config_id BIGINT UNSIGNED NULL,
            trigger_type VARCHAR(64) NOT NULL DEFAULT 'scheduled',
            sheet_id VARCHAR(128) NULL,
            sheet_title VARCHAR(255) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            source_rows INT NOT NULL DEFAULT 0,
            submitted_tasks INT NOT NULL DEFAULT 0,
            skipped_rows INT NOT NULL DEFAULT 0,
            summary_json LONGTEXT NULL,
            error TEXT NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME NULL,
            INDEX idx_submit_runs_config (config_id),
            INDEX idx_submit_runs_status (status),
            INDEX idx_submit_runs_started (started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS task_submissions (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            task_type VARCHAR(64) NOT NULL,
            source_row_id BIGINT UNSIGNED NULL,
            document_id BIGINT UNSIGNED NOT NULL,
            sheet_id VARCHAR(128) NOT NULL,
            row_index INT NOT NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            post_url VARCHAR(1000) NOT NULL,
            account_name VARCHAR(255) NULL,
            post_time VARCHAR(128) NULL,
            source_locator_json LONGTEXT NOT NULL,
            dedupe_key CHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            priority INT NOT NULL DEFAULT 0,
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            latest_execution_id BIGINT UNSIGNED NULL,
            last_error TEXT NULL,
            created_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_task_submissions_dedupe (dedupe_key),
            INDEX idx_task_submissions_status (status),
            INDEX idx_task_submissions_app (app_type),
            INDEX idx_task_submissions_sheet (document_id, sheet_id),
            INDEX idx_task_submissions_url (post_url(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS capture_action_profiles (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            app_type VARCHAR(64) NOT NULL,
            task_type VARCHAR(64) NOT NULL,
            field_combo VARCHAR(512) NOT NULL,
            action_combo VARCHAR(512) NOT NULL,
            field_combo_hash CHAR(64) NOT NULL,
            action_combo_hash CHAR(64) NOT NULL,
            field_names_json LONGTEXT NOT NULL,
            action_names_json LONGTEXT NOT NULL,
            capture_config_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            priority INT NOT NULL DEFAULT 0,
            description VARCHAR(255) NULL,
            updated_by VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_capture_action_profile (app_type, task_type, field_combo_hash),
            INDEX idx_capture_action_status (status),
            INDEX idx_capture_action_app_task (app_type, task_type),
            INDEX idx_capture_action_field_hash (field_combo_hash),
            INDEX idx_capture_action_priority (priority)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _insert_default_capture_action_profiles(cursor)
    _rename_column_if_needed(
        cursor,
        "task_submissions",
        "latest_run_id",
        "latest_execution_id",
        "BIGINT UNSIGNED NULL",
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS task_executions (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            submission_id BIGINT UNSIGNED NOT NULL,
            attempt_no INT NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            opened_url VARCHAR(1000) NULL,
            metrics_json LONGTEXT NULL,
            result_json LONGTEXT NULL,
            screenshot_path VARCHAR(700) NULL,
            error TEXT NULL,
            started_at DATETIME NULL,
            heartbeat_at DATETIME NULL,
            finished_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_task_execution_attempt (submission_id, attempt_no),
            INDEX idx_task_executions_submission (submission_id),
            INDEX idx_task_executions_status (status),
            INDEX idx_task_executions_finished (finished_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _rename_column_if_needed(
        cursor,
        "task_executions",
        "task_id",
        "submission_id",
        "BIGINT UNSIGNED NOT NULL",
    )
    _add_column_if_missing(cursor, "task_executions", "opened_url", "VARCHAR(1000) NULL AFTER status")
    _add_column_if_missing(cursor, "task_executions", "metrics_json", "LONGTEXT NULL AFTER opened_url")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS field_capture_observations (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            subject_type VARCHAR(64) NOT NULL,
            subject_id BIGINT UNSIGNED NULL,
            target_type VARCHAR(64) NULL,
            target_id BIGINT UNSIGNED NULL,
            task_type VARCHAR(64) NOT NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            field_name VARCHAR(64) NOT NULL,
            action_template_key VARCHAR(128) NULL,
            action_names_json LONGTEXT NULL,
            page_state VARCHAR(64) NULL,
            extraction_source VARCHAR(64) NULL,
            value_text TEXT NULL,
            value_number BIGINT NULL,
            accepted TINYINT NOT NULL DEFAULT 0,
            confidence DECIMAL(6,4) NULL,
            evidence_json LONGTEXT NULL,
            quality_error TEXT NULL,
            screenshot_path VARCHAR(700) NULL,
            observed_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_field_capture_subject (subject_type, subject_id, field_name),
            INDEX idx_field_capture_subject (subject_type, subject_id),
            INDEX idx_field_capture_target (target_type, target_id),
            INDEX idx_field_capture_field (app_type, task_type, field_name),
            INDEX idx_field_capture_template (action_template_key),
            INDEX idx_field_capture_state (page_state, accepted),
            INDEX idx_field_capture_observed (observed_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS derived_records (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            source_submission_id BIGINT UNSIGNED NULL,
            source_execution_id BIGINT UNSIGNED NULL,
            source_task_type VARCHAR(64) NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            record_type VARCHAR(64) NOT NULL,
            relation_type VARCHAR(64) NULL,
            unique_key VARCHAR(1000) NOT NULL,
            dedupe_key CHAR(64) NOT NULL,
            title VARCHAR(255) NULL,
            url VARCHAR(1000) NULL,
            payload_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_derived_record_dedupe (dedupe_key),
            INDEX idx_derived_record_source (source_submission_id, source_execution_id),
            INDEX idx_derived_record_type (app_type, record_type),
            INDEX idx_derived_record_status (status),
            INDEX idx_derived_record_url (url(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS writeback_plans (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            submission_id BIGINT UNSIGNED NULL,
            execution_id BIGINT UNSIGNED NULL,
            document_id BIGINT UNSIGNED NOT NULL,
            sheet_id VARCHAR(128) NOT NULL,
            row_index INT NOT NULL,
            column_mapping_id BIGINT UNSIGNED NOT NULL,
            field_name VARCHAR(64) NOT NULL,
            value_text TEXT NULL,
            payload_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'planned',
            error TEXT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            applied_at DATETIME NULL,
            INDEX idx_writeback_plans_status (status),
            INDEX idx_writeback_plans_submission (submission_id),
            INDEX idx_writeback_plans_sheet (document_id, sheet_id),
            INDEX idx_writeback_plans_field (field_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _rename_column_if_needed(cursor, "writeback_plans", "task_id", "submission_id", "BIGINT UNSIGNED NULL")
    _rename_column_if_needed(cursor, "writeback_plans", "run_id", "execution_id", "BIGINT UNSIGNED NULL")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS corrections (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            target_type VARCHAR(64) NOT NULL,
            target_id BIGINT UNSIGNED NULL,
            document_id BIGINT UNSIGNED NULL,
            sheet_id VARCHAR(128) NULL,
            row_index INT NULL,
            field_name VARCHAR(64) NULL,
            old_value TEXT NULL,
            new_value TEXT NULL,
            reason TEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'planned',
            operator_name VARCHAR(64) NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            applied_at DATETIME NULL,
            INDEX idx_corrections_target (target_type, target_id),
            INDEX idx_corrections_status (status),
            INDEX idx_corrections_sheet (document_id, sheet_id, row_index)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _rename_table_if_needed(cursor, old_name: str, new_name: str) -> None:
    if _table_exists(cursor, new_name) or not _table_exists(cursor, old_name):
        return
    cursor.execute(f"RENAME TABLE `{old_name}` TO `{new_name}`")


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _rename_column_if_needed(
    cursor,
    table_name: str,
    old_name: str,
    new_name: str,
    column_definition: str,
) -> None:
    if not _column_exists(cursor, table_name, old_name) or _column_exists(cursor, table_name, new_name):
        return
    cursor.execute(
        f"ALTER TABLE `{table_name}` CHANGE COLUMN `{old_name}` `{new_name}` {column_definition}"
    )


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
    return cursor.fetchone() is not None


def _add_column_if_missing(cursor, table_name: str, column_name: str, column_definition: str) -> None:
    if _column_exists(cursor, table_name, column_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {column_definition}")


def _modify_column(cursor, table_name: str, column_name: str, column_definition: str) -> None:
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
    column = cursor.fetchone()
    if column is None:
        return
    current_definition = f"{column.get('Type', '')} {'NULL' if column.get('Null') == 'YES' else 'NOT NULL'}"
    if _normalize_column_definition(current_definition) == _normalize_column_definition(column_definition):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` MODIFY COLUMN `{column_name}` {column_definition}")


def _normalize_column_definition(definition: str) -> str:
    return " ".join(definition.strip().lower().split())


def _drop_column_if_exists(cursor, table_name: str, column_name: str) -> None:
    if not _column_exists(cursor, table_name, column_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` DROP COLUMN `{column_name}`")


def _drop_index_if_exists(cursor, table_name: str, index_name: str) -> None:
    if not _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` DROP INDEX `{index_name}`")


def _add_unique_index_if_missing(cursor, table_name: str, index_name: str, columns: str) -> None:
    if _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD UNIQUE KEY `{index_name}` ({columns})")


def _add_index_if_missing(cursor, table_name: str, index_name: str, columns: str) -> None:
    if _index_exists(cursor, table_name, index_name):
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD INDEX `{index_name}` ({columns})")


def _index_exists(cursor, table_name: str, index_name: str) -> bool:
    cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = %s", (index_name,))
    return cursor.fetchone() is not None


def _ensure_wechat_demand_intake_tables(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS wechat_chats (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            chat_key CHAR(64) NOT NULL,
            chat_name VARCHAR(255) NOT NULL,
            chat_type VARCHAR(32) NOT NULL DEFAULT 'group',
            external_chat_id VARCHAR(128) NULL,
            customer_name VARCHAR(255) NULL,
            owner_name VARCHAR(255) NULL,
            business_platform VARCHAR(128) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            first_seen_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            last_seen_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_wechat_chat_key (chat_key),
            INDEX idx_wechat_chat_name (chat_name),
            INDEX idx_wechat_chat_customer (customer_name),
            INDEX idx_wechat_chat_owner (owner_name),
            INDEX idx_wechat_chat_platform (business_platform),
            INDEX idx_wechat_chat_status (status),
            INDEX idx_wechat_chat_seen (last_seen_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _add_column_if_missing(cursor, "wechat_chats", "customer_name", "VARCHAR(255) NULL")
    _add_column_if_missing(cursor, "wechat_chats", "owner_name", "VARCHAR(255) NULL")
    _add_column_if_missing(cursor, "wechat_chats", "business_platform", "VARCHAR(128) NULL")
    _add_index_if_missing(cursor, "wechat_chats", "idx_wechat_chat_customer", "customer_name")
    _add_index_if_missing(cursor, "wechat_chats", "idx_wechat_chat_owner", "owner_name")
    _add_index_if_missing(cursor, "wechat_chats", "idx_wechat_chat_platform", "business_platform")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS wechat_capture_runs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            run_key CHAR(64) NULL,
            chat_id BIGINT UNSIGNED NULL,
            source_app VARCHAR(32) NOT NULL DEFAULT 'crawler',
            source_type VARCHAR(32) NOT NULL DEFAULT 'wechat_group',
            source_key CHAR(64) NULL,
            source_name VARCHAR(255) NULL,
            external_source_id VARCHAR(128) NULL,
            ops_source_context_id CHAR(36) NULL,
            capture_mode VARCHAR(32) NOT NULL DEFAULT 'unread',
            target_date DATE NULL,
            device_serial VARCHAR(128) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            screenshot_dir VARCHAR(700) NULL,
            screenshot_count INT NOT NULL DEFAULT 0,
            message_count INT NOT NULL DEFAULT 0,
            error TEXT NULL,
            meta_json LONGTEXT NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_wechat_capture_run_key (run_key),
            INDEX idx_wechat_capture_chat (chat_id),
            INDEX idx_wechat_capture_source (source_app, source_type, source_key),
            INDEX idx_wechat_capture_ops_source (ops_source_context_id),
            INDEX idx_wechat_capture_status (status),
            INDEX idx_wechat_capture_mode (capture_mode),
            INDEX idx_wechat_capture_target_date (target_date),
            INDEX idx_wechat_capture_started (started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _add_column_if_missing(cursor, "wechat_capture_runs", "run_key", "CHAR(64) NULL")
    _modify_column(cursor, "wechat_capture_runs", "chat_id", "BIGINT UNSIGNED NULL")
    _add_column_if_missing(cursor, "wechat_capture_runs", "source_app", "VARCHAR(32) NOT NULL DEFAULT 'crawler'")
    _add_column_if_missing(cursor, "wechat_capture_runs", "source_type", "VARCHAR(32) NOT NULL DEFAULT 'wechat_group'")
    _add_column_if_missing(cursor, "wechat_capture_runs", "source_key", "CHAR(64) NULL")
    _add_column_if_missing(cursor, "wechat_capture_runs", "source_name", "VARCHAR(255) NULL")
    _add_column_if_missing(cursor, "wechat_capture_runs", "external_source_id", "VARCHAR(128) NULL")
    _add_column_if_missing(cursor, "wechat_capture_runs", "ops_source_context_id", "CHAR(36) NULL")
    _add_unique_index_if_missing(cursor, "wechat_capture_runs", "uk_wechat_capture_run_key", "run_key")
    _add_index_if_missing(cursor, "wechat_capture_runs", "idx_wechat_capture_source", "source_app, source_type, source_key")
    _add_index_if_missing(cursor, "wechat_capture_runs", "idx_wechat_capture_ops_source", "ops_source_context_id")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS wechat_message_observations (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            message_fingerprint CHAR(64) NULL,
            run_id BIGINT UNSIGNED NOT NULL,
            chat_id BIGINT UNSIGNED NULL,
            source_key CHAR(64) NULL,
            source_name VARCHAR(255) NULL,
            screen_index INT NOT NULL DEFAULT 0,
            bubble_index INT NOT NULL DEFAULT 0,
            message_order INT NOT NULL DEFAULT 0,
            message_date DATE NULL,
            display_time_text VARCHAR(64) NULL,
            inferred_message_time DATETIME NULL,
            sender_name VARCHAR(255) NULL,
            message_type VARCHAR(32) NOT NULL DEFAULT 'text',
            message_text LONGTEXT NULL,
            normalized_message_text VARCHAR(700) NULL,
            attachment_name VARCHAR(255) NULL,
            attachment_size_text VARCHAR(64) NULL,
            screenshot_path VARCHAR(700) NULL,
            bbox_json LONGTEXT NULL,
            raw_json LONGTEXT NULL,
            confidence DECIMAL(5,4) NULL,
            observation_key CHAR(64) NOT NULL,
            parser_type VARCHAR(32) NULL,
            parse_run_id BIGINT UNSIGNED NULL,
            first_seen_run_id BIGINT UNSIGNED NULL,
            latest_seen_run_id BIGINT UNSIGNED NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_wechat_observation_key (observation_key),
            UNIQUE KEY uk_wechat_message_fingerprint (message_fingerprint),
            INDEX idx_wechat_obs_source_date_status (source_key, message_date, status, inferred_message_time),
            INDEX idx_wechat_obs_source_name_date_status (source_name, message_date, status, inferred_message_time),
            INDEX idx_wechat_obs_chat_time (chat_id, inferred_message_time),
            INDEX idx_wechat_obs_chat_date (chat_id, message_date),
            INDEX idx_wechat_obs_run_order (run_id, screen_index, bubble_index),
            INDEX idx_wechat_obs_status (status),
            INDEX idx_wechat_obs_sender (sender_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _modify_column(cursor, "wechat_message_observations", "chat_id", "BIGINT UNSIGNED NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "message_fingerprint", "CHAR(64) NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "source_key", "CHAR(64) NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "source_name", "VARCHAR(255) NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "message_order", "INT NOT NULL DEFAULT 0")
    _add_column_if_missing(cursor, "wechat_message_observations", "normalized_message_text", "VARCHAR(700) NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "parser_type", "VARCHAR(32) NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "parse_run_id", "BIGINT UNSIGNED NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "first_seen_run_id", "BIGINT UNSIGNED NULL")
    _add_column_if_missing(cursor, "wechat_message_observations", "latest_seen_run_id", "BIGINT UNSIGNED NULL")
    _add_column_if_missing(
        cursor,
        "wechat_message_observations",
        "updated_at",
        "DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP",
    )
    _add_unique_index_if_missing(
        cursor,
        "wechat_message_observations",
        "uk_wechat_message_fingerprint",
        "message_fingerprint",
    )
    _add_index_if_missing(
        cursor,
        "wechat_message_observations",
        "idx_wechat_obs_source_date_status",
        "source_key, message_date, status, inferred_message_time",
    )
    _add_index_if_missing(
        cursor,
        "wechat_message_observations",
        "idx_wechat_obs_source_name_date_status",
        "source_name, message_date, status, inferred_message_time",
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS wechat_ocr_observations (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            run_id BIGINT UNSIGNED NOT NULL,
            source_key CHAR(64) NULL,
            source_name VARCHAR(255) NULL,
            screen_index INT NOT NULL DEFAULT 0,
            line_index INT NOT NULL DEFAULT 0,
            message_date DATE NULL,
            screenshot_path VARCHAR(700) NULL,
            ocr_text TEXT NULL,
            bbox_json LONGTEXT NULL,
            raw_json LONGTEXT NULL,
            confidence DECIMAL(5,4) NULL,
            observation_key CHAR(64) NOT NULL,
            parser_type VARCHAR(32) NOT NULL DEFAULT 'ocr',
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_wechat_ocr_observation_key (observation_key),
            INDEX idx_wechat_ocr_run_screen (run_id, screen_index, line_index),
            INDEX idx_wechat_ocr_source_date (source_key, message_date),
            INDEX idx_wechat_ocr_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _add_column_if_missing(cursor, "wechat_ocr_observations", "source_key", "CHAR(64) NULL")
    _add_column_if_missing(cursor, "wechat_ocr_observations", "source_name", "VARCHAR(255) NULL")
    _add_index_if_missing(cursor, "wechat_ocr_observations", "idx_wechat_ocr_source_date", "source_key, message_date")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS wechat_demand_intake_offsets (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            source_key CHAR(64) NOT NULL,
            source_name VARCHAR(255) NULL,
            last_observation_id BIGINT UNSIGNED NULL,
            last_message_time DATETIME NULL,
            last_intake_run_at DATETIME NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            meta_json LONGTEXT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_wechat_intake_offset_source (source_key),
            INDEX idx_wechat_intake_offset_status (status),
            INDEX idx_wechat_intake_offset_seen (last_intake_run_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _add_column_if_missing(cursor, "wechat_demand_intake_offsets", "source_name", "VARCHAR(255) NULL")
    _add_column_if_missing(cursor, "wechat_demand_intake_offsets", "last_observation_id", "BIGINT UNSIGNED NULL")
    _add_column_if_missing(cursor, "wechat_demand_intake_offsets", "last_message_time", "DATETIME NULL")
    _add_column_if_missing(cursor, "wechat_demand_intake_offsets", "last_intake_run_at", "DATETIME NULL")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS wechat_demand_intake_runs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            run_key CHAR(64) NOT NULL,
            source_key CHAR(64) NOT NULL,
            source_name VARCHAR(255) NULL,
            from_observation_id BIGINT UNSIGNED NULL,
            to_observation_id BIGINT UNSIGNED NULL,
            context_count INT NOT NULL DEFAULT 0,
            new_message_count INT NOT NULL DEFAULT 0,
            candidate_count INT NOT NULL DEFAULT 0,
            model_name VARCHAR(128) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            error TEXT NULL,
            raw_model_json LONGTEXT NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_wechat_intake_run_key (run_key),
            INDEX idx_wechat_intake_run_source (source_key, started_at),
            INDEX idx_wechat_intake_run_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _add_column_if_missing(cursor, "wechat_demand_intake_runs", "raw_model_json", "LONGTEXT NULL")
    _add_index_if_missing(cursor, "wechat_demand_intake_runs", "idx_wechat_intake_run_source", "source_key, started_at")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS demand_intake_runs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            run_key CHAR(64) NULL,
            chat_id BIGINT UNSIGNED NULL,
            source_app VARCHAR(32) NOT NULL DEFAULT 'crawler',
            source_type VARCHAR(32) NOT NULL DEFAULT 'wechat_group',
            source_key CHAR(64) NULL,
            source_name VARCHAR(255) NULL,
            ops_source_context_id CHAR(36) NULL,
            source_capture_run_id BIGINT UNSIGNED NULL,
            from_observation_id BIGINT UNSIGNED NULL,
            to_observation_id BIGINT UNSIGNED NULL,
            model_name VARCHAR(128) NULL,
            prompt_version VARCHAR(64) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            message_count INT NOT NULL DEFAULT 0,
            candidate_count INT NOT NULL DEFAULT 0,
            error TEXT NULL,
            meta_json LONGTEXT NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_demand_intake_run_key (run_key),
            INDEX idx_demand_intake_chat (chat_id),
            INDEX idx_demand_intake_source (source_app, source_type, source_key),
            INDEX idx_demand_intake_ops_source (ops_source_context_id),
            INDEX idx_demand_intake_capture (source_capture_run_id),
            INDEX idx_demand_intake_status (status),
            INDEX idx_demand_intake_started (started_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _add_column_if_missing(cursor, "demand_intake_runs", "run_key", "CHAR(64) NULL")
    _modify_column(cursor, "demand_intake_runs", "chat_id", "BIGINT UNSIGNED NULL")
    _add_column_if_missing(cursor, "demand_intake_runs", "source_app", "VARCHAR(32) NOT NULL DEFAULT 'crawler'")
    _add_column_if_missing(cursor, "demand_intake_runs", "source_key", "CHAR(64) NULL")
    _add_column_if_missing(cursor, "demand_intake_runs", "source_name", "VARCHAR(255) NULL")
    _add_column_if_missing(cursor, "demand_intake_runs", "ops_source_context_id", "CHAR(36) NULL")
    _add_unique_index_if_missing(cursor, "demand_intake_runs", "uk_demand_intake_run_key", "run_key")
    _add_index_if_missing(cursor, "demand_intake_runs", "idx_demand_intake_source", "source_app, source_type, source_key")
    _add_index_if_missing(cursor, "demand_intake_runs", "idx_demand_intake_ops_source", "ops_source_context_id")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS demand_intake_candidates (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            candidate_key CHAR(64) NOT NULL,
            intake_run_id BIGINT UNSIGNED NOT NULL,
            source_chat_id BIGINT UNSIGNED NOT NULL,
            business_category VARCHAR(64) NULL,
            secondary_category VARCHAR(128) NULL,
            tertiary_category VARCHAR(255) NULL,
            start_time DATETIME NULL,
            deadline DATETIME NULL,
            business_name VARCHAR(255) NULL,
            demand_title VARCHAR(500) NULL,
            demand_content LONGTEXT NULL,
            confidence DECIMAL(5,4) NULL,
            match_suggestion VARCHAR(64) NULL,
            matched_demand_id BIGINT UNSIGNED NULL,
            approved_demand_id BIGINT UNSIGNED NULL,
            ai_reason LONGTEXT NULL,
            missing_fields_json LONGTEXT NULL,
            risk_notes_json LONGTEXT NULL,
            raw_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            reviewed_by VARCHAR(128) NULL,
            reviewed_at DATETIME NULL,
            review_note TEXT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_demand_candidate_key (candidate_key),
            INDEX idx_demand_candidate_run (intake_run_id),
            INDEX idx_demand_candidate_chat (source_chat_id),
            INDEX idx_demand_candidate_status (status),
            INDEX idx_demand_candidate_category (business_category, secondary_category),
            INDEX idx_demand_candidate_deadline (deadline),
            INDEX idx_demand_candidate_match (matched_demand_id),
            INDEX idx_demand_candidate_approved (approved_demand_id)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS demand_candidate_evidence (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            candidate_id BIGINT UNSIGNED NOT NULL,
            evidence_order INT NOT NULL DEFAULT 0,
            message_observation_id BIGINT UNSIGNED NOT NULL,
            message_time DATETIME NULL,
            display_time_text VARCHAR(64) NULL,
            sender_name VARCHAR(255) NULL,
            message_text LONGTEXT NULL,
            screenshot_path VARCHAR(700) NULL,
            evidence_reason VARCHAR(500) NULL,
            raw_json LONGTEXT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_demand_evidence_message (candidate_id, message_observation_id),
            INDEX idx_demand_evidence_candidate (candidate_id, evidence_order),
            INDEX idx_demand_evidence_message (message_observation_id),
            INDEX idx_demand_evidence_time (message_time),
            INDEX idx_demand_evidence_sender (sender_name)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _ensure_profile_metric_tables(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_targets (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            profile_key VARCHAR(191) NOT NULL,
            account_name VARCHAR(255) NULL,
            platform VARCHAR(64) NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            homepage_url VARCHAR(1000) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            source_json LONGTEXT NULL,
            first_seen_date DATE NULL,
            latest_seen_date DATE NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_profile_key (profile_key),
            INDEX idx_profile_status (status),
            INDEX idx_profile_app (app_type),
            INDEX idx_profile_url (homepage_url(191))
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_metric_sources (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            target_id BIGINT UNSIGNED NOT NULL,
            metric_date DATE NOT NULL,
            source_type VARCHAR(64) NOT NULL DEFAULT 'tencent_docs',
            source_name VARCHAR(191) NULL,
            source_key VARCHAR(191) NOT NULL,
            source_locator_json LONGTEXT NULL,
            requested_fields_json LONGTEXT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            attempts INT NOT NULL DEFAULT 0,
            max_attempts INT NOT NULL DEFAULT 3,
            last_error TEXT NULL,
            latest_metric_id BIGINT UNSIGNED NULL,
            writeback_status VARCHAR(32) NULL,
            writeback_error TEXT NULL,
            written_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_profile_metric_source (source_type, source_key),
            INDEX idx_profile_metric_source_target (target_id),
            INDEX idx_profile_metric_source_date (metric_date),
            INDEX idx_profile_metric_source_status (status),
            INDEX idx_profile_metric_writeback (writeback_status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _add_column_if_missing(cursor, "profile_metric_sources", "attempts", "INT NOT NULL DEFAULT 0")
    _add_column_if_missing(cursor, "profile_metric_sources", "max_attempts", "INT NOT NULL DEFAULT 3")
    _add_column_if_missing(cursor, "profile_metric_sources", "last_error", "TEXT NULL")
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_metric_runs (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            target_id BIGINT UNSIGNED NOT NULL,
            metric_date DATE NOT NULL,
            app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
            homepage_url VARCHAR(1000) NOT NULL,
            status VARCHAR(32) NOT NULL,
            fans_count INT NULL,
            growth_count INT NULL,
            read_count INT NULL,
            metrics_json LONGTEXT NULL,
            screenshot_path VARCHAR(700) NULL,
            error TEXT NULL,
            crawled_at DATETIME NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_profile_metric_target_date (target_id, metric_date),
            INDEX idx_profile_metric_target (target_id),
            INDEX idx_profile_metric_date (metric_date),
            INDEX idx_profile_metric_status (status),
            INDEX idx_profile_metric_app (app_type)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS profile_metric_writebacks (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            metric_source_id BIGINT UNSIGNED NOT NULL,
            metric_id BIGINT UNSIGNED NULL,
            sink_type VARCHAR(64) NOT NULL DEFAULT 'tencent_docs',
            sink_locator_json LONGTEXT NULL,
            field_name VARCHAR(64) NOT NULL DEFAULT 'fans_count',
            status VARCHAR(32) NOT NULL,
            error TEXT NULL,
            written_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY uk_profile_metric_writeback_field (metric_source_id, field_name),
            INDEX idx_profile_metric_writeback_source (metric_source_id),
            INDEX idx_profile_metric_writeback_metric (metric_id),
            INDEX idx_profile_metric_writeback_status (status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )


def _insert_default_capture_action_profiles(cursor) -> None:
    rows = [
        (
            "unknown",
            "read_count",
            "read_count",
            "open_link,ui_controls,screenshot,tap_retry",
            '["read_count"]',
            '["open_link","ui_controls","screenshot","tap_retry"]',
            '{"max_scrolls":0,"open_retries":"DOC_LINK_READS_OPEN_RETRIES"}',
            0,
            "Default read-count profile for simple UI capture.",
        ),
        (
            "alipay",
            "read_count",
            "read_count",
            "open_link,ui_controls,screenshot,tap_retry",
            '["read_count"]',
            '["open_link","ui_controls","screenshot","tap_retry"]',
            '{"max_scrolls":0,"open_retries":"DOC_LINK_READS_OPEN_RETRIES"}',
            10,
            "Alipay read-count profile using UI controls and screenshot.",
        ),
        (
            "antfortune",
            "read_count",
            "read_count",
            "open_link,ui_controls,screenshot,tap_retry",
            '["read_count"]',
            '["open_link","ui_controls","screenshot","tap_retry"]',
            '{"max_scrolls":0,"open_retries":"DOC_LINK_READS_OPEN_RETRIES"}',
            10,
            "Ant Fortune read-count profile using UI controls and screenshot.",
        ),
        (
            "tenpay",
            "read_count",
            "read_count",
            "open_link,ui_controls,screenshot,ocr,tap_retry",
            '["read_count"]',
            '["open_link","ui_controls","screenshot","ocr","tap_retry"]',
            '{"max_scrolls":0,"open_retries":"DOC_LINK_READS_OPEN_RETRIES"}',
            20,
            "Tenpay read-count profile with OCR enabled by app policy.",
        ),
        (
            "unknown",
            "article_detail",
            "comment_count,screenshot",
            "open_link,ui_controls,screenshot,scroll",
            '["comment_count","screenshot"]',
            '["open_link","ui_controls","screenshot","scroll"]',
            '{"max_scrolls":1}',
            0,
            "Default article detail profile for comments and screenshot.",
        ),
        (
            "tenpay",
            "detail",
            "trade_details",
            "open_link,screenshot,ocr,scroll,click_detail",
            '["trade_details"]',
            '["open_link","screenshot","ocr","scroll","click_detail"]',
            '{"max_scrolls":2}',
            20,
            "Tenpay detail profile requiring OCR and detail click actions.",
        ),
        (
            "alipay",
            "initial_check",
            "account_name",
            "open_link,ui_controls,screenshot",
            '["account_name"]',
            '["open_link","ui_controls","screenshot"]',
            '{"max_scrolls":0}',
            15,
            "Online-doc check profile for account nickname in Alipay.",
        ),
        (
            "antfortune",
            "initial_check",
            "account_name",
            "open_link,ui_controls,screenshot",
            '["account_name"]',
            '["open_link","ui_controls","screenshot"]',
            '{"max_scrolls":0}',
            15,
            "Online-doc check profile for account nickname in Ant Fortune.",
        ),
        (
            "alipay",
            "detail",
            "account_name,read_count,screenshot",
            "open_link,ui_controls,screenshot,tap_retry",
            '["account_name","read_count","screenshot"]',
            '["open_link","ui_controls","screenshot","tap_retry"]',
            '{"max_scrolls":0,"open_retries":"DOC_LINK_READS_OPEN_RETRIES"}',
            15,
            "Online-doc detail profile for account nickname, read count, and screenshot in Alipay.",
        ),
        (
            "antfortune",
            "detail",
            "account_name,read_count,screenshot",
            "open_link,ui_controls,screenshot,tap_retry",
            '["account_name","read_count","screenshot"]',
            '["open_link","ui_controls","screenshot","tap_retry"]',
            '{"max_scrolls":0,"open_retries":"DOC_LINK_READS_OPEN_RETRIES"}',
            15,
            "Online-doc detail profile for account nickname, read count, and screenshot in Ant Fortune.",
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO capture_action_profiles (
            app_type, task_type, field_combo, action_combo,
            field_combo_hash, action_combo_hash,
            field_names_json, action_names_json, capture_config_json,
            priority, description, updated_by
        )
        VALUES (
            %s, %s, %s, %s,
            SHA2(%s, 256), SHA2(%s, 256),
            %s, %s, %s,
            %s, %s, 'system'
        )
        ON DUPLICATE KEY UPDATE
            action_combo = VALUES(action_combo),
            action_combo_hash = VALUES(action_combo_hash),
            action_names_json = VALUES(action_names_json),
            capture_config_json = VALUES(capture_config_json),
            priority = VALUES(priority),
            description = VALUES(description),
            status = 'active',
            updated_by = 'system',
            updated_at = CURRENT_TIMESTAMP
        """,
        [
            (
                app_type,
                task_type,
                field_combo,
                action_combo,
                field_combo,
                action_combo,
                field_names_json,
                action_names_json,
                config_json,
                priority,
                description,
            )
            for (
                app_type,
                task_type,
                field_combo,
                action_combo,
                field_names_json,
                action_names_json,
                config_json,
                priority,
                description,
            ) in rows
        ],
    )
    cursor.execute(
        """
        UPDATE capture_action_profiles
        SET status = 'disabled',
            description = CONCAT(COALESCE(description, ''), ' Disabled: split into per-app profiles.'),
            updated_by = 'system',
            updated_at = CURRENT_TIMESTAMP
        WHERE app_type = 'alipay,antfortune'
          AND task_type IN ('initial_check', 'detail')
          AND status = 'active'
        """
    )


def _insert_default_profile_action_profiles(cursor) -> None:
    rows = [
        (
            "alipay_profile_daily_fans_v1",
            "alipay",
            "profile_daily_metrics",
            "fans_count,growth_count",
            "open_profile,capture_fans,open_exact_fans_if_abbreviated,writeback",
            '["fans_count","growth_count"]',
            '["open_profile","capture_fans","open_exact_fans_if_abbreviated","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":true},"holding":{"enabled":false}}',
            '{"growth_count":{"source":"previous_day_fans_count"}}',
            30,
            "Alipay profile fans metrics: exact fans when needed, no post read scan.",
        ),
        (
            "antfortune_profile_daily_fans_v1",
            "antfortune",
            "profile_daily_metrics",
            "fans_count,growth_count",
            "open_profile,capture_fans,open_exact_fans_if_abbreviated,writeback",
            '["fans_count","growth_count"]',
            '["open_profile","capture_fans","open_exact_fans_if_abbreviated","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":true},"holding":{"enabled":false}}',
            '{"growth_count":{"source":"previous_day_fans_count"}}',
            30,
            "Ant Fortune profile fans metrics: exact fans when needed, no post read scan.",
        ),
        (
            "tenpay_profile_daily_fans_v1",
            "tenpay",
            "profile_daily_metrics",
            "fans_count,growth_count",
            "reset_app,open_profile,capture_home,ui_controls,ocr,tenpay_counter_layout,open_exact_fans_if_abbreviated,verify_account_anchor,writeback",
            '["fans_count","growth_count"]',
            '["reset_app","open_profile","capture_home","ui_controls","ocr","tenpay_counter_layout","open_exact_fans_if_abbreviated","verify_account_anchor","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":true,"home_candidate_only_when_abbreviated":true,"ocr":true,"tenpay_counter_layout":true,"detail_page_pattern":"TA鐨勭矇涓?{count}浜?","require_account_anchor":true,"reject_stale_detail_page":true},"render_ready":{"initial_wait_seconds":8,"recapture_if_title_only":true,"recapture_wait_seconds":12},"recovery":{"reset_app_before_profile":true,"device_fail_fast":true},"holding":{"enabled":false}}',
            '{"growth_count":{"source":"previous_day_fans_count"}}',
            30,
            "Tenpay profile fans metrics: reset app, use UI/OCR, infer middle fans counter, no post read scan.",
        ),
        (
            "unknown_profile_daily_fans_v1",
            "unknown",
            "profile_daily_metrics",
            "fans_count,growth_count",
            "open_profile,capture_fans,writeback",
            '["fans_count","growth_count"]',
            '["open_profile","capture_fans","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":false},"holding":{"enabled":false}}',
            '{"growth_count":{"source":"previous_day_fans_count"}}',
            0,
            "Fallback profile fans metrics action profile.",
        ),
        (
            "alipay_profile_daily_metrics_v1",
            "alipay",
            "profile_daily_metrics",
            "fans_count,growth_count,read_count",
            "open_profile,capture_fans,open_exact_fans_if_abbreviated,scan_recent_posts,tap_post,capture_read_count,aggregate_max_recent_posts,writeback",
            '["fans_count","growth_count","read_count"]',
            '["open_profile","capture_fans","open_exact_fans_if_abbreviated","scan_recent_posts","tap_post","capture_read_count","aggregate_max_recent_posts","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":true},"read_count":{"recent_posts_limit":3,"ocr":false},"holding":{"enabled":false}}',
            '{"read_count":{"source":"recent_posts","method":"max","max_posts":3},"growth_count":{"source":"previous_day_fans_count"}}',
            20,
            "Alipay profile metrics: exact fans when needed, max read count from recent 3 posts.",
        ),
        (
            "antfortune_profile_daily_metrics_v1",
            "antfortune",
            "profile_daily_metrics",
            "fans_count,growth_count,read_count",
            "open_profile,capture_fans,open_exact_fans_if_abbreviated,scan_recent_posts,tap_post,capture_read_count,aggregate_max_recent_posts,writeback",
            '["fans_count","growth_count","read_count"]',
            '["open_profile","capture_fans","open_exact_fans_if_abbreviated","scan_recent_posts","tap_post","capture_read_count","aggregate_max_recent_posts","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":true},"read_count":{"recent_posts_limit":3,"ocr":false},"holding":{"enabled":false}}',
            '{"read_count":{"source":"recent_posts","method":"max","max_posts":3},"growth_count":{"source":"previous_day_fans_count"}}',
            20,
            "Ant Fortune profile metrics: exact fans when needed, max read count from recent 3 posts.",
        ),
        (
            "tenpay_profile_daily_metrics_v1",
            "tenpay",
            "profile_daily_metrics",
            "fans_count,growth_count,read_count",
            "reset_app,open_profile,capture_home,ui_controls,ocr,tenpay_counter_layout,open_exact_fans_if_abbreviated,verify_account_anchor,scan_recent_posts,tap_post,capture_read_count,aggregate_max_recent_posts,writeback",
            '["fans_count","growth_count","read_count"]',
            '["reset_app","open_profile","capture_home","ui_controls","ocr","tenpay_counter_layout","open_exact_fans_if_abbreviated","verify_account_anchor","scan_recent_posts","tap_post","capture_read_count","aggregate_max_recent_posts","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":true,"home_candidate_only_when_abbreviated":true,"ocr":true,"tenpay_counter_layout":true,"detail_page_pattern":"TA的粉丝({count}人)","require_account_anchor":true,"reject_stale_detail_page":true},"read_count":{"recent_posts_limit":3,"ocr":true,"aggregation":"max"},"render_ready":{"initial_wait_seconds":8,"recapture_if_title_only":true,"recapture_wait_seconds":12},"recovery":{"reset_app_before_profile":true,"device_fail_fast":true},"holding":{"enabled":false}}',
            '{"read_count":{"source":"recent_posts","method":"max","max_posts":3},"growth_count":{"source":"previous_day_fans_count"}}',
            10,
            "Tenpay profile metrics: reset app, use UI/OCR, infer middle fans counter, click exact fans page for abbreviated counts, and verify account anchor.",
        ),
        (
            "unknown_profile_daily_metrics_v1",
            "unknown",
            "profile_daily_metrics",
            "fans_count,growth_count,read_count",
            "open_profile,capture_fans,scan_recent_posts,tap_post,capture_read_count,aggregate_max_recent_posts,writeback",
            '["fans_count","growth_count","read_count"]',
            '["open_profile","capture_fans","scan_recent_posts","tap_post","capture_read_count","aggregate_max_recent_posts","writeback"]',
            '{"fans_count":{"exact_if_abbreviated":false},"read_count":{"recent_posts_limit":3,"ocr":false},"holding":{"enabled":false}}',
            '{"read_count":{"source":"recent_posts","method":"max","max_posts":3},"growth_count":{"source":"previous_day_fans_count"}}',
            0,
            "Fallback profile metrics action profile.",
        ),
    ]
    cursor.executemany(
        """
        INSERT INTO profile_action_profiles (
            action_profile_key, app_type, task_type, field_combo, action_combo,
            field_names_json, action_names_json, action_config_json,
            aggregation_policy_json, priority, description, updated_by
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'system')
        ON DUPLICATE KEY UPDATE
            action_combo = VALUES(action_combo),
            action_names_json = VALUES(action_names_json),
            action_config_json = VALUES(action_config_json),
            aggregation_policy_json = VALUES(aggregation_policy_json),
            priority = VALUES(priority),
            description = VALUES(description),
            status = 'active',
            updated_by = 'system',
            updated_at = CURRENT_TIMESTAMP
        """,
        rows,
    )
