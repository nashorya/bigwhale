"""
UserDB 封装类 — 所有数据库操作的唯一入口。
core/ 层禁止直接 import nonebot 模块。

使用 aiosqlite 异步驱动，所有方法均为参数化查询，
实例化时绑定 user_id，自动携带 WHERE user_id = ?，
从根源防止跨用户数据访问。
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator

import aiosqlite


# ──────────────────────────────────────────────
# 数据库连接管理
# ──────────────────────────────────────────────


@asynccontextmanager
async def get_db_conn() -> AsyncGenerator[aiosqlite.Connection, None]:
    """
    异步上下文管理器，获取数据库连接。
    从环境变量 DB_PATH 读取数据库路径，默认 data/kaoyan.db。
    使用 WAL 模式，开启外键约束，返回 Row 工厂以支持按列名访问。

    用法：
        async with get_db_conn() as conn:
            db = UserDB(user_id, conn)
            persona = await db.get_active_persona()
    """
    db_path = os.environ.get("DB_PATH", "data/kaoyan.db")
    conn = await aiosqlite.connect(db_path)
    # 启用 WAL 模式和外键约束
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    # 使用 Row 工厂，支持按列名访问（row["column_name"]）
    conn.row_factory = aiosqlite.Row
    try:
        yield conn
    finally:
        await conn.close()


# ──────────────────────────────────────────────
# UserDB 封装类
# ──────────────────────────────────────────────


class UserDB:
    """
    绑定 user_id 的数据库操作类。
    实例化后，所有查询自动携带 WHERE user_id = ?，
    上层代码无需手动传入 user_id，从根源防止串用户。

    所有方法均使用参数化查询，禁止字符串拼接 SQL。
    """

    def __init__(self, user_id: str, conn: aiosqlite.Connection) -> None:
        self._uid = user_id
        self._conn = conn

    @staticmethod
    async def _table_columns(conn: aiosqlite.Connection, table_name: str) -> set[str]:
        cursor = await conn.execute(f"PRAGMA table_info({table_name})")
        return {row[1] for row in await cursor.fetchall()}

    @classmethod
    async def initialize_database(cls) -> None:
        """按 init.sql 初始化数据库，并补齐旧版本缺失的列。"""
        db_path = os.environ.get("DB_PATH", "data/kaoyan.db")
        if db_path != ":memory:":
            Path(db_path).expanduser().resolve().parent.mkdir(
                parents=True, exist_ok=True
            )

        schema_path = Path(__file__).resolve().parents[3] / "init.sql"
        schema_sql = schema_path.read_text(encoding="utf-8")
        conn = await aiosqlite.connect(db_path)
        try:
            await conn.execute("PRAGMA journal_mode=WAL")
            await conn.execute("PRAGMA foreign_keys=ON")

            word_columns = await cls._table_columns(conn, "word_bank")
            if word_columns:
                if "rank_order" not in word_columns:
                    await conn.execute(
                        "ALTER TABLE word_bank ADD COLUMN rank_order INTEGER DEFAULT 0"
                    )
                if "category" not in word_columns:
                    await conn.execute(
                        "ALTER TABLE word_bank ADD COLUMN category TEXT DEFAULT 'core'"
                    )

            await conn.executescript(schema_sql)

            migrations = {
                "users": {
                    "is_banned": "BOOLEAN NOT NULL DEFAULT 0",
                    "ban_reason": "TEXT",
                    "banned_at": "DATETIME",
                },
                "weekly_plan": {
                    "scheduled_time": "TEXT",
                    "reminder_sent": "INTEGER NOT NULL DEFAULT 0",
                },
                "word_bank": {
                    "phase": "TEXT NOT NULL DEFAULT 'base'",
                    "rank_order": "INTEGER DEFAULT 0",
                    "category": "TEXT DEFAULT 'core'",
                },
                "user_word_status": {
                    "last_pushed_at": "DATETIME",
                    "total_seen": "INTEGER NOT NULL DEFAULT 0",
                    "total_correct": "INTEGER NOT NULL DEFAULT 0",
                    "last_seen_at": "TEXT",
                    "created_at": "TEXT",
                },
            }
            for table_name, required_columns in migrations.items():
                existing = await cls._table_columns(conn, table_name)
                for column_name, column_type in required_columns.items():
                    if column_name not in existing:
                        await conn.execute(
                            f"ALTER TABLE {table_name} "
                            f"ADD COLUMN {column_name} {column_type}"
                        )

            await conn.commit()
        finally:
            await conn.close()

    async def commit(self) -> None:
        """提交当前事务。供外部批量操作后统一提交使用。"""
        await self._conn.commit()

    # ── 人物卡相关 ──────────────────────────────

    async def get_active_persona(self) -> str:
        """
        获取用户当前激活的角色名。
        如果用户尚未配置，返回默认角色 'kitty'。
        对应表：persona_config
        """
        cursor = await self._conn.execute(
            "SELECT active_persona FROM persona_config WHERE user_id = ?",
            (self._uid,),
        )
        row = await cursor.fetchone()
        return row["active_persona"] if row else "kitty"

    # ── 知识点相关 ──────────────────────────────

    async def get_knowledge_points(self, subject_id: int) -> list[dict]:
        """
        获取指定科目下的所有知识点。
        对应表：knowledge_points
        返回字典列表，每个字典包含知识点的全部字段。
        """
        cursor = await self._conn.execute(
            "SELECT * FROM knowledge_points WHERE user_id = ? AND subject_id = ?",
            (self._uid, subject_id),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def update_mastery(self, kp_id: int, new_level: int) -> None:
        """
        更新指定知识点的掌握程度。
        同时更新 last_review_at 为当前时间。
        对应表：knowledge_points
        参数：
            kp_id: 知识点 ID
            new_level: 新的掌握等级（1-5）
        """
        await self._conn.execute(
            """UPDATE knowledge_points
               SET mastery_level = ?, last_review_at = ?
               WHERE id = ? AND user_id = ?""",
            (new_level, datetime.now().isoformat(), kp_id, self._uid),
        )
        await self._conn.commit()

    # ── 积分相关 ────────────────────────────────

    async def get_points_balance(self) -> int:
        """
        获取用户当前积分余额。
        如果用户无积分账户记录，返回 0。
        对应表：points_account
        """
        cursor = await self._conn.execute(
            "SELECT balance FROM points_account WHERE user_id = ?",
            (self._uid,),
        )
        row = await cursor.fetchone()
        return row["balance"] if row else 0

    async def deduct_points(
        self, amount: int, reason: str, ref_id: str | None = None
    ) -> bool:
        """
        扣除积分。余额不足时不扣除，返回 False。
        成功扣除后同时写入积分流水（points_ledger）。
        对应表：points_account, points_ledger

        参数：
            amount: 扣除数量（正整数）
            reason: 扣费原因（枚举值，如 'daily_subscription'）
            ref_id: 关联业务 ID（可选）
        返回：
            True 扣除成功，False 余额不足
        """
        # 先查询当前余额
        balance = await self.get_points_balance()
        if balance < amount:
            return False

        new_balance = balance - amount

        # 更新余额和累计消费
        await self._conn.execute(
            """UPDATE points_account
               SET balance = ?, total_spent = total_spent + ?
               WHERE user_id = ?""",
            (new_balance, amount, self._uid),
        )

        # 写入积分流水
        await self._conn.execute(
            """INSERT INTO points_ledger
               (user_id, delta, balance_after, reason, ref_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                self._uid,
                -amount,
                new_balance,
                reason,
                ref_id,
                datetime.now().isoformat(),
            ),
        )

        await self._conn.commit()
        return True

    # ── 日程相关 ────────────────────────────────

    async def get_daily_plan(self, date: str) -> list[dict]:
        """
        获取指定日期的每日学习计划。
        对应表：daily_plan

        参数：
            date: 日期字符串，格式 'YYYY-MM-DD'
        返回：
            计划列表，按优先级降序排列
        """
        cursor = await self._conn.execute(
            """SELECT * FROM daily_plan
               WHERE user_id = ? AND plan_date = ?
               ORDER BY priority_score DESC""",
            (self._uid, date),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_user_schedule(self) -> list[dict]:
        """
        获取用户的课程表/日程安排。
        对应表：user_schedule
        返回按星期和时间段排序的日程列表。
        """
        cursor = await self._conn.execute(
            """SELECT * FROM user_schedule
               WHERE user_id = ?
               ORDER BY day_of_week, time_slot""",
            (self._uid,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ── 考试配置 ────────────────────────────────

    async def get_exam_date(self) -> str | None:
        """
        获取用户设置的考试日期。
        如果未设置，返回 None。
        对应表：exam_config
        """
        cursor = await self._conn.execute(
            "SELECT exam_date FROM exam_config WHERE user_id = ?",
            (self._uid,),
        )
        row = await cursor.fetchone()
        return row["exam_date"] if row else None

    # ── 周计划（AI 驱动） ──────────────────────

    async def save_weekly_plan(self, week_start: str, plan_items: list[dict]) -> None:
        """
        保存 LLM 生成的周计划。
        先清除同一 week_start 的旧计划，再批量写入。

        plan_items 每项字段：
            plan_date, day_index, subject_id(可None), kp_id(可None),
            topic_name, subject_name, order_in_day,
            estimated_minutes, notes(可None)
        """
        # 清除旧计划
        await self._conn.execute(
            "DELETE FROM weekly_plan WHERE user_id = ? AND week_start = ?",
            (self._uid, week_start),
        )
        # 批量插入
        for item in plan_items:
            await self._conn.execute(
                """INSERT INTO weekly_plan
                   (user_id, week_start, plan_date, day_index,
                    subject_id, kp_id, topic_name, subject_name,
                    order_in_day, estimated_minutes, scheduled_time, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    self._uid,
                    week_start,
                    item["plan_date"],
                    item["day_index"],
                    item.get("subject_id"),
                    item.get("kp_id"),
                    item["topic_name"],
                    item["subject_name"],
                    item.get("order_in_day", 0),
                    item.get("estimated_minutes", 60),
                    item.get("scheduled_time", ""),
                    item.get("notes"),
                ),
            )
        await self._conn.commit()

    async def get_weekly_plan(self, week_start: str) -> list[dict]:
        """
        获取指定轮次的周计划（按日期和排列顺序排序）。
        """
        cursor = await self._conn.execute(
            """SELECT * FROM weekly_plan
               WHERE user_id = ? AND week_start = ?
               ORDER BY day_index, order_in_day""",
            (self._uid, week_start),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_today_from_weekly_plan(self, date_str: str) -> list[dict]:
        """
        获取某日来自周计划的条目（优先于遗忘曲线）。
        """
        cursor = await self._conn.execute(
            """SELECT * FROM weekly_plan
               WHERE user_id = ? AND plan_date = ?
               ORDER BY order_in_day""",
            (self._uid, date_str),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_weekly_plan_done(self, plan_id: int) -> None:
        """标记某条周计划为完成。"""
        from datetime import datetime

        await self._conn.execute(
            """UPDATE weekly_plan
               SET status = 'done', completed_at = ?
               WHERE id = ? AND user_id = ?""",
            (datetime.now().isoformat(), plan_id, self._uid),
        )
        await self._conn.commit()

    async def get_latest_week_start(self) -> str | None:
        """获取用户最新的周计划起始日期。"""
        cursor = await self._conn.execute(
            """SELECT week_start FROM weekly_plan
               WHERE user_id = ?
               ORDER BY week_start DESC LIMIT 1""",
            (self._uid,),
        )
        row = await cursor.fetchone()
        return row["week_start"] if row else None

    async def get_pending_reminders(
        self, plan_date: str, scheduled_time: str
    ) -> list[dict]:
        """
        获取今日某时段待提醒的周计划条目。
        只返回 status='pending' 且 reminder_sent=0 的条目。
        """
        cursor = await self._conn.execute(
            """SELECT * FROM weekly_plan
               WHERE user_id = ? AND plan_date = ?
                 AND scheduled_time = ?
                 AND status = 'pending'
                 AND reminder_sent = 0
               ORDER BY order_in_day""",
            (self._uid, plan_date, scheduled_time),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def mark_reminder_sent(self, plan_id: int) -> None:
        """标记某条周计划已发送提醒。"""
        await self._conn.execute(
            "UPDATE weekly_plan SET reminder_sent = 1 WHERE id = ? AND user_id = ?",
            (plan_id, self._uid),
        )
        await self._conn.commit()

    # ── Scheduler 专用方法 ─────────────────────

    async def get_active_subjects(self) -> list[dict]:
        """
        获取用户所有激活状态的科目。
        对应表：subjects + subject_status
        只返回 status='active' 的科目。
        """
        cursor = await self._conn.execute(
            """SELECT s.* FROM subjects s
               JOIN subject_status ss ON s.id = ss.subject_id AND ss.user_id = s.user_id
               WHERE s.user_id = ? AND ss.status = 'active'""",
            (self._uid,),
        )
        rows = await cursor.fetchall()
        if rows:
            return [dict(row) for row in rows]

        # 回退：如果 subject_status 为空（旧数据），直接查 subjects
        cursor = await self._conn.execute(
            "SELECT * FROM subjects WHERE user_id = ?",
            (self._uid,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_all_knowledge_points(self) -> list[dict]:
        """
        获取用户所有科目的知识点（仅限 active 科目）。
        对应表：knowledge_points
        """
        subjects = await self.get_active_subjects()
        if not subjects:
            return []

        all_kps = []
        for subj in subjects:
            kps = await self.get_knowledge_points(subj["id"])
            for kp in kps:
                kp["subject_name"] = subj["name"]
            all_kps.extend(kps)
        return all_kps

    async def get_checkin_streak(self) -> dict:
        """
        获取用户打卡连击数据。
        对应表：checkin_streak
        """
        cursor = await self._conn.execute(
            "SELECT * FROM checkin_streak WHERE user_id = ?",
            (self._uid,),
        )
        row = await cursor.fetchone()
        if row:
            return dict(row)
        return {
            "current_streak": 0,
            "longest_streak": 0,
            "last_complete_date": None,
            "total_checkins": 0,
        }

    async def clear_daily_plan(self, date: str) -> None:
        """
        清除指定日期的每日计划（重新生成前调用）。
        对应表：daily_plan
        """
        await self._conn.execute(
            "DELETE FROM daily_plan WHERE user_id = ? AND plan_date = ?",
            (self._uid, date),
        )
        await self._conn.commit()

    async def insert_daily_plan(
        self,
        date: str,
        kp_id: int,
        priority_score: float,
        estimated_minutes: int,
    ) -> None:
        """
        插入一条每日计划记录。
        对应表：daily_plan
        """
        await self._conn.execute(
            """INSERT INTO daily_plan
               (user_id, plan_date, kp_id, priority_score, estimated_minutes)
               VALUES (?, ?, ?, ?, ?)""",
            (self._uid, date, kp_id, priority_score, estimated_minutes),
        )


async def get_all_user_ids(conn: "aiosqlite.Connection") -> list[str]:
    """
    获取所有已注册用户的 user_id 列表。
    此方法不绑定特定用户，直接使用连接。
    对应表：users
    """
    cursor = await conn.execute("SELECT user_id FROM users")
    rows = await cursor.fetchall()
    return [row["user_id"] for row in rows]
