# 定时任务在国内机器上差 8 小时

## 症状

`crontab` 里写 `0 9 * * *`（想每天九点跑），实际每天下午五点才动；日志里时间戳看着正常，产物时间对不上。换一台机器又准了。

## 原因

三个时区来源打架，排查时只看了其中一个：

1. **系统时区**（`/etc/timezone` 或 `timedatectl`）—— crond 按它解释表达式。
2. **crond 自己的环境变量**，某些版本读 `/etc/sysconfig/cron` 里的 `CRON_TZ`。
3. **进程内的时区**（Python 的 `TZ` / `tzdata`）—— 产物里的时间戳按它写。

系统时区是 UTC、脚本里按本地时间处理，就会整体偏一个时区差。

## 做法

1. 先确认 crond 实际按什么算：`timedatectl`（看 `Time zone`），再到日志 `journalctl -u cron` 里找被调起的时间点对不对得上。
2. 表达式统一按系统时区写；脚本里**显式**处理时区，不依赖默认：

   ```python
   from zoneinfo import ZoneInfo
   from datetime import datetime
   now = datetime.now(ZoneInfo("Asia/Shanghai"))
   ```

3. 容器的场景更要注意：容器默认 UTC，`-e TZ=Asia/Shanghai` 只影响进程内的库，不影响宿主 crond。

## 验证

把任务临时改成每分钟一次，打印 `date` 与 Python 里 `datetime.now()` 两个时间，两者都该是目标时区；都对之后再把表达式改回每天九点。
