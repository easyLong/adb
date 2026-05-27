CREATE DATABASE IF NOT EXISTS alipay_crawler
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE alipay_crawler;

CREATE TABLE IF NOT EXISTS posts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    url VARCHAR(700) NOT NULL UNIQUE,
    source_app VARCHAR(32) NOT NULL DEFAULT 'unknown',
    post_time DATETIME NOT NULL,
    doc_row_index INT NULL COMMENT 'Tencent sheet row number, 1-based',
    doc_file_id VARCHAR(128) NULL,
    doc_sheet_id VARCHAR(128) NULL,
    fetched_at DATETIME NOT NULL,
    last_seen_at DATETIME NOT NULL,

    check_status VARCHAR(20) DEFAULT 'pending',
    check_time DATETIME NULL,
    check_error TEXT NULL,
    check_retries INT DEFAULT 0,
    account_name VARCHAR(255) NULL,

    content MEDIUMTEXT NULL,
    read_count INT DEFAULT 0,
    comment_count INT DEFAULT 0,
    screenshot_path VARCHAR(700) NULL,
    batch_status VARCHAR(20) DEFAULT 'pending',
    batch_time DATETIME NULL,
    batch_error TEXT NULL,
    batch_retries INT DEFAULT 0,

    written_back TINYINT DEFAULT 0,
    written_back_at DATETIME NULL,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    INDEX idx_post_time (post_time),
    INDEX idx_check_status (check_status),
    INDEX idx_batch_status (batch_status),
    INDEX idx_written_back (written_back),
    INDEX idx_doc_row (doc_file_id, doc_sheet_id, doc_row_index)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

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

CREATE TABLE IF NOT EXISTS crawl_tasks (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    job_id BIGINT UNSIGNED NULL,
    legacy_post_id INT NULL,
    source_id BIGINT UNSIGNED NULL,
    source_type VARCHAR(64) NOT NULL,
    source_record_key VARCHAR(191) NOT NULL,
    source_locator_json LONGTEXT NULL,
    app_type VARCHAR(64) NOT NULL DEFAULT 'unknown',
    original_url VARCHAR(1000) NOT NULL,
    canonical_url VARCHAR(1000) NULL,
    source_time DATETIME NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'pending',
    priority INT NOT NULL DEFAULT 0,
    scheduled_at DATETIME NULL,
    locked_at DATETIME NULL,
    attempts INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 3,
    error TEXT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uk_source_record (source_type, source_record_key),
    INDEX idx_task_status (status),
    INDEX idx_task_app (app_type),
    INDEX idx_task_source (source_id),
    INDEX idx_task_job (job_id),
    INDEX idx_legacy_post (legacy_post_id),
    INDEX idx_original_url (original_url(191))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawl_results (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    task_id BIGINT UNSIGNED NULL,
    legacy_post_id INT NULL,
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
    INDEX idx_result_legacy_post (legacy_post_id),
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
    legacy_post_id INT NULL,
    sink_type VARCHAR(64) NOT NULL,
    sink_locator_json LONGTEXT NULL,
    status VARCHAR(32) NOT NULL,
    error TEXT NULL,
    written_at DATETIME NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_writeback_task (task_id),
    INDEX idx_writeback_result (result_id),
    INDEX idx_writeback_legacy_post (legacy_post_id),
    INDEX idx_writeback_sink (sink_type),
    INDEX idx_writeback_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS crawl_task_submissions (
    id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
    task_type VARCHAR(64) NOT NULL DEFAULT 'batch',
    source_id BIGINT UNSIGNED NULL,
    source_type VARCHAR(64) NOT NULL,
    source_name VARCHAR(191) NULL,
    source_record_key VARCHAR(191) NOT NULL,
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
    UNIQUE KEY uk_submission_record (source_type, source_record_key, task_type),
    INDEX idx_submission_status (status),
    INDEX idx_submission_app (app_type),
    INDEX idx_submission_source (source_id),
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
