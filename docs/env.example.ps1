$env:TENCENT_DOC_URL = "https://docs.qq.com/sheet/DY1hCSG96TkVySmp1?tab=BB08J2"
$env:TENCENT_DOC_FILE_ID = "DY1hCSG96TkVySmp1"
$env:TENCENT_DOC_SHEET_ID = "BB08J2"
$env:TENCENT_DOC_READ_RANGE = "A1:P625"

$env:TENCENT_DOC_CLIENT_ID = "your-client-id"
$env:TENCENT_DOC_OPEN_ID = "your-open-id"
$env:TENCENT_DOC_ACCESS_TOKEN = "your-access-token"
# Optional: used only when ACCESS_TOKEN is not set.
$env:TENCENT_DOC_CLIENT_SECRET = "your-client-secret"

$env:MYSQL_HOST = "localhost"
$env:MYSQL_PORT = "3306"
$env:MYSQL_USER = "root"
$env:MYSQL_PASSWORD = "your-mysql-password"
$env:MYSQL_DATABASE = "post_supplement_lib"

$env:ADB_PATH = "D:\Code\adb\platform-tools\adb.exe"
$env:DEVICE_SERIAL = "your-adb-device-serial"

$env:FETCH_INTERVAL_MINUTES = "5"
$env:CHECK_INTERVAL_MINUTES = "10"
$env:POST_ELIGIBLE_HOURS = "2"
$env:FETCH_LIMIT = "10"
$env:BATCH_LIMIT = "0"
$env:BATCH_TIME = "10:00"
$env:REPORT_TIME = "11:30"
$env:SCROLL_TIMES = "2"

# Zero-based column indexes in the test sheet:
# J=post time, L=account/check result, N=post URL, O=read count, P=comment count, Q=batch status.
$env:TENCENT_DOC_COL_POST_TIME = "9"
$env:TENCENT_DOC_COL_ACCOUNT_NAME = "11"
$env:TENCENT_DOC_COL_URL = "13"
$env:TENCENT_DOC_COL_READ_COUNT = "14"
$env:TENCENT_DOC_COL_COMMENT_COUNT = "15"
$env:TENCENT_DOC_COL_BATCH_STATUS = "16"

$env:ENABLE_CHECKER = "true"
$env:BATCH_REQUIRES_CHECK_SUCCESS = "true"
$env:BATCH_NEXT_DAY_ONLY = "true"
