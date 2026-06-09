"""
core/points_service.py 验证脚本
"""
import os
import sys
import asyncio

os.environ["SHORE_USER_SALT"] = "test_salt_32chars_for_verify!!"
os.environ["DB_PATH"] = ":memory:"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "shore"))

from core.points_service import (
    register_bonus,
    daily_deduct,
    spend,
    grant,
    get_account_summary,
    settle_invite,
    REGISTER_BONUS,
    DAILY_SUBSCRIPTION,
)
from core.user_db import get_db_conn, UserDB


async def test():
    async with get_db_conn() as conn:
        init_sql = os.path.join(os.path.dirname(__file__), "..", "init.sql")
        with open(init_sql, encoding="utf-8") as f:
            await conn.executescript(f.read())

        uid = "test_points_user"
        inviter_uid = "test_inviter_user"

        # 创建用户（先插入邀请者，再插入被邀请者，满足外键约束）
        await conn.execute(
            "INSERT INTO users (user_id, invite_code) VALUES (?, ?)",
            (inviter_uid, "PTS002"),
        )
        await conn.execute(
            "INSERT INTO users (user_id, invite_code, invited_by) VALUES (?, ?, ?)",
            (uid, "PTS001", inviter_uid),
        )
        # 创建 persona_config
        await conn.execute(
            "INSERT INTO persona_config (user_id) VALUES (?)",
            (uid,),
        )
        await conn.commit()

        db = UserDB(uid, conn)
        inviter_db = UserDB(inviter_uid, conn)

        # 1. register_bonus
        balance = await register_bonus(uid, db)
        assert balance == REGISTER_BONUS, f"注册赠送后余额应为 {REGISTER_BONUS}，实际: {balance}"
        print(f"  register_bonus OK: 余额 {balance}")

        # 给邀请者也注册
        await register_bonus(inviter_uid, inviter_db)
        print(f"  register_bonus (邀请者) OK")

        # 2. grant
        new_bal = await grant(uid, 100, "recharge", db)
        assert new_bal == 300, f"充值后余额应为 300，实际: {new_bal}"
        print(f"  grant OK: 余额 {new_bal}")

        # 3. spend
        ok = await spend(uid, 50, "plan_generate", db)
        assert ok, "扣费应成功"
        bal = await db.get_points_balance()
        assert bal == 250, f"扣50后余额应为 250，实际: {bal}"
        print(f"  spend OK: 扣50，余额 {bal}")

        # spend 余额不足
        ok = await spend(uid, 9999, "plan_generate", db)
        assert not ok, "余额不足应返回 False"
        print("  spend (余额不足) OK: 正确拒绝")

        # 4. daily_deduct
        ok = await daily_deduct(uid, db)
        assert ok, "每日扣费应成功"
        bal = await db.get_points_balance()
        assert bal == 250 - DAILY_SUBSCRIPTION, f"扣订阅后余额应为 {250 - DAILY_SUBSCRIPTION}，实际: {bal}"
        print(f"  daily_deduct OK: 扣{DAILY_SUBSCRIPTION}，余额 {bal}")

        # daily_deduct 余额不足
        # 先把余额扣到 5
        current = await db.get_points_balance()
        if current > 5:
            await spend(uid, current - 5, "test_drain", db)
        ok = await daily_deduct(uid, db)
        assert not ok, "余额<10 应拒绝扣费"
        bal = await db.get_points_balance()
        assert bal == 5, f"拒绝扣费后余额应不变: {bal}"
        print(f"  daily_deduct (余额不足) OK: 拒绝扣费，余额 {bal}")

        # 5. get_account_summary
        # 先充值回来
        await grant(uid, 200, "test_recharge", db)
        summary = await get_account_summary(uid, db)
        assert summary["balance"] == 205
        assert summary["estimated_days"] > 0
        assert summary["unlocked_personas"] == ["kitty"]
        assert len(summary["recent_ledger"]) > 0
        assert summary["balance_status"] == "normal"
        print(f"  get_account_summary OK: balance={summary['balance']}, days={summary['estimated_days']}, status={summary['balance_status']}")
        print(f"    最近流水: {len(summary['recent_ledger'])} 条")

        # 6. settle_invite
        # 先标记被邀请者初始化完成
        await conn.execute(
            "UPDATE users SET init_complete = 1 WHERE user_id = ?",
            (uid,),
        )
        await conn.commit()

        ok = await settle_invite(uid, db)
        assert ok, "邀请结算应成功"

        # 验证被邀请者 +50
        bal_invitee = await db.get_points_balance()
        assert bal_invitee == 255, f"被邀请者应为 255，实际: {bal_invitee}"
        print(f"  settle_invite (被邀请者) OK: +50 → 余额 {bal_invitee}")

        # 验证邀请者 +100
        inviter_bal = await inviter_db.get_points_balance()
        assert inviter_bal == 300, f"邀请者应为 300，实际: {inviter_bal}"
        print(f"  settle_invite (邀请者) OK: +100 → 余额 {inviter_bal}")

        # 重复结算应返回 False
        ok = await settle_invite(uid, db)
        assert not ok, "重复结算应返回 False"
        print("  settle_invite (重复) OK: 正确拒绝")

        # 验证流水完整性
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM points_ledger WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        print(f"  流水总计 OK: {row['cnt']} 条")

    print()
    print("✅ points_service.py 所有测试通过！")


if __name__ == "__main__":
    asyncio.run(test())
