"""
人物卡相关指令处理器。
薄胶水层：解析输入 → 调用 core/ → 渲染结果。

指令集：
  #选择角色              — 展示角色列表
  #选择角色 [名称/id]    — 切换到指定角色（需已解锁）
  #当前角色              — 查看当前使用的角色信息
  #解锁角色 [名称]       — 花费 80 积分解锁新角色
  #角色商店              — 查看所有角色及解锁/价格状态
"""

from __future__ import annotations

import json

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..core import persona_engine
from ..core.points_service import spend
from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import UserDB, get_db_conn
from .admin import check_banned

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

PERSONA_UNLOCK_COST = 80  # 积分

# 中文名 / 英文 ID → 标准 ID
_NAME_TO_ID: dict[str, str] = {
    "咪咪": "kitty",     "kitty": "kitty",
    "真学姐": "makoto",  "makoto": "makoto",
    "卑弥呼": "himiko",  "himiko": "himiko",
    "艾莉亚": "alya",    "alya": "alya",
}

# 角色 ID → 中文名
_ID_TO_NAME: dict[str, str] = {
    "kitty": "咪咪",
    "makoto": "真学姐",
    "himiko": "卑弥呼",
    "alya": "艾莉亚",
}

# 默认免费解锁的角色
_FREE_PERSONAS: set[str] = {"kitty"}


# ──────────────────────────────────────────────
# #选择角色
# ──────────────────────────────────────────────

choose_persona_cmd = on_command("选择角色", priority=5, block=True)


@choose_persona_cmd.handle()
async def handle_choose_persona(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#选择角色 [名称/id] — 切换角色，不带参数时展示列表"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    raw = sanitize_input(args.extract_plain_text())
    if not raw:
        await matcher.finish(_build_persona_list())

    persona_id = _NAME_TO_ID.get(raw.strip())
    if not persona_id:
        await matcher.finish(
            f"未找到角色「{raw}」。\n"
            "发送 [#选择角色] 查看可选角色列表。"
        )

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        unlocked = await _get_unlocked(conn, uid)

        if persona_id not in unlocked:
            name = _ID_TO_NAME.get(persona_id, persona_id)
            await matcher.finish(
                f"角色「{name}」尚未解锁。\n"
                f"发送 [#解锁角色 {name}] 花费 {PERSONA_UNLOCK_COST} 积分解锁。"
            )

        current = await db.get_active_persona()
        if current == persona_id:
            await matcher.finish(
                f"你当前已在使用「{_ID_TO_NAME.get(persona_id, persona_id)}」。"
            )

        await conn.execute(
            "UPDATE persona_config SET active_persona = ? WHERE user_id = ?",
            (persona_id, uid),
        )
        await conn.commit()

    persona_name = _ID_TO_NAME.get(persona_id, persona_id)
    if persona_engine.is_loaded():
        card = persona_engine.get_persona(persona_id)
        tagline = card.get("tagline", "") if card else ""
        response = f"✓ 已切换到「{persona_name}」。"
        if tagline:
            response += f"\n「{tagline}」"
    else:
        response = f"✓ 已切换到「{persona_name}」。"

    await matcher.finish(response)


# ──────────────────────────────────────────────
# #当前角色
# ──────────────────────────────────────────────

current_persona_cmd = on_command("当前角色", priority=5, block=True)


@current_persona_cmd.handle()
async def handle_current_persona(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#当前角色 — 查看当前使用的角色信息"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        persona_id = await db.get_active_persona()
        unlocked = await _get_unlocked(conn, uid)

    persona_name = _ID_TO_NAME.get(persona_id, persona_id)

    if not persona_engine.is_loaded():
        await matcher.finish(
            f"🎭 当前角色：{persona_name}\n\n"
            "发送 [#选择角色] 切换其他角色。"
        )

    card = persona_engine.get_persona(persona_id)
    if card is None:
        await matcher.finish(f"🎭 当前角色：{persona_name}")

    archetype = card.get("archetype", "")
    tagline = card.get("tagline", "")

    lines = [f"🎭 当前角色：{persona_name}"]
    if archetype:
        lines.append(f"风格：{archetype}")
    if tagline:
        lines.append(f"「{tagline}」")
    lines.append("")
    lines.append(f"已解锁角色：{'、'.join(_ID_TO_NAME.get(p, p) for p in unlocked)}")
    lines.append("发送 [#选择角色] 切换角色  ·  [#角色商店] 查看全部")

    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# #解锁角色
# ──────────────────────────────────────────────

unlock_persona_cmd = on_command("解锁角色", priority=5, block=True)


@unlock_persona_cmd.handle()
async def handle_unlock_persona(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#解锁角色 [名称] — 花费 80 积分解锁新角色"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    raw = sanitize_input(args.extract_plain_text())
    if not raw:
        await matcher.finish("用法：#解锁角色 [角色名称]\n发送 [#角色商店] 查看全部角色。")

    persona_id = _NAME_TO_ID.get(raw.strip())
    if not persona_id:
        await matcher.finish(
            f"未找到角色「{raw}」。\n"
            "发送 [#角色商店] 查看所有可解锁角色。"
        )

    persona_name = _ID_TO_NAME.get(persona_id, persona_id)

    if persona_id in _FREE_PERSONAS:
        await matcher.finish(f"「{persona_name}」是默认角色，无需解锁，直接发送 [#选择角色 {persona_name}] 即可切换。")

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        unlocked = await _get_unlocked(conn, uid)

        if persona_id in unlocked:
            await matcher.finish(
                f"「{persona_name}」已解锁。发送 [#选择角色 {persona_name}] 切换。"
            )

        ok = await spend(uid, PERSONA_UNLOCK_COST, "persona_unlock", db, ref_id=persona_id)
        if not ok:
            balance = await db.get_points_balance()
            await matcher.finish(
                f"积分不足。解锁「{persona_name}」需要 {PERSONA_UNLOCK_COST} 积分，"
                f"当前余额 {balance} 积分。\n"
                "发送 [#充值] 了解充值方式。"
            )

        unlocked.append(persona_id)
        await conn.execute(
            "UPDATE persona_config SET unlocked_personas = ? WHERE user_id = ?",
            (json.dumps(unlocked, ensure_ascii=False), uid),
        )
        await conn.commit()

    await matcher.finish(
        f"🎉 「{persona_name}」解锁成功！\n"
        f"发送 [#选择角色 {persona_name}] 立即切换。"
    )


# ──────────────────────────────────────────────
# #角色商店
# ──────────────────────────────────────────────

persona_shop_cmd = on_command("角色商店", priority=5, block=True)


@persona_shop_cmd.handle()
async def handle_persona_shop(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#角色商店 — 查看所有角色及解锁/价格状态"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        unlocked = await _get_unlocked(conn, uid)

    if not persona_engine.is_loaded():
        await matcher.finish("角色数据加载中，请稍后重试。")

    personas = persona_engine.get_persona_list()
    lines = ["🏪 角色商店", ""]

    for p in personas:
        pid = p["id"]
        is_unlocked = pid in unlocked
        price_str = "免费" if pid in _FREE_PERSONAS else f"{PERSONA_UNLOCK_COST} 积分"
        status = "✅ 已解锁" if is_unlocked else f"🔒 {price_str}"
        lines.append(f"{p['name']}  {p['archetype']}")
        lines.append(f"  「{p['tagline']}」")
        lines.append(f"  {status}")
        lines.append("")

    lines.append("发送 [#解锁角色 名称] 解锁  ·  [#选择角色 名称] 切换")
    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

async def _get_unlocked(conn, uid: str) -> list[str]:
    """从数据库读取已解锁角色列表"""
    cursor = await conn.execute(
        "SELECT unlocked_personas FROM persona_config WHERE user_id = ?",
        (uid,),
    )
    row = await cursor.fetchone()
    if row and row["unlocked_personas"]:
        try:
            return json.loads(row["unlocked_personas"])
        except (json.JSONDecodeError, TypeError):
            pass
    return ["kitty"]


def _build_persona_list() -> str:
    """构建角色列表展示文本"""
    if not persona_engine.is_loaded():
        return (
            "可选角色：咪咪 / 真学姐 / 卑弥呼 / 艾莉亚\n"
            "发送 [#选择角色 名称] 切换  ·  [#角色商店] 查看解锁状态"
        )

    personas = persona_engine.get_persona_list()
    lines = ["可选角色：", ""]
    for i, p in enumerate(personas, 1):
        lines.append(f"{i}. {p['name']}（{p['archetype']}）")
        lines.append(f"   「{p['tagline']}」")
        lines.append("")
    lines.append("用法：#选择角色 [名称]  ·  #角色商店 查看解锁状态")
    return "\n".join(lines)
