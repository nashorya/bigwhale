"""
院校配置与学情相关指令处理器。
薄胶水层：解析输入 → 调用 core/ → 渲染结果。

指令集：
  #查看院校配置              — 查看目标院校/专业及科目列表
  #停用科目 [名称]           — 暂停指定科目的学习计划
  #激活科目 [名称]           — 恢复指定科目
  #删除科目 [名称]           — 请求删除科目（需 #确认删除 二次确认）
  #确认删除                  — 确认执行上一步的删除请求
  #倒计时                    — 查看距考试剩余天数
  #设置考试日期 [YYYY-MM-DD] — 更新考试日期
  #学情                      — 全科学情概览（消耗 5 积分）
  #学情 [科目名]             — 指定科目学情（消耗 5 积分）
"""

from __future__ import annotations

from datetime import date, datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..core.points_service import spend
from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import UserDB, get_db_conn
from .admin import check_banned

# 学情报告费用
REPORT_COST = 5

# 等待二次确认的删除请求：uid → subject_name
_pending_delete: dict[str, str] = {}


# ──────────────────────────────────────────────
# #查看院校配置
# ──────────────────────────────────────────────

school_config_cmd = on_command("查看院校配置", priority=5, block=True)


@school_config_cmd.handle()
async def handle_school_config(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#查看院校配置 — 查看目标院校、专业及科目列表"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        # 目标院校
        cursor = await conn.execute(
            """SELECT school_name, major_name, is_primary
               FROM target_schools WHERE user_id = ?
               ORDER BY is_primary DESC""",
            (uid,),
        )
        schools = await cursor.fetchall()

        # 科目及状态
        cursor = await conn.execute(
            """SELECT s.name, s.category, ss.status
               FROM subjects s
               LEFT JOIN subject_status ss
                 ON s.id = ss.subject_id AND ss.user_id = s.user_id
               WHERE s.user_id = ?
               ORDER BY s.category, s.name""",
            (uid,),
        )
        subjects = await cursor.fetchall()

    if not schools and not subjects:
        await matcher.finish(
            "尚未配置院校信息。\n"
            "发送 [#开始] 重新完成初始化向导。"
        )

    lines = ["🏫 院校配置", ""]

    if schools:
        lines.append("【目标院校】")
        for s in schools:
            flag = "（第一志愿）" if s["is_primary"] else ""
            lines.append(f"  · {s['school_name']} - {s['major_name']}{flag}")
        lines.append("")

    if subjects:
        lines.append("【科目列表】")
        for s in subjects:
            status = s["status"] or "active"
            icon = "✅" if status == "active" else ("⏸️" if status == "suspended" else "❌")
            lines.append(f"  {icon} {s['name']}（{s['category']}）")
        lines.append("")
        lines.append(
            "发送 [#停用科目 名称] 暂停  ·  [#激活科目 名称] 恢复\n"
            "发送 [#删除科目 名称] 删除  ·  [#添加科目 名称 类型] 新增"
        )

    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# #停用科目
# ──────────────────────────────────────────────

suspend_subject_cmd = on_command("停用科目", priority=5, block=True)


@suspend_subject_cmd.handle()
async def handle_suspend_subject(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#停用科目 [名称] — 暂停指定科目的学习计划"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    name = sanitize_input(args.extract_plain_text(), max_length=50)
    if not name:
        await matcher.finish("用法：#停用科目 [科目名称]")

    async with get_db_conn() as conn:
        subject_id = await _find_subject_id(conn, uid, name)
        if subject_id is None:
            await matcher.finish(f"找不到科目「{name}」，请检查名称。")

        await conn.execute(
            """INSERT INTO subject_status (user_id, subject_id, status, suspended_at)
               VALUES (?, ?, 'suspended', ?)
               ON CONFLICT(user_id, subject_id) DO UPDATE
               SET status = 'suspended', suspended_at = ?""",
            (uid, subject_id, datetime.now().isoformat(), datetime.now().isoformat()),
        )
        await conn.commit()

    await matcher.finish(
        f"⏸️ 科目「{name}」已暂停。\n"
        "该科目不会出现在每日计划中，发送 [#激活科目 名称] 可恢复。"
    )


# ──────────────────────────────────────────────
# #激活科目
# ──────────────────────────────────────────────

activate_subject_cmd = on_command("激活科目", priority=5, block=True)


@activate_subject_cmd.handle()
async def handle_activate_subject(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#激活科目 [名称] — 恢复已暂停的科目"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    name = sanitize_input(args.extract_plain_text(), max_length=50)
    if not name:
        await matcher.finish("用法：#激活科目 [科目名称]")

    async with get_db_conn() as conn:
        subject_id = await _find_subject_id(conn, uid, name)
        if subject_id is None:
            await matcher.finish(f"找不到科目「{name}」，请检查名称。")

        await conn.execute(
            """INSERT INTO subject_status (user_id, subject_id, status)
               VALUES (?, ?, 'active')
               ON CONFLICT(user_id, subject_id) DO UPDATE
               SET status = 'active', suspended_at = NULL, suspend_reason = NULL""",
            (uid, subject_id),
        )
        await conn.commit()

    await matcher.finish(
        f"✅ 科目「{name}」已激活。\n"
        "发送 [#生成计划] 重新生成今日学习计划。"
    )


# ──────────────────────────────────────────────
# #删除科目
# ──────────────────────────────────────────────

delete_subject_cmd = on_command("删除科目", priority=5, block=True)


@delete_subject_cmd.handle()
async def handle_delete_subject(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#删除科目 [名称] — 请求删除科目，需二次确认"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    name = sanitize_input(args.extract_plain_text(), max_length=50)
    if not name:
        await matcher.finish("用法：#删除科目 [科目名称]")

    async with get_db_conn() as conn:
        subject_id = await _find_subject_id(conn, uid, name)
        if subject_id is None:
            await matcher.finish(f"找不到科目「{name}」，请检查名称。")

        # 统计该科目知识点数量
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM knowledge_points WHERE user_id = ? AND subject_id = ?",
            (uid, subject_id),
        )
        kp_count = (await cursor.fetchone())["cnt"]

    # 记录待删除请求
    _pending_delete[uid] = name

    await matcher.finish(
        f"⚠️ 确认删除科目「{name}」？\n"
        f"该科目共有 {kp_count} 个知识点，删除后不可恢复。\n\n"
        "发送 [#确认删除] 执行删除，或发送其他指令取消。"
    )


# ──────────────────────────────────────────────
# #确认删除
# ──────────────────────────────────────────────

confirm_delete_cmd = on_command("确认删除", priority=5, block=True)


@confirm_delete_cmd.handle()
async def handle_confirm_delete(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#确认删除 — 确认执行科目删除"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    pending = _pending_delete.pop(uid, None)
    if not pending:
        await matcher.finish("没有等待确认的删除请求。\n发送 [#删除科目 名称] 开始删除流程。")

    async with get_db_conn() as conn:
        subject_id = await _find_subject_id(conn, uid, pending)
        if subject_id is None:
            await matcher.finish(f"科目「{pending}」已不存在。")

        # 删除关联数据（knowledge_points, subject_status, daily_plan 关联会级联）
        await conn.execute(
            "DELETE FROM subject_status WHERE user_id = ? AND subject_id = ?",
            (uid, subject_id),
        )
        await conn.execute(
            "DELETE FROM knowledge_points WHERE user_id = ? AND subject_id = ?",
            (uid, subject_id),
        )
        await conn.execute(
            "DELETE FROM subjects WHERE id = ? AND user_id = ?",
            (subject_id, uid),
        )
        await conn.commit()

    await matcher.finish(f"🗑️ 科目「{pending}」及其所有知识点已删除。")


# ──────────────────────────────────────────────
# #倒计时
# ──────────────────────────────────────────────

countdown_cmd = on_command("倒计时", priority=5, block=True)


@countdown_cmd.handle()
async def handle_countdown(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#倒计时 — 查看距考试剩余天数"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        exam_date_str = await db.get_exam_date()

    if not exam_date_str:
        await matcher.finish(
            "还未设置考试日期。\n"
            "发送 [#设置考试日期 YYYY-MM-DD] 设置考试日期。"
        )

    try:
        exam_date = date.fromisoformat(str(exam_date_str)[:10])
    except ValueError:
        await matcher.finish("考试日期格式错误，请重新设置。")

    today = date.today()
    days_left = (exam_date - today).days

    if days_left < 0:
        await matcher.finish(
            f"考试日期（{exam_date}）已过。\n"
            "发送 [#设置考试日期 YYYY-MM-DD] 更新日期。"
        )
    elif days_left == 0:
        await matcher.finish("📅 今天就是考试日！加油！")
    else:
        # 计算备考阶段
        from ..core.scheduler import get_study_phase
        phase = get_study_phase(days_left)
        phase_names = {
            "foundation": "基础期",
            "intensify": "强化期",
            "sprint": "冲刺期",
            "sprint_final": "最终冲刺",
        }
        phase_zh = phase_names.get(phase, phase)

        await matcher.finish(
            f"⏰ 距考试还有 {days_left} 天\n"
            f"考试日期：{exam_date}\n"
            f"当前阶段：{phase_zh}"
        )


# ──────────────────────────────────────────────
# #设置考试日期
# ──────────────────────────────────────────────

set_exam_date_cmd = on_command("设置考试日期", priority=5, block=True)


@set_exam_date_cmd.handle()
async def handle_set_exam_date(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#设置考试日期 [YYYY-MM-DD] — 更新考试日期"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    date_str = sanitize_input(args.extract_plain_text(), max_length=20).strip()
    if not date_str:
        await matcher.finish("用法：#设置考试日期 [YYYY-MM-DD]\n例如：#设置考试日期 2026-12-20")

    try:
        exam_date = date.fromisoformat(date_str)
    except ValueError:
        await matcher.finish(
            f"日期格式错误「{date_str}」。\n"
            "请使用 YYYY-MM-DD 格式，如：2026-12-20"
        )

    if exam_date <= date.today():
        await matcher.finish("考试日期应为未来日期，请重新输入。")

    async with get_db_conn() as conn:
        await conn.execute(
            """INSERT INTO exam_config (user_id, exam_date, updated_at)
               VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE
               SET exam_date = ?, updated_at = ?""",
            (
                uid,
                date_str,
                datetime.now().isoformat(),
                date_str,
                datetime.now().isoformat(),
            ),
        )
        await conn.commit()

    days_left = (exam_date - date.today()).days
    await matcher.finish(
        f"✅ 考试日期已更新：{exam_date}\n"
        f"距考试还有 {days_left} 天。"
    )


# ──────────────────────────────────────────────
# #学情
# ──────────────────────────────────────────────

report_cmd = on_command("学情", priority=5, block=True)


@report_cmd.handle()
async def handle_report(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#学情 [科目名] — 学情概览（消耗 5 积分）"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    subject_filter = sanitize_input(args.extract_plain_text(), max_length=50)

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 扣费
        ok = await spend(uid, REPORT_COST, "report", db)
        if not ok:
            balance = await db.get_points_balance()
            await matcher.finish(
                f"积分不足（需要 {REPORT_COST} 积分，当前 {balance} 积分）。\n"
                "发送 [#充值] 了解充值方式。"
            )

        today = date.today().isoformat()

        if subject_filter:
            lines = await _build_subject_report(conn, uid, subject_filter, today)
        else:
            lines = await _build_global_report(conn, uid, today)

    await matcher.finish("\n".join(lines))


async def _build_global_report(conn, uid: str, today: str) -> list[str]:
    """构建全科学情报告"""
    # 各科目知识点数量及平均掌握度
    cursor = await conn.execute(
        """SELECT s.name, COUNT(kp.id) as total,
                  AVG(kp.mastery_level) as avg_mastery,
                  SUM(CASE WHEN kp.mastery_level >= 4 THEN 1 ELSE 0 END) as mastered
           FROM subjects s
           LEFT JOIN knowledge_points kp ON s.id = kp.subject_id AND kp.user_id = s.user_id
           WHERE s.user_id = ?
           GROUP BY s.id, s.name
           ORDER BY s.name""",
        (uid,),
    )
    rows = await cursor.fetchall()

    # 今日打卡进度
    cursor2 = await conn.execute(
        """SELECT COUNT(*) as done FROM daily_plan
           WHERE user_id = ? AND plan_date = ? AND status = 'done'""",
        (uid, today),
    )
    done_today = (await cursor2.fetchone())["done"]

    cursor3 = await conn.execute(
        "SELECT COUNT(*) as total FROM daily_plan WHERE user_id = ? AND plan_date = ?",
        (uid, today),
    )
    total_today = (await cursor3.fetchone())["total"]

    lines = ["📊 学情报告（全科）", ""]

    if total_today > 0:
        rate = round(done_today / total_today * 100)
        lines.append(f"今日进度：{done_today}/{total_today}（{rate}%）")
        lines.append("")

    if rows:
        lines.append("【各科掌握度】")
        for row in rows:
            total = row["total"] or 0
            if total == 0:
                continue
            avg = round(row["avg_mastery"] or 0, 1)
            mastered = row["mastered"] or 0
            bar = _mastery_bar(avg)
            lines.append(f"  {row['name']}  {bar}  均 {avg}/5  ({mastered}/{total} 已掌握)")
        lines.append("")

    lines.append("发送 [#学情 科目名] 查看单科详情。")
    return lines


async def _build_subject_report(
    conn, uid: str, subject_filter: str, today: str
) -> list[str]:
    """构建单科学情报告"""
    cursor = await conn.execute(
        "SELECT id, name FROM subjects WHERE user_id = ? AND name LIKE ? LIMIT 1",
        (uid, f"%{subject_filter}%"),
    )
    subj = await cursor.fetchone()
    if not subj:
        return [f"找不到科目「{subject_filter}」。"]

    subject_id = subj["id"]
    subject_name = subj["name"]

    # 全部知识点分布
    cursor = await conn.execute(
        """SELECT mastery_level, COUNT(*) as cnt
           FROM knowledge_points WHERE user_id = ? AND subject_id = ?
           GROUP BY mastery_level ORDER BY mastery_level""",
        (uid, subject_id),
    )
    dist = {row["mastery_level"]: row["cnt"] for row in await cursor.fetchall()}
    total = sum(dist.values())

    # 今日该科目打卡
    cursor = await conn.execute(
        """SELECT COUNT(*) as done FROM daily_plan dp
           JOIN knowledge_points kp ON dp.kp_id = kp.id
           WHERE dp.user_id = ? AND dp.plan_date = ?
             AND kp.subject_id = ? AND dp.status = 'done'""",
        (uid, today, subject_id),
    )
    done_today = (await cursor.fetchone())["done"]

    lines = [f"📊 {subject_name} 学情报告", ""]

    if total > 0:
        avg = sum(lv * cnt for lv, cnt in dist.items()) / total
        mastered = dist.get(4, 0) + dist.get(5, 0)
        weak = dist.get(1, 0) + dist.get(2, 0)

        lines.append(f"知识点总数：{total}")
        lines.append(f"已掌握（4-5）：{mastered}  薄弱（1-2）：{weak}")
        lines.append(f"平均掌握度：{avg:.1f}/5")
        lines.append("")
        lines.append("掌握度分布：")
        for lv in range(1, 6):
            cnt = dist.get(lv, 0)
            bar = "█" * min(cnt, 10) + "░" * max(0, 10 - cnt)
            lines.append(f"  {lv}星 {bar} {cnt}")
        lines.append("")

    if done_today > 0:
        lines.append(f"今日已打卡：{done_today} 个知识点")

    lines.append("发送 [#错题本 科目名] 查看薄弱知识点。")
    return lines


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

async def _find_subject_id(conn, uid: str, name: str) -> int | None:
    """按名称模糊查找科目 ID"""
    cursor = await conn.execute(
        "SELECT id FROM subjects WHERE user_id = ? AND name LIKE ? LIMIT 1",
        (uid, f"%{name}%"),
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


def _mastery_bar(avg: float) -> str:
    """生成掌握度进度条（满分 5）"""
    filled = round(avg)
    return "⭐" * filled + "☆" * (5 - filled)
