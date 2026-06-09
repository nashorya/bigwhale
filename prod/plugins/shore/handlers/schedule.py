"""
日程相关指令处理器 + 定时任务注册。
薄胶水层：解析输入 → 调用 core/ → 渲染结果。

本模块在启动时替换 scheduler.py 中的占位回调（replace_existing=True）：
  · 00:05 → 所有用户重新生成每日计划
  · 07:30 → 早安推送
  · 22:30 → 晚间复盘
"""

from __future__ import annotations

import asyncio
from datetime import date

from nonebot import on_command, get_bot, get_driver
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent
from nonebot.matcher import Matcher

from ..core.security import get_or_create_uid
from ..core.user_db import get_db_conn, UserDB, get_all_user_ids
from ..core.scheduler import Scheduler
from ..core import persona_engine
from .admin import check_banned


# ──────────────────────────────────────────────
# #生成计划 — 手动触发当日计划生成
# ──────────────────────────────────────────────

gen_plan_cmd = on_command("生成计划", priority=5, block=True)


@gen_plan_cmd.handle()
async def handle_gen_plan(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """手动触发当日计划生成"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 检查是否有科目和知识点
        subjects = await db.get_active_subjects()
        if not subjects:
            await matcher.finish(
                "你还没有配置科目和知识点。\n"
                "发送 [#同步 文件名.csv] 上传你的知识点库。"
            )

        all_kps = await db.get_all_knowledge_points()
        if not all_kps:
            await matcher.finish(
                "知识点库为空。\n"
                "发送 [#同步 文件名.csv] 上传你的知识点数据。"
            )

        plan = await Scheduler.generate_daily_plan(db)

        if not plan:
            await matcher.finish("知识点数据不足，无法生成计划。")

    await matcher.finish(
        f"✅ 今日学习计划已生成（{len(plan)} 个知识点）\n"
        f"发送 [#今日计划] 查看详情。"
    )


# ──────────────────────────────────────────────
# 定时任务：替换 scheduler.py 的占位回调
# ──────────────────────────────────────────────

async def daily_plan_job() -> None:
    """
    每日 00:05 — 为所有用户重新生成当日学习计划。
    替换 scheduler.py 中的 _placeholder_daily_plan_job。
    """
    async with get_db_conn() as conn:
        user_ids = await get_all_user_ids(conn)

    for uid in user_ids:
        try:
            async with get_db_conn() as conn:
                db = UserDB(uid, conn)
                await Scheduler.generate_daily_plan(db)
        except Exception:
            # 单个用户失败不影响其他用户
            pass


async def morning_push_job() -> None:
    """
    每日 07:30 — 早安推送。
    替换 scheduler.py 中的 _placeholder_morning_push_job。
    """
    try:
        bot: Bot = get_bot()  # type: ignore
    except ValueError:
        return  # Bot 未连接

    async with get_db_conn() as conn:
        user_ids = await get_all_user_ids(conn)

    for uid in user_ids:
        try:
            async with get_db_conn() as conn:
                db = UserDB(uid, conn)

                # 检查是否已完成初始化
                cursor = await conn.execute(
                    "SELECT init_complete, is_banned FROM users WHERE user_id = ?",
                    (uid,),
                )
                user = await cursor.fetchone()
                if not user or not user["init_complete"] or user["is_banned"]:
                    continue

                # 检查订阅状态
                cursor = await conn.execute(
                    "SELECT subscription_active FROM points_account WHERE user_id = ?",
                    (uid,),
                )
                account = await cursor.fetchone()
                if not account or not account["subscription_active"]:
                    continue

                # 注入 Memory 上下文
                memory_hint = ""
                try:
                    from ..core.memory_store import (
                        get_recent_memories,
                        format_memories_for_prompt,
                    )
                    recent = await get_recent_memories(conn, uid, days=2, limit=10)
                    if recent:
                        memory_hint = format_memories_for_prompt(recent)
                except Exception:
                    pass  # Memory 不可用时降级

                # 生成早安内容
                content = await Scheduler.generate_morning_content(db)
                if memory_hint:
                    content["memory_hint"] = memory_hint

                # 渲染角色口吻（只传安全的人类可读字段）
                persona_id = await db.get_active_persona()
                safe_data = {
                    "days_left": content.get("days_left", "?"),
                    "phase": _phase_label(content.get("phase", "foundation")),
                    "subject_summary": content.get("subject_summary", ""),
                    "suggestion": content.get("suggestion", ""),
                    "total_minutes": content.get("total_minutes", 0),
                }
                message = None
                if persona_engine.is_loaded():
                    try:
                        rendered = persona_engine.render(
                            persona_id, "morning_push", safe_data
                        )
                        # 如果 render 返回了 fallback 乱码格式（以 [ 开头），不使用
                        if rendered and not rendered.startswith("["):
                            message = rendered
                    except Exception:
                        pass
                if not message:
                    message = _format_morning_fallback(content)

            # 查找 QQ 号（从内存映射反查）
            qq = _uid_to_qq(uid)
            if qq:
                await bot.send_private_msg(user_id=int(qq), message=message)
                await asyncio.sleep(0.5)  # 避免发送过快

        except Exception:
            pass


async def evening_summary_job() -> None:
    """
    每日 22:30 — 晚间复盘推送。
    替换 scheduler.py 中的 _placeholder_evening_summary_job。
    """
    try:
        bot: Bot = get_bot()  # type: ignore
    except ValueError:
        return

    async with get_db_conn() as conn:
        user_ids = await get_all_user_ids(conn)

    for uid in user_ids:
        try:
            async with get_db_conn() as conn:
                db = UserDB(uid, conn)

                cursor = await conn.execute(
                    "SELECT init_complete, is_banned FROM users WHERE user_id = ?",
                    (uid,),
                )
                user = await cursor.fetchone()
                if not user or not user["init_complete"] or user["is_banned"]:
                    continue

                cursor = await conn.execute(
                    "SELECT subscription_active FROM points_account WHERE user_id = ?",
                    (uid,),
                )
                account = await cursor.fetchone()
                if not account or not account["subscription_active"]:
                    continue

                # 注入 Memory 上下文
                memory_hint = ""
                try:
                    from ..core.memory_store import (
                        get_daily_memories,
                        format_memories_for_prompt,
                    )
                    today_str = date.today().isoformat()
                    today_memories = await get_daily_memories(conn, uid, today_str)
                    if today_memories:
                        memory_hint = format_memories_for_prompt(today_memories)
                except Exception:
                    pass

                # 生成晚间复盘内容
                content = await Scheduler.generate_evening_content(db)
                if memory_hint:
                    content["memory_hint"] = memory_hint

                # 直接使用 fallback 格式（persona render 晚间模板不可靠）
                message = _format_evening_fallback(content)

            qq = _uid_to_qq(uid)
            if qq:
                await bot.send_private_msg(user_id=int(qq), message=message)
                await asyncio.sleep(0.5)

        except Exception:
            pass


# ──────────────────────────────────────────────
# 定时学习提醒（基于周计划的 scheduled_time）
# ──────────────────────────────────────────────

# 有效提醒时段列表
_REMINDER_TIMES = [
    "09:00", "10:00", "11:00",
    "14:00", "15:00", "16:00",
    "19:00", "20:00", "21:00",
]


async def study_reminder_job() -> None:
    """
    每 30 分钟运行一次 — 检查是否有周计划条目需要在当前时段提醒。
    匹配规则：当前时间在某个 scheduled_time ±15分钟窗口内。
    """
    from datetime import datetime

    print("[提醒Job] 触发执行")

    try:
        bot: Bot = get_bot()  # type: ignore
    except ValueError:
        print("[提醒Job] Bot 未连接，跳过")
        return

    now = datetime.now()
    today_str = now.date().isoformat()
    print(f"[提醒Job] 当前时间: {now.strftime('%H:%M:%S')}, 日期: {today_str}")

    # 找到当前最接近的提醒时段（±15分钟窗口）
    matched_time = None
    for t in _REMINDER_TIMES:
        hour, minute = int(t[:2]), int(t[3:])
        target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        diff = abs((now - target).total_seconds())
        if diff <= 15 * 60:  # 15分钟窗口
            matched_time = t
            break

    if not matched_time:
        print(f"[提醒Job] 当前不在任何提醒时段，跳过")
        return

    print(f"[提醒Job] 匹配到时段: {matched_time}")

    # 遍历所有用户，检查是否有待提醒
    async with get_db_conn() as conn:
        user_ids = await get_all_user_ids(conn)

    print(f"[提醒Job] 找到 {len(user_ids)} 个用户")

    for uid in user_ids:
        try:
            async with get_db_conn() as conn:
                db = UserDB(uid, conn)

                # 检查订阅状态
                cursor = await conn.execute(
                    "SELECT subscription_active FROM points_account WHERE user_id = ?",
                    (uid,),
                )
                account = await cursor.fetchone()
                if not account or not account["subscription_active"]:
                    print(f"[提醒Job] 用户 {uid[:8]}... 未订阅，跳过")
                    continue

                # 查询待提醒条目
                items = await db.get_pending_reminders(today_str, matched_time)
                if not items:
                    print(f"[提醒Job] 用户 {uid[:8]}... 无待提醒条目 (date={today_str}, time={matched_time})")
                    continue

                print(f"[提醒Job] 用户 {uid[:8]}... 有 {len(items)} 个待提醒")

                # 获取角色卡
                persona_id = await db.get_active_persona()

                # 构建学习任务描述
                task_lines = []
                for item in items:
                    mins = item.get("estimated_minutes", 60)
                    notes = item.get("notes", "")
                    note_str = f"（{notes}）" if notes else ""
                    task_lines.append(
                        f"{item['subject_name']}：{item['topic_name']}（约{mins}分钟）{note_str}"
                    )
                task_desc = "\n".join(task_lines)

                # 用 LLM + 角色卡生成个性化提醒
                message = None
                try:
                    from ..core import ai_service
                    import os

                    # 获取角色卡人设
                    persona_desc = ""
                    if persona_engine.is_loaded():
                        try:
                            card = persona_engine.get_persona(persona_id)
                            if card:
                                persona_desc = card.get("character_notes", "")
                        except Exception:
                            pass

                    system_prompt = (
                        f"你是一个考研备考陪伴助手。\n"
                        f"{f'你的人设：{persona_desc}' if persona_desc else ''}\n"
                        f"请用你的人设风格提醒用户该学习了。\n"
                        f"回复要简短活泼（80字以内），带上鼓励。\n"
                        f"不要使用 markdown 格式。\n"
                        f"必须提到具体要学的科目和知识点。"
                    )

                    user_msg = (
                        f"现在是 {matched_time}，请提醒我学习以下内容：\n{task_desc}\n"
                        f"完成后可以发送 [#完成 科目名] 标记完成。"
                    )

                    # 使用独立的提醒模型
                    reminder_model = os.environ.get("REMINDER_MODEL", "gemini-2.5-flash-lite")
                    gemini_client = ai_service._get_gemini_openai_client()
                    import asyncio as _asyncio
                    resp = await _asyncio.wait_for(
                        gemini_client.chat.completions.create(
                            model=reminder_model,
                            messages=[
                                {"role": "system", "content": system_prompt},
                                {"role": "user", "content": user_msg},
                            ],
                            temperature=0.8,
                            max_tokens=200,
                        ),
                        timeout=15,
                    )
                    if resp.choices:
                        message = resp.choices[0].message.content
                except Exception as e:
                    print(f"[提醒] LLM 生成失败: {e}")

                # LLM 失败时用纯文本兜底
                if not message:
                    lines = [f"📖 [{matched_time}] 该学习了！", ""]
                    for item in items:
                        mins = item.get("estimated_minutes", 60)
                        notes = item.get("notes", "")
                        note_str = f"（{notes}）" if notes else ""
                        lines.append(
                            f"  · {item['subject_name']}：{item['topic_name']}（约{mins}分钟）{note_str}"
                        )
                    lines.append("")
                    lines.append("完成后发送 [#完成 科目名]")
                    message = "\n".join(lines)

                # 标记已提醒
                for item in items:
                    await db.mark_reminder_sent(item["id"])

            # 发送消息
            qq = _uid_to_qq(uid)
            if qq:
                await bot.send_private_msg(
                    user_id=int(qq), message=message
                )
                await asyncio.sleep(0.3)

        except Exception:
            pass  # 单用户失败不影响其他


# ──────────────────────────────────────────────
# 启动时注册真实回调（覆盖占位函数）
# ──────────────────────────────────────────────

def register_real_jobs() -> None:
    """
    在 Bot 启动后调用，注册所有定时任务到全局 scheduler。
    使用单例 AsyncIOScheduler，确保任务正确运行。
    """
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger

        global _scheduler
        if _scheduler is None:
            _scheduler = AsyncIOScheduler(
                job_defaults={"misfire_grace_time": 300}  # 5分钟宽限期
            )

        # 注册定时任务
        _scheduler.add_job(
            daily_plan_job,
            CronTrigger(hour=0, minute=5),
            id="daily_plan_gen",
            replace_existing=True,
        )

        _scheduler.add_job(
            morning_push_job,
            CronTrigger(hour=7, minute=30),
            id="morning_push",
            replace_existing=True,
        )

        _scheduler.add_job(
            evening_summary_job,
            CronTrigger(hour=22, minute=30),
            id="evening_summary",
            replace_existing=True,
        )

        # 学习提醒（每30分钟检查）
        _scheduler.add_job(
            study_reminder_job,
            CronTrigger(minute="0,30"),
            id="study_reminder",
            replace_existing=True,
        )

        # 自动推词（每45分钟）
        from .word_push import auto_push_words_job
        from apscheduler.triggers.interval import IntervalTrigger
        _scheduler.add_job(
            auto_push_words_job,
            IntervalTrigger(minutes=25),
            id="auto_word_push",
            replace_existing=True,
        )

        if not _scheduler.running:
            _scheduler.start()

        # 打印已注册的任务，便于调试
        jobs = _scheduler.get_jobs()
        print(f"[调度器] 已启动，注册了 {len(jobs)} 个定时任务：")
        for job in jobs:
            print(f"  - {job.id}: {job.trigger}")

    except ImportError:
        print("[调度器] 警告：APScheduler 未安装，定时任务不可用")


# 全局 scheduler 单例
_scheduler = None


# 注册 NoneBot2 启动事件
driver = get_driver()


@driver.on_startup
async def _on_startup():
    """Bot 启动时注册定时任务"""
    register_real_jobs()

    # 加载 PersonaEngine
    if not persona_engine.is_loaded():
        persona_engine.load_personas()


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

def _uid_to_qq(uid: str) -> str | None:
    """
    从 security 模块的内存映射中反查 QQ 号。
    如果找不到（Bot 重启后未收到该用户消息），返回 None。
    """
    from ..core.security import _qq_to_uid
    for qq, mapped_uid in _qq_to_uid.items():
        if mapped_uid == uid:
            return qq
    return None


def _phase_label(phase: str) -> str:
    """将备考阶段英文标识转为中文标签"""
    return {
        "foundation": "基础阶段",
        "intensify": "强化阶段",
        "sprint": "冲刺阶段",
        "sprint_final": "最后冲刺",
    }.get(phase, phase)


def _format_morning_fallback(content: dict) -> str:
    """早安推送默认格式（自然语言，不暴露内部数据）"""
    days_left = content.get("days_left", "?")
    phase = _phase_label(content.get("phase", "foundation"))
    suggestion = content.get("suggestion", "")
    total_minutes = content.get("total_minutes", 0)
    subject_stats = content.get("subject_stats", {})

    lines = [
        "☀️ 早安！新的一天，继续加油！",
        "",
        f"📅 距考试还有 {days_left} 天 · {phase}",
        f"📖 今日计划约 {total_minutes} 分钟",
    ]

    # 各科进度摘要（自然语言）
    if subject_stats:
        lines.append("")
        lines.append("📊 各科进度：")
        for name, stats in subject_stats.items():
            avg = stats.get("avg_mastery", 0)
            total = stats.get("total", 0)
            mastered = stats.get("mastered_count", 0)
            # 用星级表示掌握度
            stars = "⭐" * min(int(avg), 5)
            if not stars:
                stars = "☆"
            lines.append(f"  · {name}：{stars}（已掌握 {mastered}/{total}）")

    if suggestion:
        lines.append("")
        lines.append(f"💡 {suggestion}")

    lines.append("")
    lines.append("发送 #今日计划 查看详情 📋")
    return "\n".join(lines)


def _format_evening_fallback(content: dict) -> str:
    """晚间复盘默认格式"""
    rate = content.get("rate", 0)
    done = content.get("done_count", 0)
    total = content.get("total_count", 0)
    streak = content.get("streak", 0)
    top3 = content.get("top3", "暂无")
    days_left = content.get("days_left", "?")

    lines = [
        "🌙 今日复盘",
        "",
        f"📊 今日完成：{done}/{total}（{rate}%）",
    ]

    if done == total and total > 0:
        lines.append("🎉 太棒了！今天全部完成！")
    elif rate >= 80:
        lines.append("👍 表现不错，继续加油！")
    elif rate >= 50:
        lines.append("💪 完成了一半以上，明天再努努力！")
    elif total > 0:
        lines.append("🙌 明天提高一点就好，继续加油！")

    if streak > 0:
        lines.append(f"🔥 连续打卡：{streak} 天")

    if content.get("improved_list"):
        improved = content["improved_list"][:3]
        lines.append(f"⬆️ 掌握度提升：{'、'.join(improved)}")

    lines.append("")
    lines.append(f"📝 明日重点：{top3}")
    lines.append(f"⛳ 距考试还有 {days_left} 天")
    lines.append("")
    lines.append("好好休息，明天见！😴")
    return "\n".join(lines)
