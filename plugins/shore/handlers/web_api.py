"""
官网 Web API — 让用户在官网与 bot 对话。

路由（注册到 NoneBot2 内置的 FastAPI app 上）：
  GET  /api/personas      — 角色列表（id/name/archetype/tagline/first_message）
  POST /api/chat          — 网页聊天（复用 core/ai_service 陪聊链路）
  POST /api/chat/reset    — 清空某个网页会话的历史

设计说明：
- 网页用户没有 QQ 号，用前端生成的随机 session_id 标识，
  经 hash_user_id() 哈希后作为内存 key，不落库、不存原始 session_id。
- 对话历史仅存内存（与 handlers/emotion.py 的 _chat_history 同策略），
  Bot 重启即清空。
- 所有用户输入经过 sanitize_input() 清洗。
"""

import logging
from collections import defaultdict
from datetime import date, datetime, timedelta

import nonebot
from nonebot.drivers import ReverseDriver
from pydantic import BaseModel, Field

from ..core import ai_service, persona_engine
from ..core.security import hash_user_id, sanitize_input
from ..core.user_db import UserDB, get_db_conn

logger = logging.getLogger("shore.web_api")


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    persona_id: str = "kitty"
    message: str = Field(min_length=1, max_length=500)


class ResetRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)


class PlanItemRequest(BaseModel):
    plan_date: str = Field(min_length=10, max_length=10)
    scheduled_time: str = Field(default="", max_length=20)
    subject_name: str = Field(default="自定义", max_length=50)
    topic_name: str = Field(min_length=1, max_length=120)
    estimated_minutes: int = Field(default=45, ge=1, le=600)
    status: str = Field(default="pending", max_length=16)
    notes: str = Field(default="", max_length=300)


class SavePlanRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    week_start: str = Field(min_length=10, max_length=10)
    items: list[PlanItemRequest] = Field(default_factory=list)


class PlanStatusRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    plan_id: int = Field(gt=0)
    status: str = Field(min_length=1, max_length=16)


class GeneratePlanRequest(BaseModel):
    session_id: str = Field(min_length=8, max_length=64)
    goal: str = Field(min_length=1, max_length=500)
    week_start: str | None = Field(default=None, max_length=10)
    daily_minutes: int = Field(default=120, ge=30, le=600)


# 网页会话历史：hashed_session_id → [{"role": "user"/"model", "text": "..."}]
_web_history: dict[str, list[dict[str, str]]] = defaultdict(list)
_MAX_HISTORY = 20
_MAX_SESSIONS = 500  # 简单防爆内存

VALID_PERSONAS = {"kitty", "makoto", "himiko", "alya"}
DEFAULT_PERSONA = "kitty"
_ROUTES_REGISTERED = False


def _web_uid(session_id: str) -> str:
    safe_session = sanitize_input(session_id, max_length=64)
    return hash_user_id(f"web:{safe_session}")


def _parse_iso_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _format_plan_item(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "plan_date": item.get("plan_date", ""),
        "scheduled_time": item.get("scheduled_time", ""),
        "subject_name": item.get("subject_name", ""),
        "topic_name": item.get("topic_name", ""),
        "estimated_minutes": item.get("estimated_minutes", 45),
        "status": item.get("status", "pending"),
        "notes": item.get("notes", "") or "",
    }


def _map_ai_plan_to_weekly_items(
    plan: list[dict],
    week_start_date: date,
) -> list[dict]:
    day_counters: dict[int, int] = {}
    items = []
    for entry in plan[:35]:
        try:
            plan_date = _parse_iso_date(str(entry.get("date", "")))
        except ValueError:
            day_num = int(entry.get("day", 1) or 1)
            plan_date = week_start_date + timedelta(days=day_num - 1)

        day_index = (plan_date - week_start_date).days
        if day_index < 0 or day_index > 6:
            continue

        order = day_counters.get(day_index, 0)
        day_counters[day_index] = order + 1

        topic_name = sanitize_input(str(entry.get("topic", "")), max_length=120)
        if not topic_name:
            continue

        subject_name = (
            sanitize_input(str(entry.get("subject", "学习")), max_length=50) or "学习"
        )
        scheduled_time = sanitize_input(str(entry.get("time", "")), max_length=20)
        notes = sanitize_input(str(entry.get("notes", "")), max_length=300)

        try:
            estimated_minutes = int(entry.get("estimated_minutes", 45))
        except (TypeError, ValueError):
            estimated_minutes = 45
        estimated_minutes = max(1, min(600, estimated_minutes))

        items.append(
            {
                "plan_date": plan_date.isoformat(),
                "day_index": day_index,
                "subject_id": None,
                "kp_id": None,
                "topic_name": topic_name,
                "subject_name": subject_name,
                "order_in_day": order,
                "estimated_minutes": estimated_minutes,
                "scheduled_time": scheduled_time,
                "notes": notes or None,
            }
        )

    return items


def _ensure_personas_loaded() -> None:
    if not persona_engine.is_loaded():
        try:
            count = persona_engine.load_personas()
            logger.info("web_api 懒加载人物卡: %d 个角色", count)
        except Exception as e:
            logger.warning("web_api 人物卡加载失败: %s", e)


def _build_system_prompt(persona_id: str) -> str:
    """根据角色卡构建陪聊 system prompt（与 handlers/emotion.py 保持一致）。"""
    _ensure_personas_loaded()

    persona_desc = ""
    card = None
    if persona_engine.is_loaded():
        card = persona_engine.get_persona(persona_id)
        if card:
            persona_desc = card.get("character_notes", "")

    if persona_desc and card:
        name = card.get("name", "助手")
        tone = card.get("tone_profile", {})
        self_ref = tone.get("self_ref", name)
        return (
            f"你是一个名叫【{name}】的学习陪伴助手。\n"
            f"你的人设：{persona_desc}\n\n"
            f"你自称【{self_ref}】。\n"
            "你现在正在官网网页上陪用户学习、计划和聊天，请保持人设回复。\n"
            "回复要简短自然（50字以内），像朋友之间聊天。\n"
            "如果用户表达了负面情绪，给予温暖的安慰和鼓励。\n"
            "不要使用 markdown 格式，用纯文本回复。"
        )

    return (
        "你是一个温暖的学习陪伴助手。\n"
        "回复要简短自然（50字以内），像朋友之间聊天。\n"
        "不要使用 markdown 格式，用纯文本回复。"
    )


def _register_routes() -> None:
    """将路由挂到 NoneBot2 内置的 FastAPI app 上。"""
    global _ROUTES_REGISTERED
    if _ROUTES_REGISTERED:
        return

    driver = nonebot.get_driver()
    if not isinstance(driver, ReverseDriver):
        logger.warning("当前 driver 非 ReverseDriver，跳过 web_api 注册")
        return

    try:
        from fastapi import FastAPI
        from fastapi import Query
        from fastapi.middleware.cors import CORSMiddleware
    except ImportError:
        logger.warning("fastapi/pydantic 不可用，跳过 web_api 注册")
        return

    app = nonebot.get_app()
    if not isinstance(app, FastAPI):
        logger.warning("nonebot.get_app() 不是 FastAPI 实例，跳过 web_api 注册")
        return

    # 允许本地开发前端跨域（Vite 默认 5173）
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
            "http://127.0.0.1:4173",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @driver.on_startup
    async def initialize_web_database():
        await UserDB.initialize_database()
        logger.info("数据库结构已初始化")

    @app.get("/api/personas")
    async def get_personas():
        _ensure_personas_loaded()
        result = []
        for item in persona_engine.get_persona_list():
            pid = item.get("id", "")
            card = persona_engine.get_persona(pid) or {}
            result.append(
                {
                    "id": pid,
                    "name": item.get("name", ""),
                    "archetype": item.get("archetype", ""),
                    "tagline": item.get("tagline", ""),
                    "first_message": card.get("first_message", ""),
                    "emoji_set": card.get("tone_profile", {}).get("emoji_set", []),
                }
            )
        return {"personas": result}

    @app.post("/api/chat")
    async def web_chat(req: ChatRequest):
        # 输入清洗 + 会话标识哈希（不存原始 session_id）
        message = sanitize_input(req.message, max_length=500)
        if not message:
            return {"ok": False, "error": "消息不能为空"}

        persona_id = (
            req.persona_id if req.persona_id in VALID_PERSONAS else DEFAULT_PERSONA
        )
        uid = _web_uid(req.session_id)

        # 防爆内存：会话数超限时清掉最早的一半
        if uid not in _web_history and len(_web_history) >= _MAX_SESSIONS:
            for key in list(_web_history.keys())[: _MAX_SESSIONS // 2]:
                _web_history.pop(key, None)

        system_prompt = _build_system_prompt(persona_id)
        try:
            reply = await ai_service.generate_chat_response(
                system_prompt=system_prompt,
                history=_web_history[uid][-_MAX_HISTORY:],
                user_message=message,
            )
        except Exception as e:
            logger.error("web 聊天 LLM 调用失败: %s", e)
            return {"ok": False, "error": "AI 服务暂时不可用，请稍后再试"}

        _web_history[uid].append({"role": "user", "text": message})
        _web_history[uid].append({"role": "model", "text": reply})
        # 截断历史，防止无限增长
        if len(_web_history[uid]) > _MAX_HISTORY * 2:
            _web_history[uid] = _web_history[uid][-_MAX_HISTORY:]

        return {"ok": True, "reply": reply, "persona_id": persona_id}

    @app.post("/api/chat/reset")
    async def reset_chat(req: ResetRequest):
        uid = _web_uid(req.session_id)
        _web_history.pop(uid, None)
        return {"ok": True}

    @app.get("/api/plan")
    async def get_web_plan(session_id: str = Query(min_length=8, max_length=64)):
        uid = _web_uid(session_id)
        async with get_db_conn() as conn:
            db = UserDB(uid, conn)
            await db.ensure_user_exists()
            week_start = await db.get_latest_week_start()
            if not week_start:
                return {"ok": True, "week_start": None, "items": []}

            items = await db.get_weekly_plan(week_start)
            return {
                "ok": True,
                "week_start": week_start,
                "items": [_format_plan_item(item) for item in items],
            }

    @app.put("/api/plan")
    async def save_web_plan(req: SavePlanRequest):
        uid = _web_uid(req.session_id)
        try:
            week_start_date = _parse_iso_date(
                sanitize_input(req.week_start, max_length=10)
            )
        except ValueError:
            return {"ok": False, "error": "week_start 格式应为 YYYY-MM-DD"}

        plan_items = []
        for order, item in enumerate(req.items[:35]):
            try:
                plan_date = _parse_iso_date(
                    sanitize_input(item.plan_date, max_length=10)
                )
            except ValueError:
                return {"ok": False, "error": "plan_date 格式应为 YYYY-MM-DD"}

            day_index = (plan_date - week_start_date).days
            if day_index < 0 or day_index > 6:
                return {"ok": False, "error": "计划日期必须在当前周的 7 天内"}

            topic_name = sanitize_input(item.topic_name, max_length=120)
            if not topic_name:
                return {"ok": False, "error": "计划内容不能为空"}
            status = sanitize_input(item.status, max_length=16)
            if status not in {"pending", "done", "skipped"}:
                status = "pending"

            plan_items.append(
                {
                    "plan_date": plan_date.isoformat(),
                    "day_index": day_index,
                    "subject_id": None,
                    "kp_id": None,
                    "topic_name": topic_name,
                    "subject_name": sanitize_input(item.subject_name, max_length=50)
                    or "自定义",
                    "order_in_day": order,
                    "estimated_minutes": item.estimated_minutes,
                    "scheduled_time": sanitize_input(
                        item.scheduled_time, max_length=20
                    ),
                    "notes": sanitize_input(item.notes, max_length=300) or None,
                    "status": status,
                    "completed_at": datetime.now().isoformat()
                    if status == "done"
                    else None,
                }
            )

        async with get_db_conn() as conn:
            db = UserDB(uid, conn)
            await db.ensure_user_exists()
            await db.save_weekly_plan(week_start_date.isoformat(), plan_items)
            saved = await db.get_weekly_plan(week_start_date.isoformat())

        return {
            "ok": True,
            "week_start": week_start_date.isoformat(),
            "items": [_format_plan_item(item) for item in saved],
        }

    @app.post("/api/plan/generate")
    async def generate_web_plan(req: GeneratePlanRequest):
        uid = _web_uid(req.session_id)
        goal = sanitize_input(req.goal, max_length=500)
        if not goal:
            return {"ok": False, "error": "请先输入想学习的内容"}

        try:
            week_start_date = (
                _parse_iso_date(sanitize_input(req.week_start, max_length=10))
                if req.week_start
                else date.today()
            )
        except ValueError:
            return {"ok": False, "error": "week_start 格式应为 YYYY-MM-DD"}

        plan = await ai_service.generate_web_study_plan(
            goal=goal,
            today=week_start_date.isoformat(),
            daily_minutes=req.daily_minutes,
        )
        if not plan:
            return {"ok": False, "error": "AI 暂时没生成出计划，请稍后重试"}

        plan_items = _map_ai_plan_to_weekly_items(plan, week_start_date)
        if not plan_items:
            return {"ok": False, "error": "AI 计划格式无效，请换个学习目标重试"}

        async with get_db_conn() as conn:
            db = UserDB(uid, conn)
            await db.ensure_user_exists()
            await db.save_weekly_plan(week_start_date.isoformat(), plan_items)
            saved = await db.get_weekly_plan(week_start_date.isoformat())

        return {
            "ok": True,
            "week_start": week_start_date.isoformat(),
            "items": [_format_plan_item(item) for item in saved],
        }

    @app.post("/api/plan/status")
    async def update_web_plan_status(req: PlanStatusRequest):
        uid = _web_uid(req.session_id)
        status = sanitize_input(req.status, max_length=16)
        if status not in {"pending", "done", "skipped"}:
            return {"ok": False, "error": "计划状态无效"}

        async with get_db_conn() as conn:
            db = UserDB(uid, conn)
            await db.ensure_user_exists()
            await db.update_weekly_plan_status(req.plan_id, status)

        return {"ok": True}

    logger.info(
        "web_api 路由注册完成: /api/personas, /api/chat, /api/chat/reset, /api/plan"
    )
    _ROUTES_REGISTERED = True


_register_routes()
