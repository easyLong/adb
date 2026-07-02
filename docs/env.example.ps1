# Legacy PowerShell env example.
# New deployments should prefer project-root .env, and it should contain MySQL only.

$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "your-mysql-password"
$env:MYSQL_DATABASE = "crawler_app"
$env:MYSQL_OPS_DATABASE = "ops_platform"
$env:MYSQL_CONNECT_TIMEOUT = "10"
$env:MYSQL_READ_TIMEOUT = "30"
$env:MYSQL_WRITE_TIMEOUT = "30"
$env:MYSQL_CONNECT_RETRIES = "3"
$env:MYSQL_CONNECT_RETRY_DELAY = "2"
$env:MYSQL_CONNECT_RETRY_MAX_DELAY = "30"

# Tencent Docs OpenAPI credentials are stored in MySQL app_config, not here:
# .\scripts\run.ps1 -Task config -ConfigSet "TENCENT_DOC_ACCESS_TOKEN=..."
