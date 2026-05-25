#!/usr/bin/env python3
"""
自然语言事件解析模块

从用户输入的自然语言中提取结构化事件信息。
"""

import re
from datetime import datetime, timedelta, time
from typing import Dict, Optional, List
import dateparser
from dateutil import tz


class NLPParser:
    """事件自然语言解析器"""

    # 时间模式定义
    TIME_PATTERNS = {
        r'(\d{1,2})[:：](\d{2})\s*(?:至|到|-|～)\s*(\d{1,2})[:：](\d{2})': 'time_range_exact',
        r'(\d{1,2})点(?:半)?到(\d{1,2})点(?:半)?': 'time_range_chinese',
        r'(\d{1,2})[:：](\d{2})': 'time_single',
        r'上午|早上|早|下午|晚上|傍晚': 'time_period'
    }

    # 日期相对表达
    DATE_RELATIVE = {
        '今天': 0, '明天': 1, '后天': 2,
        '大后天': 3, '本周': 'this_week', '下周': 'next_week',
        '下周一': 'next_monday', '下周二': 'next_tuesday',
        '下周三': 'next_wednesday', '下周四': 'next_thursday',
        '下周五': 'next_friday', '下周六': 'next_saturday',
        '下周日': 'next_sunday',
        '周一': 'monday', '周二': 'tuesday', '周三': 'wednesday',
        '周四': 'thursday', '周五': 'friday', '周六': 'saturday', '周日': 'sunday'
    }

    # 时长模式
    DURATION_PATTERNS = [
        r'(\d+)\s*(?:小?时|hr|小时)',
        r'(\d+)\s*(?:分钟|分|min|分钟)',
        r'(\d+)\s*(?:半小时|half)'
    ]

    def __init__(self, user_timezone: str = "Asia/Shanghai"):
        self.user_timezone = tz.gettz(user_timezone)
        self.user_timezone_name = user_timezone

    def parse(self, text: str) -> Dict:
        """
        解析自然语言事件文本

        返回结构:
        {
            "title": str,                    # 事件标题
            "start": datetime,               # 开始时间 (本地时区)
            "end": datetime,                 # 结束时间
            "duration_minutes": int,         # 时长(分钟)
            "location": Optional[str],       # 地点
            "attendees": List[str],          # 参会者
            "constraints": Dict,             # 时间约束
            "raw_text": str                  # 原始文本
        }
        """
        result = {
            "title": self._extract_title(text),
            "start": None,
            "end": None,
            "duration_minutes": 60,  # 默认 1 小时
            "location": None,
            "attendees": [],
            "constraints": {},
            "raw_text": text
        }

        # 1. 提取时间信息
        start, end = self._parse_datetime(text)
        if start and end:
            result["start"] = start
            result["end"] = end
            result["duration_minutes"] = int((end - start).total_seconds() / 60)
        elif start:
            # 只有开始时间,使用默认时长
            result["start"] = start

        # 2. 提取时长 (如果未从时间范围推断)
        if result["duration_minutes"] == 60:
            duration = self._extract_duration(text)
            if duration:
                result["duration_minutes"] = duration
                if result["start"] and not result["end"]:
                    result["end"] = result["start"] + timedelta(minutes=duration)

        # 3. 提取地点
        location = self._extract_location(text)
        if location:
            result["location"] = location

        # 4. 提取参会者
        attendees = self._extract_attendees(text)
        if attendees:
            result["attendees"] = attendees

        # 5. 提取约束条件
        constraints = self._extract_constraints(text)
        result["constraints"].update(constraints)

        return result

    def _extract_title(self, text: str) -> str:
        """提取事件标题"""
        patterns_to_remove = [
            r'\d{1,2}[点:：]\d{0,2}\s*(?:至|到|-|～)\s*\d{1,2}[点:：]\d{0,2}',  # 时间范围
            r'\d{1,2}[点:：]\d{0,2}',  # 单一时间
            r'(今天|明天|后天|大后天)',
            r'(下个?|这|本)\s*(周|星期|礼拜)\s*[一二三四五六日天]?',
            r'(周一|周二|周三|周四|周五|周六|周日|星期[一二三四五六日天])',
            r'(上午|早上|下午|晚上|傍晚|中午)',
            r'在\w+(?:会议室|室|点)?',
            r'请\w+参加',
            r'@\w+',
            r'\s+',  # 多余空白
        ]

        title = text
        for pattern in patterns_to_remove:
            title = re.sub(pattern, ' ', title, flags=re.IGNORECASE)

        return title.strip()[:100] or "未命名事件"

    def _parse_datetime(self, text: str) -> tuple[Optional[datetime], Optional[datetime]]:
        """解析日期时间"""
        settings = {
            'TIMEZONE': self.user_timezone_name,
            'RETURN_AS_TIMEZONE_AWARE': True,
            'PREFER_DATES_FROM': 'future'
        }

        # 1. 先检测相对日期（明天、后天、下周五等）
        target_date = None
        today = datetime.now(tz=self.user_timezone).date()
        for rel, offset in self.DATE_RELATIVE.items():
            if rel in text:
                if offset == 'this_week':
                    continue
                elif offset == 'next_week':
                    target_date = today + timedelta(days=7)
                elif isinstance(offset, int):
                    target_date = today + timedelta(days=offset)
                elif offset in ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']:
                    weekday_map = {'monday': 0, 'tuesday': 1, 'wednesday': 2,
                                   'thursday': 3, 'friday': 4, 'saturday': 5, 'sunday': 6}
                    target_weekday = weekday_map[offset]
                    days_ahead = (target_weekday - today.weekday()) % 7
                    if days_ahead == 0:
                        days_ahead = 7
                    target_date = today + timedelta(days=days_ahead)
                break

        # 2. 检测时段前缀
        period_prefix = ''
        for p in ['下午', '晚上', '傍晚', '上午', '早上']:
            if p in text:
                period_prefix = p
                break

        # 3. 查找时间范围（"3点到4点"、"15:00-16:00"）
        range_match = re.search(r'(\d{1,2}[点:：]\d{0,2})\s*(?:至|到|-|～)\s*(\d{1,2}[点:：]\d{0,2})', text)
        if range_match:
            start_h, start_m = self._parse_time_str(range_match.group(1), period_prefix)
            end_h, end_m = self._parse_time_str(range_match.group(2), period_prefix)
            base_date = target_date or today
            start = datetime.combine(base_date, time(start_h, start_m), tzinfo=self.user_timezone)
            end = datetime.combine(base_date, time(end_h, end_m), tzinfo=self.user_timezone)
            if end < start:
                end += timedelta(days=1)
            return start, end

        # 4. 单一时间（"3点"、"15:00"）
        single_match = re.search(r'(\d{1,2}[点:：]\d{0,2})', text)
        if single_match:
            h, m = self._parse_time_str(single_match.group(1), period_prefix)
            base_date = target_date or today
            start = datetime.combine(base_date, time(h, m), tzinfo=self.user_timezone)
            return start, None

        # 5. 只有相对日期，无具体时间
        if target_date:
            hour = 9 if period_prefix in ('上午', '早上') else 14
            start = datetime.combine(target_date, time(hour, 0), tzinfo=self.user_timezone)
            return start, None

        # 6. 尝试通用解析
        parsed = dateparser.parse(text, settings=settings)
        if parsed:
            return parsed, None

        return None, None

    def _parse_time_str(self, time_str: str, period_prefix: str = '') -> tuple[int, int]:
        """解析时间字符串为 (hour, minute)，处理时段前缀"""
        time_str = time_str.replace('点', ':').rstrip(':')
        if ':' in time_str:
            parts = time_str.split(':')
            h, m = int(parts[0]), int(parts[1]) if len(parts) > 1 and parts[1] else 0
        else:
            h, m = int(time_str), 0

        # 下午/晚上时段，小时 < 12 则 +12
        if period_prefix in ('下午', '晚上', '傍晚') and h < 12:
            h += 12
        return h, m

    def _extract_duration(self, text: str) -> Optional[int]:
        """提取时长(分钟)"""
        for pattern in self.DURATION_PATTERNS:
            match = re.search(pattern, text)
            if match:
                value = int(match.group(1))
                if '半小时' in pattern:
                    return 30
                elif '分钟' in pattern:
                    return value
                else:  # 小时
                    return value * 60
        return None

    def _extract_location(self, text: str) -> Optional[str]:
        """提取地点"""
        location_patterns = [
            r'在(?:.*?)(会议室|室|地点|线上|Zoom|Teams|腾讯会议)',
            r'地点[：:]\s*(\S+)',
            r'在@(\w+)'
        ]

        for pattern in location_patterns:
            match = re.search(pattern, text)
            if match:
                return match.group(1) if len(match.groups()) == 1 else match.group(0)
        return None

    def _extract_attendees(self, text: str) -> List[str]:
        """提取参会者"""
        attendees = []

        # @用户名
        mentions = re.findall(r'@(\w+)', text)
        attendees.extend(mentions)

        # "邀请XX参加"
        invites = re.findall(r'邀请(.+?)参加', text)
        for invite in invites:
            attendees.append(invite)

        return list(set(attendees))

    def _extract_constraints(self, text: str) -> Dict:
        """提取时间约束"""
        constraints = {}

        # 时长优先级
        if '紧急' in text or ' ASAP' in text or '尽快' in text:
            constraints["priority"] = "high"

        # 时间段偏好
        if '上午' in text or '早上' in text:
            constraints["time_preference"] = "morning"
        elif '下午' in text:
            constraints["time_preference"] = "afternoon"
        elif '晚上' in text:
            constraints["time_preference"] = "evening"

        # 截止时间
        deadline_match = re.search(r'(?:前|之前|before)\s*完成', text)
        if deadline_match and '本周' in text:
            constraints["deadline"] = "end_of_week"

        return constraints


def parse_event(text: str, user_timezone: str = "Asia/Shanghai") -> Dict:
    """便捷函数: 解析事件文本"""
    parser = NLPParser(user_timezone)
    return parser.parse(text)
