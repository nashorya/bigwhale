"""
管理员后台指令处理器（v3.3）。
所有 #admin 指令的入口，非管理员静默忽略。

安全措施：
  - 管理员 QQ 号从 ADMIN_QQ_LIST 环境变量读取
  - 响应中 QQ 号只显示后4位 ****XXXX
  - 所有操作写入 admin_log 表
"""

from __future__ import annotations

import logging
logger = logging.getLogger("shore.admin")

import json
import os
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.matcher import Matcher

from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import get_db_conn, UserDB
from ..core import points_service
from ..core import persona_engine


# ──────────────────────────────────────────────
# 管理员身份验证
# ──────────────────────────────────────────────

def is_admin(qq: str) -> bool:
    """
    检查 QQ 号是否为管理员。
    从环境变量 ADMIN_QQ_LIST 读取（逗号分隔）。
    """
    admin_list = os.environ.get("ADMIN_QQ_LIST", "").split(",")
    return qq.strip() in [a.strip() for a in admin_list if a.strip()]


def _mask_qq(qq: str) -> str:
    """QQ 号脱敏：只显示后4位"""
    if len(qq) <= 4:
        return f"****{qq}"
    return f"****{qq[-4:]}"


async def _log_admin_action(
    conn, admin_qq: str, action: str, target_uid: str, detail: dict | None = None
) -> None:
    """写入 admin_log 表"""
    await conn.execute(
        """INSERT INTO admin_log (admin_qq_suffix, action, target_user_id, detail)
           VALUES (?, ?, ?, ?)""",
        (
            admin_qq[-4:] if len(admin_qq) > 4 else admin_qq,
            action,
            target_uid,
            json.dumps(detail, ensure_ascii=False) if detail else None,
        ),
    )


# ──────────────────────────────────────────────
# 封禁检查（供其他 handler 调用）
# ──────────────────────────────────────────────

async def check_banned(uid: str) -> bool:
    """
    检查用户是否被封禁。返回 True 表示已封禁。
    供所有 handler 在消息入口处调用。
    """
    async with get_db_conn() as conn:
        cursor = await conn.execute(
            "SELECT is_banned FROM users WHERE user_id = ?", (uid,)
        )
        row = await cursor.fetchone()
        return bool(row and row["is_banned"])


# ──────────────────────────────────────────────
# #admin 指令入口
# ──────────────────────────────────────────────

admin_cmd = on_command("admin", priority=1, block=True)


@admin_cmd.handle()
async def handle_admin(bot: Bot, event: PrivateMessageEvent, matcher: Matcher):
    """管理员指令分发入口"""
    qq = str(event.user_id)

    # 非管理员处理
    if not is_admin(qq):
        # 如果 ADMIN_QQ_LIST 未配置，给出提示帮助排查
        admin_list_raw = os.environ.get("ADMIN_QQ_LIST", "").strip()
        if not admin_list_raw:
            await matcher.finish(
                "⚠️ ADMIN_QQ_LIST 环境变量未配置。\n"
                "请在 .env 文件中设置：\n"
                f"ADMIN_QQ_LIST={qq}"
            )
        # 有配置但不匹配 → 静默忽略
        await matcher.finish()

    # 解析子指令
    raw = event.get_plaintext().strip()
    # 去掉开头的 #admin
    if raw.startswith("#admin"):
        raw = raw[6:].strip()
    elif raw.startswith("admin"):
        raw = raw[5:].strip()

    if not raw:
        await matcher.finish(_help_text())

    # 分发子指令
    if raw.startswith("发放积分"):
        await _handle_grant(bot, event, matcher, qq, raw[4:].strip())
    elif raw.startswith("查积分"):
        await _handle_query_points(bot, event, matcher, qq, raw[3:].strip())
    elif raw.startswith("查用户"):
        await _handle_query_user(bot, event, matcher, qq, raw[3:].strip())
    elif raw.startswith("封禁"):
        await _handle_ban(bot, event, matcher, qq, raw[2:].strip())
    elif raw.startswith("解封"):
        await _handle_unban(bot, event, matcher, qq, raw[2:].strip())
    elif raw.startswith("角色统计"):
        await _handle_persona_stats(bot, event, matcher, qq)
    elif raw.startswith("查角色卡"):
        await _handle_persona_detail(bot, event, matcher, qq, raw[4:].strip())
    else:
        await matcher.finish(_help_text())


# ──────────────────────────────────────────────
# 子指令处理
# ──────────────────────────────────────────────

async def _handle_grant(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    admin_qq: str, args: str
):
    """#admin 发放积分 [QQ号] [数量] [备注]"""
    parts = args.split(maxsplit=2)
    if len(parts) < 2:
        await matcher.finish(
            "用法：#admin 发放积分 [QQ号] [数量] [备注]\n"
            "示例：#admin 发放积分 123456 200 月度包充值"
        )

    target_qq = parts[0]
    try:
        amount = int(parts[1])
    except ValueError:
        await matcher.finish("积分数量必须为整数。")
        return

    reason = parts[2] if len(parts) > 2 else "管理员发放"
    target_uid = get_or_create_uid(target_qq)

    async with get_db_conn() as conn:
        db = UserDB(target_uid, conn)

        # 检查用户是否存在
        cursor = await conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (target_uid,)
        )
        if not await cursor.fetchone():
            await matcher.finish(f"用户 {_mask_qq(target_qq)} 不存在。")

        # 发放积分
        new_balance = await points_service.grant(
            target_uid, amount, "admin_grant", db,
            ref_id=admin_qq[-4:],
        )

        # 写入管理员日志
        await _log_admin_action(
            conn, admin_qq, "grant_points", target_uid,
            {"amount": amount, "reason": reason, "balance_after": new_balance},
        )
        await conn.commit()

    response = (
        f"【管理员操作】积分发放\n"
        f"\n"
        f"用户：{_mask_qq(target_qq)}\n"
        f"发放：+{amount} 积分\n"
        f"备注：{reason}\n"
        f"操作后余额：{new_balance} 积分\n"
        f"\n"
        f"操作成功 ✓"
    )
    await matcher.finish(response)


async def _handle_query_points(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    admin_qq: str, args: str
):
    """#admin 查积分 [QQ号]"""
    target_qq = args.strip()
    if not target_qq:
        await matcher.finish("用法：#admin 查积分 [QQ号]")

    target_uid = get_or_create_uid(target_qq)

    async with get_db_conn() as conn:
        db = UserDB(target_uid, conn)

        cursor = await conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (target_uid,)
        )
        if not await cursor.fetchone():
            await matcher.finish(f"用户 {_mask_qq(target_qq)} 不存在。")

        summary = await points_service.get_account_summary(target_uid, db)

        # 查最近10条流水
        cursor = await conn.execute(
            """SELECT delta, reason, created_at
               FROM points_ledger WHERE user_id = ?
               ORDER BY created_at DESC LIMIT 10""",
            (target_uid,),
        )
        ledger = await cursor.fetchall()

    lines = [
        f"【积分查询】{_mask_qq(target_qq)}",
        "",
        f"当前余额：{summary['balance']} 积分",
        f"预计可用：{summary['estimated_days']} 天",
        "",
        "最近流水（最新10条）：",
    ]
    for row in ledger:
        delta = row["delta"]
        sign = "+" if delta > 0 else ""
        dt = row["created_at"][:16] if row["created_at"] else ""
        lines.append(f"  {sign}{delta:>5}  {row['reason']:<20s}  {dt}")

    if not ledger:
        lines.append("  （暂无流水记录）")

    await matcher.finish("\n".join(lines))


async def _handle_query_user(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    admin_qq: str, args: str
):
    """#admin 查用户 [QQ号]"""
    target_qq = args.strip()
    if not target_qq:
        await matcher.finish("用法：#admin 查用户 [QQ号]")

    target_uid = get_or_create_uid(target_qq)

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (target_uid,)
        )
        user = await cursor.fetchone()
        if not user:
            await matcher.finish(f"用户 {_mask_qq(target_qq)} 不存在。")

        # 角色信息
        cursor = await conn.execute(
            "SELECT active_persona FROM persona_config WHERE user_id = ?",
            (target_uid,),
        )
        persona_row = await cursor.fetchone()
        persona_name = persona_row["active_persona"] if persona_row else "未设置"

        # 被邀请人数
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE invited_by = ?",
            (target_uid,),
        )
        invite_count = (await cursor.fetchone())["cnt"]

    init_status = "已完成" if user["init_complete"] else "未完成"
    invite_info = "无"
    if user["invited_by"]:
        settled = "积分已结算" if user["invite_settled"] else "待结算"
        invite_info = f"有（{settled}）"

    ban_status = "正常"
    if user["is_banned"]:
        ban_status = f"已封禁（{user['ban_reason'] or '无原因'}）"

    response = (
        f"【用户信息】{_mask_qq(target_qq)}\n"
        f"\n"
        f"注册时间：{user['registered_at']}\n"
        f"初始化：{init_status}\n"
        f"邀请者：{invite_info}\n"
        f"被邀请人数：{invite_count} 人\n"
        f"当前角色：{persona_name}\n"
        f"封禁状态：{ban_status}"
    )
    await matcher.finish(response)


async def _handle_ban(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    admin_qq: str, args: str
):
    """#admin 封禁 [QQ号] [原因]"""
    parts = args.split(maxsplit=1)
    if not parts:
        await matcher.finish("用法：#admin 封禁 [QQ号] [原因]")

    target_qq = parts[0]
    reason = parts[1] if len(parts) > 1 else "未说明原因"
    target_uid = get_or_create_uid(target_qq)

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (target_uid,)
        )
        if not await cursor.fetchone():
            await matcher.finish(f"用户 {_mask_qq(target_qq)} 不存在。")

        now = datetime.now().isoformat()
        await conn.execute(
            """UPDATE users
               SET is_banned = 1, ban_reason = ?, banned_at = ?
               WHERE user_id = ?""",
            (reason, now, target_uid),
        )

        await _log_admin_action(
            conn, admin_qq, "ban_user", target_uid,
            {"reason": reason},
        )
        await conn.commit()

    response = (
        f"【管理操作】封禁用户\n"
        f"\n"
        f"用户：{_mask_qq(target_qq)}\n"
        f"原因：{reason}\n"
        f"状态：已封禁 ✓\n"
        f"\n"
        f"该用户的消息将被静默忽略。"
    )
    await matcher.finish(response)


async def _handle_unban(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    admin_qq: str, args: str
):
    """#admin 解封 [QQ号]"""
    target_qq = args.strip()
    if not target_qq:
        await matcher.finish("用法：#admin 解封 [QQ号]")

    target_uid = get_or_create_uid(target_qq)

    async with get_db_conn() as conn:
        await conn.execute(
            """UPDATE users
               SET is_banned = 0, ban_reason = NULL, banned_at = NULL
               WHERE user_id = ?""",
            (target_uid,),
        )

        await _log_admin_action(conn, admin_qq, "unban_user", target_uid)
        await conn.commit()

    response = (
        f"【管理操作】解封用户\n"
        f"\n"
        f"用户：{_mask_qq(target_qq)}\n"
        f"状态：已解封 ✓\n"
        f"\n"
        f"该用户现在可以正常使用 Bot。"
    )
    await matcher.finish(response)


async def _handle_persona_stats(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    admin_qq: str
):
    """#admin 角色统计"""
    async with get_db_conn() as conn:
        # 总用户数
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM users")
        total = (await cursor.fetchone())["cnt"]

        # 角色分布
        cursor = await conn.execute(
            """SELECT active_persona, COUNT(*) as cnt
               FROM persona_config
               GROUP BY active_persona
               ORDER BY cnt DESC"""
        )
        distribution = await cursor.fetchall()

        # 解锁情况
        cursor = await conn.execute(
            "SELECT unlocked_personas FROM persona_config"
        )
        all_unlocked = await cursor.fetchall()

    # 统计解锁
    unlock_count = {"kitty": 0, "makoto": 0, "himiko": 0, "alya": 0}
    for row in all_unlocked:
        try:
            unlocked = json.loads(row["unlocked_personas"])
            for pid in unlocked:
                if pid in unlock_count:
                    unlock_count[pid] += 1
        except (json.JSONDecodeError, TypeError):
            unlock_count["kitty"] += 1

    # 角色名映射
    name_map = {
        "kitty": "咪咪（温柔治愈）",
        "makoto": "真学姐（铁血督导）",
        "himiko": "卑弥呼（深夜疲惫搭子）",
        "alya": "艾莉亚（傲娇卷王）",
    }

    lines = [f"【角色分布统计】", "", f"总用户数：{total} 人", "当前激活角色："]

    for row in distribution:
        pid = row["active_persona"]
        cnt = row["cnt"]
        pct = cnt / total * 100 if total > 0 else 0
        bar_full = int(pct / 10)
        bar = "█" * bar_full + "░" * (10 - bar_full)
        name = name_map.get(pid, pid)
        lines.append(f"  {name}  {bar}  {cnt}人  {pct:.1f}%")

    lines.append("")
    lines.append("解锁情况：")
    for pid, name in name_map.items():
        cnt = unlock_count.get(pid, 0)
        if pid == "kitty":
            lines.append(f"  {name.split('（')[0]}  {cnt}人（默认解锁）")
        else:
            pct = cnt / total * 100 if total > 0 else 0
            lines.append(f"  {name.split('（')[0]}  {cnt}人（{pct:.1f}%）")

    await matcher.finish("\n".join(lines))


async def _handle_persona_detail(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    admin_qq: str, args: str
):
    """#admin 查角色卡 [角色id]"""
    persona_id = args.strip().lower()
    if not persona_id:
        await matcher.finish("用法：#admin 查角色卡 [角色id]\n可选：kitty / makoto / himiko / alya")

    # 加载角色
    if not persona_engine.is_loaded():
        persona_engine.load_personas()

    persona = persona_engine.get_persona(persona_id)
    if not persona:
        await matcher.finish(f"角色 {persona_id} 不存在。")

    tone = persona.get("tone_profile", {})
    catchphrases = tone.get("catchphrase", [])

    # 统计脚本覆盖
    script_counts = {}
    for key in ["daily_scripts", "checkin_scripts", "emotion_scripts", "milestone_scripts"]:
        scripts = persona.get(key, {})
        if isinstance(scripts, dict):
            script_counts[key] = len(scripts)
        elif isinstance(scripts, list):
            script_counts[key] = len(scripts)
        else:
            script_counts[key] = 0

    lines = [
        f"【角色卡内容】{persona.get('name', persona_id)} ({persona_id})",
        "",
        "基本信息：",
        f"  风格：{persona.get('archetype', '未知')}",
        f"  简介：{persona.get('tagline', '')}",
        f"  话量：{tone.get('verbosity', '?')} | 正式程度：{tone.get('formality', '?')}",
        f"  Emoji：{'无' if not tone.get('emoji_set') else ' '.join(tone['emoji_set'])}",
        "",
        "口癖（catchphrase）：",
    ]
    for cp in catchphrases[:6]:
        lines.append(f"  · {cp}")

    lines.append("")
    lines.append("脚本覆盖：")
    for key, cnt in script_counts.items():
        check = "✓" if cnt > 0 else "✗"
        lines.append(f"  {key:<20s} {check} {cnt}个")

    lines.append("")
    lines.append(f"文件路径：personas/builtin/{persona_id}.json")

    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# 帮助文本
# ──────────────────────────────────────────────

def _help_text() -> str:
    return (
        "【管理员指令】\n"
        "\n"
        "#admin 发放积分 [QQ号] [数量] [备注]\n"
        "#admin 查积分 [QQ号]\n"
        "#admin 查用户 [QQ号]\n"
        "#admin 封禁 [QQ号] [原因]\n"
        "#admin 解封 [QQ号]\n"
        "#admin 角色统计\n"
        "#admin 查角色卡 [角色id]"
    )
