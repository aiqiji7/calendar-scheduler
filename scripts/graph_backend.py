#!/usr/bin/env python3
"""
Microsoft Graph Calendar 后端

实现 backend.CalendarBackend 协议，负责 Graph API 请求与响应归一化。
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from dateutil import tz as dateutil_tz

from auth import get_auth_manager
from backend import (
    AuthError,
    CalendarBackendError,
    ConflictError,
    NotFoundError,
    normalize_calendar,
    normalize_event,
)


def _local_tz():
    """查询参数中 naive 时间所属的时区（与 CalDAV 后端保持一致）"""
    name = (os.getenv("CALENDAR_TIME_ZONE") or "Asia/Shanghai").strip()
    return dateutil_tz.gettz(name) or timezone.utc


def _to_utc_z(value: datetime) -> str:
    """转换为 Graph 接受的 UTC 字符串（naive 按本地时区解释，不是 UTC）"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=_local_tz())
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_utc_iso(value: datetime) -> str:
    """转换为带 Z 的 UTC ISO8601 字符串（用于 free/busy）"""
    if value.tzinfo is None:
        value = value.replace(tzinfo=_local_tz())
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _graph_datetime(value: Any) -> Any:
    """从 Graph 的 {dateTime, timeZone} 结构中取出时间字符串"""
    if isinstance(value, dict):
        return value.get("dateTime")
    return value


def _graph_location(value: Any) -> Optional[str]:
    """从 Graph 的 location 结构中取出显示名称"""
    if isinstance(value, dict):
        return value.get("displayName")
    return value


def _to_graph_local_naive(value: object, time_zone: str) -> str:
    """
    把契约时间转换为 Graph 需要的“本地 naive 字符串”

    datetime 带 tzinfo 时先转到目标时区再去掉时区；naive 时间按原值处理。
    """
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise CalendarBackendError(f"无法解析时间: {value}") from exc
    else:
        raise CalendarBackendError(f"无法解析时间: {value}")

    if dt.tzinfo is not None:
        target_tz = dateutil_tz.gettz(time_zone)
        if target_tz is not None:
            dt = dt.astimezone(target_tz)
        dt = dt.replace(tzinfo=None)

    return dt.strftime("%Y-%m-%dT%H:%M:%S")


class GraphBackend:
    """Microsoft Graph 日历后端"""

    provider_name = "graph"

    def __init__(self):
        self.auth = get_auth_manager()
        self.graph_api_version = os.getenv("GRAPH_API_VERSION", "v1.0")
        self.base_url = f"https://graph.microsoft.com/{self.graph_api_version}"
        self.session: Optional[requests.Session] = None
        self._default_calendar_id: Optional[str] = None

    def _get_session(self) -> requests.Session:
        """延迟创建 Session，避免连接泄漏"""
        if self.session is None:
            self.session = requests.Session()
        return self.session

    def _get_headers(self) -> Dict[str, str]:
        """获取 API 请求头"""
        token = self.auth.get_access_token()
        if not token:
            raise AuthError("未认证, 请先完成 OAuth2 流程", status=401)
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """发送 Graph API 请求"""
        # nextLink 是完整 URL，不能再拼 base_url
        url = endpoint if endpoint.startswith("http") else f"{self.base_url}{endpoint}"
        headers = self._get_headers()

        session = self._get_session()
        response = session.request(
            method=method,
            url=url,
            headers=headers,
            json=data if data else None,
        )

        if response.status_code not in (200, 201, 204):
            body = response.text or ""
            message = f"API 请求失败 ({response.status_code})"
            if response.status_code == 401:
                raise AuthError(message, status=401, body=body)
            if response.status_code == 404:
                raise NotFoundError(message, status=404, body=body)
            if response.status_code in (409, 412):
                raise ConflictError(message, status=response.status_code, body=body)
            raise CalendarBackendError(message, status=response.status_code, body=body)

        if response.status_code == 204:
            return {}

        return response.json()

    def _normalize_graph_event(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """把 Graph 原始事件转换为统一契约"""
        event_type = raw.get("type")
        recurring = bool(
            raw.get("recurrence")
            or raw.get("seriesMasterId")
            or event_type in ("seriesMaster", "occurrence", "exception")
        )
        return normalize_event(
            source="graph",
            event_id=raw.get("id", ""),
            subject=raw.get("subject") or "",
            start=_graph_datetime(raw.get("start")),
            end=_graph_datetime(raw.get("end")),
            is_all_day=bool(raw.get("isAllDay", False)),
            show_as=(
                "cancelled"
                if raw.get("isCancelled")
                else (raw.get("showAs") or "busy")
            ),
            location=_graph_location(raw.get("location")),
            categories=raw.get("categories") or [],
            recurring=recurring,
            etag=raw.get("@odata.etag"),
            uid=raw.get("iCalUId"),
        )

    # ========== 日历管理 ==========

    def list_calendars(self) -> List[Dict]:
        """获取用户所有日历"""
        result = self._request("GET", "/me/calendars")
        return [
            normalize_calendar(
                source="graph",
                calendar_id=cal.get("id", ""),
                name=cal.get("name", ""),
                is_default=cal.get("isDefaultCalendar", False),
            )
            for cal in result.get("value", [])
        ]

    def get_default_calendar(self) -> Dict:
        """获取默认日历"""
        calendars = self.list_calendars()
        for cal in calendars:
            if cal.get("default"):
                return cal

        # Graph 返回值一般会携带标记为默认的日历，不存在则抛错
        raise CalendarBackendError("未找到默认日历")

    def _resolve_calendar_id(self, calendar_id: str) -> str:
        """将约定的 primary 映射为 Graph 实际默认日历 ID。"""
        if calendar_id != "primary":
            return calendar_id

        if self._default_calendar_id:
            return self._default_calendar_id

        default_calendar = self.get_default_calendar()
        self._default_calendar_id = default_calendar.get("id")

        if not self._default_calendar_id:
            raise CalendarBackendError("默认日历缺少 ID")

        return self._default_calendar_id

    # ========== 事件管理 ==========

    def list_events(
        self,
        calendar_id: str = "primary",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        max_results: int = 100,
    ) -> List[Dict]:
        """
        列出日历事件

        参数:
        - calendar_id: 日历 ID (默认 "primary" 为主日历)
        - start_time: 开始时间筛选
        - end_time: 结束时间筛选
        - max_results: 最大返回数
        """
        params = []
        if start_time and end_time:
            # calendarView 端点支持 startDateTime/endDateTime 范围查询
            endpoint = f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/calendarView"
            params.append(f"startDateTime={_to_utc_z(start_time)}")
            params.append(f"endDateTime={_to_utc_z(end_time)}")
        else:
            endpoint = f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events"

        params.append(f"$top={max_results}")
        endpoint += "?" + "&".join(params)

        events: List[Dict] = []
        next_endpoint: Optional[str] = endpoint
        while next_endpoint and len(events) < max_results:
            result = self._request("GET", next_endpoint)
            for raw in result.get("value", []):
                events.append(self._normalize_graph_event(raw))
                if len(events) >= max_results:
                    break
            # nextLink 是完整 URL，直接用于下一页请求
            next_endpoint = result.get("@odata.nextLink")

        return events

    def get_event(self, event_id: str, calendar_id: str = "primary") -> Dict:
        """获取单个事件详情"""
        raw = self._request(
            "GET",
            f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events/{event_id}",
        )
        return self._normalize_graph_event(raw)

    def create_event(
        self,
        subject: str,
        start_time: datetime,
        end_time: datetime,
        body: Optional[str] = None,
        location: Optional[str] = None,
        attendees: Optional[List[Dict]] = None,
        calendar_id: str = "primary",
        is_all_day: bool = False,
        time_zone: str = "Asia/Shanghai",
    ) -> Dict:
        """
        创建新事件

        参数:
        - subject: 事件标题
        - start_time/end_time: 开始/结束时间 (datetime)
        - body: 事件描述内容 (HTML 格式)
        - location: 地点
        - attendees: 参会者列表 [{"email": "...", "name": "..."}]
        - calendar_id: 日历 ID
        - is_all_day: 是否全天事件
        - time_zone: 时区 (默认 Asia/Shanghai)
        """
        # Graph 全天事件要求起止时间位于当日零点
        if is_all_day:
            start_time = start_time.replace(hour=0, minute=0, second=0, microsecond=0)
            end_time = end_time.replace(hour=0, minute=0, second=0, microsecond=0)

        # Graph API 需要特定格式
        event_data = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body or "",
            },
            "start": {
                "dateTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": time_zone,
            },
            "end": {
                "dateTime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": time_zone,
            },
            "isAllDay": is_all_day,
        }

        if location:
            event_data["location"] = {"displayName": location}

        if attendees:
            event_data["attendees"] = [
                {
                    "emailAddress": {
                        "address": att["email"],
                        "name": att.get("name", ""),
                    },
                    "type": "required",
                }
                for att in attendees
            ]

        raw = self._request(
            "POST",
            f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events",
            event_data,
        )
        return self._normalize_graph_event(raw)

    def update_event(
        self,
        event_id: str,
        updates: Dict,
        calendar_id: str = "primary",
        time_zone: str = "Asia/Shanghai",
    ) -> Dict:
        """
        更新事件

        参数:
        - event_id: 事件 ID
        - updates: 契约字段 (subject/start/end/location/body/is_all_day)
        - calendar_id: 日历 ID
        - time_zone: 时间的时区 (默认 Asia/Shanghai)
        """
        payload: Dict[str, Any] = {}

        if "subject" in updates:
            payload["subject"] = updates.get("subject")

        if "start" in updates:
            payload["start"] = {
                "dateTime": _to_graph_local_naive(updates.get("start"), time_zone),
                "timeZone": time_zone,
            }

        if "end" in updates:
            payload["end"] = {
                "dateTime": _to_graph_local_naive(updates.get("end"), time_zone),
                "timeZone": time_zone,
            }

        if "location" in updates:
            payload["location"] = {
                "displayName": updates.get("location") or "",
            }

        if "body" in updates:
            payload["body"] = {
                "contentType": "HTML",
                "content": updates.get("body") or "",
            }

        if "is_all_day" in updates:
            payload["isAllDay"] = bool(updates.get("is_all_day"))

        if not payload:
            raise CalendarBackendError("没有需要更新的字段")

        raw = self._request(
            "PATCH",
            f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events/{event_id}",
            payload,
        )
        return self._normalize_graph_event(raw)

    def delete_event(self, event_id: str, calendar_id: str = "primary"):
        """删除事件"""
        self._request(
            "DELETE",
            f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events/{event_id}",
        )

    # ========== 辅助功能 ==========

    def get_free_busy(
        self,
        schedules: List[str],
        start_time: datetime,
        end_time: datetime,
    ) -> Dict:
        """
        获取多人空闲/忙状态

        参数:
        - schedules: 用户邮箱或 ID 列表
        - start_time/end_time: 查询时间范围

        返回: { "freeBusyStatus": "free"/"tentative"/"busy" }
        """
        data = {
            "schedules": schedules,
            "startTime": {
                "dateTime": _to_utc_iso(start_time),
                "timeZone": "UTC",
            },
            "endTime": {
                "dateTime": _to_utc_iso(end_time),
                "timeZone": "UTC",
            },
        }

        return self._request("POST", "/me/getFreeBusySchedule", data)

    def find_meeting_times(
        self,
        attendees: List[str],
        duration_minutes: int,
        time_constraints: Optional[Dict] = None,
    ) -> Dict:
        """
        寻找共同空闲时间 (推荐会议时间)

        参数:
        - attendees: 参会者列表
        - duration_minutes: 会议时长
        - time_constraints: 时间约束 {"timeSlots": [...], "isYearSupported": false}

        返回: meetingTimeSuggestions 列表
        """
        data = {
            "attendees": [{"type": "required", "emailAddress": {"address": email}} for email in attendees],
            "timeConstraint": {
                "activityDomain": "work",
                "timeSlots": time_constraints.get("timeSlots", []) if time_constraints else []
            },
            "meetingDuration": f"PT{duration_minutes}M",
            "isYearSupported": False,
            "isReminderSet": True,
            "maxCandidates": 10
        }

        result = self._request("POST", "/me/findMeetingTimes", data)
        return result.get("meetingTimeSuggestions", [])


# 兼容旧代码中的类名
CalendarAPI = GraphBackend
