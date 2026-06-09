"""
core/scheduler.py 验证脚本
"""
import os
import sys
import asyncio
from datetime import date, datetime, timedelta

os.environ["SHORE_USER_SALT"] = "test_salt_32chars_for_verify!!"
os.environ["DB_PATH"] = ":memory:"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "shore"))

from core.scheduler import (
    calc_priority,
    get_study_phase,
    calc_next_review_date,
    Scheduler,
)
from core.user_db import get_db_conn, UserDB


async def test():
    # ─── 1. 纯函数测试 ───
    print("=== 纯函数测试 ===")

    # calc_priority 基本逻辑
    kp_basic = {
        "importance": 3,
        "mastery_level": 1,
        "last_review_at": None,
        "next_review_at": None,
    }
    p = calc_priority(kp_basic, days_left=100)
    # base = 3 * (6-1) = 15, forget = min(2.0, 1 + 30/14) ≈ 3.14 -> 2.0
    # overdue = 1.0, sprint = 1.0
    assert p == 15 * 2.0 * 1.0 * 1.0, f"基本优先级计算错误: {p}"
    print(f"  calc_priority (基本) OK: {p}")

    # 冲刺系数
    p_sprint = calc_priority(kp_basic, days_left=20)
    # sprint_factor = 1.3 (days_left<=30 且 importance=3)
    assert p_sprint == 15 * 2.0 * 1.0 * 1.3, f"冲刺系数错误: {p_sprint}"
    print(f"  calc_priority (冲刺) OK: {p_sprint}")

    # 已复习过的知识点
    yesterday = (datetime.now() - timedelta(days=1)).isoformat()
    kp_reviewed = {
        "importance": 2,
        "mastery_level": 3,
        "last_review_at": yesterday,
        "next_review_at": None,
    }
    p_rev = calc_priority(kp_reviewed, days_left=100)
    # base = 2*(6-3)=6, forget = min(2.0, 1+1/14) ≈ 1.07
    base_expected = 6
    forget_expected = min(2.0, 1 + 1 / 14)
    assert abs(p_rev - base_expected * forget_expected) < 0.01
    print(f"  calc_priority (已复习) OK: {p_rev:.2f}")

    # 逾期知识点
    past_date = (datetime.now() - timedelta(days=5)).isoformat()
    kp_overdue = {
        "importance": 2,
        "mastery_level": 2,
        "last_review_at": (datetime.now() - timedelta(days=10)).isoformat(),
        "next_review_at": past_date,
    }
    p_od = calc_priority(kp_overdue, days_left=100)
    # overdue_factor = 1.5
    assert p_od > calc_priority({**kp_overdue, "next_review_at": None}, days_left=100)
    print(f"  calc_priority (逾期) OK: {p_od:.2f}")

    # get_study_phase
    assert get_study_phase(200) == "foundation"
    assert get_study_phase(100) == "intensify"
    assert get_study_phase(50) == "sprint"
    assert get_study_phase(10) == "sprint_final"
    print("  get_study_phase OK")

    # calc_next_review_date
    nrd = calc_next_review_date(1)
    expected = (date.today() + timedelta(days=1)).isoformat()
    assert nrd == expected, f"next_review 1: {nrd} != {expected}"
    nrd5 = calc_next_review_date(5)
    expected5 = (date.today() + timedelta(days=30)).isoformat()
    assert nrd5 == expected5
    print("  calc_next_review_date OK")

    # ─── 2. 集成测试（数据库） ───
    print()
    print("=== 集成测试 ===")

    async with get_db_conn() as conn:
        init_sql = os.path.join(os.path.dirname(__file__), "..", "init.sql")
        with open(init_sql, encoding="utf-8") as f:
            await conn.executescript(f.read())

        uid = "test_scheduler_user"

        # 创建用户
        await conn.execute(
            "INSERT INTO users (user_id, invite_code) VALUES (?, ?)",
            (uid, "SCH001"),
        )
        # 设置考试日期（100天后）
        exam = (date.today() + timedelta(days=100)).isoformat()
        await conn.execute(
            "INSERT INTO exam_config (user_id, exam_date) VALUES (?, ?)",
            (uid, exam),
        )
        # 创建科目
        await conn.execute(
            "INSERT INTO subjects (id, user_id, name, category) VALUES (?, ?, ?, ?)",
            (1, uid, "数学一", "公共课"),
        )
        await conn.execute(
            "INSERT INTO subjects (id, user_id, name, category) VALUES (?, ?, ?, ?)",
            (2, uid, "408", "专业课"),
        )
        # 创建知识点
        kps_data = [
            (1, uid, 1, "极限", 1, 3),
            (2, uid, 1, "导数", 2, 2),
            (3, uid, 1, "积分", 3, 1),
            (4, uid, 2, "B树", 1, 3),
            (5, uid, 2, "进程调度", 2, 3),
            (6, uid, 2, "TCP握手", 3, 2),
            (7, uid, 2, "页面置换", 4, 2),
        ]
        for kp_id, u, sid, name, mastery, imp in kps_data:
            await conn.execute(
                "INSERT INTO knowledge_points (id, user_id, subject_id, topic_name, mastery_level, importance) VALUES (?,?,?,?,?,?)",
                (kp_id, u, sid, name, mastery, imp),
            )
        await conn.commit()

        db = UserDB(uid, conn)

        # generate_daily_plan
        plan = await Scheduler.generate_daily_plan(db, daily_capacity=120)
        assert len(plan) > 0, f"计划应非空，实际: {len(plan)}"
        print(f"  generate_daily_plan OK: 生成 {len(plan)} 条计划")
        for item in plan:
            print(f"    - {item['topic_name']} (优先级 {item['priority_score']}, 预估 {item['estimated_minutes']}min)")

        # 验证写入数据库
        saved_plan = await db.get_daily_plan(date.today().isoformat())
        assert len(saved_plan) == len(plan), f"数据库中应有 {len(plan)} 条，实际: {len(saved_plan)}"
        print(f"  数据库写入 OK: {len(saved_plan)} 条")

        # generate_morning_content
        morning = await Scheduler.generate_morning_content(db)
        assert "days_left" in morning
        assert "subject_summary" in morning
        assert "suggestion" in morning
        assert morning["days_left"] == 100
        print(f"  generate_morning_content OK: days_left={morning['days_left']}, phase={morning['phase']}")
        print(f"    摘要: {morning['subject_summary']}")
        print(f"    建议: {morning['suggestion']}")

        # generate_evening_content
        evening = await Scheduler.generate_evening_content(db)
        assert "rate" in evening
        assert "streak" in evening
        assert "top3" in evening
        print(f"  generate_evening_content OK: rate={evening['rate']}%, streak={evening['streak']}")
        print(f"    Top3: {evening['top3']}")

    print()
    print("✅ scheduler.py 所有测试通过！")


if __name__ == "__main__":
    asyncio.run(test())
