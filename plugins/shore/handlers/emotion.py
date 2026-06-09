"""
情绪陪伴相关指令处理器 — LLM 多轮对话版。

指令集：
  #陪我聊 [内容]  — 主动开启情绪陪伴会话（消耗 5 积分）
  #结束聊天       — 结束当前情绪陪伴会话

被动监听：
  on_message(priority=10, block=False) — 已在陪伴模式中时响应所有消息，
  或检测情绪信号词自动触发。
"""

from __future__ import annotations

import logging
from collections import defaultdict

from nonebot import on_command, on_message
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.exception import StopPropagation
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..core import ai_service, emotion_detector, persona_engine
from ..core.points_service import spend
from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import UserDB, get_db_conn
from .admin import check_banned

logger = logging.getLogger("shore.emotion")

# 情绪陪伴每次消耗积分
EMOTION_SESSION_COST = 5

# 内存中的对话历史（uid → list[{"role": "user"/"model", "text": "..."}]）
# 会话结束或 Bot 重启后自动清空
_chat_history: dict[str, list[dict[str, str]]] = defaultdict(list)

# 历史消息最大保留条数（防止 token 爆炸）
_MAX_HISTORY = 20


def _build_system_prompt(persona_id: str) -> str:
    """根据角色卡构建陪聊 system prompt"""
    # 懒加载人物卡（首次调用时自动加载）
    if not persona_engine.is_loaded():
        try:
            count = persona_engine.load_personas()
            logger.info("懒加载人物卡成功: %d 个角色", count)
        except Exception as e:
            logger.warning("人物卡加载失败: %s", e)

    # 尝试从角色卡获取人设
    persona_desc = ""
    if persona_engine.is_loaded():
        try:
            card = persona_engine.get_persona(persona_id)
            if card:
                persona_desc = card.get("character_notes", "")
                logger.info("加载角色人设: %s (%s)", card.get("name", ""), persona_id)
        except Exception:
            pass

    if persona_desc:
        # 获取角色名和口癖等信息
        card = persona_engine.get_persona(persona_id)
        name = card.get("name", "助手") if card else "助手"
        tone = card.get("tone_profile", {}) if card else {}
        self_ref = tone.get("self_ref", name)
        return (
            f"你是一个名叫【{name}】的考研备考陪伴助手。\n"
            f"你的人设：{persona_desc}\n\n"
            f"你自称【{self_ref}】。\n"
            "你现在正在陪用户聊天，请保持人设回复。\n"
            "回复要简短自然（50字以内），像朋友之间聊天。\n"
            "如果用户表达了负面情绪，给予温暖的安慰和鼓励。\n"
            "不要使用 markdown 格式，用纯文本回复。"
        )

    return (
        "你是一个温暖的考研备考陪伴助手。\n"
        "你现在正在陪用户聊天。\n"
        "回复要简短自然（50字以内），像朋友之间聊天。\n"
        "如果用户表达了负面情绪，给予温暖的安慰和鼓励。\n"
        "不要使用 markdown 格式，用纯文本回复。"
    )


# ──────────────────────────────────────────────
# #陪我聊
# ──────────────────────────────────────────────

talk_cmd = on_command("陪我聊", priority=5, block=True)


@talk_cmd.handle()
async def handle_talk(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#陪我聊 [内容] — 主动开启情绪陪伴"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    content = sanitize_input(args.extract_plain_text(), max_length=200)

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 检查是否已在陪伴模式中
        already_in = await emotion_detector.is_in_companion_mode(db)
        if already_in:
            # 已在陪伴模式，直接用 LLM 回复
            persona_id = await db.get_active_persona()
            if content:
                system_prompt = _build_system_prompt(persona_id)
                response = await ai_service.generate_chat_response(
                    system_prompt=system_prompt,
                    history=_chat_history[uid][-_MAX_HISTORY:],
                    user_message=content,
                )
                _chat_history[uid].append({"role": "user", "text": content})
                _chat_history[uid].append({"role": "model", "text": response})
            else:
                response = "说说你想聊什么吧~"
            await matcher.finish(response)

        # 扣费
        ok = await spend(uid, EMOTION_SESSION_COST, "emotion_session", db)
        if not ok:
            balance = await db.get_points_balance()
            await matcher.finish(
                f"积分不足（需要 {EMOTION_SESSION_COST} 积分，当前 {balance} 积分）。\n"
                "发送 [#充值] 了解充值方式。"
            )

        # 开启陪伴会话
        await emotion_detector.start_session(
            uid,
            "command",
            db,
            trigger_detail=content or "(主动指令)",
        )

        persona_id = await db.get_active_persona()

    # 清空历史（新会话）
    _chat_history[uid] = []

    # 用 LLM 生成开场回复
    system_prompt = _build_system_prompt(persona_id)
    opening_msg = content or "我想聊聊天"

    response = await ai_service.generate_chat_response(
        system_prompt=system_prompt,
        history=[],
        user_message=opening_msg,
    )

    # 记录到历史
    _chat_history[uid].append({"role": "user", "text": opening_msg})
    _chat_history[uid].append({"role": "model", "text": response})

    # 追加提示
    response += "\n\n（发送 [#结束聊天] 退出陪伴模式）"
    await matcher.finish(response)


# ──────────────────────────────────────────────
# #结束聊天
# ──────────────────────────────────────────────

end_talk_cmd = on_command("结束聊天", aliases={"退出聊天"}, priority=5, block=True)


@end_talk_cmd.handle()
async def handle_end_talk(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#结束聊天 / #退出聊天 — 结束情绪陪伴会话"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        in_mode = await emotion_detector.is_in_companion_mode(db)

        if not in_mode:
            await matcher.finish("当前未在陪伴模式中。")

        await emotion_detector.end_session(uid, db)

    # 清空聊天历史
    _chat_history.pop(uid, None)

    # 固定告别语（不依赖 LLM，避免超时）
    await matcher.finish("好的，陪伴模式已结束啦～有需要随时再来找我聊天哦！")


# ──────────────────────────────────────────────
# 被动消息监听（陪伴模式 + 情绪检测）
# ──────────────────────────────────────────────

# priority=10 低于命令（priority=5），block=False 不阻断后续处理器
emotion_listener = on_message(priority=10, block=False)


@emotion_listener.handle()
async def handle_emotion_listener(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """
    被动监听私聊消息：
    1. 已在陪伴模式 → 用 LLM 多轮对话回复
    2. 检测到情绪信号 → 自动触发陪伴
    """
    uid = get_or_create_uid(str(event.user_id))

    text = event.get_plaintext().strip()
    if not text:
        return

    # 跳过指令消息（以 # 开头）
    if text.startswith("#"):
        return

    # 如果已在陪伴模式中，用 LLM 回复
    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        if await emotion_detector.is_in_companion_mode(db):
            persona_id = await db.get_active_persona()
            system_prompt = _build_system_prompt(persona_id)

            response = await ai_service.generate_chat_response(
                system_prompt=system_prompt,
                history=_chat_history[uid][-_MAX_HISTORY:],
                user_message=text,
            )

            _chat_history[uid].append({"role": "user", "text": text})
            _chat_history[uid].append({"role": "model", "text": response})

            await bot.send(event, response)
            raise StopPropagation  # 阻止 catch-all handler 再次触发

    # 检测情绪信号
    triggered, category = emotion_detector.detect(text)
    if not triggered:
        return

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 封禁检查
        cursor = await conn.execute(
            "SELECT is_banned FROM users WHERE user_id = ?", (uid,)
        )
        row = await cursor.fetchone()
        if row and row["is_banned"]:
            return

        # 已在陪伴模式中，不重复触发
        if await emotion_detector.is_in_companion_mode(db):
            return

        # 余额检查
        balance = await db.get_points_balance()
        if balance < EMOTION_SESSION_COST:
            return  # 静默跳过

        # 扣费
        ok = await spend(uid, EMOTION_SESSION_COST, "emotion_session", db)
        if not ok:
            return

        # 获取详细信号用于记录
        detail = emotion_detector.detect_detailed(text)

        # 开启陪伴会话
        await emotion_detector.start_session(
            uid,
            "user_confide",
            db,
            trigger_detail=f"category={category}",
            mood_signal=detail.get("matched_words"),
        )

        persona_id = await db.get_active_persona()

    # 清空历史（新会话）
    _chat_history[uid] = []

    # 用 LLM 生成关怀回复
    system_prompt = _build_system_prompt(persona_id)
    response = await ai_service.generate_chat_response(
        system_prompt=system_prompt,
        history=[],
        user_message=text,
    )

    _chat_history[uid].append({"role": "user", "text": text})
    _chat_history[uid].append({"role": "model", "text": response})

    response += "\n\n（发送 [#结束聊天] 退出陪伴模式）"
    await bot.send(event, response)
    raise StopPropagation  # 阻止 catch-all handler
