#!/usr/bin/env python3
"""
CalDAV 传输层

封装 WebDAV/CalDAV 的底层 HTTP 交互、XML 构造与多状态解析，
供 caldav_backend 复用。仅处理协议细节，不涉及事件语义。
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import quote, urljoin
from xml.etree import ElementTree
from xml.sax.saxutils import escape

import requests
from requests.auth import HTTPBasicAuth

from backend import (
    AuthError,
    CalendarBackendError,
    ConflictError,
    NotFoundError,
)

# QQ 邮箱 CalDAV 预设
QQ_PRESET = {"key": "qq", "label": "QQ 邮箱", "base_url": "https://dav.qq.com"}

DAV_NS = "DAV:"
CALDAV_NS = "urn:ietf:params:xml:ns:caldav"

_XML_DECL = '<?xml version="1.0" encoding="utf-8" ?>'
_SUCCESS_STATUSES = (200, 201, 204, 207)
_REDIRECT_STATUSES = (301, 302, 303, 307, 308)


def ensure_trailing_slash(url: str) -> str:
    """确保集合 URL 以斜杠结尾（QQ 对缺斜杠的集合会返回畸形 href）"""
    if not url:
        return url
    return url if url.endswith("/") else url + "/"


def absolute_url(base: str, href: str) -> str:
    """把相对 href 解析为绝对集合 URL，并强制补尾斜杠"""
    if not href:
        return ""
    return ensure_trailing_slash(urljoin(base, href))


def resource_url(collection: str, name: str) -> str:
    """由集合 URL 与资源名拼出资源 URL（自动补 .ics 后缀）"""
    base = ensure_trailing_slash(collection)
    filename = name if name.endswith(".ics") else name + ".ics"
    return urljoin(base, quote(filename, safe="-_.~"))


class CalDAVSession:
    """CalDAV 会话：统一超时、认证、错误映射"""

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        timeout: int = 30,
    ):
        self.base_url = (base_url or QQ_PRESET["base_url"]).rstrip("/")
        self.username = username
        self.timeout = timeout
        self.session = requests.Session()
        self.session.auth = HTTPBasicAuth(username, password)
        self.session.headers.update(
            {
                "User-Agent": "outlook-calendar-scheduler/1.0",
                "Content-Type": 'application/xml; charset="utf-8"',
            }
        )

    # ========== 底层请求 ==========

    def _send(
        self,
        method: str,
        url: str,
        *,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        use_auth: bool = True,
    ) -> requests.Response:
        """发送原始请求，不重定向；调用方自行处理状态码"""
        original_auth = self.session.auth
        if not use_auth:
            # requests 会把 auth=None 视为沿用 Session 配置，必须临时清空
            self.session.auth = None
        try:
            return self.session.request(
                method,
                url,
                data=data,
                headers=headers,
                timeout=self.timeout,
                allow_redirects=False,
            )
        finally:
            if not use_auth:
                self.session.auth = original_auth

    def _check(self, response: requests.Response, action: str) -> requests.Response:
        """统一状态码映射；错误信息不包含账号密码或 Authorization"""
        status = response.status_code
        if status in _SUCCESS_STATUSES:
            return response
        if status == 401:
            raise AuthError("CalDAV 认证失败，请检查 CALDAV_USERNAME / CALDAV_PASSWORD", status=401)
        if status in (404, 410):
            raise NotFoundError("CalDAV 资源不存在", status=status)
        if status in (409, 412):
            raise ConflictError("CalDAV 请求冲突", status=status)
        body = response.text or ""
        raise CalendarBackendError(f"CalDAV {action} 请求失败", status=status, body=body)

    # ========== 协议方法 ==========

    def propfind(self, url: str, body: bytes, depth: int = 0) -> requests.Response:
        """PROPFIND 请求"""
        headers = {"Depth": str(depth)}
        response = self._send("PROPFIND", url, data=body, headers=headers)
        return self._check(response, "PROPFIND")

    def report(self, url: str, body: bytes, depth: int = 1) -> requests.Response:
        """REPORT 请求（calendar-query）"""
        headers = {"Depth": str(depth)}
        response = self._send("REPORT", url, data=body, headers=headers)
        return self._check(response, "REPORT")

    def put(
        self,
        url: str,
        data: bytes,
        content_type: str = "text/calendar; charset=utf-8",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> requests.Response:
        """PUT 资源；QQ 会忽略 If-None-Match / If-Match，调用方无需设置"""
        headers = {"Content-Type": content_type}
        if extra_headers:
            headers.update(extra_headers)
        response = self._send("PUT", url, data=data, headers=headers)
        return self._check(response, "PUT")

    def delete(self, url: str) -> requests.Response:
        """DELETE 资源；404/410 由调用方决定是否视为幂等成功"""
        response = self._send("DELETE", url)
        return self._check(response, "DELETE")

    def get(self, url: str, use_auth: bool = True) -> requests.Response:
        """GET 请求（QQ 单资源 GET 不可用，仅用于服务入口探测）"""
        response = self._send("GET", url, use_auth=use_auth)
        return self._check(response, "GET")

    def mkcalendar(self, url: str, display_name: str) -> requests.Response:
        """MKCALENDAR 请求；QQ 会分配自己的规范 href，调用方需重新查询"""
        response = self._send(
            "MKCALENDAR",
            ensure_trailing_slash(url),
            data=mkcalendar_body(display_name),
        )
        return self._check(response, "MKCALENDAR")


# ========== XML 构造器 ==========


def _xml(text: str) -> bytes:
    """编码 XML 字符串"""
    return (text.strip() + "\n").encode("utf-8")


def propfind_principal_body() -> bytes:
    """查询 current-user-principal / principal-URL"""
    return _xml(
        f"""{_XML_DECL}
<d:propfind xmlns:d="{DAV_NS}">
  <d:prop>
    <d:current-user-principal/>
    <d:principal-URL/>
  </d:prop>
</d:propfind>"""
    )


def propfind_home_body() -> bytes:
    """查询 calendar-home-set"""
    return _xml(
        f"""{_XML_DECL}
<d:propfind xmlns:d="{DAV_NS}" xmlns:c="{CALDAV_NS}">
  <d:prop>
    <c:calendar-home-set/>
  </d:prop>
</d:propfind>"""
    )


def propfind_collections_body() -> bytes:
    """查询集合列表所需的属性"""
    return _xml(
        f"""{_XML_DECL}
<d:propfind xmlns:d="{DAV_NS}" xmlns:c="{CALDAV_NS}">
  <d:prop>
    <d:resourcetype/>
    <d:displayname/>
    <c:supported-calendar-component-set/>
  </d:prop>
</d:propfind>"""
    )


def propfind_resource_body() -> bytes:
    """查询单个资源的 getetag / resourcetype"""
    return _xml(
        f"""{_XML_DECL}
<d:propfind xmlns:d="{DAV_NS}">
  <d:prop>
    <d:getetag/>
    <d:resourcetype/>
  </d:prop>
</d:propfind>"""
    )


def calendar_query_body(
    start_utc,
    end_utc,
    want_expand: bool = False,
) -> bytes:
    """
    构造 calendar-query 请求体

    want_expand 仅为保留参数：QQ 会忽略 C:expand，递归需调用方本地展开。
    """
    start_text = _format_utc(start_utc)
    end_text = _format_utc(end_utc)
    if want_expand:
        data_node = (
            f"<c:calendar-data>"
            f'<c:expand start="{start_text}" end="{end_text}"/>'
            f"</c:calendar-data>"
        )
    else:
        data_node = "<c:calendar-data/>"

    return _xml(
        f"""{_XML_DECL}
<c:calendar-query xmlns:d="{DAV_NS}" xmlns:c="{CALDAV_NS}">
  <d:prop>
    <d:getetag/>
    {data_node}
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:time-range start="{start_text}" end="{end_text}"/>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
    )


def calendar_query_by_uid_body(uid: str) -> bytes:
    """构造按 UID 过滤的 calendar-query 请求体"""
    safe_uid = escape(uid or "")
    return _xml(
        f"""{_XML_DECL}
<c:calendar-query xmlns:d="{DAV_NS}" xmlns:c="{CALDAV_NS}">
  <d:prop>
    <d:getetag/>
    <c:calendar-data/>
  </d:prop>
  <c:filter>
    <c:comp-filter name="VCALENDAR">
      <c:comp-filter name="VEVENT">
        <c:prop-filter name="UID">
          <c:text-match collation="i;octet">{safe_uid}</c:text-match>
        </c:prop-filter>
      </c:comp-filter>
    </c:comp-filter>
  </c:filter>
</c:calendar-query>"""
    )


def mkcalendar_body(display_name: str) -> bytes:
    """构造 MKCALENDAR 请求体"""
    safe_name = escape(display_name or "")
    return _xml(
        f"""{_XML_DECL}
<c:mkcalendar xmlns:d="{DAV_NS}" xmlns:c="{CALDAV_NS}">
  <d:set>
    <d:prop>
      <d:displayname>{safe_name}</d:displayname>
      <c:supported-calendar-component-set>
        <c:comp name="VEVENT"/>
      </c:supported-calendar-component-set>
    </d:prop>
  </d:set>
</c:mkcalendar>"""
    )


def _format_utc(value) -> str:
    """把 datetime/date 格式化为 CalDAV time-range 要求的 UTC 形式"""
    from datetime import date, datetime, time, timezone

    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
    elif isinstance(value, date):
        dt = datetime.combine(value, time.min, tzinfo=timezone.utc)
    else:
        return str(value)
    return dt.strftime("%Y%m%dT%H%M%SZ")


# ========== 多状态解析 ==========


def _propstat_ok(propstat: ElementTree.Element) -> bool:
    """仅接受状态码为 200 的 propstat"""
    status = propstat.find(f"{{{DAV_NS}}}status")
    if status is None or not status.text:
        return False
    return " 200 " in f" {status.text.strip()} "


def parse_multistatus(xml_bytes: bytes) -> List[Dict[str, Any]]:
    """解析 207 Multi-Status，返回 href 及常用属性"""
    if not xml_bytes:
        return []

    try:
        root = ElementTree.fromstring(xml_bytes)
    except ElementTree.ParseError as exc:
        raise CalendarBackendError(f"CalDAV 响应不是合法 XML: {exc}") from exc

    results: List[Dict[str, Any]] = []
    for response in root.findall(f"{{{DAV_NS}}}response"):
        href_node = response.find(f"{{{DAV_NS}}}href")
        href = (href_node.text or "").strip() if href_node is not None else ""
        if not href:
            continue

        entry: Dict[str, Any] = {
            "href": href,
            "displayname": "",
            "resourcetype": [],
            "components": [],
            "etag": None,
            "ical": None,
            "is_calendar": False,
        }

        for propstat in response.findall(f"{{{DAV_NS}}}propstat"):
            if not _propstat_ok(propstat):
                continue
            prop = propstat.find(f"{{{DAV_NS}}}prop")
            if prop is None:
                continue

            displayname = prop.find(f"{{{DAV_NS}}}displayname")
            if displayname is not None and displayname.text:
                entry["displayname"] = displayname.text.strip()

            resourcetype = prop.find(f"{{{DAV_NS}}}resourcetype")
            if resourcetype is not None:
                types = []
                for child in list(resourcetype):
                    if not isinstance(child.tag, str):
                        continue
                    types.append(child.tag)
                    if child.tag == f"{{{CALDAV_NS}}}calendar":
                        entry["is_calendar"] = True
                entry["resourcetype"] = types

            comp_set = prop.find(f"{{{CALDAV_NS}}}supported-calendar-component-set")
            if comp_set is not None:
                entry["components"] = [
                    (comp.get("name") or "").upper()
                    for comp in comp_set.findall(f"{{{CALDAV_NS}}}comp")
                    if comp.get("name")
                ]

            etag = prop.find(f"{{{DAV_NS}}}getetag")
            if etag is not None and etag.text:
                entry["etag"] = etag.text.strip()

            ical = prop.find(f"{{{CALDAV_NS}}}calendar-data")
            if ical is not None and ical.text:
                entry["ical"] = ical.text

        results.append(entry)

    return results


# ========== 服务发现 ==========


@dataclass
class DiscoveryResult:
    """CalDAV 服务发现结果"""

    principal: str
    home: str
    calendars: List[Dict[str, Any]] = field(default_factory=list)


def _propfind_principal(session: CalDAVSession, url: str) -> str:
    """在指定 URL 上查询 current-user-principal"""
    response = session.propfind(url, propfind_principal_body(), depth=0)
    try:
        root = ElementTree.fromstring(response.content)
    except ElementTree.ParseError as exc:
        raise CalendarBackendError(f"principal 响应不是合法 XML: {exc}") from exc
    node = root.find(f".//{{{DAV_NS}}}current-user-principal/{{{DAV_NS}}}href")
    if node is not None and node.text:
        return node.text.strip()

    # 少数服务只返回 principal-URL
    node = root.find(f".//{{{DAV_NS}}}principal-URL/{{{DAV_NS}}}href")
    if node is not None and node.text:
        return node.text.strip()
    return ""


def discover(session: CalDAVSession) -> DiscoveryResult:
    """
    完整发现链路：well-known → principal → calendar-home → 集合列表

    QQ 的 well-known 返回 301 且 Location 为相对路径，必须用 urljoin 解析。
    """
    base = session.base_url
    well_known = urljoin(base + "/", "/.well-known/caldav")

    # 1. well-known：先匿名探测，401 再带认证；404/405/410 回退到 base_url
    response = session._send("GET", well_known, use_auth=False)
    if response.status_code == 401:
        response = session._send("GET", well_known, use_auth=True)

    entry = base
    if response.status_code in _REDIRECT_STATUSES:
        location = response.headers.get("Location") or ""
        if location:
            entry = urljoin(well_known, location)
    elif response.status_code in (404, 405, 410):
        entry = base
    elif response.status_code == 200:
        entry = well_known

    # 2. principal
    try:
        principal_href = _propfind_principal(session, ensure_trailing_slash(entry))
    except (NotFoundError, CalendarBackendError):
        principal_href = ""
        if ensure_trailing_slash(entry) != ensure_trailing_slash(base):
            try:
                principal_href = _propfind_principal(session, ensure_trailing_slash(base))
            except (NotFoundError, CalendarBackendError):
                principal_href = ""

    if not principal_href:
        raise CalendarBackendError("未找到日历服务入口，请检查 CALDAV_BASE_URL")

    principal = ensure_trailing_slash(urljoin(entry, principal_href))

    # 3. calendar-home-set
    home_href = ""
    try:
        response = session.propfind(principal, propfind_home_body(), depth=0)
        root = ElementTree.fromstring(response.content)
        node = root.find(f".//{{{CALDAV_NS}}}calendar-home-set/{{{DAV_NS}}}href")
        if node is not None and node.text:
            home_href = node.text.strip()
    except (NotFoundError, CalendarBackendError, ElementTree.ParseError):
        home_href = ""

    if not home_href:
        raise CalendarBackendError("未找到日历集合入口（calendar-home-set）")

    home = ensure_trailing_slash(urljoin(principal, home_href))

    # 4. 子集合列表（Depth:1）；home 自身、inbox/outbox、principal 均需过滤
    response = session.propfind(home, propfind_collections_body(), depth=1)
    entries = parse_multistatus(response.content)

    calendars: List[Dict[str, Any]] = []
    home_key = home.rstrip("/")
    principal_key = principal.rstrip("/")

    for entry_item in entries:
        href = absolute_url(home, entry_item.get("href", ""))
        if not href:
            continue

        key = href.rstrip("/")
        if key == home_key:
            continue
        if key == principal_key:
            continue

        # inbox / outbox 不是日历集合
        tail = key.rsplit("/", 1)[-1].lower()
        if tail in ("inbox", "outbox"):
            continue

        calendars.append(
            {
                "href": href,
                "displayname": entry_item.get("displayname") or "",
                "components": entry_item.get("components") or [],
                "is_calendar": bool(entry_item.get("is_calendar")),
            }
        )

    return DiscoveryResult(principal=principal, home=home, calendars=calendars)
