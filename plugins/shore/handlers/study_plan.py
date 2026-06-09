"""
学期备考计划指令处理器 — AI 驱动的备考规划。

指令集：
  #生成备考计划          — 多轮会话：课表→置信度→大纲→学期规划→赠送月计划
  #查看备考计划          — 查看当前学期规划摘要
  #本月目标              — 查看当前月的学习目标
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date, datetime
from typing import Any

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, Message, MessageSegment
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.typing import T_State

from ..core.points_service import spend
from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import get_db_conn, UserDB
from ..core import ai_service
from ..core.scheduler import Scheduler
from .admin import check_banned

logger = logging.getLogger("shore.study_plan")

# 生成备考计划的积分费用
_PLAN_COST = int(os.environ.get("SEMESTER_PLAN_COST", "80"))


# ──────────────────────────────────────────────
# #生成备考计划（多轮会话）
# ──────────────────────────────────────────────

gen_study_plan_cmd = on_command(
    "生成备考计划", aliases={"生成学期规划"}, priority=5, block=True
)


@gen_study_plan_cmd.handle()
async def _step0_check(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 0 步：检查前置条件"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    state["uid"] = uid
    state["qq_id"] = event.user_id

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 检查科目
        subjects = await db.get_active_subjects()
        if not subjects:
            await matcher.finish(
                "你还没有配置科目和知识点。\n"
                "请先完成初始化向导（发送 [#开始]）。"
            )

        # 获取知识点
        all_kps = await db.get_all_knowledge_points()
        if not all_kps:
            await matcher.finish(
                "知识点库为空，无法生成规划。\n"
                "请先通过初始化向导导入知识点。"
            )

        # 获取院校信息
        cursor = await conn.execute(
            "SELECT school_name, major_name FROM target_schools "
            "WHERE user_id = ? AND is_primary = 1 LIMIT 1",
            (uid,),
        )
        school_row = await cursor.fetchone()
        if not school_row:
            cursor = await conn.execute(
                "SELECT school_name, major_name FROM target_schools "
                "WHERE user_id = ? LIMIT 1",
                (uid,),
            )
            school_row = await cursor.fetchone()

        state["school"] = school_row["school_name"] if school_row else "目标院校"
        state["major"] = school_row["major_name"] if school_row else "目标专业"

        # 获取考试日期
        exam_date_str = await db.get_exam_date()
        if not exam_date_str:
            await matcher.finish(
                "未设置考试日期。\n"
                "请先完成初始化向导（发送 [#开始]）。"
            )

        exam_date = date.fromisoformat(exam_date_str)
        days_left = (exam_date - date.today()).days
        if days_left <= 0:
            await matcher.finish("考试日期已过，请更新考试日期后再生成规划。")

        state["exam_date_str"] = exam_date_str
        state["days_left"] = days_left

        # 构建科目+知识点摘要
        subjects_with_kps = []
        subject_names = []
        for s in subjects:
            kps = await db.get_knowledge_points(s["id"])
            subjects_with_kps.append({
                "name": s["name"],
                "category": s.get("category", ""),
                "knowledge_points": [kp["topic_name"] for kp in kps],
            })
            subject_names.append(s["name"])
        state["subjects_with_kps"] = subjects_with_kps
        state["subject_names"] = subject_names

        # 检查积分
        balance = await db.get_points_balance()
        if balance < _PLAN_COST:
            await matcher.finish(
                f"积分不足（需要 {_PLAN_COST} 积分，当前 {balance} 积分）。\n"
                "发送 [#充值] 了解充值方式。"
            )

    # 询问是否上传课表
    await matcher.pause(
        "📋 生成备考计划前，是否要上传你的大学课表？\n"
        "上传后 AI 会避开上课时间安排复习。\n"
        "\n"
        "回复 [是] 上传课表\n"
        "回复 [否] 跳过，直接进入下一步"
    )


@gen_study_plan_cmd.handle()
async def _step1_timetable_choice(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 1 步：用户选择是否上传课表"""
    reply = event.get_plaintext().strip()

    if reply in ("是", "yes", "Y", "y", "1"):
        state["want_timetable"] = True
        await matcher.pause(
            "📅 你的课表是几月到几月的？\n"
            "（例如回复：3-6月）"
        )
    else:
        state["want_timetable"] = False
        state["timetable_prompt"] = ""


@gen_study_plan_cmd.handle()
async def _step2_timetable_months(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 2 步：获取课表月份"""
    if not state.get("want_timetable"):
        return
    if state.get("timetable_months"):
        return

    months_text = event.get_plaintext().strip()
    state["timetable_months"] = months_text
    await matcher.pause(
        f"好的，{months_text}课表。\n"
        "请发送课表文件（支持 xlsx 或图片）👇"
    )


@gen_study_plan_cmd.handle()
async def _step3_timetable_upload(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 3 步：接收课表文件并解析"""
    if not state.get("want_timetable"):
        return
    if state.get("timetable_parsed"):
        return

    uid = state["uid"]
    months = state.get("timetable_months", "")
    msg = event.get_message()

    timetable_data = None
    source_type = ""

    # 检查图片消息
    for seg in msg:
        if seg.type == "image":
            url = seg.data.get("url", "")
            if url:
                await bot.send_private_msg(
                    user_id=event.user_id,
                    message="📷 收到图片课表，正在用 AI 识别…"
                )
                try:
                    import httpx
                    async with httpx.AsyncClient(timeout=30) as client:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        image_bytes = resp.content
                    content_type = resp.headers.get("content-type", "image/jpeg")
                    from ..core.timetable_parser import parse_image_timetable
                    timetable_data = await parse_image_timetable(image_bytes, content_type)
                    source_type = "image"
                except Exception as e:
                    logger.error("图片课表识别失败: %s", e)
                    await bot.send_private_msg(
                        user_id=event.user_id,
                        message=f"❌ 图片识别失败：{e}\n请重新发送，或回复 [跳过]。"
                    )
                    await matcher.reject()
            break

    # 检查文件消息
    if not timetable_data:
        for seg in msg:
            if seg.type == "file":
                file_info = seg.data
                try:
                    file_data = await bot.call_api(
                        "get_file",
                        file_id=file_info.get("file_id", file_info.get("id", "")),
                    )
                    file_path = file_data.get("file", file_data.get("path", ""))
                    if file_path and file_path.endswith(".xlsx"):
                        from ..core.timetable_parser import parse_xlsx_timetable
                        timetable_data = parse_xlsx_timetable(file_path)
                        source_type = "xlsx"
                except Exception as e:
                    logger.error("文件课表解析失败: %s", e)
                    await bot.send_private_msg(
                        user_id=event.user_id,
                        message=f"❌ 文件解析失败：{e}\n请重新发送，或回复 [跳过]。"
                    )
                    await matcher.reject()
                break

    # 跳过
    if not timetable_data:
        text = event.get_plaintext().strip()
        if text in ("跳过", "skip", "否", "no"):
            state["timetable_prompt"] = ""
            state["timetable_parsed"] = True
            return
        if not timetable_data:
            await bot.send_private_msg(
                user_id=event.user_id,
                message="请发送课表图片或 xlsx 文件。\n回复 [跳过] 可跳过。"
            )
            await matcher.reject()

    # 保存课表
    from ..core.timetable_parser import format_busy_desc, make_timetable_prompt_section
    busy_desc = format_busy_desc(timetable_data)
    timetable_prompt = make_timetable_prompt_section(timetable_data, months)

    async with get_db_conn() as conn:
        await conn.execute(
            """INSERT INTO user_timetable
               (user_id, timetable_months, timetable_json, free_desc, source_type)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, months, json.dumps(timetable_data, ensure_ascii=False),
             busy_desc, source_type),
        )
        await conn.commit()

    busy_count = len(timetable_data.get("busy", []))
    await bot.send_private_msg(
        user_id=event.user_id,
        message=f"✅ 课表解析成功！检测到 {busy_count} 个有课时段。"
    )
    state["timetable_prompt"] = timetable_prompt
    state["timetable_parsed"] = True


@gen_study_plan_cmd.handle()
async def _step4_confidence_check(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 4 步：置信度自评 — 告知用户哪些科目需要补大纲"""
    if state.get("confidence_done"):
        return

    school = state["school"]
    subject_names = state["subject_names"]

    await bot.send_private_msg(
        user_id=state["qq_id"],
        message="🧠 正在评估各科目知识点置信度…"
    )

    confidence = await ai_service.check_subject_confidence(subject_names, school)
    low_subjects = [s for s, c in confidence.items() if str(c).lower() == "low"]

    # 展示结果
    lines = ["📊 科目置信度评估结果：", ""]
    for subj, level in confidence.items():
        if str(level).lower() == "high":
            lines.append(f"  ✅ {subj}：把握充足")
        else:
            lines.append(f"  ⚠️ {subj}：把握不足，建议补充考纲")
    lines.append("")

    state["low_subjects"] = low_subjects
    state["confidence_done"] = True

    if low_subjects:
        low_str = "、".join(low_subjects)
        lines.append(
            f"以下科目可能是自命题或冷门，建议补充考纲：{low_str}\n\n"
            "回复 [是] 上传考纲（文本或文件）\n"
            "回复 [否] 跳过，AI 将依据猜测生成"
        )
        await bot.send_private_msg(
            user_id=state["qq_id"], message="\n".join(lines)
        )
        await matcher.pause()
    else:
        lines.append("所有科目置信度良好，无需补充考纲。")
        await bot.send_private_msg(
            user_id=state["qq_id"], message="\n".join(lines)
        )
        state["syllabi"] = {}
        state["syllabus_done"] = True


@gen_study_plan_cmd.handle()
async def _step5_syllabus_choice(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 5 步：用户选择是否上传大纲"""
    if state.get("syllabus_done"):
        return
    if not state.get("low_subjects"):
        state["syllabi"] = {}
        state["syllabus_done"] = True
        return

    reply = event.get_plaintext().strip()

    if reply in ("是", "yes", "Y", "y", "1"):
        low_str = "、".join(state["low_subjects"])
        await matcher.pause(
            f"📝 请发送以下科目的考纲文本（直接粘贴即可）：\n"
            f"科目：{low_str}\n\n"
            "格式不限，纯文本/大纲要点均可。\n"
            "回复 [跳过] 可跳过。"
        )
    else:
        state["syllabi"] = {}
        state["syllabus_done"] = True


@gen_study_plan_cmd.handle()
async def _step6_syllabus_upload(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 6 步：接收大纲文本"""
    if state.get("syllabus_done"):
        return

    text = event.get_plaintext().strip()

    if text in ("跳过", "skip"):
        state["syllabi"] = {}
        state["syllabus_done"] = True
        return

    if text:
        # 将大纲文本关联到所有低置信科目
        syllabi = {}
        for subj in state.get("low_subjects", []):
            syllabi[subj] = text
        state["syllabi"] = syllabi
        state["syllabus_done"] = True

        await bot.send_private_msg(
            user_id=state["qq_id"],
            message=f"✅ 已收到考纲文本（{len(text)} 字），将注入 AI 生成。"
        )
    else:
        await bot.send_private_msg(
            user_id=state["qq_id"],
            message="请发送考纲文本，或回复 [跳过]。"
        )
        await matcher.reject()


@gen_study_plan_cmd.handle()
async def _step7_generate(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, state: T_State
):
    """第 7 步：并发生成 知识清单 + 学期计划 + 赠送月计划"""
    uid = state["uid"]
    school = state["school"]
    major = state["major"]
    exam_date_str = state["exam_date_str"]
    days_left = state["days_left"]
    subjects_with_kps = state["subjects_with_kps"]
    subject_names = state["subject_names"]
    timetable_prompt = state.get("timetable_prompt", "")
    syllabi = state.get("syllabi", {})

    # 扣费
    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        ok = await spend(uid, _PLAN_COST, "semester_plan_generation", db)
        if not ok:
            balance = await db.get_points_balance()
            await matcher.finish(
                f"积分不足（需要 {_PLAN_COST} 积分，当前 {balance} 积分）。\n"
                "发送 [#充值] 了解充值方式。"
            )

    await bot.send_private_msg(
        user_id=state["qq_id"],
        message=(
            f"🤖 正在并发生成学期规划 + 核心知识清单…\n"
            f"📚 {school} · {major}\n"
            f"📅 距考试 {days_left} 天\n"
            f"{'📝 已注入考纲' if syllabi else ''}\n"
            "（Claude AI 生成约需 30-60 秒，请稍候）"
        ),
    )

    # 并发生成：学期计划 + 知识清单
    plan_task = ai_service.generate_semester_plan(
        school=school,
        major=major,
        exam_date=exam_date_str,
        days_left=days_left,
        subjects=subjects_with_kps,
    )
    checklist_task = ai_service.generate_knowledge_checklist(
        subjects=subject_names,
        school=school,
        syllabi=syllabi if syllabi else None,
    )

    plan, checklist = await asyncio.gather(plan_task, checklist_task)

    if not plan:
        await matcher.finish(
            "😔 AI 生成规划失败，请稍后重试。\n"
            f"已退还 {_PLAN_COST} 积分。"
        )

    # 写入数据库
    current_month = date.today().strftime("%Y-%m")
    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 版本号
        cursor = await conn.execute(
            "SELECT MAX(plan_version) as max_ver FROM study_plan WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        new_version = (row["max_ver"] or 0) + 1 if row else 1

        # 写入 study_plan
        await conn.execute(
            """INSERT INTO study_plan
               (user_id, plan_version, plan_json, exam_date, total_months, model_used)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, new_version, json.dumps(plan, ensure_ascii=False),
             exam_date_str, len(plan.get("phases", [])), "claude-sonnet-4-6"),
        )

        # 写入月目标
        goal_count = 0
        for phase in plan.get("phases", []):
            for goal in phase.get("goals", []):
                await conn.execute(
                    """INSERT INTO monthly_goals
                       (user_id, plan_version, month, subject_name,
                        goal_title, goal_detail, priority)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (uid, new_version, goal.get("month", ""),
                     goal.get("subject", ""), goal.get("title", ""),
                     goal.get("detail", ""), goal.get("priority", 2)),
                )
                goal_count += 1

        # 写入知识清单
        checklist_count = 0
        if checklist:
            for item in checklist:
                await conn.execute(
                    """INSERT INTO knowledge_checklist
                       (user_id, plan_version, subject, topic,
                        importance, suggested_month, notes)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (uid, new_version,
                     item.get("科目", ""),
                     item.get("知识点或技能", ""),
                     item.get("重要程度_高中低", "medium"),
                     item.get("建议学习月份", ""),
                     item.get("备注", "")),
                )
                checklist_count += 1

        await conn.commit()

    # 渲染学期规划摘要
    lines = _format_plan_summary(plan, days_left)
    if checklist_count > 0:
        lines.append(f"\n📋 同时生成了 {checklist_count} 个核心知识点清单")
    await bot.send_private_msg(
        user_id=state["qq_id"],
        message="\n".join(lines),
    )

    # ── 自动赠送当月月度日程 ──────────────────────
    await bot.send_private_msg(
        user_id=state["qq_id"],
        message="🎁 赠送服务：正在为你自动生成本月学习日程…\n（无需额外积分）",
    )

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            """SELECT subject_name, goal_title, goal_detail, priority
               FROM monthly_goals
               WHERE user_id = ? AND month = ?
               ORDER BY priority""",
            (uid, current_month),
        )
        month_goals = [dict(row) for row in await cursor.fetchall()]

    if month_goals:
        semester_phase = None
        for phase in plan.get("phases", []):
            for goal in phase.get("goals", []):
                if goal.get("month", "") == current_month:
                    semester_phase = phase.get("focus", "")
                    break
            if semester_phase:
                break

        monthly_plan = await ai_service.generate_monthly_schedule(
            school=school,
            major=major,
            days_left=days_left,
            current_month_goals=month_goals,
            today=date.today().isoformat(),
            semester_plan_phase=semester_phase,
            timetable_prompt=timetable_prompt,
        )

        if monthly_plan:
            async with get_db_conn() as conn:
                db = UserDB(uid, conn)
                saved = await Scheduler.save_weekly_plan_from_ai(db, monthly_plan)
            if saved:
                from .weekly_plan import _format_monthly_summary
                summary_lines = _format_monthly_summary(saved)
                await bot.send_private_msg(
                    user_id=state["qq_id"],
                    message="\n".join(summary_lines),
                )
            else:
                await bot.send_private_msg(
                    user_id=state["qq_id"],
                    message="⚠️ 月度日程保存失败，可稍后手动发送 [#生成月计划]。"
                )
        else:
            await bot.send_private_msg(
                user_id=state["qq_id"],
                message="⚠️ 月度日程生成失败，可稍后手动发送 [#生成月计划]。"
            )
    else:
        await bot.send_private_msg(
            user_id=state["qq_id"],
            message=(
                f"本月（{current_month}）暂无学习目标，无法生成月度日程。\n"
                "可能是学期计划从下个月才开始安排。"
            ),
        )

    await matcher.finish()


# ──────────────────────────────────────────────
# #查看备考计划
# ──────────────────────────────────────────────

view_plan_cmd = on_command("查看备考计划", priority=5, block=True)


@view_plan_cmd.handle()
async def handle_view_plan(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#查看备考计划 — 显示最新学期规划摘要"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            """SELECT plan_json, exam_date, created_at
               FROM study_plan
               WHERE user_id = ?
               ORDER BY plan_version DESC LIMIT 1""",
            (uid,),
        )
        row = await cursor.fetchone()

    if not row:
        await matcher.finish(
            "还没有生成过备考计划。\n"
            "发送 [#生成备考计划] 让 AI 为你规划学期路线。"
        )

    plan = json.loads(row["plan_json"])
    exam_date = date.fromisoformat(row["exam_date"])
    days_left = (exam_date - date.today()).days

    lines = _format_plan_summary(plan, days_left)
    lines.append(f"\n生成于：{row['created_at'][:16]}")
    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# #本月目标
# ──────────────────────────────────────────────

monthly_goals_cmd = on_command("本月目标", priority=5, block=True)


@monthly_goals_cmd.handle()
async def handle_monthly_goals(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#本月目标 — 查看当月学习目标"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    current_month = date.today().strftime("%Y-%m")

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            """SELECT subject_name, goal_title, goal_detail, priority, status
               FROM monthly_goals
               WHERE user_id = ? AND month = ?
               ORDER BY priority, subject_name""",
            (uid, current_month),
        )
        goals = [dict(row) for row in await cursor.fetchall()]

    if not goals:
        await matcher.finish(
            f"本月（{current_month}）暂无学习目标。\n"
            "发送 [#生成备考计划] 让 AI 为你规划学期路线。"
        )

    priority_map = {1: "🔴", 2: "🟡", 3: "🟢"}
    status_map = {"pending": "⬜", "in_progress": "🔄", "done": "✅"}

    lines = [f"📋 本月学习目标（{current_month}）", ""]

    by_subject: dict[str, list] = {}
    for g in goals:
        by_subject.setdefault(g["subject_name"], []).append(g)

    for subj, items in by_subject.items():
        lines.append(f"【{subj}】")
        for g in items:
            icon = status_map.get(g["status"], "⬜")
            pri = priority_map.get(g["priority"], "🟡")
            lines.append(f"  {icon}{pri} {g['goal_title']}")
            if g.get("goal_detail"):
                lines.append(f"      {g['goal_detail'][:50]}")
        lines.append("")

    lines.append("发送 [#生成月计划] 让 AI 基于本月目标安排日程")
    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

def _format_plan_summary(plan: dict, days_left: int) -> list[str]:
    """格式化学期规划摘要"""
    phases = plan.get("phases", [])
    summary = plan.get("summary", "")

    lines = [
        "📚 学期备考规划",
        f"距考试 {days_left} 天",
        "",
    ]

    if summary:
        lines.append(f"📌 {summary}")
        lines.append("")

    for phase in phases:
        name = phase.get("name", "")
        months = phase.get("months", [])
        focus = phase.get("focus", "")
        month_range = f"（{'→'.join(months)}）" if months else ""

        lines.append(f"▸ {name}{month_range}")
        if focus:
            lines.append(f"  重点：{focus}")

        goals = phase.get("goals", [])
        if goals:
            by_subj: dict[str, list[str]] = {}
            for g in goals:
                subj = g.get("subject", "")
                title = g.get("title", "")
                by_subj.setdefault(subj, []).append(title)
            for subj, titles in by_subj.items():
                lines.append(f"  {subj}：{'、'.join(titles[:3])}")
        lines.append("")

    lines.append("发送 [#本月目标] 查看当月目标  ·  [#生成月计划] 安排本月日程")
    return lines
