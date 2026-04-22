#!/usr/bin/env python3
"""
定时排程推荐算法模块

基于用户偏好、现有事件和约束条件推荐最佳时间段。
"""

from datetime import datetime, timedelta, time
from dateutil import tz
from typing import List, Dict, Optional, Tuple
import json
from pathlib import Path


class SmartScheduler:
    """智能排程器"""

    def __init__(self, timezone: str = "Asia/Shanghai"):
        self.timezone = timezone
        self.preference_path = Path(__file__).parent.parent / ".outlook_calendar_preferences.json"
        self.preferences = self._load_preferences()
        self._tz = tz.gettz(self.preferences.get("time_zone", self.timezone))

    def _load_preferences(self) -> Dict:
        """加载用户偏好"""
        if self.preference_path.exists():
            try:
                with open(self.preference_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except:
                pass

        # 默认偏好
        default = {
            "working_hours": {
                "start": "09:00",
                "end": "18:00"
            },
            "buffer_minutes": 15,
            "default_duration_minutes": 60,
            "preferred_durations": [30, 60, 90, 120],
            "time_zone": self.timezone,
            "avoid_lunch_break": True,
            "lunch_break": {
                "start": "12:00",
                "end": "14:00"
            },
            "preferred_days": ["monday", "tuesday", "wednesday", "thursday", "friday"],
            "min_free_window": 30,
            "learn_from_choices": True
        }
        self._save_preferences(default)
        return default

    def _save_preferences(self, prefs: Optional[Dict] = None):
        """保存用户偏好"""
        prefs = prefs or self.preferences
        try:
            with open(self.preference_path, 'w', encoding='utf-8') as f:
                json.dump(prefs, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"警告: 无法保存偏好设置: {e}")

    def learn_from_choice(self, selected_slot: Dict):
        """从用户选择中学习偏好"""
        if not self.preferences.get("learn_from_choices", True):
            return

        # 记录选择的时间特征
        day_of_week = selected_slot["start"].strftime("%A").lower()
        hour = selected_slot["start"].hour

        # 更新用户偏好的时间段 (简单统计)
        if "chosen_hours" not in self.preferences:
            self.preferences["chosen_hours"] = {}

        hour_key = f"{hour:02d}:00"
        self.preferences["chosen_hours"][hour_key] = self.preferences["chosen_hours"].get(hour_key, 0) + 1

        # 更新偏好工作日
        if "chosen_days" not in self.preferences:
            self.preferences["chosen_days"] = {}

        self.preferences["chosen_days"][day_of_week] = self.preferences["chosen_days"].get(day_of_week, 0) + 1

        self._save_preferences()

    def suggest_times(
        self,
        duration_minutes: int,
        constraints: Optional[Dict],
        existing_events: List[Dict],
        lookahead_days: int = 7,
        num_suggestions: int = 3
    ) -> List[Dict]:
        """
        推荐最佳时间段

        参数:
        - duration_minutes: 会议时长
        - constraints: 时间约束 (如 deadline、时间段偏好)
        - existing_events: 现有事件列表 (Graph API 返回)
        - lookahead_days: 向前看多少天
        - num_suggestions: 推荐数量

        返回: 推荐时间段列表,每个元素为:
        {
            "start": datetime,
            "end": datetime,
            "score": float,  # 适宜度分数 0-1
            "reason": str    # 推荐理由
        }
        """
        now = datetime.now(self._tz)
        end_date = now + timedelta(days=lookahead_days)

        # 1. 生成候选时间窗口
        candidates = self._generate_candidates(now, end_date, duration_minutes, constraints)

        # 2. 计算每个候选的冲突和分数
        scored_candidates = []
        for candidate in candidates:
            score, reason = self._score_candidate(candidate, existing_events)
            if score > 0:
                scored_candidates.append({
                    **candidate,
                    "score": round(score, 3),
                    "reason": reason
                })

        # 3. 排序并返回前 N 个
        scored_candidates.sort(key=lambda x: x["score"], reverse=True)
        return scored_candidates[:num_suggestions]

    def _generate_candidates(
        self,
        start_date: datetime,
        end_date: datetime,
        duration_minutes: int,
        constraints: Optional[Dict]
    ) -> List[Dict]:
        """生成候选时间段"""
        candidates = []
        working_start = self._parse_time(self.preferences["working_hours"]["start"])
        working_end = self._parse_time(self.preferences["working_hours"]["end"])

        cursor = start_date.replace(
            hour=working_start.hour,
            minute=working_start.minute,
            second=0,
            microsecond=0
        )
        if cursor < start_date:
            cursor = cursor + timedelta(days=1)
            if cursor.date() > start_date.date():
                cursor = cursor.replace(hour=working_start.hour, minute=working_start.minute, second=0, microsecond=0)

        while cursor < end_date:
            weekday = cursor.strftime("%A").lower()
            preferred_days = self.preferences.get("preferred_days", ["monday", "tuesday", "wednesday", "thursday", "friday"])
            if weekday not in preferred_days:
                cursor = self._next_day_start(cursor, working_start)
                continue

            day_end = self._combine_time(cursor, working_end)
            current = cursor
            if current < start_date:
                current = start_date

            while current < min(day_end, end_date):
                if self.preferences.get("avoid_lunch_break", True):
                    lunch_start = self._parse_time(self.preferences["lunch_break"]["start"])
                    lunch_end = self._parse_time(self.preferences["lunch_break"]["end"])
                    if lunch_start <= current.time() < lunch_end:
                        current = current.replace(hour=lunch_end.hour, minute=lunch_end.minute, second=0, microsecond=0)
                        continue

                if constraints and constraints.get("time_preference") == "morning" and current.hour >= 12:
                    current += timedelta(minutes=15)
                    continue

                candidate_end = current + timedelta(minutes=duration_minutes)
                if candidate_end <= day_end and candidate_end <= end_date:
                    candidates.append({
                        "start": current,
                        "end": candidate_end
                    })

                current += timedelta(minutes=15)

            cursor = self._next_day_start(cursor, working_start)

        return candidates

    def _next_day_start(self, current: datetime, working_start: time) -> datetime:
        """移动到下一天的工作开始时间"""
        next_day = (current.date() + timedelta(days=1))
        return self._combine_date_time(next_day, working_start, current.tzinfo)

    def _combine_time(self, anchor: datetime, target_time: time) -> datetime:
        """将 anchor 当日替换为目标时间"""
        return self._combine_date_time(anchor.date(), target_time, anchor.tzinfo)

    def _combine_date_time(self, target_date, target_time: time, tzinfo):
        """兼容时区信息地合成 datetime"""
        dt = datetime.combine(target_date, target_time)
        return dt if tzinfo is None else dt.replace(tzinfo=tzinfo)

    def _coerce_datetime(self, value: object) -> Optional[datetime]:
        """将 Graph 或本地时间字段转成 datetime，确保带时区"""
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, dict):
            value = value.get("dateTime")
            if not isinstance(value, str):
                return None
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        elif isinstance(value, str):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        else:
            return None

        # 确保返回带时区的 datetime
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=self._tz)
        return dt

    def _score_candidate(self, candidate: Dict, existing_events: List[Dict]) -> Tuple[float, str]:
        """
        计算候选时间段适宜度分数

        考虑因素:
        - 与现有事件的距离 (需缓冲)
        - 用户历史选择偏好
        - 时间偏好 (上午/下午)
        - 是否接近截止时间
        """
        buffer = timedelta(minutes=self.preferences["buffer_minutes"])

        # 检查缓冲冲突
        candidate_start = candidate["start"]
        candidate_end = candidate["end"]

        # 确保候选时间带时区
        if candidate_start.tzinfo is None:
            candidate_start = candidate_start.replace(tzinfo=self._tz)
        if candidate_end.tzinfo is None:
            candidate_end = candidate_end.replace(tzinfo=self._tz)

        for event in existing_events:
            event_start = self._coerce_datetime(event.get("start"))
            event_end = self._coerce_datetime(event.get("end"))

            if not event_start or not event_end:
                continue

            if (candidate_start - buffer < event_end and candidate_end + buffer > event_start):
                return 0.0, "与现有事件冲突"

        score = 1.0
        reasons = ["符合工作时间"]

        # 历史偏好加分
        hour_key = f"{candidate['start'].hour:02d}:00"
        if "chosen_hours" in self.preferences:
            chosen_count = self.preferences["chosen_hours"].get(hour_key, 0)
            if chosen_count > 0:
                bonus = min(0.2, chosen_count * 0.05)
                score += bonus
                reasons.append(f"历史选择加分 ({hour_key})")

        # 上午偏好
        if candidate["start"].hour < 12:
            score += 0.1
            reasons.append("上午时段")

        return min(1.0, score), "、".join(reasons)

    def _parse_time(self, time_str: str) -> time:
        """解析时间字符串 HH:MM"""
        hour, minute = map(int, time_str.split(":"))
        return time(hour=hour, minute=minute)


def get_scheduler() -> SmartScheduler:
    """获取排程器实例"""
    return SmartScheduler()
