# Legacy PowerShell env example.
# New deployments should prefer project-root .env, and it should contain MySQL only.

$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "your-mysql-password"
$env:MYSQL_DATABASE = "finance_crawler"

# Tencent Docs OpenAPI credentials are stored in MySQL app_config, not here:
# .\scripts\run.ps1 -Task config -ConfigSet "TENCENT_DOC_ACCESS_TOKEN=..."
