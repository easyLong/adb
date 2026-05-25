CREATE DATABASE IF NOT EXISTS alipay_crawler
    DEFAULT CHARACTER SET utf8mb4
    DEFAULT COLLATE utf8mb4_unicode_ci;

USE alipay_crawler;

CREATE TABLE IF NOT EXISTS posts (
    id INT AUTO_INCREMENT PRIMARY KEY,
    url VARCHAR(700) NOT NULL UNIQUE,
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
