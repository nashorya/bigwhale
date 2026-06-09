"""
SessionManager + UserSession — 用户会话管理。
core/ 层禁止直接 import nonebot 模块。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass
class UserSession:
    """
    单个用户的内存会话状态。
    仅存在于内存中，用于保存当前会话的轻量级状态。
    持久化字段（如 active_persona）由 UserDB 负责读写数据库。
    """

    user_id: str                                    # 哈希后的用户标识
    active_persona: str = "kitty"                  # 当前激活角色
    companion_mode: bool = False                    # 是否处于情绪陪伴模式
    immersive_mode: bool = False                    # 是否处于沉浸学习模式（#开始学习）
    immersive_subject: str | None = None            # 沉浸模式当前科目
    last_word_push: datetime | None = None          # 上次推词时间（用于 8 分钟间隔检查）
    emotion_cooldown_until: datetime | None = None  # 情绪熔断冷却到期时间
    last_active: datetime = field(default_factory=datetime.now)  # 最后活跃时间


class SessionManager:
    """
    用户会话管理器，管理所有活跃会话。
    按 user_id 索引，不同用户的 Session 完全隔离。
    超过 idle_minutes 无活动的 Session 自动从内存卸载，
    下次消息到来时从数据库重新加载。
    """

    # 类级别共享的会话字典
    _sessions: dict[str, UserSession] = {}

    # 默认空闲超时时间：120 分钟
    DEFAULT_IDLE_MINUTES: int = 120

    @classmethod
    async def get(cls, user_id: str, *, load_persona_fn=None) -> UserSession:
        """
        获取指定用户的 Session。
        如果内存中不存在，则创建新 Session；如果提供了 load_persona_fn，
        则通过该回调从数据库加载持久化状态（如 active_persona）。

        参数:
            user_id: 哈希后的用户标识
            load_persona_fn: 可选的异步回调函数，签名 async (user_id) -> str，
                             用于从数据库加载用户当前激活的角色名。
                             由 handler 层注入，避免 core/ 直接依赖 UserDB。
        """
        if user_id not in cls._sessions:
            persona = "kitty"
            if load_persona_fn is not None:
                persona = await load_persona_fn(user_id)
            cls._sessions[user_id] = UserSession(
                user_id=user_id,
                active_persona=persona,
            )

        session = cls._sessions[user_id]
        session.last_active = datetime.now()
        return session

    @classmethod
    async def evict_idle(cls, idle_minutes: int | None = None) -> list[str]:
        """
        清理超过指定空闲时间的 Session，释放内存。
        应由定时任务（APScheduler）定期调用。

        参数:
            idle_minutes: 空闲超时分钟数，默认 120 分钟

        返回:
            被卸载的 user_id 列表（可用于日志记录）
        """
        if idle_minutes is None:
            idle_minutes = cls.DEFAULT_IDLE_MINUTES

        cutoff = datetime.now() - timedelta(minutes=idle_minutes)
        to_remove = [
            uid for uid, s in cls._sessions.items()
            if s.last_active < cutoff
        ]
        for uid in to_remove:
            del cls._sessions[uid]

        return to_remove

    @classmethod
    def remove(cls, user_id: str) -> None:
        """手动移除指定用户的 Session（如用户主动退出时使用）。"""
        cls._sessions.pop(user_id, None)

    @classmethod
    def active_count(cls) -> int:
        """返回当前活跃 Session 数量。"""
        return len(cls._sessions)

    @classmethod
    def clear_all(cls) -> None:
        """清空所有 Session（仅用于测试）。"""
        cls._sessions.clear()
