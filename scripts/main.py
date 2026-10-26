#!/usr/bin/env python3
"""
Outlook Calendar Scheduler - 主入口

使用示例:

1. 首次认证:
$ python main.py auth

2. 列出日历:
$ python main.py list-calendars

3. 查询事件:
$ python main.py list-events --start 2025-04-21 --end 2025-04-28

4. 创建事件 (直接写入):
$ python main.py create-event "团队会议" --start 2025-04-22T14:00 --end 2025-04-22T15:00

5. 智能排程:
$ python main.py suggest "项目评审会" --duration 120 --deadline friday
"""

import sys
import os

# 添加父目录到路径，使得 from scripts.xxx 可以正确解析
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 先导入 scripts 包触发 __init__.py 的环境变量加载
import scripts

import argparse
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional

# 现在可以正常导入同级模块
from auth import get_auth_manager
from backend import format_event_time
from calendar_api import get_calendar_client
from nlp_parser import apply_completed_title_if_needed, parse_event
from scheduler import get_scheduler
from preference_learner import get_learner


def _detect_backend_name() -> str:
    """判断当前配置的后端名称（供 auth 等需要提前分支的场景使用）"""
    try:
        return getattr(get_calendar_client(), "provider_name", "未知")
    except Exception:
        # 后端尚未实现时，回退到环境变量推断
        configured = (os.getenv("CALENDAR_BACKEND") or "").strip().lower()
        if configured:
            return configured
        has_caldav = bool((os.getenv("CALDAV_USERNAME") or "").strip())
        has_azure = bool((os.getenv("AZURE_CLIENT_ID") or "").strip())
        return "caldav" if has_caldav and not has_azure else "graph"


def _start_local_callback_server(redirect_uri: str, result: Dict[str, object]):
    parsed_redirect = urlparse(redirect_uri)
    host = parsed_redirect.hostname or "localhost"
    port = parsed_redirect.port or 80
    path = parsed_redirect.path or "/"

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            if not self.path.startswith(path):
                self.send_response(404)
                self.end_headers()
                return

            query = parse_qs(urlparse(self.path).query)
            code = query.get("code", [None])[0]
            error = query.get("error", [None])[0]

            if error:
                result["error"] = error
                message = f"授权失败: {error}"
                self._respond_html(400, f"<html><body><h3>{message}</h3></body></html>")
            elif code:
                result["code"] = code
                message = "授权成功，可关闭页面并返回终端继续。"
                self._respond_html(200, f"<html><body><h3>{message}</h3></body></html>")
            else:
                message = "等待 Microsoft 登录并完成授权..."
                self._respond_html(200, f"<html><body><p>{message}</p></body></html>")

            if code or error:
                result["done"].set()

        def log_message(self, format, *args):
            return

        def _respond_html(self, status: int, body: str):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(body.encode("utf-8"))

    server = HTTPServer((host, port), CallbackHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _mask_account(value: str) -> str:
    """掩码邮箱/账号：前 2 字符 + ***@域名"""
    text = (value or "").strip()
    if not text:
        return "未知"
    if "@" in text:
        local, domain = text.split("@", 1)
        return f"{local[:2]}***@{domain}"
    return f"{text[:2]}***"


def _probe_caldav():
    """CalDAV 只读探针：验证连接并列出日历"""
    print("正在验证 CalDAV 连接...")
    try:
        client = get_calendar_client()
        calendars = client.list_calendars()
    except Exception as exc:
        print(f"认证失败: {exc}")
        sys.exit(1)

    print(f"✓ 认证成功: {_mask_account(os.getenv('CALDAV_USERNAME', ''))}")
    print(f"发现 {len(calendars)} 个日历:")
    for cal in calendars:
        default_mark = " [默认]" if cal.get("default") else ""
        print(f"  - {cal['name']} ({cal['id']}){default_mark}")


def cmd_auth(args):
    """执行 OAuth2 认证流程（自动打开浏览器并监听回调）"""
    # CalDAV 不使用 Microsoft OAuth，改为执行一次只读探针
    if _detect_backend_name() != "graph":
        _probe_caldav()
        return

    auth_mgr = get_auth_manager()

    if auth_mgr.is_authenticated():
        print("已认证")
        return

    redirect_uri = os.getenv("REDIRECT_URI", auth_mgr.redirect_uri)
    auth_url = auth_mgr.get_authorization_url()

    callback_result: Dict[str, object] = {"done": threading.Event()}
    use_local_server = False
    server = None
    thread = None

    parsed_redirect = urlparse(redirect_uri)
    if parsed_redirect.scheme in {"http", "https"} and parsed_redirect.hostname in {"localhost", "127.0.0.1"}:
        try:
            server, thread = _start_local_callback_server(redirect_uri, callback_result)
            use_local_server = True
            print("已启动本地回调监听，完成浏览器授权后将自动回填授权码。")
        except Exception as exc:
            print(f"本地回调服务启动失败，降级为手工输入模式: {exc}")

    print(f"\n请在浏览器中打开以下 URL 授权:\n{auth_url}\n")

    if use_local_server:
        code = None
        try:
            try:
                webbrowser.open(auth_url)
            except Exception:
                pass

            if callback_result["done"].wait(timeout=240):
                code = callback_result.get("code")
            else:
                print("等待授权回调超时，退回手工输入。")

            if "error" in callback_result:
                print(f"认证失败: {callback_result['error']}")
                sys.exit(1)
        finally:
            if server:
                server.shutdown()
                server.server_close()
            if thread:
                thread.join(timeout=1)

        if code:
            try:
                result = auth_mgr.acquire_token_by_code(code)
                print("认证成功!")
                print(f"  用户: {result.get('id_token_claims', {}).get('name', 'Unknown')}")
                return
            except Exception as e:
                print(f"认证失败: {e}")
                print("请检查上方链接和网络环境后重试。")
                sys.exit(1)

    code = input("授权后请输入回调 URL 中的授权码（或完整回调链接）: ").strip()
    if code.startswith("http") and "code=" in code:
        parsed_code = parse_qs(urlparse(code).query)
        code = (parsed_code.get("code") or [None])[0] or code
    if not code:
        print("未获取到授权码，已取消认证")
        sys.exit(1)

    try:
        result = auth_mgr.acquire_token_by_code(code)
        print("认证成功!")
        print(f"  用户: {result.get('id_token_claims', {}).get('name', 'Unknown')}")
    except Exception as e:
        print(f"认证失败: {e}")
        sys.exit(1)


def cmd_list_calendars(args):
    """列出所有日历"""
    client = get_calendar_client()
    calendars = client.list_calendars()

    print(f"找到 {len(calendars)} 个日历:")
    for cal in calendars:
        default_mark = " (默认)" if cal.get("default") else ""
        print(f"  - {cal['name']} (ID: {cal['id']}){default_mark}")


def cmd_list_events(args):
    """列出事件"""
    client = get_calendar_client()

    try:
        start = datetime.fromisoformat(args.start)
        end = datetime.fromisoformat(args.end)
    except ValueError:
        start = datetime.strptime(args.start, "%Y-%m-%d")
        end = datetime.strptime(args.end, "%Y-%m-%d")

    events = client.list_events(start_time=start, end_time=end)

    print(f"找到 {len(events)} 个事件:")
    for event in events:
        subject = event.get("subject", "无主题")
        time_str = format_event_time(event)
        print(f"  - {subject} ({time_str})")


def cmd_create_event(args):
    """直接创建事件"""
    is_completed = False
    raw_text = None
    if args.text:
        parsed = parse_event(args.text)
        # 验证解析结果，确保时间信息有效
        if parsed.get("start") is None:
            print("错误: 无法从文本中解析出开始时间")
            print("提示: 请使用类似'明天下午2点开会'的格式，或直接使用 --start/--end 参数")
            sys.exit(1)
        start = parsed["start"]
        # 如果只有开始时间没有结束时间，使用默认时长
        if parsed.get("end") is None:
            duration = parsed.get("duration_minutes", 60)
            from datetime import timedelta
            end = start + timedelta(minutes=duration)
        else:
            end = parsed["end"]
        title = parsed.get("title") or args.title or "未命名事件"
        is_completed = bool(parsed.get("is_completed"))
        raw_text = args.text
    else:
        # 验证命令行参数
        if not args.start or not args.end:
            print("错误: 缺少必需的时间参数")
            print("用法: python main.py create-event '标题' --start 2025-04-24T10:00 --end 2025-04-24T11:00")
            print("   或: python main.py create-event --text '明天下午2点开会'")
            sys.exit(1)
        try:
            start = datetime.fromisoformat(args.start)
            end = datetime.fromisoformat(args.end)
        except ValueError as e:
            print(f"错误: 时间格式无效 - {e}")
            print("提示: 使用 ISO 8601 格式，如 2025-04-24T10:00")
            sys.exit(1)
        if not args.title:
            print("错误: 缺少事件标题")
            sys.exit(1)
        title = args.title

    # 已完成/已发生事件：标题末尾追加 [已完成]
    title = apply_completed_title_if_needed(
        title,
        is_completed=is_completed,
        start=start,
        end=end,
        raw_text=raw_text,
    )

    client = get_calendar_client()
    try:
        event = client.create_event(
            subject=title,
            start_time=start,
            end_time=end,
            location=args.location,
            body=args.body
        )
        print(f"事件创建成功! ID: {event['id']}")
        print(f"  标题: {title}")
        # 显示创建的事件时间（本地时间）
        start_str = start.strftime("%Y-%m-%d %H:%M") if hasattr(start, 'strftime') else str(start)
        end_str = end.strftime("%Y-%m-%d %H:%M") if hasattr(end, 'strftime') else str(end)
        print(f"  时间: {start_str} - {end_str}")
        if title.endswith("[已完成]"):
            print("  状态: 已完成")

    except Exception as e:
        print(f"创建事件失败: {e}")
        sys.exit(1)


def cmd_suggest(args):
    """智能排程推荐"""
    parsed = parse_event(args.text) if args.text else {"duration_minutes": args.duration, "constraints": {}}
    duration = parsed.get("duration_minutes", args.duration)

    client = get_calendar_client()
    sched = get_scheduler()
    # 使用带时区的当前时间，避免查询窗口与排程器内部基准错位
    now = datetime.now(sched._tz)
    end_lookahead = now + timedelta(days=args.days)

    events = client.list_events(start_time=now, end_time=end_lookahead)
    suggestions = sched.suggest_times(
        duration_minutes=duration,
        constraints=parsed.get("constraints", {}),
        existing_events=events,
        lookahead_days=args.days,
        num_suggestions=args.num
    )

    if not suggestions:
        print("未找到合适的时间段，请调整约束条件或延长排程范围。")
        return

    print(f"为您推荐以下 {len(suggestions)} 个时间段:")
    for i, sug in enumerate(suggestions, 1):
        start_str = sug["start"].strftime("%Y-%m-%d %H:%M")
        end_str = sug["end"].strftime("%Y-%m-%d %H:%M")
        print(f"\n{i}. {start_str} - {end_str}")
        print(f"   适宜度: {sug['score'] * 100:.0f}%")
        print(f"   理由: {sug['reason']}")

    choice = input("\n请选择序号(1-{}),或输入 0 取消: ".format(len(suggestions)))
    try:
        idx = int(choice) - 1
        if idx == -1:
            print("取消排程。")
            return
        if 0 <= idx < len(suggestions):
            selected = suggestions[idx]
            event = client.create_event(
                subject=args.title or parsed.get("title", "智能排程事件"),
                start_time=selected["start"],
                end_time=selected["end"],
                body="<p>由 Claude 智能排程创建</p>"
            )
            print(f"已创建事件! ID: {event['id']}")

            learner = get_learner()
            learner.record_suggestion_response(suggestions, idx)
            learner.record_event_created(event)
        else:
            print("无效选择")
    except ValueError:
        print("输入无效")


def cmd_delete_event(args):
    """删除事件"""
    client = get_calendar_client()
    try:
        client.delete_event(args.event_id)
        print(f"事件删除成功! ID: {args.event_id}")
    except Exception as e:
        print(f"删除事件失败: {e}")
        sys.exit(1)


def cmd_status(args):
    """查看技能状态和偏好"""
    learner = get_learner()
    prefs = learner.get_preference_summary()

    backend_name = _detect_backend_name()
    print("=== Outlook Calendar Scheduler 状态 ===")
    print(f"后端: {backend_name}")

    # CalDAV 额外展示账号/服务器/默认日历；取不到时不影响其他输出
    if backend_name == "caldav":
        print(f"账号: {_mask_account(os.getenv('CALDAV_USERNAME', ''))}")
        print(f"服务器: {os.getenv('CALDAV_BASE_URL') or 'https://dav.qq.com'}")
        try:
            calendars = get_calendar_client().list_calendars()
            default_name = next(
                (cal["name"] for cal in calendars if cal.get("default")), "未确定"
            )
        except Exception:
            default_name = "未确定"
        print(f"默认日历: {default_name}")

    print(f"工作时长: {prefs['working_hours']['start']} - {prefs['working_hours']['end']}")
    print(f"缓冲时间: {prefs['buffer_minutes']} 分钟")
    print(f"已创建事件数: {prefs.get('analyzed', {}).get('total_events_created', 0)}")
    print(f"推荐接受率: {(1 - prefs.get('analyzed', {}).get('rejection_rate', 0)) * 100:.1f}%")


def main():
    parser = argparse.ArgumentParser(description="Outlook Calendar Scheduler")
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    subparsers.add_parser("auth", help="执行 OAuth2 认证")

    subparsers.add_parser("list-calendars", help="列出所有日历")

    list_events_parser = subparsers.add_parser("list-events", help="列出事件")
    list_events_parser.add_argument("--start", required=True, help="开始时间 ISO格式")
    list_events_parser.add_argument("--end", required=True, help="结束时间 ISO格式")

    create_parser = subparsers.add_parser("create-event", help="直接创建事件")
    create_parser.add_argument("title", nargs="?", help="事件标题")
    create_parser.add_argument("--start", help="开始时间 ISO格式")
    create_parser.add_argument("--end", help="结束时间 ISO格式")
    create_parser.add_argument("--location", help="地点")
    create_parser.add_argument("--body", help="事件内容 HTML")
    create_parser.add_argument("--text", help="自然语言描述 (如'明天下午2点开会')")

    suggest_parser = subparsers.add_parser("suggest", help="智能排程推荐")
    suggest_parser.add_argument("text", nargs="?", help="自然语言描述事件")
    suggest_parser.add_argument("--title", help="事件标题")
    suggest_parser.add_argument("--duration", type=int, default=60, help="时长(分钟)")
    suggest_parser.add_argument("--days", type=int, default=7, help="排程天数范围")
    suggest_parser.add_argument("--num", type=int, default=3, help="推荐数量")

    delete_parser = subparsers.add_parser("delete-event", help="删除事件")
    delete_parser.add_argument("event_id", help="事件 ID")

    subparsers.add_parser("status", help="查看偏好状态")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    commands = {
        "auth": cmd_auth,
        "list-calendars": cmd_list_calendars,
        "list-events": cmd_list_events,
        "create-event": cmd_create_event,
        "delete-event": cmd_delete_event,
        "suggest": cmd_suggest,
        "status": cmd_status
    }

    try:
        commands[args.command](args)
    except Exception as e:
        print(f"错误: {e}")
        if args.command != "auth" and _detect_backend_name() == "graph":
            print("提示: 请先运行 'python main.py auth' 完成认证")
        sys.exit(1)


if __name__ == "__main__":
    main()
