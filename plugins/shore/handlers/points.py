"""
积分相关指令处理器。
薄胶水层：解析输入 → 调用 core/ → 渲染结果。

指令集：
  #积分             — 查看积分余额与账户概览
  #积分明细         — 查看最近 10 条积分流水
  #充值             — 充值说明（静态文案）
  #积分说明         — 积分制度说明（静态文案）
  #推词档位 [档位]  — 切换英语推词档位（basic/enhanced/sprint）
"""

from __future__ import annotations

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..core.points_service import (
    DAILY_SUBSCRIPTION,
    WORD_TIER_EXTRA,
    get_account_summary,
)
from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import UserDB, get_db_conn
from .admin import check_banned

# 推词档位合法值
_VALID_TIERS = {"basic", "enhanced", "sprint"}

# 推词档位中文名
_TIER_NAMES: dict[str, str] = {
    "basic": "基础档（不推词）",
    "enhanced": "强化档（+5 积分/天）",
    "sprint": "冲刺档（+10 积分/天）",
}


# ──────────────────────────────────────────────
# #积分
# ──────────────────────────────────────────────

points_cmd = on_command("积分", priority=5, block=True)


@points_cmd.handle()
async def handle_points(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#积分 — 查看积分余额与账户概览"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        summary = await get_account_summary(uid, db)

    balance = summary["balance"]
    est_days = summary["estimated_days"]
    sub_active = summary["subscription_active"]
    word_tier = summary["word_tier"]
    status = summary["balance_status"]

    # 余额状态提示
    status_tips = {
        "normal": "",
        "low": "\n⚠️ 余额偏低，建议及时充值。",
        "urgent": "\n🔴 余额紧张！余量不足 1 天，请立即充值。",
        "empty": "\n❌ 余额耗尽，订阅已暂停。发送 [#充值] 恢复服务。",
    }

    daily_cost = DAILY_SUBSCRIPTION + WORD_TIER_EXTRA.get(word_tier, 0)
    sub_str = "订阅中" if sub_active else "已暂停"
    tier_str = _TIER_NAMES.get(word_tier, word_tier)

    lines = [
        "💰 积分账户",
        "",
        f"余额：{balance} 积分",
        f"可用约 {est_days} 天（每日消耗 {daily_cost} 积分）",
        f"订阅状态：{sub_str}",
        f"推词档位：{tier_str}",
        status_tips.get(status, ""),
        "",
        "发送 [#积分明细] 查看流水  ·  [#充值] 了解充值",
    ]
    await matcher.finish("\n".join(l for l in lines if l is not None))


# ──────────────────────────────────────────────
# #积分明细
# ──────────────────────────────────────────────

points_detail_cmd = on_command("积分明细", priority=5, block=True)


@points_detail_cmd.handle()
async def handle_points_detail(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#积分明细 — 查看最近 10 条积分流水"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            """SELECT delta, balance_after, reason, created_at
               FROM points_ledger WHERE user_id = ?
               ORDER BY created_at DESC LIMIT 10""",
            (uid,),
        )
        rows = await cursor.fetchall()

    if not rows:
        await matcher.finish("暂无积分流水记录。")

    lines = ["📋 积分明细（最近 10 条）", ""]
    for row in rows:
        delta = row["delta"]
        sign = "+" if delta > 0 else ""
        reason_zh = _reason_to_zh(row["reason"])
        date_str = str(row["created_at"])[:16]  # 取 YYYY-MM-DD HH:MM
        lines.append(f"{sign}{delta}  {reason_zh}  余额→{row['balance_after']}")
        lines.append(f"  {date_str}")
        lines.append("")

    await matcher.finish("\n".join(lines).rstrip())


# ──────────────────────────────────────────────
# #充值
# ──────────────────────────────────────────────

recharge_cmd = on_command("充值", priority=5, block=True)


@recharge_cmd.handle()
async def handle_recharge(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#充值 — 充值说明"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    await matcher.finish(
        "💳 充值说明\n"
        "\n"
        "上岸目前处于内测阶段，暂不支持在线充值。\n"
        "\n"
        "如需积分，请联系管理员手动发放。\n"
        "\n"
        "· 每日消耗：20 积分（基础档）\n"
        "· 推词额外：enhanced +5/天  sprint +10/天\n"
        "· 注册赠送：200 积分（约 10 天）\n"
        "· 邀请奖励：被邀请者 +50，邀请者 +100\n"
    )


# ──────────────────────────────────────────────
# #积分说明
# ──────────────────────────────────────────────

points_help_cmd = on_command("积分说明", priority=5, block=True)


@points_help_cmd.handle()
async def handle_points_help(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#积分说明 — 积分制度说明"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    await matcher.finish(
        "📖 积分说明\n"
        "\n"
        "【获取积分】\n"
        "· 注册赠送：200 积分\n"
        "· 邀请好友（被邀请者完成初始化后）：+100\n"
        "· 被邀请者：+50\n"
        "\n"
        "【消耗积分】\n"
        "· 每日订阅：20 积分/天\n"
        "· 推词档位额外费：enhanced +5/天，sprint +10/天\n"
        "· 解锁角色：80 积分/个\n"
        "· 情绪陪伴：5 积分/次\n"
        "· 学情报告：5 积分/次\n"
        "\n"
        "【余额预警】\n"
        "· 余额 < 50：低余额提醒\n"
        "· 余额 < 20：紧急提醒\n"
        "· 余额 < 10：暂停订阅\n"
        "\n"
        "发送 [#积分] 查看当前余额。"
    )


# ──────────────────────────────────────────────
# #推词档位
# ──────────────────────────────────────────────

word_tier_cmd = on_command("推词档位", priority=5, block=True)


@word_tier_cmd.handle()
async def handle_word_tier(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#推词档位 [档位] — 切换英语推词档位"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    raw = sanitize_input(args.extract_plain_text()).lower()

    if not raw:
        # 展示当前档位及说明
        async with get_db_conn() as conn:
            cursor = await conn.execute(
                "SELECT word_tier FROM points_account WHERE user_id = ?",
                (uid,),
            )
            row = await cursor.fetchone()
        current = row["word_tier"] if row else "basic"
        lines = [
            f"当前推词档位：{_TIER_NAMES.get(current, current)}",
            "",
            "可选档位：",
            "  basic    — 仅学科知识点，不推英语单词（+0/天）",
            "  enhanced — 每日推送基础词汇，强化英语（+5/天）",
            "  sprint   — 高频词冲刺推送（+10/天）",
            "",
            "用法：#推词档位 [basic/enhanced/sprint]",
        ]
        await matcher.finish("\n".join(lines))

    if raw not in _VALID_TIERS:
        await matcher.finish(
            f"无效档位「{raw}」。\n"
            "可选：basic / enhanced / sprint\n"
            "发送 [#推词档位] 查看说明。"
        )

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            "SELECT word_tier FROM points_account WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        if not row:
            await matcher.finish("账户信息异常，请联系管理员。")

        current = row["word_tier"]
        if current == raw:
            await matcher.finish(
                f"当前已是「{_TIER_NAMES.get(raw, raw)}」，无需切换。"
            )

        await conn.execute(
            "UPDATE points_account SET word_tier = ? WHERE user_id = ?",
            (raw, uid),
        )
        await conn.commit()

    extra = WORD_TIER_EXTRA.get(raw, 0)
    daily_total = DAILY_SUBSCRIPTION + extra
    await matcher.finish(
        f"✓ 推词档位已切换：{_TIER_NAMES.get(raw, raw)}\n"
        f"每日总消耗：{daily_total} 积分（基础 {DAILY_SUBSCRIPTION} + 推词 {extra}）\n"
        "调整将从明日起生效。"
    )


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

def _reason_to_zh(reason: str) -> str:
    """积分流水原因 → 中文描述"""
    _map = {
        "register_bonus": "注册赠送",
        "daily_subscription": "每日订阅",
        "invite_bonus_invitee": "邀请奖励（被邀请）",
        "invite_bonus_inviter": "邀请奖励（邀请者）",
        "persona_unlock": "解锁角色",
        "emotion_session": "情绪陪伴",
        "report": "学情报告",
        "admin_grant": "管理员发放",
    }
    return _map.get(reason, reason)
