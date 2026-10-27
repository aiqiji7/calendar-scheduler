# Calendar Scheduler

一个日程安排SKILL，通过 **Microsoft Graph API** 或 **CalDAV** 管理日历的 Python 工具，支持事件增删改查、智能排程推荐、自然语言解析和多时区处理。内置 Graph 与 CalDAV 双后端，事件契约统一，排程逻辑与后端无关。

## 特性

- **双后端**：`graph`（Outlook / 个人 Microsoft 账户，OAuth2 + MSAL）与 `caldav`（QQ 邮箱等，HTTP Basic + 16 位授权码）
- **日历操作**：列出日历、查询/创建/更新/删除事件
- **智能排程**：扫描日历空闲时间，按工作时间、缓冲时间、午休避让与历史偏好打分推荐
- **自然语言解析**：`明天下午2点开会` → 结构化事件
- **已完成事件标记**：识别已完成或已发生的事件，写入时在标题末尾追加 `[已完成]`
- **偏好学习**：记录用户选择，沉淀工作时间与会议习惯

## 快速开始

```bash
# 1. 安装依赖
uv sync

# 2. 配置环境变量：从模板复制后填值（.env 已被 .gitignore 忽略）
cp .env.example .env        # Windows: copy .env.example .env

#    Graph：
CALENDAR_BACKEND=graph
AZURE_CLIENT_ID="your-client-id"
AZURE_TENANT_ID="your-tenant-id"
AZURE_CLIENT_SECRET="your-client-secret"
REDIRECT_URI="http://localhost:8000/auth/microsoft/callback"

#    CalDAV（QQ 邮箱，需先在网页端开启 CalDAV 并生成授权码）：
CALENDAR_BACKEND=caldav
CALDAV_BASE_URL=https://dav.qq.com
CALDAV_USERNAME=123456789@qq.com
CALDAV_PASSWORD=16位授权码

# 3. 首次认证（CalDAV 下为一次只读连接探针）
python run_calendar.py auth
```

`CALENDAR_BACKEND` 留空时自动推断：配置了 `CALDAV_USERNAME` 且未配置 `AZURE_CLIENT_ID` 则走 `caldav`，否则走 `graph`。完整环境变量清单见 [SKILL.md](SKILL.md#configuration)。

## 常用命令

```bash
python run_calendar.py list-calendars
python run_calendar.py list-events --start 2025-04-21 --end 2025-04-28
python run_calendar.py create-event "团队会议" --start 2025-04-22T14:00 --end 2025-04-22T15:00
python run_calendar.py create-event --text "明天下午2点开会"
python run_calendar.py update-event <event-id> --title "团队会议(改期)" --start 2025-04-23T14:00 --end 2025-04-23T15:00
python run_calendar.py suggest "项目评审会" --duration 120 --days 7 --num 3
python run_calendar.py delete-event <event-id>
python run_calendar.py status
```

`run_calendar.py` 是可移植启动器：优先使用 `uv`，找不到时回退到缓存虚拟环境；自检命令 `python run_calendar.py --launcher-self-test` 不触发认证，也不调用 Graph API。

## Python 调用

```python
from scripts.backend import get_backend

client = get_backend()                       # 也可显式传 "graph" / "caldav"
events = client.list_events(start_time=start, end_time=end)
```

需在仓库根目录运行（或自行把根目录加入 `sys.path`）；`scripts/__init__.py` 会自动加载 `.env`。跨模块导入采用「相对导入优先 + 裸导入回退」，包导入与脚本运行两种方式都可用。`scripts/calendar_api.py` 是旧入口兼容层，新代码请直接用 `scripts.backend.get_backend`。

## 目录结构

| 路径 | 说明 |
|------|------|
| `run_calendar.py` | 可移植启动器 |
| `scripts/backend.py` | 后端抽象、事件契约、`get_backend()` |
| `scripts/graph_backend.py` | Graph REST 实现 |
| `scripts/caldav_backend.py`、`scripts/caldav_dav.py` | CalDAV 裸 DAV 实现 |
| `scripts/scheduler.py` | 排程算法 |
| `scripts/nlp_parser.py` | 自然语言解析 |
| `scripts/preference_learner.py` | 偏好学习 |
| `scripts/timezone_utils.py` | 时区与夏令时处理 |
| `references/` | Graph API、OAuth2 配置、时区处理参考文档 |

## 已知限制（QQ 邮箱实测）

CalDAV 后端在 QQ 邮箱上存在若干服务端限制：单资源 GET 恒为 404、`If-Match` 被忽略（写入为 last-write-wins）、`C:expand` 被忽略（递归事件在本地展开）、不支持修改递归系列中的单个实例。完整清单与规避方式见 [SKILL.md](SKILL.md#caldav-已知限制qq-邮箱实测)。

## 文档

- [SKILL.md](SKILL.md) — 权威使用文档：配置、完整命令、模块说明、排障
- [AGENTS.md](AGENTS.md) — 面向 AI 编码 agent 的仓库约束与验证方式
- [references/](references/) — Graph API / OAuth2 / 时区细节
- [.env.example](.env.example) — 环境变量模板（复制为 `.env` 后填值）

## 许可证

[MIT](LICENSE) © 2026 aiqiji7
