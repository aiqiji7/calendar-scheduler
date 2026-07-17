# CLAUDE.md
This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

Outlook Calendar Scheduler 是一个通过 Microsoft Graph API 管理 Outlook 日历的 Python 技能。支持 OAuth2 认证、事件增删改查、智能排程推荐、自然语言解析和用户偏好学习。

## 常用命令

### 环境设置
```bash
# 安装依赖
uv sync

# 配置环境变量（必需）
export AZURE_CLIENT_ID="your-client-id"
export AZURE_TENANT_ID="your-tenant-id"
export AZURE_CLIENT_SECRET="your-client-secret"
export REDIRECT_URI="http://localhost:8000/auth/microsoft/callback"
```

### 运行程序
```bash
# 首次认证（必须在其他命令前执行）
python run_outlook_calendar.py auth

# 列出所有日历
python run_outlook_calendar.py list-calendars

# 查询事件
python run_outlook_calendar.py list-events --start 2025-04-21 --end 2025-04-28

# 创建事件（直接写入）
python run_outlook_calendar.py create-event "团队会议" --start 2025-04-22T14:00 --end 2025-04-22T15:00
python run_outlook_calendar.py create-event --text "明天下午2点开会"

# 智能排程
python run_outlook_calendar.py suggest "项目评审会" --duration 120 --deadline friday

# 删除事件
python run_outlook_calendar.py delete-event <event-id>

# 查看状态
python run_outlook_calendar.py status
```

### Python 直接调用
```python
from scripts.auth import get_auth_manager
from scripts.calendar_api import get_calendar_client
from scripts.scheduler import get_scheduler
from scripts.nlp_parser import parse_event

# 解析自然语言
parsed = parse_event("明天下午2点开会")
# 返回: {title, start, end, duration_minutes, location, attendees, constraints}

# 获取日历客户端
client = get_calendar_client()
events = client.list_events(start_time=start, end_time=end)
event = client.create_event(subject, start_time, end_time, ...)

# 智能排程
scheduler = get_scheduler()
suggestions = scheduler.suggest_times(
    duration_minutes=60,
    constraints={},
    existing_events=events,
    lookahead_days=7,
    num_suggestions=3
)
```

## 架构概述

### 模块职责

| 模块 | 职责 |
|------|------|
| `auth.py` | OAuth2 认证管理，使用 MSAL 库，令牌缓存在 `.outlook_calendar_token_cache.json` |
| `calendar_api.py` | Graph API 封装，处理 REST 请求和响应转换 |
| `scheduler.py` | 排程算法：生成候选时间段，根据缓冲冲突、用户偏好计算适宜度分数 |
| `nlp_parser.py` | 自然语言解析，提取时间、地点、参会者、时长等信息 |
| `preference_learner.py` | 记录用户选择历史，学习偏好模式，存储在 `.outlook_calendar_preferences.json` 和 `.outlook_calendar_history.json` |
| `timezone_utils.py` | UTC/本地时区转换，夏令时处理 |
| `main.py` | CLI 入口，包含所有子命令处理函数 |

### 数据流

```
用户输入
  → nlp_parser.parse_event() 提取结构化信息（含 is_completed）
    → 如果是已确认/已完成事件 → apply_completed_title_if_needed() 追加后缀 [已完成]
      → calendar_api.create_event() 直接写入
    → 如果是未来事件 → scheduler.suggest_times() 推荐时间段
      → preference_learner 记录用户选择
        → calendar_api.create_event() 写入选中的时间段
```

### 已完成事件标记

当写入日历时识别到事件已完成/已发生，标题末尾自动追加 `[已完成]`：
- 文本含完成语义：`已完成`、`完成了`、`做完了`、`done` 等
- 或事件结束时间已过去
- 示例：`今天下午3点开会已经完成` → 标题 `开会[已完成]`

### 关键依赖

- `msal==1.28.0` - Microsoft 认证库
- `requests` - HTTP 请求
- `dateparser` - 自然语言日期解析
- `python-dateutil` + `pytz` - 时区处理
- `python-dotenv` - 环境变量加载

### 配置文件

- 用户偏好：`.outlook_calendar_preferences.json`
- 历史记录：`.outlook_calendar_history.json`
- 令牌缓存：`.outlook_calendar_token_cache.json`

默认工作时间是 09:00-18:00，缓冲时间为15分钟，避开午休（12:00-14:00）。
