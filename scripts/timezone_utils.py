#!/usr/bin/env python3
"""
多时区处理模块

处理 UTC 与本地时区转换、夏令时等。
"""

from datetime import datetime
from typing import Optional, Dict
from dateutil import tz


class TimezoneHandler:
    """时区处理工具"""

    def __init__(self, user_timezone: str = "Asia/Shanghai"):
        self.user_timezone_str = user_timezone
        self.user_tz = tz.gettz(user_timezone)
        self.utc_tz = tz.UTC

        if not self.user_tz:
            raise ValueError(f"无效的时区: {user_timezone}")

    def to_utc(self, local_dt: datetime) -> datetime:
        """将本地时间转换为 UTC"""
        if local_dt.tzinfo is None:
            local_dt = local_dt.replace(tzinfo=self.user_tz)
        return local_dt.astimezone(self.utc_tz)

    def from_utc(self, utc_dt: datetime) -> datetime:
        """将 UTC 转换为本地时间"""
        if utc_dt.tzinfo is None:
            utc_dt = utc_dt.replace(tzinfo=self.utc_tz)
        return utc_dt.astimezone(self.user_tz)

    def format_for_graph_api(self, dt: datetime) -> str:
        """格式化为 Graph API 所需格式 (ISO 8601)"""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.user_tz)
        utc_dt = self.to_utc(dt)
        return utc_dt.isoformat()

    def parse_from_string(self, dt_str: str, is_utc: bool = False) -> datetime:
        """从字符串解析为 datetime"""
        # 处理 ISO 格式
        from dateutil import parser
        dt = parser.parse(dt_str)

        if is_utc and dt.tzinfo is None:
            dt = dt.replace(tzinfo=self.utc_tz)

        return dt

    def get_local_now(self) -> datetime:
        """获取当前本地时间"""
        return datetime.now(tz=self.user_tz)

    def get_utc_now(self) -> datetime:
        """获取当前 UTC 时间"""
        return datetime.now(tz=self.utc_tz)

    def is_dst(self, dt: Optional[datetime] = None) -> bool:
        """检查是否夏令时"""
        dt = dt or self.get_local_now()
        return bool(self.user_tz.dst(dt))

    def get_timezone_info(self) -> Dict:
        """获取时区信息"""
        now = self.get_local_now()
        offset = now.utcoffset()
        return {
            "timezone": self.user_timezone_str,
            "offset_hours": offset.total_seconds() / 3600,
            "is_dst": self.is_dst(now),
            "current_time": now.isoformat()
        }


def get_timezone_handler(user_timezone: str = "Asia/Shanghai") -> TimezoneHandler:
    """获取时区处理器实例"""
    return TimezoneHandler(user_timezone)
