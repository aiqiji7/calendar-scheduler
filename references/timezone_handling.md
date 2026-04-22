# 时区处理指南

## 核心原则

1. **所有 Graph API 调用使用 UTC 时间**
   - API 接受的 `dateTime` 字段需配合 `timeZone` 参数
   - 或直接发送 UTC 时间并在 `timeZone` 中指定 "UTC"

2. **本地显示时转换为用户时区**
   - 从 API 返回的时间根据用户时区转换后再展示

3. **时区标识**
   - Windows: `China Standard Time` (中国标准时间)
   - IANA: `Asia/Shanghai` (用于 dateutil/pytz)
   - Graph API 支持的列表见 [时区值参考](https://learn.microsoft.com/en-us/graph/api/resources/datetimezone)

## 常见时区值

| 地区 | Windows 时区名 | IANA 时区名 |
|------|---------------|-------------|
| 中国 (上海) | China Standard Time | Asia/Shanghai |
| 美国 (东部) | Eastern Standard Time | America/New_York |
| 日本 | Tokyo Standard Time | Asia/Tokyo |
| 英国 | GMT Standard Time | Europe/London |

## 示例流程

### 创建事件（本地输入 → UTC 发送）

```python
from datetime import datetime
from timezone_utils import get_timezone_handler

tz_handler = get_timezone_handler("Asia/Shanghai")

# 用户本地时间
local_start = datetime(2025, 4, 22, 14, 0)  # 4月22日 14:00 (上海)
local_end = datetime(2025, 4, 22, 15, 0)

# 转换为 UTC 格式供 API 使用
utc_start = tz_handler.to_utc(local_start)
utc_end = tz_handler.to_utc(local_end)

# Graph API 请求体
event_data = {
    "start": {
        "dateTime": utc_start.isoformat(),
        "timeZone": "UTC"
    },
    "end": {
        "dateTime": utc_end.isoformat(),
        "timeZone": "UTC"
    }
}
```

或更简单的方式（让 Graph API 处理时区转换）:

```python
event_data = {
    "start": {
        "dateTime": local_start.isoformat(),  # ISO 格式本地时间
        "timeZone": "China Standard Time"     # Windows 时区名
    },
    "end": {
        "dateTime": local_end.isoformat(),
        "timeZone": "China Standard Time"
    }
}
```

### 读取事件（UTC 返回 → 本地显示）

```python
from dateutil import parser

# Graph API 返回
api_start = "2025-04-22T06:00:00Z"  # UTC

# 解析并转换为本地时间
utc_dt = parser.parse(api_start)  # 自动识别 UTC
local_dt = tz_handler.from_utc(utc_dt)
print(local_dt.strftime("%Y-%m-%d %H:%M"))  # 2025-04-22 14:00
```

## 夏令时处理

- 使用时区对象自动处理 DST 切换
- `dateutil.tz.gettz()` 和 `pytz` 都会自动适应夏令时
- 建议统一使用 `dateutil.tz` 保持一致性

```python
from dateutil import tz

ny_tz = tz.gettz("America/New_York")
dt_ny = datetime(2025, 7, 1, 9, 0, tzinfo=ny_tz)  # 夏令时期间
utc_dt = dt_ny.astimezone(tz.UTC)
```

## 用户时区获取

可从 Graph API 获取用户时区设置:

```python
from calendar_api import get_calendar_client

client = get_calendar_client()
user = client._request("GET", "/me")
user_timezone = user.get("mailboxSettings", {}).get("timeZone", "UTC")
```

后续可集成到偏好系统，自动适配每个用户的时区。

## 错误排查

| 问题 | 可能原因 | 解决方案 |
|------|---------|----------|
| 事件时间偏差 1 小时 | 时区未正确指定或 DST 未处理 | 使用 Windows 时区名或显式转换 UTC |
| 跨时区会议时间错乱 | 参会者时区不同 | 明确指定 `timeZone` 字段 |
| 缓存时间错误 | 缓存中的 datetime 对象带时区信息 | 序列化时使用 `.isoformat()` |
