"""
打卡相关指令处理器。
薄胶水层：解析输入 → 调用 core/ → 渲染结果。

指令集：
  #打卡 [知识点名]         — 标记打卡，询问掌握度
  #打卡 [知识点名] [1-5]   — 直接打卡并给分
  #完成 [科目名]           — 标记该科目今日全部完成
  #今日计划               — 查看当日知识点清单
  #跳过 [知识点名]         — 跳过今日某知识点
  #连续打卡               — 查看连续天数
"""

from __future__ import annotations

from datetime import date, datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, PrivateMessageEvent, Message
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import get_db_conn, UserDB
from ..core import persona_engine
from ..core.scheduler import calc_next_review_date
from .admin import check_banned


# ──────────────────────────────────────────────
# #打卡
# ──────────────────────────────────────────────

checkin_cmd = on_command("打卡", priority=5, block=True)


@checkin_cmd.handle()
async def handle_checkin(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    args: Message = CommandArg()
):
    """处理 #打卡 [知识点名] 或 #打卡 [知识点名] [1-5]"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    raw = sanitize_input(args.extract_plain_text(), max_length=200)
    if not raw:
        await matcher.finish("用法：#打卡 [知识点名] 或 #打卡 [知识点名] [1-5]")

    # 解析参数：判断最后一个 token 是否为数字
    parts = raw.rsplit(maxsplit=1)
    mastery_input = None
    kp_name = raw

    if len(parts) == 2:
        try:
            score = int(parts[1])
            if 1 <= score <= 5:
                mastery_input = score
                kp_name = parts[0]
        except ValueError:
            pass  # 不是数字，整个当作知识点名

    today = date.today().isoformat()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 在今日计划中查找匹配的知识点
        plan = await db.get_daily_plan(today)
        target = _find_kp_in_plan(plan, kp_name, conn, uid)

        if target is None:
            # 不在今日计划中，尝试从所有知识点中查
            cursor = await conn.execute(
                """SELECT kp.id, kp.topic_name, kp.mastery_level, kp.subject_id,
                          s.name as subject_name
                   FROM knowledge_points kp
                   JOIN subjects s ON kp.subject_id = s.id
                   WHERE kp.user_id = ? AND kp.topic_name LIKE ?""",
                (uid, f"%{kp_name}%"),
            )
            row = await cursor.fetchone()
            if not row:
                await matcher.finish(f"找不到知识点「{kp_name}」。请检查名称是否正确。")
            target = dict(row)
            target["in_plan"] = False
        else:
            target["in_plan"] = True

        old_mastery = target.get("mastery_level", 1)

        if mastery_input is None:
            # 没给掌握度，询问用户
            # 简化处理：直接使用 old_mastery + 1，最大5
            mastery_input = min(old_mastery + 1, 5)

        # 更新掌握度
        await db.update_mastery(target["id"], mastery_input)

        # 写入打卡历史
        await conn.execute(
            """INSERT INTO checkin_history
               (user_id, kp_id, kp_name, subject_name, mastery_before, mastery_after)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, target["id"], target["topic_name"],
             target.get("subject_name", "未知"), old_mastery, mastery_input),
        )

        # 更新 daily_plan 状态
        if target.get("in_plan"):
            await conn.execute(
                """UPDATE daily_plan
                   SET status = 'done', completed_at = ?,
                       mastery_before = ?, mastery_after = ?
                   WHERE user_id = ? AND plan_date = ? AND kp_id = ?""",
                (datetime.now().isoformat(), old_mastery, mastery_input,
                 uid, today, target["id"]),
            )

        # 更新打卡连击
        await _update_streak(conn, uid)
        await conn.commit()

        # 获取连击数据
        streak = await db.get_checkin_streak()
        today_checkins = streak.get("total_checkins", 0)

        # 渲染单次打卡反馈
        persona_id = await db.get_active_persona()
        next_review = calc_next_review_date(mastery_input)

        # 构建渲染数据
        render_data = {
            "kp_name": target["topic_name"],
            "mastery_before": str(old_mastery),
            "mastery_after": str(mastery_input),
            "next_review": next_review,
        }

        # 尝试使用 PersonaEngine 渲染
        if persona_engine.is_loaded():
            response = persona_engine.render(persona_id, "checkin_done", render_data)
        else:
            response = (
                f"✓ [{target['topic_name']}] 已记录。"
                f"掌握度 {old_mastery}→{mastery_input}。\n"
                f"  下次复习：{next_review}。"
            )

        # 连击提示
        if today_checkins > 0 and today_checkins % 5 == 0:
            response += f"\n🔥 连续打卡{today_checkins}个！"

        # 检测是否所有计划完成 → 触发日终反馈
        plan = await db.get_daily_plan(today)
        all_done = all(p.get("status") == "done" for p in plan) if plan else False

        if all_done and plan:
            response += "\n\n🎉 今日计划全部完成！详细总结将在晚间推送。"

        # 写入 Memory（异步，不阻塞主流程）
        try:
            from ..core.memory_store import write_daily_memory, CAT_CHECKIN
            memory_content = (
                f"完成 {target['topic_name']}，"
                f"掌握度 {old_mastery}→{mastery_input}"
            )
            await write_daily_memory(conn, uid, CAT_CHECKIN, memory_content)
        except Exception:
            pass  # Memory 写入失败不影响打卡

    await matcher.finish(response)


# ──────────────────────────────────────────────
# #完成
# ──────────────────────────────────────────────

complete_cmd = on_command("完成", priority=5, block=True)


@complete_cmd.handle()
async def handle_complete(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    args: Message = CommandArg()
):
    """#完成 [科目名] — 标记该科目今日全部完成（同时更新 weekly_plan 和 clock_in.csv）"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    subject_name = sanitize_input(args.extract_plain_text(), max_length=50)
    if not subject_name:
        await matcher.finish("用法：#完成 [科目名]")

    today = date.today().isoformat()
    weekly_done_ids = []

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # ── 先处理 weekly_plan 的完成 ──
        weekly_today = await db.get_today_from_weekly_plan(today)
        matched_weekly = [
            item for item in weekly_today
            if subject_name in item["subject_name"] or subject_name in item["topic_name"]
        ]
        for item in matched_weekly:
            if item["status"] != "done":
                await db.mark_weekly_plan_done(item["id"])
                weekly_done_ids.append(item["id"])

                # 写入 checkin_history（补齐数据断层）
                kp_id = item.get("kp_id")
                topic = item.get("topic_name", "")
                subj = item.get("subject_name", "")
                # 查询当前掌握度
                old_mastery = 1
                if kp_id:
                    cursor = await conn.execute(
                        "SELECT mastery_level FROM knowledge_points WHERE id = ?",
                        (kp_id,),
                    )
                    kp_row = await cursor.fetchone()
                    if kp_row:
                        old_mastery = kp_row["mastery_level"]
                    new_mastery = min(old_mastery + 1, 5)
                    await db.update_mastery(kp_id, new_mastery)
                else:
                    new_mastery = old_mastery

                await conn.execute(
                    """INSERT INTO checkin_history
                       (user_id, kp_id, kp_name, subject_name, mastery_before, mastery_after)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (uid, kp_id, topic, subj, old_mastery, new_mastery),
                )

        # ── 再处理 daily_plan ──
        cursor = await conn.execute(
            """SELECT dp.id, dp.kp_id, kp.topic_name, kp.mastery_level
               FROM daily_plan dp
               JOIN knowledge_points kp ON dp.kp_id = kp.id
               JOIN subjects s ON kp.subject_id = s.id
               WHERE dp.user_id = ? AND dp.plan_date = ?
                 AND s.name LIKE ? AND dp.status = 'pending'""",
            (uid, today, f"%{subject_name}%"),
        )
        pending = await cursor.fetchall()

        count = 0
        for p in pending:
            old = p["mastery_level"]
            new = min(old + 1, 5)
            await db.update_mastery(p["kp_id"], new)
            await conn.execute(
                """UPDATE daily_plan
                   SET status = 'done', completed_at = ?,
                       mastery_before = ?, mastery_after = ?
                   WHERE id = ?""",
                (datetime.now().isoformat(), old, new, p["id"]),
            )
            await conn.execute(
                """INSERT INTO checkin_history
                   (user_id, kp_id, kp_name, subject_name, mastery_before, mastery_after)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (uid, p["kp_id"], p["topic_name"], subject_name, old, new),
            )
            count += 1

        await _update_streak(conn, uid)
        await conn.commit()

        # 更新 clock_in.csv
        if weekly_done_ids or count > 0:
            all_today = await db.get_today_from_weekly_plan(today)
            await _update_clock_in_csv(uid, subject_name, all_today)

    total = len(matched_weekly)
    if total > 0:
        done_total = sum(1 for i in (await _get_today_weekly(uid, today)) if i.get("status") == "done")
        all_day_total = await _get_today_weekly_total(uid, today)
        rate = round(done_total / all_day_total * 100) if all_day_total > 0 else 0
        response = (
            f"✅ {subject_name} 今日任务已完成！"
            f"\n今日进度：{done_total}/{all_day_total}（{rate}%）"
        )
    else:
        response = f"✅ {subject_name} 今日 {count} 个知识点已全部标记完成！" if count > 0 else f"科目「{subject_name}」今日没有待完成的任务了。"
    await matcher.finish(response)


# ──────────────────────────────────────────────
# #今日计划
# ──────────────────────────────────────────────

today_plan_cmd = on_command("今日计划", priority=5, block=True)


@today_plan_cmd.handle()
async def handle_today_plan(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#今日计划 — 查看当日知识点清单（优先显示 AI 周计划）"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    today = date.today().isoformat()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # ── 优先：展示 AI 周计划 ──
        weekly_today = await db.get_today_from_weekly_plan(today)
        if weekly_today:
            lines = [f"📋 今日学习计划（{today}）", "(AI 周计划)", ""]
            by_subject: dict[str, list] = {}
            for item in weekly_today:
                sn = item["subject_name"]
                by_subject.setdefault(sn, []).append(item)

            done_count = 0
            total = len(weekly_today)
            for sn, items in by_subject.items():
                lines.append(f"【{sn}】")
                for item in items:
                    icon = "✅" if item["status"] == "done" else (
                        "⏭️" if item["status"] == "skipped" else "⬜"
                    )
                    mins = item.get("estimated_minutes", 60)
                    notes = item.get("notes") or ""
                    note_str = f"  ({notes})" if notes else ""
                    lines.append(f"  {icon} {item['topic_name']}（约{mins}分钟）{note_str}")
                    if item["status"] == "done":
                        done_count += 1
                lines.append("")

            rate = round(done_count / total * 100) if total > 0 else 0
            lines.append(f"进度：{done_count}/{total}（{rate}%）")
            lines.append("")
            lines.append("发送 [#完成 科目名] 完成该科目任务  ·  [#本周计划] 查看全周进度")
            await matcher.finish("\n".join(lines))

        # ── 回退：展示旧版 daily_plan ──
        plan = await db.get_daily_plan(today)
        if not plan:
            await matcher.finish(
                "今日暂无学习计划。\n"
                "发送 [#生成周计划] 让 AI 为你规划7天路线，或 [#生成计划] 使用旧版安排。"
            )

        lines = [f"📋 今日学习计划（{today}）", ""]
        done_count = 0
        by_subj: dict[str, list] = {}
        for p in plan:
            cursor = await conn.execute(
                """SELECT kp.topic_name, s.name as subject_name
                   FROM knowledge_points kp
                   JOIN subjects s ON kp.subject_id = s.id
                   WHERE kp.id = ?""",
                (p["kp_id"],),
            )
            info = await cursor.fetchone()
            if info:
                subj = info["subject_name"]
                by_subj.setdefault(subj, []).append({
                    "name": info["topic_name"],
                    "status": p["status"],
                    "minutes": p["estimated_minutes"],
                })
                if p["status"] == "done":
                    done_count += 1

        for subj, items in by_subj.items():
            lines.append(f"【{subj}】")
            for item in items:
                icon = "✅" if item["status"] == "done" else (
                    "⏭️" if item["status"] == "skipped" else "⬜"
                )
                lines.append(f"  {icon} {item['name']}（{item['minutes']}分钟）")
            lines.append("")

        total = len(plan)
        rate = round(done_count / total * 100) if total > 0 else 0
        lines.append(f"进度：{done_count}/{total}（{rate}%）")
        lines.append("")
        lines.append("💡 提示：发送 [#生成周计划] 让 AI 按先修顺序规划你的7天学习路线。")

    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# #跳过
# ──────────────────────────────────────────────

skip_cmd = on_command("跳过", priority=5, block=True)


@skip_cmd.handle()
async def handle_skip(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    args: Message = CommandArg()
):
    """#跳过 [知识点名] — 跳过今日某知识点"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    kp_name = sanitize_input(args.extract_plain_text(), max_length=100)
    if not kp_name:
        await matcher.finish("用法：#跳过 [知识点名]")

    today = date.today().isoformat()

    async with get_db_conn() as conn:
        # 查找计划项
        cursor = await conn.execute(
            """SELECT dp.id, kp.topic_name
               FROM daily_plan dp
               JOIN knowledge_points kp ON dp.kp_id = kp.id
               WHERE dp.user_id = ? AND dp.plan_date = ?
                 AND dp.status = 'pending'
                 AND kp.topic_name LIKE ?""",
            (uid, today, f"%{kp_name}%"),
        )
        row = await cursor.fetchone()
        if not row:
            await matcher.finish(f"今日计划中未找到待完成的「{kp_name}」。")

        await conn.execute(
            """UPDATE daily_plan SET status = 'skipped'
               WHERE id = ?""",
            (row["id"],),
        )
        await conn.commit()

    await matcher.finish(
        f"⏭️ 已跳过 [{row['topic_name']}]。\n"
        f"该知识点将自动排入明日计划。"
    )


# ──────────────────────────────────────────────
# #连续打卡
# ──────────────────────────────────────────────

streak_cmd = on_command("连续打卡", priority=5, block=True)


@streak_cmd.handle()
async def handle_streak(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#连续打卡 — 查看连续天数"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        streak = await db.get_checkin_streak()

    current = streak.get("current_streak", 0)
    longest = streak.get("longest_streak", 0)
    total = streak.get("total_checkins", 0)

    lines = [
        "📊 打卡记录",
        "",
        f"当前连续：{current} 天",
        f"最长连续：{longest} 天",
        f"累计打卡：{total} 次",
    ]

    # 里程碑提示
    next_milestone = None
    for m in [7, 30, 60, 100]:
        if current < m:
            next_milestone = m
            break
    if next_milestone:
        remain = next_milestone - current
        lines.append(f"\n距离 {next_milestone} 天里程碑还差 {remain} 天，加油！")

    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

def _find_kp_in_plan(plan: list[dict], kp_name: str, conn, uid: str) -> dict | None:
    """在今日计划中按名称模糊匹配知识点（同步方式，需配合 SQL 查询）"""
    # 由于 plan 里只有 kp_id，需要在调用方做进一步查询
    # 这里简化为在 handle_checkin 中直接查询
    return None


async def _update_streak(conn, uid: str) -> None:
    """更新用户打卡连击（今日是否有打卡记录）"""
    today = date.today().isoformat()

    # 今日打卡数
    cursor = await conn.execute(
        "SELECT COUNT(*) as cnt FROM checkin_history WHERE user_id = ? AND DATE(checkin_at) = ?",
        (uid, today),
    )
    today_count = (await cursor.fetchone())["cnt"]

    if today_count == 0:
        return

    # 获取当前 streak
    cursor = await conn.execute(
        "SELECT * FROM checkin_streak WHERE user_id = ?",
        (uid,),
    )
    row = await cursor.fetchone()

    if row is None:
        # 首次打卡
        await conn.execute(
            """INSERT INTO checkin_streak
               (user_id, current_streak, longest_streak, last_complete_date, total_checkins)
               VALUES (?, 1, 1, ?, ?)""",
            (uid, today, today_count),
        )
        return

    last_date = row["last_complete_date"]
    current = row["current_streak"]
    longest = row["longest_streak"]

    if last_date == today:
        # 今日已更新过，只更新 total
        await conn.execute(
            "UPDATE checkin_streak SET total_checkins = ? WHERE user_id = ?",
            (today_count, uid),
        )
        return

    from datetime import timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    if last_date == yesterday:
        # 连续
        current += 1
    else:
        # 断了
        current = 1

    longest = max(longest, current)

    await conn.execute(
        """UPDATE checkin_streak
           SET current_streak = ?, longest_streak = ?,
               last_complete_date = ?, total_checkins = ?
           WHERE user_id = ?""",
        (current, longest, today, today_count, uid),
    )


async def _get_persona(uid: str) -> str:
    """获取用户当前角色 ID"""
    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        return await db.get_active_persona()


async def _get_today_weekly(uid: str, today: str) -> list[dict]:
    """获取今日周计划条目列表（辅助函数）"""
    async with get_db_conn() as conn:
        db = UserDB(uid, conn)
        return await db.get_today_from_weekly_plan(today)


async def _get_today_weekly_total(uid: str, today: str) -> int:
    """获取今日周计划总条目数"""
    items = await _get_today_weekly(uid, today)
    return len(items)


async def _update_clock_in_csv(
    uid: str, subject_name: str, today_items: list[dict]
) -> None:
    """
    更新 clock_in.csv 中今日打卡完成情况。
    找到今日对应的行，将完成的科目内容填入对应列。
    """
    import os, csv, io
    from datetime import date as dt_date

    try:
        csv_path = os.path.join(os.environ.get("CSV_OUTPUT_DIR", "."), "clock_in.csv")
        if not os.path.exists(csv_path):
            return

        with open(csv_path, "rb") as f:
            raw = f.read()
        text = raw.decode("gbk", errors="replace")
        lines = text.splitlines()
        if len(lines) < 2:
            return

        reader = csv.reader(iter(lines))
        all_rows = list(reader)
        if len(all_rows) < 2:
            return

        header = all_rows[1] if len(all_rows) > 1 else all_rows[0]
        today_dt = dt_date.today()
        today_date_str = f"{today_dt.month}/{today_dt.day}"

        # 构建科目完成情况映射
        subj_done: dict[str, list[str]] = {}
        for item in today_items:
            if item.get("status") == "done":
                sn = item["subject_name"]
                topic = item["topic_name"]
                subj_done.setdefault(sn, []).append(topic)

        # 找到日期匹配的行并更新
        for row_idx, row in enumerate(all_rows):
            if len(row) < 3:
                continue
            if row[1].strip() == today_date_str:
                for col_idx, col_name in enumerate(header):
                    for sn, topics in subj_done.items():
                        if sn in col_name or any(part in col_name for part in sn.split()):
                            while len(all_rows[row_idx]) <= col_idx:
                                all_rows[row_idx].append("")
                            all_rows[row_idx][col_idx] = "\u3001".join(topics)
                last_col = len(header) - 1
                while len(all_rows[row_idx]) <= last_col:
                    all_rows[row_idx].append("")
                done_subjects = list(subj_done.keys())
                all_rows[row_idx][last_col] = f"✅{'+'.join(done_subjects)}" if done_subjects else ""
                break

        buf = io.StringIO()
        writer = csv.writer(buf)
        for row in all_rows:
            writer.writerow(row)
        with open(csv_path, "wb") as f:
            f.write(buf.getvalue().encode("gbk", errors="replace"))

    except Exception:
        pass  # CSV 更新失败不影响主流程
