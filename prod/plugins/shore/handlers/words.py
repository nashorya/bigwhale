"""
推词与知识点管理相关指令处理器。
薄胶水层：解析输入 → 调用 core/ → 渲染结果。

指令集：
  #错题本              — 查看所有科目低掌握度知识点 + 英语错词
  #错题本 [科目名]     — 查看指定科目错题（英语查错词本，其他科目查掌握度≤2 的知识点）
  #掌握 [知识点] [1-5] — 手动设置知识点掌握度
  #关联 [知识点A] [知识点B] — 建立两个知识点的关联
  #添加科目 [名称] [类型]   — 添加新科目（类型：专业课/公共课）
  #同步                — 提示通过文件同步知识点（功能开发中）
"""

from __future__ import annotations

import json
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg

from ..core.scheduler import calc_next_review_date
from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import UserDB, get_db_conn
from .admin import check_banned


# ──────────────────────────────────────────────
# #错题本
# ──────────────────────────────────────────────

error_book_cmd = on_command("错题本", priority=5, block=True)


@error_book_cmd.handle()
async def handle_error_book(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#错题本 [科目名] — 查看低掌握度知识点或英语错词"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    subject_filter = sanitize_input(args.extract_plain_text(), max_length=50)

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        # 判断是否为英语
        if subject_filter and "英语" in subject_filter:
            lines = await _get_english_error_book(conn, uid)
        elif subject_filter:
            lines = await _get_subject_error_book(conn, uid, subject_filter)
        else:
            # 全科：先展示英语错词，再展示低掌握度知识点
            lines = await _get_all_error_book(conn, uid)

    await matcher.finish("\n".join(lines))


async def _get_english_error_book(conn, uid: str) -> list[str]:
    """查询英语错词本"""
    cursor = await conn.execute(
        """SELECT wb.word, wb.meaning, uws.weight, uws.correct_streak
           FROM user_word_status uws
           JOIN word_bank wb ON uws.word_id = wb.id
           WHERE uws.user_id = ? AND uws.in_error_book = 1
           ORDER BY uws.weight DESC
           LIMIT 30""",
        (uid,),
    )
    rows = await cursor.fetchall()
    if not rows:
        return ["📖 英语错词本\n\n暂无错词。继续学习积累吧！"]

    lines = [f"📖 英语错词本（共 {len(rows)} 词）", ""]
    for row in rows:
        streak = row["correct_streak"]
        weight = round(row["weight"], 1)
        lines.append(f"· {row['word']}  {row['meaning']}")
        lines.append(f"  权重 {weight}  连续答对 {streak} 次")
    return lines


async def _get_subject_error_book(conn, uid: str, subject_name: str) -> list[str]:
    """查询指定科目低掌握度知识点（mastery_level ≤ 2）"""
    cursor = await conn.execute(
        """SELECT kp.topic_name, kp.mastery_level, kp.next_review_at,
                  s.name as subject_name
           FROM knowledge_points kp
           JOIN subjects s ON kp.subject_id = s.id
           WHERE kp.user_id = ? AND s.name LIKE ?
             AND kp.mastery_level <= 2
           ORDER BY kp.mastery_level ASC, kp.last_review_at ASC
           LIMIT 30""",
        (uid, f"%{subject_name}%"),
    )
    rows = await cursor.fetchall()
    if not rows:
        return [f"📖 {subject_name} 错题本\n\n暂无掌握度 ≤ 2 的知识点，继续保持！"]

    lines = [f"📖 {subject_name} 薄弱知识点（{len(rows)} 个）", ""]
    for row in rows:
        mastery = row["mastery_level"]
        next_review = str(row["next_review_at"] or "")[:10]
        lines.append(f"· {row['topic_name']}  掌握度 {'⭐' * mastery}{'☆' * (5 - mastery)}")
        if next_review:
            lines.append(f"  下次复习：{next_review}")
    return lines


async def _get_all_error_book(conn, uid: str) -> list[str]:
    """查询全科低掌握度知识点"""
    cursor = await conn.execute(
        """SELECT kp.topic_name, kp.mastery_level, s.name as subject_name
           FROM knowledge_points kp
           JOIN subjects s ON kp.subject_id = s.id
           WHERE kp.user_id = ? AND kp.mastery_level <= 2
           ORDER BY kp.mastery_level ASC, s.name
           LIMIT 50""",
        (uid,),
    )
    rows = await cursor.fetchall()

    # 统计英语错词数
    cursor2 = await conn.execute(
        "SELECT COUNT(*) as cnt FROM user_word_status WHERE user_id = ? AND in_error_book = 1",
        (uid,),
    )
    word_count = (await cursor2.fetchone())["cnt"]

    if not rows and word_count == 0:
        return ["📖 错题本\n\n当前无薄弱知识点，继续保持！"]

    lines = ["📖 错题本（全科汇总）", ""]

    if word_count > 0:
        lines.append(f"【英语错词】{word_count} 词  发送 [#错题本 英语] 查看详情")
        lines.append("")

    if rows:
        # 按科目分组
        by_subject: dict[str, list] = {}
        for row in rows:
            by_subject.setdefault(row["subject_name"], []).append(dict(row))

        for subj, items in by_subject.items():
            lines.append(f"【{subj}】{len(items)} 个薄弱点")
            for item in items[:5]:  # 每科最多显示 5 条
                m = item["mastery_level"]
                lines.append(f"  · {item['topic_name']}  {'⭐' * m}{'☆' * (5 - m)}")
            if len(items) > 5:
                lines.append(f"  ... 还有 {len(items) - 5} 个")
            lines.append("")

    lines.append("发送 [#错题本 科目名] 查看指定科目详情。")
    return lines


# ──────────────────────────────────────────────
# #掌握
# ──────────────────────────────────────────────

mastery_cmd = on_command("掌握", priority=5, block=True)


@mastery_cmd.handle()
async def handle_mastery(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#掌握 [知识点] [1-5] — 手动设置知识点掌握度"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    raw = sanitize_input(args.extract_plain_text(), max_length=200)
    if not raw:
        await matcher.finish("用法：#掌握 [知识点名] [1-5]")

    # 解析末尾是否为数字
    parts = raw.rsplit(maxsplit=1)
    if len(parts) < 2:
        await matcher.finish("用法：#掌握 [知识点名] [1-5]")

    try:
        score = int(parts[1])
        if not (1 <= score <= 5):
            raise ValueError
    except ValueError:
        await matcher.finish("掌握度须为 1-5 的整数。\n用法：#掌握 [知识点名] [1-5]")

    kp_name = parts[0].strip()

    async with get_db_conn() as conn:
        db = UserDB(uid, conn)

        cursor = await conn.execute(
            """SELECT kp.id, kp.topic_name, kp.mastery_level, s.name as subject_name
               FROM knowledge_points kp
               JOIN subjects s ON kp.subject_id = s.id
               WHERE kp.user_id = ? AND kp.topic_name LIKE ?
               LIMIT 1""",
            (uid, f"%{kp_name}%"),
        )
        row = await cursor.fetchone()
        if not row:
            await matcher.finish(f"找不到知识点「{kp_name}」，请检查名称。")

        old = row["mastery_level"]
        await db.update_mastery(row["id"], score)

        # 写入打卡历史（与 #打卡 一致）
        await conn.execute(
            """INSERT INTO checkin_history
               (user_id, kp_id, kp_name, subject_name, mastery_before, mastery_after)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (uid, row["id"], row["topic_name"], row["subject_name"], old, score),
        )
        await conn.commit()

    next_review = calc_next_review_date(score)
    await matcher.finish(
        f"✓ [{row['topic_name']}] 掌握度已更新：{old} → {score}\n"
        f"下次复习：{next_review}"
    )


# ──────────────────────────────────────────────
# #关联
# ──────────────────────────────────────────────

link_cmd = on_command("关联", priority=5, block=True)


@link_cmd.handle()
async def handle_link(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#关联 [知识点A] [知识点B] — 建立两个知识点的关联"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    raw = sanitize_input(args.extract_plain_text(), max_length=200)
    if not raw:
        await matcher.finish("用法：#关联 [知识点A] [知识点B]")

    # 尝试按空格分两段
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await matcher.finish("用法：#关联 [知识点A] [知识点B]\n两个知识点用空格分隔。")

    name_a, name_b = parts[0].strip(), parts[1].strip()

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            """SELECT id, topic_name FROM knowledge_points
               WHERE user_id = ? AND topic_name LIKE ? LIMIT 1""",
            (uid, f"%{name_a}%"),
        )
        row_a = await cursor.fetchone()
        if not row_a:
            await matcher.finish(f"找不到知识点「{name_a}」。")

        cursor = await conn.execute(
            """SELECT id, topic_name FROM knowledge_points
               WHERE user_id = ? AND topic_name LIKE ? LIMIT 1""",
            (uid, f"%{name_b}%"),
        )
        row_b = await cursor.fetchone()
        if not row_b:
            await matcher.finish(f"找不到知识点「{name_b}」。")

        if row_a["id"] == row_b["id"]:
            await matcher.finish("不能将知识点与自身关联。")

        # 更新 A 的 cross_links（加入 B 的 id）
        await _add_cross_link(conn, uid, row_a["id"], row_b["id"])
        # 更新 B 的 cross_links（加入 A 的 id）
        await _add_cross_link(conn, uid, row_b["id"], row_a["id"])
        await conn.commit()

    await matcher.finish(
        f"🔗 已关联：\n"
        f"  [{row_a['topic_name']}]  ↔  [{row_b['topic_name']}]"
    )


async def _add_cross_link(conn, uid: str, kp_id: int, link_id: int) -> None:
    """向 knowledge_points.cross_links 追加关联 ID"""
    cursor = await conn.execute(
        "SELECT cross_links FROM knowledge_points WHERE id = ? AND user_id = ?",
        (kp_id, uid),
    )
    row = await cursor.fetchone()
    if row is None:
        return

    try:
        links: list[int] = json.loads(row["cross_links"]) if row["cross_links"] else []
    except (json.JSONDecodeError, TypeError):
        links = []

    if link_id not in links:
        links.append(link_id)
        await conn.execute(
            "UPDATE knowledge_points SET cross_links = ? WHERE id = ? AND user_id = ?",
            (json.dumps(links), kp_id, uid),
        )


# ──────────────────────────────────────────────
# #添加科目
# ──────────────────────────────────────────────

add_subject_cmd = on_command("添加科目", priority=5, block=True)


@add_subject_cmd.handle()
async def handle_add_subject(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    args: Message = CommandArg(),
):
    """#添加科目 [名称] [类型] — 添加新科目（类型：专业课/公共课）"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    raw = sanitize_input(args.extract_plain_text(), max_length=100)
    if not raw:
        await matcher.finish("用法：#添加科目 [名称] [类型]\n类型：专业课 / 公共课")

    parts = raw.rsplit(maxsplit=1)
    if len(parts) == 2 and parts[1] in ("专业课", "公共课"):
        subject_name = parts[0].strip()
        category = parts[1]
    else:
        # 默认专业课
        subject_name = raw
        category = "专业课"

    if not subject_name:
        await matcher.finish("科目名称不能为空。")

    async with get_db_conn() as conn:
        # 检查是否已存在同名科目
        cursor = await conn.execute(
            "SELECT id FROM subjects WHERE user_id = ? AND name = ?",
            (uid, subject_name),
        )
        if await cursor.fetchone():
            await matcher.finish(f"科目「{subject_name}」已存在，无需重复添加。")

        # 插入科目
        cursor = await conn.execute(
            """INSERT INTO subjects (user_id, name, category, library_type, syllabus_source)
               VALUES (?, ?, ?, 'B', 'user_upload')""",
            (uid, subject_name, category),
        )
        subject_id = cursor.lastrowid

        # 同时插入 subject_status（active）
        await conn.execute(
            """INSERT INTO subject_status (user_id, subject_id, status)
               VALUES (?, ?, 'active')""",
            (uid, subject_id),
        )
        await conn.commit()

    await matcher.finish(
        f"✅ 科目「{subject_name}」（{category}）已添加。\n"
        "后续可发送 [#同步] 上传知识点数据。"
    )


# ──────────────────────────────────────────────
# #同步
# ──────────────────────────────────────────────

sync_cmd = on_command("同步", priority=5, block=True)


@sync_cmd.handle()
async def handle_sync(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """#同步 — 提示用户通过文件上传同步知识点（功能开发中）"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    await matcher.finish(
        "📂 知识点同步\n"
        "\n"
        "文件上传功能正在开发中。\n"
        "\n"
        "目前请联系管理员将 CSV 文件导入数据库。\n"
        "CSV 格式：科目名称, 知识点名称, 重要程度(1-3)\n"
        "\n"
        "已支持手动操作：\n"
        "· #添加科目 [名称] [类型] — 新增科目\n"
        "· #掌握 [知识点] [1-5] — 调整掌握度"
    )
