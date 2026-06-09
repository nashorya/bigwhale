"""
月计划指令处理器 — AI 驱动的学习计划管理。

指令集：
  #生成月计划          — 调用 LLM 生成28天学习日程（消耗积分）
  #本月日程            — 查看当前月度日程摘要
  #导出日程            — 以文本格式发送月度日程

打卡使用 checkin.py 中的 #完成 [科目名] 命令。
"""

from __future__ import annotations

import os
from datetime import date, timedelta

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..core.points_service import spend
from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import UserDB, get_db_conn
from ..core.scheduler import Scheduler
from .admin import check_banned

# 生成月计划的积分费用
_MONTHLY_PLAN_COST = int(os.environ.get("MONTHLY_PLAN_COST", "50"))

# ──────────────────────────────────────────────
# #生成月计划
# ──────────────────────────────────────────────

gen_monthly_plan_cmd = on_command("生成月计划", priority=5, block=True)


@gen_monthly_plan_cmd.handle()
async def handle_gen_monthly_plan(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#生成月计划 — 调用 LLM 生成28天月度学习日程"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 检查基础数据
        subjects = await db.get_active_subjects()
        if not subjects:
            await matcher.finish(
                "你还没有配置科目和知识点。\n"
                "请先完成初始化向导（发送 [#开始]）。"
            )

        all_kps = await db.get_all_knowledge_points()
        if not all_kps:
            await matcher.finish(
                "知识点库为空，无法生成计划。\n"
                "请先通过初始化向导导入知识点。"
            )

        # 检查院校信息
        cursor = await conn.execute(
            "SELECT school_name, major_name FROM target_schools WHERE user_id = ? AND is_primary = 1 LIMIT 1",
            (uid,),
        )
        school_row = await cursor.fetchone()
        if not school_row:
            cursor = await conn.execute(
                "SELECT school_name, major_name FROM target_schools WHERE user_id = ? LIMIT 1",
                (uid,),
            )
            school_row = await cursor.fetchone()

        school = school_row["school_name"] if school_row else "目标院校"
        major = school_row["major_name"] if school_row else "目标专业"

        # 扣费
        ok = await spend(uid, _MONTHLY_PLAN_COST, "monthly_plan_generation", db)
        if not ok:
            balance = await db.get_points_balance()
            await matcher.finish(
                f"积分不足（需要 {_MONTHLY_PLAN_COST} 积分，当前 {balance} 积分）。\n"
                "发送 [#充值] 了解充值方式。"
            )

    # 发送等待提示
    await bot.send_private_msg(
        user_id=event.user_id,
        message=(
            f"🤖 正在为你规划本月28天学习日程…\n"
            f"院校：{school} · {major}\n"
            "（AI 生成约需 15-40 秒，请稍候）"
        ),
    )

    # 从 monthly_goals 级联生成月度日程
    plan = None
    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        current_month = date.today().strftime("%Y-%m")

        cursor = await conn.execute(
            """SELECT subject_name, goal_title, goal_detail, priority
               FROM monthly_goals
               WHERE user_id = ? AND month = ?
               ORDER BY priority""",
            (uid, current_month),
        )
        month_goals = [dict(row) for row in await cursor.fetchall()]

        if not month_goals:
            await matcher.finish(
                "本月尚无学习目标，请先生成备考计划。\n"
                "发送 [#生成备考计划] 让 AI 规划完整学期。"
            )

        # 获取考试信息
        from ..core import ai_service
        exam_date_str = await db.get_exam_date()
        days_left = 180
        if exam_date_str:
            exam_dt = date.fromisoformat(exam_date_str)
            days_left = (exam_dt - date.today()).days

        # 获取学期计划中当前阶段描述（可选）
        semester_phase = None
        cursor = await conn.execute(
            """SELECT plan_json FROM study_plan
               WHERE user_id = ? ORDER BY created_at DESC LIMIT 1""",
            (uid,),
        )
        sp_row = await cursor.fetchone()
        if sp_row and sp_row["plan_json"]:
            import json
            try:
                sp = json.loads(sp_row["plan_json"])
                # 从学期计划的 phases 中找当前月所属阶段
                for phase in sp.get("phases", []):
                    for goal in phase.get("goals", []):
                        if goal.get("month", "") == current_month:
                            semester_phase = phase.get("focus", "")
                            break
                    if semester_phase:
                        break
            except Exception:
                pass

        # 获取课表约束（如果有）
        timetable_prompt = ""
        cursor = await conn.execute(
            """SELECT timetable_json, timetable_months FROM user_timetable
               WHERE user_id = ? ORDER BY created_at DESC LIMIT 1""",
            (uid,),
        )
        tt_row = await cursor.fetchone()
        if tt_row and tt_row["timetable_json"]:
            import json as _json
            try:
                from ..core.timetable_parser import make_timetable_prompt_section
                tt_data = _json.loads(tt_row["timetable_json"])
                tt_months = tt_row.get("timetable_months", "")
                timetable_prompt = make_timetable_prompt_section(tt_data, tt_months)
            except Exception:
                pass

        plan = await ai_service.generate_monthly_schedule(
            school=school,
            major=major,
            days_left=days_left,
            current_month_goals=month_goals,
            today=date.today().isoformat(),
            semester_plan_phase=semester_phase,
            timetable_prompt=timetable_prompt,
        )

        # 将 AI 返回的结果写入 weekly_plan 表
        if plan:
            plan = await Scheduler.save_weekly_plan_from_ai(db, plan)

    if not plan:
        await matcher.finish(
            "😔 AI 生成月度日程失败，请稍后重试。\n"
            f"已退还 {_MONTHLY_PLAN_COST} 积分（实验性功能，如问题持续请联系管理员）。"
        )

    # 渲染摘要
    lines = _format_monthly_summary(plan)
    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# #本月日程
# ──────────────────────────────────────────────

monthly_plan_cmd = on_command("本月日程", priority=5, block=True)


@monthly_plan_cmd.handle()
async def handle_monthly_plan(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#本月日程 — 查看当前月度日程摘要"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        week_start = await db.get_latest_week_start()
        if not week_start:
            await matcher.finish(
                "还没有生成过月度日程。\n"
                "发送 [#生成月计划] 让 AI 为你规划28天学习日程。"
            )
        plan = await db.get_weekly_plan(week_start)

    lines = _format_monthly_summary(plan)
    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# #导出日程
# ──────────────────────────────────────────────

export_schedule_cmd = on_command("导出日程", priority=5, block=True)


@export_schedule_cmd.handle()
async def handle_export_schedule(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#导出日程 — 以文本格式发送本周日程"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        week_start = await db.get_latest_week_start()
        if not week_start:
            await matcher.finish(
                "还没有生成过月度日程。\n"
                "发送 [#生成月计划] 让 AI 为你规划28天学习日程。"
            )
        plan = await db.get_weekly_plan(week_start)

    if not plan:
        await matcher.finish("月度日程为空，请重新发送 [#生成月计划]。")

    # 构建文本表格
    day_map: dict[str, list[dict]] = {}
    for item in plan:
        pd = item["plan_date"]
        day_map.setdefault(pd, []).append(item)

    lines = ["📅 月度学习日程", f"（从 {week_start} 起）", ""]
    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for i, (pd, items) in enumerate(sorted(day_map.items())):
        d = date.fromisoformat(pd)
        wd = weekday_zh[d.weekday()]
        week_num = i // 7 + 1
        day_in_week = i % 7 + 1
        lines.append(f"【第{week_num}周 Day{day_in_week} · {d.month}/{d.day} {wd}】")
        for item in items:
            mins = item.get("estimated_minutes", 60)
            notes = item.get("notes", "")
            note_str = f"  ({notes})" if notes else ""
            lines.append(f"  · {item['subject_name']}：{item['topic_name']}（约{mins}分钟）{note_str}")
        lines.append("")

    lines.append("发送 [#完成 科目名] 标记完成。")
    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# 内部辅助函数
# ──────────────────────────────────────────────

def _format_monthly_summary(plan: list[dict]) -> list[str]:
    """将月度日程格式化为摘要文本列表。"""
    if not plan:
        return ["月度日程为空。"]

    day_map: dict[int, list[dict]] = {}
    for item in plan:
        di = item.get("day_index", 0)
        day_map.setdefault(di, []).append(item)

    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    week_start_str = plan[0].get("week_start", date.today().isoformat()) if plan else date.today().isoformat()
    try:
        start_date = date.fromisoformat(week_start_str)
    except (ValueError, TypeError):
        start_date = date.today()

    total_done = sum(1 for i in plan if i.get("status") == "done")
    total = len(plan)
    rate = round(total_done / total * 100) if total > 0 else 0
    total_days = max(day_map.keys()) + 1 if day_map else 0
    total_weeks = (total_days + 6) // 7

    lines = [
        f"📚 本月学习日程（AI 排课 · {total_weeks}周{total_days}天）",
        f"进度：{total_done}/{total}（{rate}%）",
        "",
    ]

    current_week = -1
    for day_idx in sorted(day_map.keys()):
        items = day_map[day_idx]
        d = start_date + timedelta(days=day_idx)
        wd = weekday_zh[d.weekday()]
        week_num = day_idx // 7 + 1

        # 周分隔
        if week_num != current_week:
            current_week = week_num
            if day_idx > 0:
                lines.append("─" * 20)
            lines.append(f"📌 第{week_num}周")

        day_done = sum(1 for i in items if i.get("status") == "done")
        day_total = len(items)
        status_icon = "✅" if day_done == day_total else ("🔄" if day_done > 0 else "📖")

        lines.append(f"{status_icon} Day{day_idx+1}（{d.month}/{d.day} {wd}）")
        # 按科目聚合
        subject_topics: dict[str, list[str]] = {}
        for item in items:
            sn = item["subject_name"]
            topic = item["topic_name"]
            done_icon = "✓" if item.get("status") == "done" else "·"
            subject_topics.setdefault(sn, []).append(f"{done_icon}{topic}")
        for sn, topics in subject_topics.items():
            lines.append(f"  {sn}：{'、'.join(topics)}")

    lines.append("")
    lines.append("发送 [#打卡 科目名] 标记完成  ·  [#导出日程] 获取完整日程表")
    return lines



