#!/usr/bin/env python3
"""
用户偏好学习模块

记录和分析用户日历操作行为，优化排程推荐。
"""

from datetime import datetime
from typing import Dict, List, Optional
import json
from pathlib import Path
from scheduler import SmartScheduler


class PreferenceLearner:
    """用户偏好学习器"""

    def __init__(self, prefs_path: Optional[Path] = None):
        self.preference_path = prefs_path or (Path(__file__).parent.parent / ".outlook_calendar_preferences.json")
        self.history_path = Path(__file__).parent.parent / ".outlook_calendar_history.json"
        # 确保目录存在
        self.history_path.parent.mkdir(parents=True, exist_ok=True)
        self.scheduler = SmartScheduler()
        self._load_history()

    def _load_history(self):
        """加载操作历史"""
        if self.history_path.exists():
            try:
                with open(self.history_path, 'r', encoding='utf-8') as f:
                    self.history = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.history = {"choices": [], "rejections": [], "created_events": []}
        else:
            self.history = {"choices": [], "rejections": [], "created_events": []}

    def _save_history(self):
        """保存操作历史"""
        try:
            with open(self.history_path, 'w', encoding='utf-8') as f:
                json.dump(self.history, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"警告: 无法保存历史记录: {e}")

    def record_suggestion_response(
        self,
        suggestions: List[Dict],
        selected_index: Optional[int],
        feedback: Optional[str] = None
    ):
        """
        记录用户对推荐结果的响应

        参数:
        - suggestions: 推荐列表
        - selected_index: 用户选择的索引 (-1 表示拒绝所有)
        - feedback: 用户反馈 (如"太早了"、"时间不合适")
        """
        if selected_index >= 0 and selected_index < len(suggestions):
            selected = suggestions[selected_index]
            self.history["choices"].append({
                "timestamp": datetime.now().isoformat(),
                "start": selected["start"].isoformat(),
                "end": selected["end"].isoformat(),
                "score": selected["score"],
                "reason": selected.get("reason", ""),
                "feedback": feedback
            })

            # 学习:更新偏好权重
            self.scheduler.learn_from_choice(selected)

        elif selected_index == -1:
            self.history["rejections"].append({
                "timestamp": datetime.now().isoformat(),
                "suggestions_count": len(suggestions),
                "feedback": feedback
            })

        self._save_history()

    def record_event_created(self, event_data: Dict):
        """记录创建的事件"""
        self.history["created_events"].append({
            "timestamp": datetime.now().isoformat(),
            "subject": event_data.get("subject", ""),
            "start": event_data["start"]["dateTime"] if isinstance(event_data.get("start"), dict) else str(event_data.get("start")),
            "end": event_data["end"]["dateTime"] if isinstance(event_data.get("end"), dict) else str(event_data.get("end")),
            "duration_minutes": event_data.get("duration_minutes")
        })
        self._save_history()

    def get_preference_summary(self) -> Dict:
        """获取偏好摘要"""
        prefs = self.scheduler.preferences

        # 分析历史选择模式
        if self.history["choices"]:
            chosen_hours = {}
            chosen_days = {}

            for choice in self.history["choices"]:
                start_dt = datetime.fromisoformat(choice["start"])
                hour_key = f"{start_dt.hour:02d}:00"
                day_key = start_dt.strftime("%A").lower()

                chosen_hours[hour_key] = chosen_hours.get(hour_key, 0) + 1
                chosen_days[day_key] = chosen_days.get(day_key, 0) + 1

            # 排序
            top_hours = sorted(chosen_hours.items(), key=lambda x: x[1], reverse=True)[:3]
            top_days = sorted(chosen_days.items(), key=lambda x: x[1], reverse=True)[:3]

            prefs["analyzed"] = {
                "top_hours": [{"hour": h, "count": c} for h, c in top_hours],
                "top_days": [{"day": d, "count": c} for d, c in top_days],
                "total_events_created": len(self.history["created_events"]),
                "total_suggestions_accepted": len(self.history["choices"]),
                "rejection_rate": len(self.history["rejections"]) / max(1, (len(self.history["choices"]) + len(self.history["rejections"])))
            }

        return prefs

    def adjust_preferences_based_on_feedback(self, feedback: str):
        """
        根据用户反馈微调偏好

        例如:
        - "总是太早了" → 推后工作开始时间
        - "不喜欢午休时间" → 调整午休避开
        """
        feedback_lower = feedback.lower()

        if "早" in feedback or "morning" in feedback_lower:
            # 用户倾向晚些开始
            current_start = self.scheduler.preferences["working_hours"]["start"]
            hour, minute = map(int, current_start.split(":"))
            new_hour = min(10, hour + 1)
            self.scheduler.preferences["working_hours"]["start"] = f"{new_hour:02d}:00"
            self.scheduler._save_preferences()

        if "午休" in feedback or "lunch" in feedback_lower:
            # 调整午休时间或避开
            self.scheduler.preferences["avoid_lunch_break"] = not self.scheduler.preferences.get("avoid_lunch_break", True)
            self.scheduler._save_preferences()

    def export_learning_report(self) -> str:
        """导出学习报告"""
        summary = self.get_preference_summary()
        report_lines = [
            "=== Outlook Calendar 偏好学习报告 ===",
            f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            "【工作时间】",
            f"  {summary['working_hours']['start']} - {summary['working_hours']['end']}",
            "",
            f"【缓冲时间】{summary['buffer_minutes']} 分钟",
            "",
            "【偏好分析】"
        ]

        if "analyzed" in summary:
            ana = summary["analyzed"]
            report_lines.extend([
                f"  总创建事件数: {ana['total_events_created']}",
                f"  推荐接受率: {(1 - ana['rejection_rate']) * 100:.1f}%",
                "",
                "  最常选择的时间段:",
                *[f"    {h['hour']}: {h['count']} 次" for h in ana["top_hours"]],
                "",
                "  最常选择的工作日:",
                *[f"    {d['day']}: {d['count']} 次" for d in ana["top_days"]]
            ])

        return "\n".join(report_lines)


def get_learner() -> PreferenceLearner:
    """获取偏好学习器实例"""
    return PreferenceLearner()
