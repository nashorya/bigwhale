"""
Memory 系统单元测试。
测试记忆写入、按日期读取、语义搜索和 prompt 格式化。

注意：语义搜索测试需要 ZHIPU_API_KEY 环境变量，
      无 API Key 时跳过 embedding 相关测试。

运行：python tests/test_memory.py
"""

import os
import sys
import asyncio

os.environ["SHORE_USER_SALT"] = "test_salt_32chars_for_verify!!"
os.environ["DB_PATH"] = ":memory:"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "shore"))

from core.user_db import get_db_conn
from core.memory_store import (
    write_daily_memory,
    write_long_memory,
    get_daily_memories,
    get_recent_memories,
    format_memories_for_prompt,
    CAT_CHECKIN,
    CAT_EMOTION,
    CAT_PLAN,
)


# 判断是否有 API Key 可用
HAS_API_KEY = bool(os.environ.get("ZHIPU_API_KEY"))


async def test():
    async with get_db_conn() as conn:
        # 初始化表结构
        init_sql = os.path.join(os.path.dirname(__file__), "..", "init.sql")
        with open(init_sql, encoding="utf-8") as f:
            await conn.executescript(f.read())

        uid = "test_memory_user_001"

        # 插入测试用户
        await conn.execute(
            "INSERT INTO users (user_id, invite_code) VALUES (?, ?)",
            (uid, "MEM001"),
        )
        await conn.commit()

        print("── 测试 1：写入每日记忆 ──")
        mid1 = await write_daily_memory(
            conn, uid, CAT_CHECKIN,
            "完成 B树与B+树 知识点，掌握度 3→4",
            log_date="2026-03-17",
        )
        assert mid1 is not None
        print(f"  写入 OK: id={mid1}")

        mid2 = await write_daily_memory(
            conn, uid, CAT_EMOTION,
            "用户说压力大，进入陪伴模式",
            log_date="2026-03-17",
        )
        mid3 = await write_daily_memory(
            conn, uid, CAT_PLAN,
            "本周聚焦：极限与连续、数据结构",
            log_date="2026-03-16",
        )
        print(f"  共写入 3 条记忆 ✓")

        print()
        print("── 测试 2：按日期读取 ──")
        memories_17 = await get_daily_memories(conn, uid, "2026-03-17")
        assert len(memories_17) == 2, f"3/17 应有 2 条，实际: {len(memories_17)}"
        print(f"  3/17 记忆: {len(memories_17)} 条 ✓")

        memories_16 = await get_daily_memories(conn, uid, "2026-03-16")
        assert len(memories_16) == 1
        print(f"  3/16 记忆: {len(memories_16)} 条 ✓")

        # 按类别筛选
        checkin_only = await get_daily_memories(
            conn, uid, "2026-03-17", category=CAT_CHECKIN
        )
        assert len(checkin_only) == 1
        assert checkin_only[0]["category"] == "checkin"
        print(f"  按类别筛选 OK ✓")

        print()
        print("── 测试 3：写入长期记忆 ──")
        lid1 = await write_long_memory(conn, uid, "用户对线性代数持续感到困难")
        assert lid1 is not None
        print(f"  写入 OK: id={lid1}")

        print()
        print("── 测试 4：格式化 prompt 注入 ──")
        all_memories = memories_17 + memories_16
        prompt_text = format_memories_for_prompt(all_memories)
        assert len(prompt_text) > 0
        print(f"  格式化结果:\n{prompt_text}")

        if HAS_API_KEY:
            print()
            print("── 测试 5：语义搜索（需要 API Key）──")
            from core.memory_store import search_memories

            results = await search_memories(
                conn, uid, "B树", top_k=3, min_score=0.3
            )
            print(f"  搜索「B树」返回 {len(results)} 条:")
            for r in results:
                print(f"    [{r.get('similarity', '?')}] {r['content']}")
        else:
            print()
            print("── 测试 5：跳过（无 ZHIPU_API_KEY）──")

    print()
    print("✅ Memory 系统所有测试通过！")


if __name__ == "__main__":
    asyncio.run(test())
