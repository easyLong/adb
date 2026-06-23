"""Schema helpers for ADB device pool tables."""

from __future__ import annotations


def ensure_device_pool_tables(cursor) -> None:
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS adb_devices (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            host_id VARCHAR(191) NOT NULL DEFAULT '',
            adb_serial VARCHAR(191) NOT NULL,
            connect_type VARCHAR(32) NOT NULL DEFAULT 'unknown',
            model VARCHAR(128) NULL,
            product VARCHAR(128) NULL,
            device_name VARCHAR(128) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'online',
            last_seen_at DATETIME NULL,
            cooldown_until DATETIME NULL,
            last_error TEXT NULL,
            current_worker_id VARCHAR(128) NULL,
            lease_until DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_adb_devices_host_serial (host_id, adb_serial),
            INDEX idx_adb_devices_status (status, cooldown_until),
            INDEX idx_adb_devices_lease (lease_until),
            INDEX idx_adb_devices_seen (last_seen_at),
            INDEX idx_adb_devices_host_status (host_id, status, cooldown_until)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS adb_device_app_sessions (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            device_id BIGINT UNSIGNED NOT NULL,
            app_type VARCHAR(64) NOT NULL,
            account_key VARCHAR(191) NULL,
            login_status VARCHAR(32) NOT NULL DEFAULT 'unknown',
            risk_status VARCHAR(32) NOT NULL DEFAULT 'ok',
            cooldown_until DATETIME NULL,
            last_risk_reason TEXT NULL,
            success_count INT NOT NULL DEFAULT 0,
            failure_count INT NOT NULL DEFAULT 0,
            last_success_at DATETIME NULL,
            last_failure_at DATETIME NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            UNIQUE KEY uk_adb_device_app (device_id, app_type),
            INDEX idx_adb_device_app_status (app_type, risk_status, cooldown_until),
            INDEX idx_adb_device_app_login (app_type, login_status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    cursor.execute(
        """
        CREATE TABLE IF NOT EXISTS adb_execution_leases (
            id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
            host_id VARCHAR(191) NOT NULL DEFAULT '',
            task_scope VARCHAR(64) NOT NULL,
            task_id VARCHAR(191) NOT NULL,
            app_type VARCHAR(64) NOT NULL,
            device_id BIGINT UNSIGNED NULL,
            adb_serial VARCHAR(191) NOT NULL,
            lease_token CHAR(36) NOT NULL,
            worker_id VARCHAR(128) NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'running',
            error_type VARCHAR(64) NULL,
            error TEXT NULL,
            leased_until DATETIME NOT NULL,
            started_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            finished_at DATETIME NULL,
            UNIQUE KEY uk_adb_execution_lease_token (lease_token),
            INDEX idx_adb_execution_task (task_scope, task_id),
            INDEX idx_adb_execution_device (device_id, status),
            INDEX idx_adb_execution_status (status, leased_until),
            INDEX idx_adb_execution_host_status (host_id, status, leased_until)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
    )
    _migrate_device_pool_tables(cursor)


def _migrate_device_pool_tables(cursor) -> None:
    _ensure_column(cursor, "adb_devices", "host_id", "VARCHAR(191) NOT NULL DEFAULT '' AFTER id")
    _ensure_column(cursor, "adb_execution_leases", "host_id", "VARCHAR(191) NOT NULL DEFAULT '' AFTER id")
    _ensure_index(cursor, "adb_devices", "uk_adb_devices_host_serial", "UNIQUE KEY", "(`host_id`, `adb_serial`)")
    _ensure_index(cursor, "adb_devices", "idx_adb_devices_host_status", "INDEX", "(`host_id`, `status`, `cooldown_until`)")
    _ensure_index(cursor, "adb_execution_leases", "idx_adb_execution_host_status", "INDEX", "(`host_id`, `status`, `leased_until`)")
    _drop_index_if_exists(cursor, "adb_devices", "uk_adb_devices_serial")


def _ensure_column(cursor, table_name: str, column_name: str, definition: str) -> None:
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}` LIKE %s", (column_name,))
    if cursor.fetchone():
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD COLUMN `{column_name}` {definition}")


def _ensure_index(cursor, table_name: str, index_name: str, index_type: str, columns: str) -> None:
    cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = %s", (index_name,))
    if cursor.fetchone():
        return
    cursor.execute(f"ALTER TABLE `{table_name}` ADD {index_type} `{index_name}` {columns}")


def _drop_index_if_exists(cursor, table_name: str, index_name: str) -> None:
    cursor.execute(f"SHOW INDEX FROM `{table_name}` WHERE Key_name = %s", (index_name,))
    if not cursor.fetchone():
        return
    cursor.execute(f"ALTER TABLE `{table_name}` DROP INDEX `{index_name}`")
