"""
core/emotion_detector.py 验证脚本
"""
import os
import sys
import asyncio
import json

os.environ["SHORE_USER_SALT"] = "test_salt_32chars_for_verify!!"
os.environ["DB_PATH"] = ":memory:"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "shore"))

from core.emotion_detector import (
    detect,
    detect_detailed,
    check_system_anomaly,
    start_session,
    end_session,
    is_in_companion_mode,
    EMOTION_KEYWORDS,
)
from core.user_db import get_db_conn, UserDB


def test_detect():
    """纯函数测试：关键词检测"""
    print("=== detect 测试 ===")

    # 不触发：普通消息
    ok, cat = detect("今天学了数据结构")
    assert not ok, f"普通消息不应触发: {ok}"
    print("  普通消息 OK: 不触发")

    # 不触发：单个弱信号词
    ok, cat = detect("好烦")
    assert not ok, f"单个弱信号不应触发: {ok}"
    print("  单个弱信号 OK: 不触发")

    # 触发：2 个弱信号词
    ok, cat = detect("好烦，好累")
    assert ok, "2个弱信号应触发"
    assert cat is not None
    print(f"  2个弱信号 OK: 触发，类别={cat}")

    # 触发：1 个强信号词
    ok, cat = detect("不想学了")
    assert ok, "强信号词'不想学了'应触发"
    print(f"  强信号 '不想学了' OK: 触发，类别={cat}")

    ok, cat = detect("想聊聊")
    assert ok, "强信号词'想聊聊'应触发"
    print(f"  强信号 '想聊聊' OK: 触发，类别={cat}")

    ok, cat = detect("放弃吧")
    assert ok, "强信号词'放弃'应触发"
    print(f"  强信号 '放弃' OK: 触发，类别={cat}")

    # 不触发：空消息
    ok, cat = detect("")
    assert not ok
    ok, cat = detect("   ")
    assert not ok
    print("  空消息 OK: 不触发")

    # 触发：混合类别
    ok, cat = detect("压力好大，好累")
    assert ok
    print(f"  混合信号 OK: 触发，类别={cat}")

    # detect_detailed 测试
    detail = detect_detailed("坚持不住了，好累")
    assert detail["triggered"]
    assert "坚持不住了" in detail["matched_words"]
    assert detail["has_strong_signal"]
    print(f"  detect_detailed OK: {detail}")

    print()


async def test_session():
    """集成测试：会话管理"""
    print("=== 会话管理测试 ===")

    async with get_db_conn() as conn:
        init_sql = os.path.join(os.path.dirname(__file__), "..", "init.sql")
        with open(init_sql, encoding="utf-8") as f:
            await conn.executescript(f.read())

        uid = "test_emotion_user"

        # 创建用户
        await conn.execute(
            "INSERT INTO users (user_id, invite_code) VALUES (?, ?)",
            (uid, "EMO001"),
        )
        # 创建 persona_config
        await conn.execute(
            "INSERT INTO persona_config (user_id) VALUES (?)",
            (uid,),
        )
        await conn.commit()

        db = UserDB(uid, conn)

        # 初始状态：非陪伴模式
        assert not await is_in_companion_mode(db)
        print("  初始状态 OK: companion_mode = False")

        # 启动陪伴会话
        await start_session(
            uid, "user_confide", db,
            trigger_detail="用户说好烦好累",
            mood_signal=["好烦", "好累"],
        )

        # 验证 companion_mode
        assert await is_in_companion_mode(db)
        print("  start_session OK: companion_mode = True")

        # 验证 emotion_log
        cursor = await conn.execute(
            "SELECT * FROM emotion_log WHERE user_id = ?",
            (uid,),
        )
        log = await cursor.fetchone()
        assert log is not None
        assert log["triggered_by"] == "user_confide"
        assert log["persona_used"] == "kitty"
        assert log["session_end"] is None
        mood = json.loads(log["mood_signal"])
        assert "好烦" in mood
        print(f"  emotion_log OK: triggered_by={log['triggered_by']}, persona={log['persona_used']}")

        # 结束会话
        await end_session(uid, db)

        assert not await is_in_companion_mode(db)
        print("  end_session OK: companion_mode = False")

        # 验证 session_end 已更新
        cursor = await conn.execute(
            "SELECT session_end FROM emotion_log WHERE user_id = ?",
            (uid,),
        )
        log = await cursor.fetchone()
        assert log["session_end"] is not None
        print(f"  session_end OK: {log['session_end']}")

    print()


async def test_system_anomaly():
    """集成测试：系统异常检测"""
    print("=== 系统异常检测测试 ===")

    async with get_db_conn() as conn:
        init_sql = os.path.join(os.path.dirname(__file__), "..", "init.sql")
        with open(init_sql, encoding="utf-8") as f:
            await conn.executescript(f.read())

        uid = "test_anomaly_user"
        await conn.execute(
            "INSERT INTO users (user_id, invite_code) VALUES (?, ?)",
            (uid, "ANO001"),
        )
        await conn.execute(
            "INSERT INTO persona_config (user_id) VALUES (?)",
            (uid,),
        )
        await conn.commit()

        db = UserDB(uid, conn)

        # 正常状态：不应触发
        result = await check_system_anomaly(uid, db)
        assert result is None
        print("  正常状态 OK: 无异常")

    print()


async def main():
    test_detect()
    await test_session()
    await test_system_anomaly()
    print("✅ emotion_detector.py 所有测试通过！")


if __name__ == "__main__":
    asyncio.run(main())
