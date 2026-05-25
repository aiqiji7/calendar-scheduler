#!/usr/bin/env python3
"""
Microsoft Graph Calendar API 封装

提供日历和事件的增删改查操作。
"""

import os
from datetime import datetime
from typing import List, Dict, Optional
import requests
from auth import get_auth_manager


class CalendarAPI:
    """Outlook Calendar API 客户端"""

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
            raise Exception("未认证,请先完成 OAuth2 流程")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

    def _request(self, method: str, endpoint: str, data: Optional[Dict] = None) -> Dict:
        """发送 Graph API 请求"""
        url = f"{self.base_url}{endpoint}"
        headers = self._get_headers()

        session = self._get_session()
        response = session.request(
            method=method,
            url=url,
            headers=headers,
            json=data if data else None
        )

        if response.status_code not in (200, 201, 204):
            error_msg = response.text
            raise Exception(f"API 请求失败 ({response.status_code}): {error_msg}")

        if response.status_code == 204:
            return {}

        return response.json()

    # ========== 日历管理 ==========

    def list_calendars(self) -> List[Dict]:
        """获取用户所有日历"""
        result = self._request("GET", "/me/calendars")
        return result.get("value", [])

    def get_default_calendar(self) -> Dict:
        """获取默认日历"""
        calendars = self.list_calendars()
        for cal in calendars:
            if cal.get("isDefaultCalendar"):
                return cal

        # Graph 返回值一般会携带标记为默认的日历，不存在则抛错
        raise Exception("未找到默认日历")

    def _resolve_calendar_id(self, calendar_id: str) -> str:
        """将约定的 primary 映射为 Graph 实际默认日历 ID。"""
        if calendar_id != "primary":
            return calendar_id

        if self._default_calendar_id:
            return self._default_calendar_id

        default_calendar = self.get_default_calendar()
        self._default_calendar_id = default_calendar.get("id")

        if not self._default_calendar_id:
            raise Exception("默认日历缺少 ID")

        return self._default_calendar_id

    # ========== 事件管理 ==========

    def list_events(
        self,
        calendar_id: str = "primary",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        max_results: int = 100
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
            start_str = start_time.strftime("%Y-%m-%dT%H:%M:%S")
            end_str = end_time.strftime("%Y-%m-%dT%H:%M:%S")
            params.append(f"startDateTime={start_str}")
            params.append(f"endDateTime={end_str}")
        else:
            endpoint = f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events"

        params.append(f"$top={max_results}")

        if params:
            endpoint += "?" + "&".join(params)

        result = self._request("GET", endpoint)
        return result.get("value", [])

    def get_event(self, event_id: str, calendar_id: str = "primary") -> Dict:
        """获取单个事件详情"""
        return self._request("GET", f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events/{event_id}")

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
        time_zone: str = "Asia/Shanghai"
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
        # Graph API 需要特定格式
        event_data = {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": body or ""
            },
            "start": {
                "dateTime": start_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": time_zone
            },
            "end": {
                "dateTime": end_time.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": time_zone
            },
            "isAllDay": is_all_day
        }

        if location:
            event_data["location"] = {"displayName": location}

        if attendees:
            event_data["attendees"] = [
                {
                    "emailAddress": {
                        "address": att["email"],
                        "name": att.get("name", "")
                    },
                    "type": "required"
                }
                for att in attendees
            ]

        return self._request("POST", f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events", event_data)

    def update_event(
        self,
        event_id: str,
        updates: Dict,
        calendar_id: str = "primary"
    ) -> Dict:
        """
        更新事件

        参数:
        - event_id: 事件 ID
        - updates: 需要更新的字段 (如 {"subject": "新标题"})
        """
        return self._request("PATCH", f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events/{event_id}", updates)

    def delete_event(self, event_id: str, calendar_id: str = "primary"):
        """删除事件"""
        self._request("DELETE", f"/me/calendars/{self._resolve_calendar_id(calendar_id)}/events/{event_id}")

    # ========== 辅助功能 ==========

    def get_free_busy(
        self,
        schedules: List[str],
        start_time: datetime,
        end_time: datetime
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
                "dateTime": start_time.isoformat(),
                "timeZone": "UTC"
            },
            "endTime": {
                "dateTime": end_time.isoformat(),
                "timeZone": "UTC"
            }
        }

        return self._request("POST", "/me/getFreeBusySchedule", data)

    def find_meeting_times(
        self,
        attendees: List[str],
        duration_minutes: int,
        time_constraints: Optional[Dict] = None
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


def get_calendar_client() -> CalendarAPI:
    """获取 Calendar API 客户端实例"""
    return CalendarAPI()
