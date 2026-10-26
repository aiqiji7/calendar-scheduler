#!/usr/bin/env python3
"""
QQ 邮箱 CalDAV 后端

实现 backend.CalendarBackend 协议。QQ 的 CalDAV 行为与标准存在差异：
- 单资源 GET 永远 404，只能靠 REPORT 整集合查询
- C:expand 被忽略，递归必须本地展开
- If-None-Match / If-Match 被忽略，无乐观并发
- 集合 href 缺尾斜杠会产生畸形 href，所有请求统一补斜杠
"""

import os
import uuid
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import unquote, urljoin, urlparse

import icalendar
import recurring_ical_events
from dateutil import tz

from backend import (
    AuthError,
    CalendarBackendError,
    NotFoundError,
    normalize_calendar,
    normalize_event,
)
from caldav_dav import (
    CalDAVSession,
    calendar_query_body,
    calendar_query_by_uid_body,
    discover,
    ensure_trailing_slash,
    parse_multistatus,
    resource_url,
)

# 查询窗口分片阈值（QQ 实测 40 天稳定，放宽到 92 天）
_CHUNK_DAYS = 92

# get_event 退化查询的窗口（前后各 45 天，共 90 天）
_FALLBACK_DAYS = 45

# 默认排除的非日程集合（Tasks/好友生日 都自报 VEVENT，必须按名字排除）
_DEFAULT_EXCLUDES = "Tasks,好友生日"

# QQ 默认主日历显示名
_DEFAULT_CALENDAR_NAME = "日历"


class CalDAVBackend:
    """CalDAV 日历后端（以 QQ 邮箱为主要目标）"""

    provider_name = "caldav"

    def __init__(self):
        self.base_url = (os.getenv("CALDAV_BASE_URL") or "https://dav.qq.com").strip()
        self.username = (os.getenv("CALDAV_USERNAME") or "").strip()
        self.password = (os.getenv("CALDAV_PASSWORD") or "").strip()
        self.calendar_pref = (os.getenv("CALDAV_CALENDAR") or "").strip()

        excludes = os.getenv("CALDAV_EXCLUDE_CALENDARS")
        if excludes is None:
            excludes = _DEFAULT_EXCLUDES
        self.exclude_names = {
            item.strip().lower() for item in excludes.split(",") if item.strip()
        }

        self.time_zone_name = (os.getenv("CALENDAR_TIME_ZONE") or "Asia/Shanghai").strip()
        self._tz = tz.gettz(self.time_zone_name) or timezone.utc

        if not self.username or not self.password:
            raise AuthError("未配置 CALDAV_USERNAME / CALDAV_PASSWORD")

        self.session = CalDAVSession(
            base_url=self.base_url,
            username=self.username,
            password=self.password,
        )

        # 发现链路较慢，懒执行并缓存，避免每个命令都重跑
        self._discovery = None
        self._default_calendar: Optional[Dict[str, Any]] = None

    # ========== 发现与选择 ==========

    def _ensure_discovered(self):
        """懒执行服务发现并缓存结果"""
        if self._discovery is None:
            self._discovery = discover(self.session)
        return self._discovery

    def _candidate_calendars(self) -> List[Dict[str, Any]]:
        """筛选可作为日程写入的候选日历（保持服务端返回顺序）"""
        discovery = self._ensure_discovered()
        candidates: List[Dict[str, Any]] = []
        for cal in discovery.calendars:
            if not cal.get("is_calendar"):
                continue

            name = (cal.get("displayname") or "").strip()
            if name.lower() in self.exclude_names:
                continue

            components = [str(item).upper() for item in (cal.get("components") or [])]
            if "VEVENT" not in components:
                continue

            candidates.append(cal)
        return candidates

    def _match_preferred_calendar(
        self, candidates: List[Dict[str, Any]], preference: str
    ) -> Optional[Dict[str, Any]]:
        """按显示名精确匹配，其次按 href（规范化/解码后）匹配"""
        pref = preference.strip()
        if not pref:
            return None

        for cal in candidates:
            if (cal.get("displayname") or "").strip() == pref:
                return cal

        pref_key = pref.rstrip("/")
        pref_decoded = unquote(pref_key)
        for cal in candidates:
            href = cal.get("href") or ""
            href_key = href.rstrip("/")
            if href_key == pref_key or unquote(href_key) == pref_decoded:
                return cal
            # 允许只配置结尾路径片段（如 ABC123...）
            if pref_key and unquote(href_key).endswith("/" + pref_decoded):
                return cal
        return None

    def _selection_error(self, candidates: List[Dict[str, Any]], reason: str) -> CalendarBackendError:
        """构造带候选清单的选择失败信息"""
        lines = [
            f"{reason}。候选日历:",
        ]
        for cal in candidates:
            name = (cal.get("displayname") or "").strip() or "(未命名)"
            lines.append(f"  - {name} ({cal.get('href')})")
        if not candidates:
            lines.append("  (无)")
        return CalendarBackendError("\n".join(lines))

    def _select_calendar(self, calendar_id: str = "primary") -> Dict[str, Any]:
        """
        选择目标日历

        优先级：
        1. 显式 calendar_id（非 primary）→ 按名字/href 匹配
        2. CALDAV_CALENDAR 非空 → 按名字/href 匹配
        3. 名字等于「日历」
        4. 唯一候选
        5. 以上都不满足则报错并列出候选
        """
        candidates = self._candidate_calendars()

        if calendar_id and calendar_id not in ("primary", "default"):
            matched = self._match_preferred_calendar(candidates, calendar_id)
            if matched:
                return matched
            raise self._selection_error(candidates, f"未找到指定的日历: {calendar_id}")

        if self.calendar_pref:
            matched = self._match_preferred_calendar(candidates, self.calendar_pref)
            if matched:
                return matched
            raise self._selection_error(
                candidates, f"CALDAV_CALENDAR={self.calendar_pref} 未匹配到任何日历"
            )

        for cal in candidates:
            if (cal.get("displayname") or "").strip() == _DEFAULT_CALENDAR_NAME:
                return cal

        if len(candidates) == 1:
            return candidates[0]

        raise self._selection_error(candidates, "无法确定默认日历，请设置 CALDAV_CALENDAR")

    # ========== 日历管理 ==========

    def list_calendars(self) -> List[Dict]:
        """列出可用日历，选中项标记为默认"""
        candidates = self._candidate_calendars()
        if not candidates:
            return []

        try:
            default = self._select_calendar()
        except CalendarBackendError:
            default = None

        default_href = (default or {}).get("href")
        calendars = []
        for cal in candidates:
            calendars.append(
                normalize_calendar(
                    source=self.provider_name,
                    calendar_id=cal.get("href") or "",
                    name=(cal.get("displayname") or "").strip() or "(未命名)",
                    is_default=bool(default_href and cal.get("href") == default_href),
                )
            )
        return calendars

    def get_default_calendar(self) -> Dict:
        """获取默认日历"""
        if self._default_calendar is not None:
            return self._default_calendar

        calendars = self.list_calendars()
        for cal in calendars:
            if cal.get("default"):
                self._default_calendar = cal
                return cal

        raise CalendarBackendError("未找到默认日历，请设置 CALDAV_CALENDAR")

    # ========== 时间工具 ==========

    def _to_utc_dt(self, value) -> Optional[datetime]:
        """把 date/datetime 统一转为 UTC datetime（naive 视为本地时区）"""
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=self._tz)
            return dt.astimezone(timezone.utc)
        if isinstance(value, date):
            local_dt = datetime.combine(value, time.min, tzinfo=self._tz)
            return local_dt.astimezone(timezone.utc)
        if isinstance(value, str):
            try:
                parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
            except ValueError:
                return None
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=self._tz)
            return parsed.astimezone(timezone.utc)
        return None

    def _as_utc(self, value) -> str:
        """按契约输出带 +00:00 的 ISO8601 字符串"""
        dt = self._to_utc_dt(value)
        if dt is None:
            return str(value)
        return dt.isoformat()

    def _to_local_date(self, value) -> date:
        """把输入转为本地日期（用于全天事件）"""
        if isinstance(value, datetime):
            dt = value if value.tzinfo else value.replace(tzinfo=self._tz)
            return dt.astimezone(self._tz).date()
        if isinstance(value, date):
            return value
        dt = self._to_utc_dt(value)
        return dt.astimezone(self._tz).date() if dt else date.today()

    # ========== 事件查询 ==========

    def list_events(
        self,
        calendar_id: str = "primary",
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        max_results: int = 100,
    ) -> List[Dict]:
        """
        列出日历事件

        任一时间为空时使用 now-7d ~ now+30d；按 92 天分片 REPORT，
        递归事件在本地展开（QQ 忽略 C:expand）。
        """
        collection = ensure_trailing_slash(self._select_calendar(calendar_id)["href"])

        if start_time is None or end_time is None:
            now = datetime.now(timezone.utc)
            start_utc = now - timedelta(days=7)
            end_utc = now + timedelta(days=30)
        else:
            start_utc = self._to_utc_dt(start_time)
            end_utc = self._to_utc_dt(end_time)
            if start_utc is None or end_utc is None:
                raise CalendarBackendError("list_events 的时间参数无法解析")
            if end_utc <= start_utc:
                raise CalendarBackendError("list_events 的结束时间必须晚于开始时间")

        events: List[Dict] = []
        seen = set()

        cursor = start_utc
        while cursor < end_utc:
            chunk_end = min(cursor + timedelta(days=_CHUNK_DAYS), end_utc)
            body = calendar_query_body(cursor, chunk_end)
            response = self.session.report(collection, body, depth=1)

            for item in self._parse_query_items(response.content):
                for event in self._items_to_events(item, collection, start_utc, end_utc):
                    key = (event.get("id"), event.get("start"), event.get("end"))
                    if key in seen:
                        continue
                    seen.add(key)
                    events.append(event)
                    if max_results and len(events) >= max_results:
                        return events

            cursor = chunk_end

        return events

    def _parse_query_items(self, content: bytes) -> List[Dict[str, Any]]:
        """解析 REPORT 响应，跳过没有 calendar-data 的条目"""
        items = []
        for entry in parse_multistatus(content):
            if entry.get("ical"):
                items.append(entry)
        return items

    def _items_to_events(
        self,
        item: Dict[str, Any],
        collection: str,
        window_start: datetime,
        window_end: datetime,
    ) -> List[Dict]:
        """把单条 calendar-data 转换为契约事件（含本地递归展开）"""
        href = urljoin(collection, item.get("href") or "")
        if not href:
            return []

        ical_text = item.get("ical") or ""
        try:
            cal = icalendar.Calendar.from_ical(ical_text)
        except Exception as exc:
            raise CalendarBackendError(f"无法解析 iCalendar 数据: {exc}") from exc

        if getattr(cal, "name", "") != "VCALENDAR":
            raise CalendarBackendError("iCalendar 数据缺少 VCALENDAR 组件")

        version = str(cal.get("VERSION") or "").strip()
        if version != "2.0":
            raise CalendarBackendError(f"不支持的 iCalendar 版本: {version or '(空)'}")

        errors = getattr(cal, "errors", None)
        if errors:
            raise CalendarBackendError(f"iCalendar 数据存在解析错误: {errors[:3]}")

        master_events = cal.walk("VEVENT")
        if not master_events:
            return []

        series_recurring = any(
            comp.get("RRULE") or comp.get("RDATE") for comp in master_events
        )

        if series_recurring:
            series = recurring_ical_events.of(cal)
            components = list(series.between(window_start, window_end))
        else:
            components = master_events

        events = []
        for comp in components:
            event = self._component_to_event(
                comp,
                href=href,
                etag=item.get("etag"),
                series_recurring=series_recurring,
            )
            if event:
                events.append(event)
        return events

    def _component_to_event(
        self,
        comp,
        *,
        href: str,
        etag: Optional[str],
        series_recurring: bool,
    ) -> Optional[Dict]:
        """把 VEVENT 组件映射为契约字段"""
        start_prop = comp.get("DTSTART")
        if start_prop is None:
            return None

        start_raw = start_prop.dt
        is_all_day = isinstance(start_raw, date) and not isinstance(start_raw, datetime)

        # 结束时间回退：DTEND → DURATION → 全天取次日本地零点 → 等于 start
        end_prop = comp.get("DTEND")
        if end_prop is not None:
            end_raw = end_prop.dt
        else:
            duration_prop = comp.get("DURATION")
            if duration_prop is not None:
                end_raw = start_raw + duration_prop.dt
            elif is_all_day:
                end_raw = start_raw + timedelta(days=1)
            else:
                end_raw = start_raw

        recurrence_id = comp.get("RECURRENCE-ID")
        if recurrence_id is not None:
            event_id = f"{href}#{self._recurrence_id_text(recurrence_id)}"
            recurring = True
        elif series_recurring:
            event_id = f"{href}#{self._recurrence_id_text(start_prop)}"
            recurring = True
        else:
            event_id = href
            recurring = bool(comp.get("RRULE"))

        status = str(comp.get("STATUS") or "").strip().upper()
        transp = str(comp.get("TRANSP") or "").strip().upper()
        if status == "CANCELLED":
            show_as = "cancelled"
        elif transp == "TRANSPARENT":
            show_as = "free"
        else:
            show_as = "busy"

        location = comp.get("LOCATION")
        return normalize_event(
            source=self.provider_name,
            event_id=event_id,
            subject=str(comp.get("SUMMARY") or ""),
            start=self._as_utc(start_raw),
            end=self._as_utc(end_raw),
            is_all_day=is_all_day,
            show_as=show_as,
            location=str(location) if location else None,
            categories=self._extract_categories(comp),
            recurring=recurring,
            etag=etag,
            uid=str(comp.get("UID") or "") or None,
        )

    def _recurrence_id_text(self, prop) -> str:
        """取 RECURRENCE-ID/DTSTART 的原始时间文本，用于实例 id 后缀"""
        value = getattr(prop, "dt", None)
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, date):
            return value.isoformat()
        return str(prop)

    def _extract_categories(self, comp) -> List[str]:
        """兼容单个/多个 CATEGORIES 属性"""
        raw = comp.get("CATEGORIES")
        if raw is None:
            return []

        values = raw if isinstance(raw, list) else [raw]
        categories: List[str] = []
        for item in values:
            cats = getattr(item, "cats", None)
            if cats:
                categories.extend(str(cat) for cat in cats)
            else:
                categories.append(str(item))
        return categories

    def get_event(self, event_id: str, calendar_id: str = "primary") -> Dict:
        """
        获取单个事件

        QQ 不支持单资源 GET，也不支持 calendar-multiget；这里按 UID 过滤查询，
        UID 过滤失效（207 但 0 条）时退化为 90 天窗口内按 href 匹配。
        """
        collection = ensure_trailing_slash(self._select_calendar(calendar_id)["href"])
        target_href = event_id.split("#", 1)[0]
        target_url = (
            target_href
            if target_href.startswith("http")
            else urljoin(collection, target_href)
        )

        # 我们创建的资源文件名就是 uid，可先按 filename 猜 UID
        filename = os.path.basename(urlparse(target_url).path)
        uid_guess = unquote(filename[:-4] if filename.endswith(".ics") else filename)

        if uid_guess:
            try:
                response = self.session.report(
                    collection, calendar_query_by_uid_body(uid_guess), depth=1
                )
                items = self._parse_query_items(response.content)
            except (NotFoundError, CalendarBackendError):
                items = []

            for item in items:
                item_href = urljoin(collection, item.get("href") or "")
                if item_href.rstrip("/") != target_url.rstrip("/"):
                    continue
                events = self._items_to_events(
                    item,
                    collection,
                    datetime.now(timezone.utc) - timedelta(days=_FALLBACK_DAYS),
                    datetime.now(timezone.utc) + timedelta(days=_FALLBACK_DAYS),
                )
                if events:
                    return events[0]

        # 退化路径：QQ 不支持 UID 过滤时，查 90 天窗口后按 href 匹配
        now = datetime.now(timezone.utc)
        events = self.list_events(
            calendar_id=calendar_id,
            start_time=now - timedelta(days=_FALLBACK_DAYS),
            end_time=now + timedelta(days=_FALLBACK_DAYS),
            max_results=0,
        )
        for event in events:
            if (event.get("id") or "").split("#", 1)[0] == target_url:
                return event

        raise NotFoundError("未找到指定事件", status=404)

    # ========== 事件写入 ==========

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
        创建事件

        QQ 单资源 GET 不可用，因此返回值由入参合成；etag 取 PUT 响应头。
        时间一律写成 UTC（Z），不写 TZID / VTIMEZONE。
        """
        collection = ensure_trailing_slash(self._select_calendar(calendar_id)["href"])

        uid = uuid.uuid4().hex
        target_url = resource_url(collection, uid)

        cal = icalendar.Calendar()
        cal.add("prodid", "-//outlook-calendar-scheduler//CalDAV//CN")
        cal.add("version", "2.0")

        event = icalendar.Event()
        event.add("uid", uid)
        event.add("dtstamp", datetime.now(timezone.utc))
        event.add("summary", subject or "")

        if is_all_day:
            start_date = self._to_local_date(start_time)
            end_date = self._to_local_date(end_time)
            if end_date <= start_date:
                end_date = start_date + timedelta(days=1)
            event.add("dtstart", start_date)
            event.add("dtend", end_date)
        else:
            event.add("dtstart", self._to_utc_dt(start_time))
            event.add("dtend", self._to_utc_dt(end_time))

        if location:
            event.add("location", location)
        if body:
            event.add("description", body)

        # 参会者不写入 ICS（QQ 的 CalDAV 排程能力未验证，避免脏数据）
        cal.add_component(event)

        response = self.session.put(target_url, cal.to_ical())
        etag = response.headers.get("ETag")

        return normalize_event(
            source=self.provider_name,
            event_id=target_url,
            subject=subject,
            start=self._as_utc(start_time),
            end=self._as_utc(end_time),
            is_all_day=is_all_day,
            show_as="busy",
            location=location,
            categories=[],
            recurring=False,
            etag=etag,
            uid=uid,
        )

    def update_event(
        self,
        event_id: str,
        updates: Dict,
        calendar_id: str = "primary",
    ) -> Dict:
        """
        更新事件（v1 不支持）

        原因：QQ 的 OPTIONS 不含 PATCH，单资源 GET 永远 404（无法先读后改），
        且 If-Match 被完全忽略（无法做乐观并发控制）。覆盖写需要调用方提供
        完整 ICS，当前契约只传增量 updates，无法安全合成。
        """
        raise NotImplementedError("CalDAV 后端 v1 暂不支持更新事件")

    def delete_event(self, event_id: str, calendar_id: str = "primary") -> None:
        """删除事件；event_id 可能是 href#RECURRENCE-ID，取 # 前部分"""
        collection = ensure_trailing_slash(self._select_calendar(calendar_id)["href"])
        target = event_id.split("#", 1)[0]
        target_url = target if target.startswith("http") else urljoin(collection, target)

        try:
            self.session.delete(target_url)
        except NotFoundError:
            # 404/410 视为幂等成功
            return

    # ========== 辅助功能 ==========
