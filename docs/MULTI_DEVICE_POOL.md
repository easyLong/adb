# 多 ADB 设备池方案

目标：一台手机遇到风控、登录异常、页面整体不可用或掉线时，不让任务继续固定在这台设备上，而是把设备/App 会话冷却，后续任务切到其它可用设备。

## 核心链路

```text
任务队列
  -> DevicePool 按 app_type 选择可用设备
  -> 创建 adb_execution_leases
  -> 临时设置 DEVICE_SERIAL
  -> 执行 ADB 采集
  -> 按结果释放设备
  -> 成功生成写回；失败更新设备/App 会话健康状态
```

采集结果和写回仍然走原来的幂等链路：

```text
document: task_submissions -> task_executions -> writeback_plans
profile: profile_metric_sources -> profile_metric_runs -> profile_metric_writebacks
```

设备只是一段执行租约，不参与业务主键。

## 表

```text
adb_devices
adb_device_app_sessions
adb_execution_leases
```

`adb_devices` 记录手机本身是否在线、是否冷却、当前租约。设备按 `host_id + adb_serial` 隔离，避免 A 机器刷新设备池时把 B 机器连接的手机误标为 offline。

`adb_device_app_sessions` 记录某台手机上某个 App 的状态，比如 tenpay 是否风控、alipay 是否需要登录。

`adb_execution_leases` 记录一次任务使用了哪台设备，便于排查。

## 冷却规则

默认配置：

```text
DEVICE_POOL_ENABLED=true
DEVICE_POOL_HOST_ID=
DEVICE_LEASE_SECONDS=900
DEVICE_FAILURE_COOLDOWN_SECONDS=180
DEVICE_RISK_COOLDOWN_SECONDS=1800
DEVICE_UNAVAILABLE_COOLDOWN_SECONDS=300
DEVICE_LOGIN_COOLDOWN_SECONDS=86400
```

`DEVICE_POOL_HOST_ID` 为空时使用当前电脑名。多台机器共用同一个数据库时，每台机器只刷新和选择自己的 `host_id` 设备。

普通采集失败会让这台设备上的这个 `app_type` 短冷却，下一条同 App 任务会优先切到其它可用设备。

会触发风险冷却的典型错误：

```text
稍后再试
网络不给力
滑块/验证
profile page is unavailable
profile page is blocked
identity verification
too many requests
```

设备掉线/ADB 不可用走较短冷却。

登录异常走长冷却，避免持续打到未登录设备。

设备选择会优先最近成功、失败更少、最近在线的设备；最近失败的设备会被靠后排序。

## 常用命令

初始化表：

```powershell
.\scripts\run.ps1 -Task db
.\scripts\run.ps1 -Task crawler-app-db
```

刷新设备池：

```powershell
.\scripts\run.ps1 -Task device-pool-refresh
```

查看设备池状态：

```powershell
.\scripts\run.ps1 -Task device-pool-status
```

临时关闭设备池，回到旧的单设备逻辑：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet DEVICE_POOL_ENABLED=false
```

强制固定某台设备：

```powershell
.\scripts\run.ps1 -Task config -ConfigSet DEVICE_SERIAL=APH0219701010623
```

注意：设备池开启时，worker 会根据设备健康状态自动选择设备；`DEVICE_SERIAL` 主要用于关闭设备池后的旧单设备模式，或临时排查。

## 一致性原则

1. 任务不绑定设备。
2. 同一任务可以有多次 execution，但只以成功结果进入写回。
3. 写回前仍重新定位目标行，不依赖旧 row_index。
4. 设备风控只影响设备/App 会话，不改变任务业务身份。
5. 换设备时从动作模板第一步重新开始，不迁移页面中间状态。
6. 同一个 `host_id` 内设备互相切换；不同机器的设备互不抢占。
