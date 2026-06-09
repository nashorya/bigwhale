"""
core/user_db.py 验证脚本
"""
import os
import sys
import asyncio

os.environ["SHORE_USER_SALT"] = "test_salt_32chars_for_verify!!"
os.environ["DB_PATH"] = ":memory:"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "shore"))

from core.user_db import get_db_conn, UserDB


async def test():
    async with get_db_conn() as conn:
        # 初始化表结构
        init_sql = os.path.join(os.path.dirname(__file__), "..", "init.sql")
        with open(init_sql, encoding="utf-8") as f:
            await conn.executescript(f.read())

        uid = "test_user_hash_001"

        # 插入测试用户
        await conn.execute(
            "INSERT INTO users (user_id, invite_code) VALUES (?, ?)",
            (uid, "ABC123"),
        )
        await conn.commit()

        db = UserDB(uid, conn)

        # 1. get_active_persona（无记录 -> 默认）
        persona = await db.get_active_persona()
        assert persona == "kitty", f"默认角色应为 kitty，实际: {persona}"
        print(f"  get_active_persona (默认) OK: {persona}")

        # 插入 persona_config
        await conn.execute(
            "INSERT INTO persona_config (user_id, active_persona) VALUES (?, ?)",
            (uid, "makoto"),
        )
        await conn.commit()
        persona = await db.get_active_persona()
        assert persona == "makoto", f"应为 makoto，实际: {persona}"
        print(f"  get_active_persona (自定义) OK: {persona}")

        # 2. 知识点
        await conn.execute(
            "INSERT INTO subjects (id, user_id, name, category) VALUES (?, ?, ?, ?)",
            (1, uid, "数学", "公共课"),
        )
        await conn.execute(
            "INSERT INTO knowledge_points (id, user_id, subject_id, topic_name, mastery_level) VALUES (?, ?, ?, ?, ?)",
            (1, uid, 1, "线性代数", 2),
        )
        await conn.execute(
            "INSERT INTO knowledge_points (id, user_id, subject_id, topic_name, mastery_level) VALUES (?, ?, ?, ?, ?)",
            (2, uid, 1, "概率论", 1),
        )
        await conn.commit()

        kps = await db.get_knowledge_points(1)
        assert len(kps) == 2, f"应有 2 个知识点，实际: {len(kps)}"
        print(f"  get_knowledge_points OK: 共 {len(kps)} 个")

        # 3. update_mastery
        await db.update_mastery(1, 4)
        kps = await db.get_knowledge_points(1)
        kp1 = [k for k in kps if k["id"] == 1][0]
        assert kp1["mastery_level"] == 4, f"掌握度应为 4，实际: {kp1['mastery_level']}"
        assert kp1["last_review_at"] is not None, "last_review_at 应被更新"
        print(f"  update_mastery OK: level={kp1['mastery_level']}")

        # 4. 积分
        await conn.execute(
            "INSERT INTO points_account (user_id, balance, total_earned, total_spent) VALUES (?, ?, ?, ?)",
            (uid, 200, 200, 0),
        )
        await conn.commit()

        balance = await db.get_points_balance()
        assert balance == 200, f"应为 200，实际: {balance}"
        print(f"  get_points_balance OK: {balance}")

        ok = await db.deduct_points(50, "plan_generation")
        assert ok is True
        balance = await db.get_points_balance()
        assert balance == 150, f"扣除后应为 150，实际: {balance}"
        print(f"  deduct_points OK: 扣 50，余额 {balance}")

        fail = await db.deduct_points(999, "test_fail")
        assert fail is False, "余额不足应返回 False"
        balance_after_fail = await db.get_points_balance()
        assert balance_after_fail == 150, "余额不足时不应扣除"
        print("  deduct_points (余额不足) OK: 正确拒绝")

        # 验证积分流水
        cursor = await conn.execute(
            "SELECT * FROM points_ledger WHERE user_id = ?", (uid,)
        )
        ledger = await cursor.fetchall()
        assert len(ledger) == 1, f"应有 1 条流水，实际: {len(ledger)}"
        print(f"  积分流水 OK: 共 {len(ledger)} 条")

        # 5. 日程
        await conn.execute(
            "INSERT INTO user_schedule (user_id, day_of_week, time_slot, subject_id) VALUES (?, ?, ?, ?)",
            (uid, 1, "08:00-09:30", 1),
        )
        await conn.commit()
        schedule = await db.get_user_schedule()
        assert len(schedule) == 1
        print(f"  get_user_schedule OK: 共 {len(schedule)} 条")

        # 6. 考试日期
        exam = await db.get_exam_date()
        assert exam is None, "无记录应返回 None"
        await conn.execute(
            "INSERT INTO exam_config (user_id, exam_date) VALUES (?, ?)",
            (uid, "2026-12-20"),
        )
        await conn.commit()
        exam = await db.get_exam_date()
        assert exam == "2026-12-20", f"应为 2026-12-20，实际: {exam}"
        print(f"  get_exam_date OK: {exam}")

        # 7. daily_plan
        await conn.execute(
            "INSERT INTO daily_plan (user_id, plan_date, kp_id, priority_score, estimated_minutes) VALUES (?, ?, ?, ?, ?)",
            (uid, "2026-03-15", 1, 0.85, 30),
        )
        await conn.commit()
        plan = await db.get_daily_plan("2026-03-15")
        assert len(plan) == 1
        print(f"  get_daily_plan OK: 共 {len(plan)} 条")

        # 8. 用户隔离
        other_uid = "other_user_hash_002"
        await conn.execute(
            "INSERT INTO users (user_id, invite_code) VALUES (?, ?)",
            (other_uid, "XYZ789"),
        )
        await conn.execute(
            "INSERT INTO knowledge_points (user_id, subject_id, topic_name, mastery_level) VALUES (?, ?, ?, ?)",
            (other_uid, 1, "他人的知识点", 3),
        )
        await conn.commit()
        my_kps = await db.get_knowledge_points(1)
        assert all(
            k["user_id"] == uid for k in my_kps
        ), "不应读到其他用户的数据"
        print(f"  用户隔离 OK: 只读到自己的 {len(my_kps)} 条")

    print()
    print("✅ user_db.py 所有测试通过！")


if __name__ == "__main__":
    asyncio.run(test())
