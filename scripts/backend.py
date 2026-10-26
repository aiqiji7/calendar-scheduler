#!/usr/bin/env python3
"""
日历后端抽象与事件契约

定义后端无关的事件/日历数据结构、异常类型和 Protocol，
供 Graph / CalDAV 等具体实现复用。
"""

import os
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Protocol

# 事件时间缺少时区时的兜底时区（与 Graph 默认 UTC 行为保持一致）
_DEFAULT_TZ = timezone.utc

# Graph 常返回 7 位小数秒，Python 3.10 只接受 3/6 位
_FRACTION_RE = re.compile(r"\.(\d+)")


def _parse_iso_string(text: str) -> datetime:
    """解析 ISO8601 字符串，兼容不同位数的小数秒"""
    normalized = text.strip().replace("Z", "+00:00")
    normalized = _FRACTION_RE.sub(
        lambda match: "." + match.group(1)[:6].ljust(6, "0"),
        normalized,
    )
    return datetime.fromisoformat(normalized)


class CalendarBackendError(Exception):
    """后端通用错误"""

    def __init__(self, message: str, status: Optional[int] = None, body: str = ""):
        self.message = message
        self.status = status
        # 截断响应体，避免日志过长；不记录请求头或令牌
        self.body = (body or "")[:500]
        super().__init__(message)

    def __str__(self) -> str:
        text = self.message
        if self.status is not None and f"({self.status})" not in text:
            text += f" (HTTP {self.status})"
        if self.body:
            text += f": {self.body}"
        return text


class AuthError(CalendarBackendError):
    """认证失败或令牌无效（HTTP 401）"""


class NotFoundError(CalendarBackendError):
    """资源不存在（HTTP 404）"""


class ConflictError(CalendarBackendError):
    """并发冲突或前置条件失败（HTTP 409/412）"""


def _to_iso(value: object) -> str:
    """把 datetime/字符串统一转换为带偏移的 ISO8601 字符串"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        text = value.strip()
        try:
            # Graph 可能返回 "Z" 结尾的时间
            dt = _parse_iso_string(text)
        except ValueError:
            return text
    else:
        return str(value)

    # naive 时间按 UTC 处理，与 main.py 原有假设一致
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_DEFAULT_TZ)
    return dt.isoformat()


def _normalize_show_as(show_as: Optional[str]) -> str:
    """归一化忙闲状态：仅 free/cancelled 保留，其余视为 busy"""
    value = (show_as or "").strip().lower()
    if value in ("free", "cancelled"):
        return value
    return "busy"


def normalize_event(
    *,
    source: str,
    event_id: str,
    subject: str,
    start: object,
    end: object,
    is_all_day: bool = False,
    show_as: str = "busy",
    location: Optional[str] = None,
    categories: Optional[list] = None,
    recurring: bool = False,
    etag: Optional[str] = None,
    uid: Optional[str] = None,
) -> Dict[str, Any]:
    """把不同后端的事件统一为同一契约"""
    return {
        "id": event_id,
        "subject": (subject or "").strip() or "无主题",
        "start": _to_iso(start),
        "end": _to_iso(end),
        "is_all_day": bool(is_all_day),
        "show_as": _normalize_show_as(show_as),
        "location": location,
        "categories": list(categories) if categories else [],
        "recurring": bool(recurring),
        "etag": etag,
        "uid": uid,
        "source": source,
    }


def normalize_calendar(
    *,
    source: str,
    calendar_id: str,
    name: str,
    is_default: bool = False,
) -> Dict[str, Any]:
    """把不同后端的日历统一为同一契约"""
    return {
        "id": calendar_id,
        "name": name,
        "default": bool(is_default),
        "source": source,
    }


class CalendarBackend(Protocol):
    """日历后端协议（Graph / CalDAV 均需实现）"""

    provider_name: str

    def list_calendars(self) -> List[Dict[str, Any]]: ...

    def get_default_calendar(self) -> Dict[str, Any]: ...

    def list_events(
        self,
        calendar_id: str = "primary",
        start_time=None,
        end_time=None,
        max_results: int = 100,
    ) -> List[Dict[str, Any]]: ...

    def get_event(self, event_id: str, calendar_id: str = "primary") -> Dict[str, Any]: ...

    def create_event(
        self,
        subject: str,
        start_time: datetime,
        end_time: datetime,
        body=None,
        location=None,
        attendees=None,
        calendar_id: str = "primary",
        is_all_day: bool = False,
        time_zone: str = "Asia/Shanghai",
    ) -> Dict[str, Any]: ...

    def update_event(
        self, event_id: str, updates: Dict, calendar_id: str = "primary"
    ) -> Dict[str, Any]: ...

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> None: ...


def _parse_event_datetime(value: object) -> Optional[datetime]:
    """解析事件时间字段，失败返回 None"""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = _parse_iso_string(value)
        except ValueError:
            return None
    else:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_DEFAULT_TZ)
    return dt


def format_event_time(
    event: Dict[str, Any],
    fmt: str = "%Y-%m-%d %H:%M",
    to_local: bool = True,
) -> str:
    """格式化事件开始时间，默认转换为本地时区"""
    raw = event.get("start")
    dt = _parse_event_datetime(raw)
    if dt is None:
        # 解析失败时保留原始内容，不抛异常
        return str(raw)

    if to_local:
        dt = dt.astimezone()
    return dt.strftime(fmt)


def format_event_range(event: Dict[str, Any], fmt: str = "%Y-%m-%d %H:%M") -> str:
    """格式化事件时间范围；同一天只显示一次日期"""
    start_dt = _parse_event_datetime(event.get("start"))
    end_dt = _parse_event_datetime(event.get("end"))
    if start_dt is None or end_dt is None:
        return f"{event.get('start')} - {event.get('end')}"

    start_local = start_dt.astimezone()
    end_local = end_dt.astimezone()
    if start_local.date() == end_local.date():
        return f"{start_local.strftime(fmt)} - {end_local.strftime('%H:%M')}"
    return f"{start_local.strftime(fmt)} - {end_local.strftime(fmt)}"


def get_backend(provider: Optional[str] = None) -> CalendarBackend:
    """获取日历后端实例：显式参数 > CALENDAR_BACKEND > 自动推断"""
    explicit = (provider or "").strip()
    configured = explicit or (os.getenv("CALENDAR_BACKEND") or "").strip()
    name = configured.lower()

    if not name:
        # 空字符串视为未设置（.env 中常见 CALDAV_USERNAME= 这类空值）
        caldav_username = (os.getenv("CALDAV_USERNAME") or "").strip()
        azure_client_id = (os.getenv("AZURE_CLIENT_ID") or "").strip()
        name = "caldav" if caldav_username and not azure_client_id else "graph"

    if name == "graph":
        from graph_backend import GraphBackend

        return GraphBackend()

    if name == "caldav":
        try:
            from caldav_backend import CalDAVBackend
        except ImportError as exc:
            raise CalendarBackendError("CalDAV 后端尚未实现（P2 阶段）") from exc
        return CalDAVBackend()

    raise CalendarBackendError(
        f"无法识别的日历后端: {configured}，合法取值: graph, caldav"
    )
