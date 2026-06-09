"""
上岸 NoneBot2 插件入口。
自动注册所有 handler 子模块。

NoneBot2 会自动扫描并加载此目录下的 handler 模块。
需要在 __init__.py 中显式导入 handler，确保它们的事件处理器被注册。
"""

# 导入 handler 子模块，触发 NoneBot2 事件处理器注册
from .handlers import system as _system_handler  # noqa: F401
from .handlers import admin as _admin_handler  # noqa: F401
from .handlers import checkin as _checkin_handler  # noqa: F401
from .handlers import schedule as _schedule_handler  # noqa: F401
from .handlers import persona as _persona_handler  # noqa: F401
from .handlers import points as _points_handler  # noqa: F401
from .handlers import words as _words_handler  # noqa: F401
from .handlers import emotion as _emotion_handler  # noqa: F401
from .handlers import school as _school_handler  # noqa: F401
from .handlers import help as _help_handler  # noqa: F401
from .handlers import weekly_plan as _weekly_plan_handler  # noqa: F401
from .handlers import study_plan as _study_plan_handler  # noqa: F401
from .handlers import word_push as _word_push_handler  # noqa: F401
