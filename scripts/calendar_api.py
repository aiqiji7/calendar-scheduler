#!/usr/bin/env python3
"""
日历后端兼容层

保留原有 get_calendar_client 入口，实际后端由 backend.get_backend 决定。
"""

try:  # 作为包导入（from scripts.xxx import ...）
    from .backend import get_backend
except ImportError:  # 作为脚本直接运行（python run_calendar.py ...）
    from backend import get_backend


def get_calendar_client():
    """获取日历后端实例（默认 Graph，可通过 CALENDAR_BACKEND 切换）"""
    return get_backend()
