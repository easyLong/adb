CREATE DATABASE IF NOT EXISTS finance_crawler
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE finance_crawler;

CREATE TABLE IF NOT EXISTS data_source_links (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source_key VARCHAR(128) NOT NULL,
    data_source_link TEXT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    description TEXT NULL,
    updated_by VARCHAR(64) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_source_key (source_key),
    INDEX idx_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO data_source_links (source_key, data_source_link, status, description, updated_by)
VALUES
    ('TENCENT_DOC_URL', '', 'active', 'Tencent Docs source URL', 'system'),
    ('EXCEL_DETAIL_INPUT_PATH', '', 'unavailable', 'Local Excel detail input path', 'system'),
    ('SINGLE_TEST_LINK', '', 'unavailable', 'One-shot single detail test link', 'system')
ON DUPLICATE KEY UPDATE
    description = VALUES(description);

CREATE TABLE IF NOT EXISTS app_config (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    config_key VARCHAR(128) NOT NULL,
    config_value TEXT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    is_secret TINYINT NOT NULL DEFAULT 0,
    description VARCHAR(255) NULL,
    updated_by VARCHAR(64) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_app_config_key (config_key),
    INDEX idx_app_config_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO app_config (config_key, config_value, status, is_secret, description, updated_by)
VALUES
    ('TENCENT_DOC_CLIENT_ID', '', 'unavailable', 1, 'Tencent Docs OpenAPI Client-Id', 'system'),
    ('TENCENT_DOC_OPEN_ID', '', 'unavailable', 1, 'Tencent Docs OpenAPI Open-Id', 'system'),
    ('TENCENT_DOC_ACCESS_TOKEN', '', 'unavailable', 1, 'Tencent Docs OpenAPI Access-Token', 'system'),
    ('TENCENT_DOC_CLIENT_SECRET', '', 'unavailable', 1, 'Tencent Docs OpenAPI Client-Secret', 'system'),
    ('TENCENT_DOC_TOKEN_URL', 'https://docs.qq.com/oauth/v2/token', 'active', 0, 'Tencent Docs OpenAPI token URL', 'system')
ON DUPLICATE KEY UPDATE
    is_secret = VALUES(is_secret),
    description = VALUES(description);

CREATE TABLE IF NOT EXISTS crawl_sources (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source_type VARCHAR(64) NOT NULL,
    name VARCHAR(128) NOT NULL,
    config_json LONGTEXT NULL,
    enabled TINYINT DEFAULT 1,
    last_fetched_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_source_type_name (source_type, name),
    INDEX idx_source_type (source_type),
    INDEX idx_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawler_apps (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    app_type VARCHAR(64) NOT NULL,
    display_name VARCHAR(128) NOT NULL,
    package_name VARCHAR(128) NULL,
    enabled TINYINT DEFAULT 1,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_app_type (app_type),
    INDEX idx_enabled (enabled)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT INTO crawler_apps (app_type, display_name, package_name)
VALUES
    ('alipay', 'Alipay', 'com.eg.android.AlipayGphone'),
    ('antfortune', 'Ant Fortune', 'com.antfortune.wealth'),
    ('tenpay', 'Tenpay / Tencent Wealth', 'com.tencent.fortuneplat'),
    ('unknown', 'Unknown App', NULL)
ON DUPLICATE KEY UPDATE
    display_name = VALUES(display_name),
    package_name = VALUES(package_name),
    enabled = 1;

CREATE TABLE IF NOT EXISTS crawl_jobs (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    job_type VARCHAR(64) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'running',
    source_id BIGINT UNSIGNED NULL,
    started_at DATETIME NOT NULL,
    finished_at DATETIME NULL,
    summary_json LONGTEXT NULL,
    error TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_job_type (job_type),
    INDEX idx_status (status),
    INDEX idx_source_id (source_id),
    INDEX idx_started_at (started_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawl_results (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    task_id BIGINT UNSIGNED NULL,
    app_type VARCHAR(64) NOT NULL,
    url VARCHAR(1000) NOT NULL,
    workflow VARCHAR(64) NULL,
    status VARCHAR(32) NOT NULL,
    account_name VARCHAR(255) NULL,
    content MEDIUMTEXT NULL,
    metrics_json LONGTEXT NULL,
    screenshot_path VARCHAR(700) NULL,
    error TEXT NULL,
    crawled_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_result_task (task_id),
    INDEX idx_result_app (app_type),
    INDEX idx_result_workflow (workflow),
    INDEX idx_result_status (status),
    INDEX idx_result_url (url(191)),
    INDEX idx_crawled_at (crawled_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawl_writebacks (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    task_id BIGINT UNSIGNED NULL,
    result_id BIGINT UNSIGNED NULL,
    sink_type VARCHAR(64) NOT NULL,
    sink_locator_json LONGTEXT NULL,
    status VARCHAR(32) NOT NULL,
    error TEXT NULL,
    written_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_writeback_task (task_id),
    INDEX idx_writeback_result (result_id),
    INDEX idx_writeback_sink (sink_type),
    INDEX idx_writeback_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawl_task_submissions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    task_type VARCHAR(64) NOT NULL DEFAULT 'detail_crawl',
    source_id BIGINT UNSIGNED NULL,
    source_type VARCHAR(64) NOT NULL,
    source_name VARCHAR(191) NULL,
    -- URL-based task object key. Row/file/sheet positions belong in source_locator_json.
    crawl_object_key VARCHAR(191) NOT NULL,
    source_locator_json LONGTEXT NULL,
    app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
    original_url VARCHAR(1000) NOT NULL,
    canonical_url VARCHAR(1000) NULL,
    source_time DATETIME NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    priority INT NOT NULL DEFAULT 0,
    scheduled_at DATETIME NULL,
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    latest_execution_id BIGINT UNSIGNED NULL,
    last_error TEXT NULL,
    result_summary_json LONGTEXT NULL,
    created_by VARCHAR(64) NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_submission_record (source_type, crawl_object_key, task_type),
    INDEX idx_submission_status (status),
    INDEX idx_submission_app (app_type),
    INDEX idx_submission_source (source_id),
    INDEX idx_submission_object_task (task_type, crawl_object_key, status),
    INDEX idx_submission_schedule (scheduled_at),
    INDEX idx_submission_priority (priority),
    INDEX idx_submission_latest_execution (latest_execution_id),
    INDEX idx_submission_url (original_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawl_task_executions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    submission_id BIGINT UNSIGNED NOT NULL,
    job_id BIGINT UNSIGNED NULL,
    attempt_no INT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'queued',
    worker_id VARCHAR(128) NULL,
    app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
    url VARCHAR(1000) NOT NULL,
    account_name VARCHAR(255) NULL,
    content MEDIUMTEXT NULL,
    metrics_json LONGTEXT NULL,
    result_json LONGTEXT NULL,
    screenshot_path VARCHAR(700) NULL,
    writeback_status VARCHAR(32) NULL,
    writeback_locator_json LONGTEXT NULL,
    writeback_error TEXT NULL,
    error TEXT NULL,
    started_at DATETIME NULL,
    heartbeat_at DATETIME NULL,
    finished_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_submission_attempt (submission_id, attempt_no),
    INDEX idx_execution_submission (submission_id),
    INDEX idx_execution_job (job_id),
    INDEX idx_execution_status (status),
    INDEX idx_execution_app (app_type),
    INDEX idx_execution_started (started_at),
    INDEX idx_execution_finished (finished_at),
    INDEX idx_execution_url (url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS task_log (
    id INT AUTO_INCREMENT PRIMARY KEY,
    task_name VARCHAR(80) NOT NULL,
    status VARCHAR(20) NOT NULL,
    message TEXT NULL,
    duration FLOAT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_task_name (task_name),
    INDEX idx_created_at (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS article_detail_targets (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    article_key VARCHAR(191) NOT NULL,
    ip_name VARCHAR(255) NULL,
    product_name VARCHAR(255) NULL,
    app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
    article_url VARCHAR(1000) NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    source_json LONGTEXT NULL,
    first_seen_date DATE NULL,
    latest_seen_date DATE NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_article_key (article_key),
    INDEX idx_article_detail_status (status),
    INDEX idx_article_detail_app (app_type),
    INDEX idx_article_detail_url (article_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS article_detail_sources (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    target_id BIGINT UNSIGNED NOT NULL,
    source_date DATE NULL,
    source_type VARCHAR(64) NOT NULL DEFAULT 'tencent_docs',
    source_name VARCHAR(191) NULL,
    source_key VARCHAR(191) NOT NULL,
    source_locator_json LONGTEXT NULL,
    requested_fields_json LONGTEXT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'active',
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    last_error TEXT NULL,
    latest_run_id BIGINT UNSIGNED NULL,
    writeback_status VARCHAR(32) NULL,
    writeback_error TEXT NULL,
    written_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_article_detail_source (source_type, source_key),
    INDEX idx_article_detail_source_target (target_id),
    INDEX idx_article_detail_source_date (source_date),
    INDEX idx_article_detail_source_status (status),
    INDEX idx_article_detail_writeback (writeback_status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS article_detail_runs (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    target_id BIGINT UNSIGNED NOT NULL,
    source_id BIGINT UNSIGNED NULL,
    app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
    article_url VARCHAR(1000) NOT NULL,
    status VARCHAR(32) NOT NULL,
    article_title TEXT NULL,
    read_count INT NULL,
    comment_count INT NULL,
    like_count INT NULL,
    metrics_json LONGTEXT NULL,
    screenshot_path VARCHAR(700) NULL,
    error TEXT NULL,
    crawled_at DATETIME NOT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_article_detail_run_target (target_id),
    INDEX idx_article_detail_run_source (source_id),
    INDEX idx_article_detail_run_status (status),
    INDEX idx_article_detail_run_app (app_type),
    INDEX idx_article_detail_run_crawled (crawled_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS article_detail_writebacks (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    source_id BIGINT UNSIGNED NOT NULL,
    run_id BIGINT UNSIGNED NULL,
    sink_type VARCHAR(64) NOT NULL DEFAULT 'tencent_docs',
    sink_locator_json LONGTEXT NULL,
    field_name VARCHAR(64) NOT NULL DEFAULT 'article_detail',
    status VARCHAR(32) NOT NULL,
    error TEXT NULL,
    written_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_article_detail_writeback_field (source_id, field_name),
    INDEX idx_article_detail_writeback_source (source_id),
    INDEX idx_article_detail_writeback_run (run_id),
    INDEX idx_article_detail_writeback_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
