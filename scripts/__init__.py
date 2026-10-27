"""
Calendar Scheduler - 核心模块

自动加载环境变量配置，确保所有子模块都能访问 Azure 认证信息。
"""

import os
from pathlib import Path

# 子模块之间的跨模块导入采用「相对导入优先 + 裸导入回退」：
# 包导入（from scripts.xxx）走相对导入，脚本运行（python run_calendar.py）走裸导入，
# 因此这里不需要修改 sys.path。

# 自动加载 .env 文件（无论从哪个入口导入）
_env_loaded = False

def _load_env_once():
    """确保环境变量只加载一次"""
    global _env_loaded
    if _env_loaded:
        return

    # 查找项目根目录的 .env 文件
    # 当前文件路径: scripts/__init__.py
    # 项目根目录: scripts 的父目录
    env_path = Path(__file__).parent.parent / ".env"

    if env_path.exists():
        try:
            from dotenv import load_dotenv
            load_dotenv(env_path)
            _env_loaded = True
        except ImportError:
            # python-dotenv 未安装时静默跳过
            pass

# 导入时立即执行
_load_env_once()
