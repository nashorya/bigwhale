"""
PointsService — 积分系统：注册赠送、每日订阅扣费、通用扣费/发放、邀请结算。
core/ 层禁止直接 import nonebot 模块。

所有积分变动写入 points_ledger 流水表，防止积分丢失。
对应表：points_account, points_ledger, users, persona_config
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


# ──────────────────────────────────────────────
# 积分常量（v3.2 文档 4.2/4.3 节）
# ──────────────────────────────────────────────

REGISTER_BONUS = 200          # 新用户注册赠送
INVITE_BONUS_INVITEE = 50     # 被邀请者完成初始化后获得
INVITE_BONUS_INVITER = 100    # 邀请者被邀请者完成初始化后获得
DAILY_SUBSCRIPTION = 20       # 每日订阅费
WORD_TIER_EXTRA = {           # 推词档位额外费用
    "basic": 0,
    "enhanced": 5,
    "sprint": 10,
}
LOW_BALANCE_WARNING = 50      # 低余额首次提醒阈值
LOW_BALANCE_URGENT = 20       # 紧急提醒阈值（约 1 天）
LOW_BALANCE_SUSPEND = 10      # 暂停订阅阈值


# ──────────────────────────────────────────────
# 内部辅助：带流水的积分变动
# ──────────────────────────────────────────────

async def _modify_balance(
    db: Any,
    delta: int,
    reason: str,
    ref_id: str | None = None,
) -> int:
    """
    修改积分余额并写入流水。返回变动后余额。
    调用方需保证事务内执行。

    参数：
        db: UserDB 实例
        delta: 变动量（正数增加，负数减少）
        reason: 原因枚举值
        ref_id: 关联业务 ID
    """
    # 读取当前余额
    cursor = await db._conn.execute(
        "SELECT balance FROM points_account WHERE user_id = ?",
        (db._uid,),
    )
    row = await cursor.fetchone()

    if row is None:
        # 账户不存在，先创建
        await db._conn.execute(
            "INSERT INTO points_account (user_id, balance) VALUES (?, 0)",
            (db._uid,),
        )
        current = 0
    else:
        current = row["balance"]

    new_balance = current + delta

    # 更新余额和累计统计
    if delta > 0:
        await db._conn.execute(
            """UPDATE points_account
               SET balance = ?, total_earned = total_earned + ?
               WHERE user_id = ?""",
            (new_balance, delta, db._uid),
        )
    else:
        await db._conn.execute(
            """UPDATE points_account
               SET balance = ?, total_spent = total_spent + ?
               WHERE user_id = ?""",
            (new_balance, abs(delta), db._uid),
        )

    # 写入流水
    await db._conn.execute(
        """INSERT INTO points_ledger
           (user_id, delta, balance_after, reason, ref_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (db._uid, delta, new_balance, reason, ref_id, datetime.now().isoformat()),
    )

    return new_balance


# ──────────────────────────────────────────────
# 公开方法
# ──────────────────────────────────────────────

async def register_bonus(user_id: str, db: Any) -> int:
    """
    新用户注册赠送 200 积分。

    创建 points_account 记录（如不存在），发放初始积分。
    返回发放后余额。

    参数：
        user_id: 用户 ID（应与 db._uid 一致）
        db: UserDB 实例
    """
    new_balance = await _modify_balance(
        db, REGISTER_BONUS, "register_bonus",
    )
    await db.commit()
    return new_balance


async def daily_deduct(user_id: str, db: Any) -> bool:
    """
    每日订阅扣费（每日 00:01 调用）。

    扣费逻辑（v3.2 文档 4.3）：
      - 余额 ≥ 20 → 正常扣除，返回 True
      - 余额 10-19 → 扣除但需推送余额预警，返回 True
      - 余额 < 10 → 不扣除，返回 False

    参数：
        user_id: 用户 ID
        db: UserDB 实例

    返回：
        True 扣费成功，False 余额不足未扣费
    """
    balance = await db.get_points_balance()

    if balance < LOW_BALANCE_SUSPEND:
        # 余额不足，不扣费，暂停订阅
        await db._conn.execute(
            "UPDATE points_account SET subscription_active = 0 WHERE user_id = ?",
            (db._uid,),
        )
        await db.commit()
        return False

    # 计算推词档位额外费用
    cursor = await db._conn.execute(
        "SELECT word_tier FROM points_account WHERE user_id = ?",
        (db._uid,),
    )
    row = await cursor.fetchone()
    word_tier = row["word_tier"] if row else "basic"
    extra = WORD_TIER_EXTRA.get(word_tier, 0)
    total_deduct = DAILY_SUBSCRIPTION + extra

    # 扣费
    await _modify_balance(db, -total_deduct, "daily_subscription")

    # 确保订阅状态为 active
    await db._conn.execute(
        "UPDATE points_account SET subscription_active = 1 WHERE user_id = ?",
        (db._uid,),
    )

    await db.commit()
    return True


async def spend(
    user_id: str,
    amount: int,
    reason: str,
    db: Any,
    *,
    ref_id: str | None = None,
) -> bool:
    """
    通用扣费。余额不足返回 False，不扣费。

    参数：
        user_id: 用户 ID
        amount: 扣除数量（正整数）
        reason: 原因枚举值（如 'emotion_session', 'plan_generate', 'report'）
        db: UserDB 实例
        ref_id: 关联业务 ID

    返回：
        True 扣费成功，False 余额不足
    """
    balance = await db.get_points_balance()
    if balance < amount:
        return False

    await _modify_balance(db, -amount, reason, ref_id)
    await db.commit()
    return True


async def grant(
    user_id: str,
    amount: int,
    reason: str,
    db: Any,
    *,
    ref_id: str | None = None,
) -> int:
    """
    积分发放。

    参数：
        user_id: 用户 ID
        amount: 发放数量（正整数）
        reason: 原因枚举值（如 'invite_bonus', 'recharge'）
        db: UserDB 实例
        ref_id: 关联业务 ID

    返回：
        发放后余额
    """
    new_balance = await _modify_balance(db, amount, reason, ref_id)
    await db.commit()
    return new_balance


async def get_account_summary(user_id: str, db: Any) -> dict:
    """
    获取积分账户摘要。

    返回：
        {
            'balance': int,
            'estimated_days': int,  # 按当前订阅费预估可用天数
            'subscription_active': bool,
            'word_tier': str,
            'total_earned': int,
            'total_spent': int,
            'recent_ledger': list[dict],  # 最近 5 条流水
            'unlocked_personas': list[str],  # 已解锁角色列表
            'balance_status': str,  # 'normal' / 'low' / 'urgent' / 'empty'
        }
    """
    # 账户基本信息
    cursor = await db._conn.execute(
        "SELECT * FROM points_account WHERE user_id = ?",
        (db._uid,),
    )
    row = await cursor.fetchone()

    if row is None:
        return {
            "balance": 0,
            "estimated_days": 0,
            "subscription_active": False,
            "word_tier": "basic",
            "total_earned": 0,
            "total_spent": 0,
            "recent_ledger": [],
            "unlocked_personas": ["kitty"],
            "balance_status": "empty",
        }

    balance = row["balance"]
    word_tier = row["word_tier"]
    daily_cost = DAILY_SUBSCRIPTION + WORD_TIER_EXTRA.get(word_tier, 0)
    estimated_days = balance // daily_cost if daily_cost > 0 else 0

    # 余额状态
    if balance <= 0:
        status = "empty"
    elif balance < LOW_BALANCE_URGENT:
        status = "urgent"
    elif balance < LOW_BALANCE_WARNING:
        status = "low"
    else:
        status = "normal"

    # 最近 5 条流水
    cursor = await db._conn.execute(
        """SELECT delta, balance_after, reason, ref_id, created_at
           FROM points_ledger WHERE user_id = ?
           ORDER BY created_at DESC LIMIT 5""",
        (db._uid,),
    )
    ledger_rows = await cursor.fetchall()
    recent_ledger = [dict(r) for r in ledger_rows]

    # 已解锁角色
    cursor = await db._conn.execute(
        "SELECT unlocked_personas FROM persona_config WHERE user_id = ?",
        (db._uid,),
    )
    persona_row = await cursor.fetchone()
    if persona_row and persona_row["unlocked_personas"]:
        try:
            unlocked = json.loads(persona_row["unlocked_personas"])
        except (json.JSONDecodeError, TypeError):
            unlocked = ["kitty"]
    else:
        unlocked = ["kitty"]

    return {
        "balance": balance,
        "estimated_days": estimated_days,
        "subscription_active": bool(row["subscription_active"]),
        "word_tier": word_tier,
        "total_earned": row["total_earned"],
        "total_spent": row["total_spent"],
        "recent_ledger": recent_ledger,
        "unlocked_personas": unlocked,
        "balance_status": status,
    }


async def settle_invite(invitee_uid: str, db: Any) -> bool:
    """
    邀请积分结算。被邀请者完成初始化后调用。

    操作：
      1. 检查 users.invite_settled 是否已结算
      2. 给被邀请者发放 +50 积分
      3. 找到邀请者 user_id，给邀请者发放 +100 积分
      4. 标记 invite_settled = 1

    参数：
        invitee_uid: 被邀请者的 user_id
        db: 被邀请者的 UserDB 实例

    返回：
        True 结算成功，False 已结算过或无邀请者
    """
    # 检查是否已结算
    cursor = await db._conn.execute(
        "SELECT invited_by, invite_settled FROM users WHERE user_id = ?",
        (invitee_uid,),
    )
    row = await cursor.fetchone()

    if row is None:
        return False
    if row["invite_settled"]:
        return False  # 已结算过
    if not row["invited_by"]:
        return False  # 无邀请者（自然注册）

    inviter_uid = row["invited_by"]

    # 给被邀请者发放 +50
    await _modify_balance(db, INVITE_BONUS_INVITEE, "invite_bonus_invitee")

    # 给邀请者发放 +100（需要切换到邀请者的上下文）
    # 先确保邀请者有 points_account
    cursor = await db._conn.execute(
        "SELECT 1 FROM points_account WHERE user_id = ?",
        (inviter_uid,),
    )
    if not await cursor.fetchone():
        await db._conn.execute(
            "INSERT INTO points_account (user_id, balance) VALUES (?, 0)",
            (inviter_uid,),
        )

    # 读取邀请者余额
    cursor = await db._conn.execute(
        "SELECT balance FROM points_account WHERE user_id = ?",
        (inviter_uid,),
    )
    inviter_row = await cursor.fetchone()
    inviter_balance = inviter_row["balance"] if inviter_row else 0
    new_inviter_balance = inviter_balance + INVITE_BONUS_INVITER

    # 更新邀请者余额
    await db._conn.execute(
        """UPDATE points_account
           SET balance = ?, total_earned = total_earned + ?
           WHERE user_id = ?""",
        (new_inviter_balance, INVITE_BONUS_INVITER, inviter_uid),
    )

    # 写入邀请者流水
    await db._conn.execute(
        """INSERT INTO points_ledger
           (user_id, delta, balance_after, reason, ref_id, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            inviter_uid,
            INVITE_BONUS_INVITER,
            new_inviter_balance,
            "invite_bonus_inviter",
            invitee_uid,  # ref_id 记录被邀请者 ID
            datetime.now().isoformat(),
        ),
    )

    # 标记已结算
    await db._conn.execute(
        "UPDATE users SET invite_settled = 1 WHERE user_id = ?",
        (invitee_uid,),
    )

    await db.commit()
    return True
