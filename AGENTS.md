# AGENTS.md

面向编码 agent 的仓库约束。**文档权威源是 [SKILL.md](SKILL.md)**：命令、环境变量、模块说明一律以那里为准，本文件不复制清单，只记录代码里看不出来、且容易踩错的东西。

> 兼容性：本文件需 Claude Code **v2.1.277+** 才会被自动读取（仓库内无 `CLAUDE.md` 时生效）。低版本请在本目录自建 `CLAUDE.md`，内容只写一行 `@AGENTS.md`。

## 验证方式

```bash
# 无需认证、不调用 Graph API，仅校验启动器能定位 scripts/main.py 与 uv
python run_calendar.py --launcher-self-test

# 查看当前后端、账号与偏好状态（需已配置 .env）
python run_calendar.py status
```

改动 CLI 或后端后，至少跑通上面两条；涉及日历读写的行为变更，用 `list-events` / `create-event` 在真实账号上验证再声明完成。

## 结构要点

- `run_calendar.py` → `scripts/main.py`：启动器负责定位脚本与 uv，业务逻辑不要写进启动器。
- `scripts/backend.py`：后端 Protocol、12 键事件契约、`get_backend()`（显式参数 > `CALENDAR_BACKEND` > 自动推断）。
- 新增/修改能力必须同时适配 `graph_backend.py` 与 `caldav_backend.py`，契约不一致会直接破坏排程与 NLP 链路。
- `scripts/calendar_api.py` 只是旧入口兼容层，新代码不要用。

## 非显然约束

- **导入方式**：跨模块导入统一写「相对导入优先 + 裸导入回退」（`try: from .x import y` / `except ImportError: from x import y`），因此包导入（`from scripts.backend import ...`）与脚本运行（`python run_calendar.py`、`python scripts/main.py`）都可用。新增模块照此模式，**不要再往 `sys.path` 里塞路径**，否则会遮蔽宿主项目的同名模块（`backend`、`scheduler` 等都过于通用）。
- **时区**：API 层统一 UTC，展示层转本地时区；Graph 收到不带偏移的查询时间时按本地时区解释，不是 UTC。
- **CalDAV 不可依赖服务端语义**：`If-Match`/`If-None-Match` 被忽略（last-write-wins）、单资源 GET 恒 404（只能走 `REPORT calendar-query`）、`C:expand` 被忽略（递归事件本地展开）、集合 URL 必须带尾斜杠、写入统一 UTC `Z` 不写 `TZID`/`VTIMEZONE`。详见 SKILL.md 的 CalDAV 已知限制。
- **`[已完成]` 后缀**：`create-event` 写入前会判断完成语义文案或结束时间已过，命中则在标题末尾追加，已有后缀不重复。
- **敏感文件**：`.env`、`.calendar_token_cache.json`、`.calendar_preferences.json`、`.calendar_history.json` 已在 `.gitignore`。不要提交，不要在日志或异常里打印令牌与授权码。

## 文档同步

修改 CLI 参数、环境变量、后端行为时，同步更新 `SKILL.md`（权威）与 `README.md`；本文件只在约束本身变化时改。
