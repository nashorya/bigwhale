"""
英语推词交互 handler。
薄胶水层：解析用户输入 → 选词 → 推送 → 更新权重。

指令集：
  #推词          — 开始一轮推词（5 词 × 3 轮 = 15 词）
  #推词统计      — 查看推词学习统计
"""

from __future__ import annotations

import random
from datetime import datetime

from nonebot import on_command
from nonebot.adapters.onebot.v11 import Bot, Message, PrivateMessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.typing import T_State

from ..core.security import get_or_create_uid, sanitize_input
from ..core.user_db import get_db_conn
from .admin import check_banned


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

WORDS_PER_ROUND = 5    # 每轮推送单词数
TOTAL_ROUNDS = 3       # 总轮数
WEIGHT_WRONG = 0.5     # 答错时权重增加值
WEIGHT_RIGHT = 0.15    # 答对时权重减少值
STREAK_THRESHOLD = 3   # 连续答对 N 次后标记为已掌握


# ──────────────────────────────────────────────
# #推词
# ──────────────────────────────────────────────

word_push_cmd = on_command("推词", priority=4, block=True)


@word_push_cmd.handle()
async def handle_word_push_start(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    state: T_State,
    args: Message = CommandArg(),
):
    """#推词 — 开始推词"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    # 检查是否是 #推词统计
    arg_text = args.extract_plain_text().strip()
    if arg_text == "统计":
        await _show_stats(matcher, uid)
        return

    # 选第一轮词
    words = await _pick_words(uid, WORDS_PER_ROUND)
    if not words:
        await matcher.finish(
            "📚 词库为空，请先导入词库。\n"
            "管理员执行：python import_words.py"
        )

    state["uid"] = uid
    state["round"] = 1
    state["words"] = words
    state["total_correct"] = 0
    state["total_wrong"] = 0

    # 推送第一轮
    msg = _format_quiz(words, round_num=1)
    await matcher.send(msg)


@word_push_cmd.got("answer")
async def handle_word_answer(
    bot: Bot,
    event: PrivateMessageEvent,
    matcher: Matcher,
    state: T_State,
):
    """处理用户答题回复"""
    uid = state["uid"]
    words = state["words"]
    round_num = state["round"]
    answer_text = event.get_plaintext().strip()

    # 检查退出
    if answer_text in ("退出", "结束", "q", "quit"):
        total_c = state["total_correct"]
        total_w = state["total_wrong"]
        total = total_c + total_w
        await matcher.finish(
            f"📊 本次推词结束！\n"
            f"共学习 {total} 词，"
            f"认识 {total_c} 词，不认识 {total_w} 词\n"
            f"正确率 {round(total_c / total * 100) if total else 0}%"
        )

    # 解析用户回复：输入认识的词的序号（如 "1 3 5" 或 "135"）
    known_indices = _parse_answer(answer_text, len(words))

    # 更新每个词的状态
    results = []
    round_correct = 0
    round_wrong = 0

    async with get_db_conn() as conn:
        now = datetime.now().isoformat()
        for i, w in enumerate(words):
            correct = (i + 1) in known_indices
            if correct:
                round_correct += 1
            else:
                round_wrong += 1

            # 更新 user_word_status
            await _update_word_status(conn, uid, w["id"], correct, now)

            results.append({
                "word": w["word"],
                "meaning": w["meaning"],
                "correct": correct,
            })
        await conn.commit()

    state["total_correct"] += round_correct
    state["total_wrong"] += round_wrong

    # 显示本轮结果
    result_msg = _format_results(results, round_num)

    # 检查是否还有下一轮
    if round_num < TOTAL_ROUNDS:
        next_words = await _pick_words(uid, WORDS_PER_ROUND)
        if next_words:
            state["round"] = round_num + 1
            state["words"] = next_words
            quiz_msg = _format_quiz(next_words, round_num=round_num + 1)
            await matcher.reject(f"{result_msg}\n\n{quiz_msg}")
        # 没有更多词了
    # 所有轮结束或词用完
    total_c = state["total_correct"]
    total_w = state["total_wrong"]
    total = total_c + total_w
    rate = round(total_c / total * 100) if total else 0

    summary = (
        f"{result_msg}\n\n"
        f"{'─' * 20}\n"
        f"📊 推词完成！共 {total} 词\n"
        f"✅ 认识 {total_c}  ❌ 不认识 {total_w}\n"
        f"正确率 {rate}%\n"
    )
    if total_w > 0:
        summary += "\n不认识的词已加入错词本，发 [#错题本 英语] 查看。"

    await matcher.finish(summary)


# ──────────────────────────────────────────────
# 选词算法
# ──────────────────────────────────────────────

async def _pick_words(uid: str, count: int) -> list[dict]:
    """
    按权重选词。
    优先选：① 已有记录且权重高的 ② 从未见过的新词（按词频排序）
    """
    async with get_db_conn() as conn:
        # 先选权重高的已学过的词（复习）
        cursor = await conn.execute(
            """SELECT wb.id, wb.word, wb.meaning, wb.frequency, wb.rank_order,
                      uws.weight, uws.correct_streak
               FROM user_word_status uws
               JOIN word_bank wb ON uws.word_id = wb.id
               WHERE uws.user_id = ? AND uws.weight >= 0.5
               ORDER BY uws.weight DESC, RANDOM()
               LIMIT ?""",
            (uid, count),
        )
        review_words = [dict(row) for row in await cursor.fetchall()]

        # 如果复习词不够，补充新词
        remaining = count - len(review_words)
        new_words = []
        if remaining > 0:
            # 获取已学过的 word_id 列表
            cursor = await conn.execute(
                "SELECT word_id FROM user_word_status WHERE user_id = ?",
                (uid,),
            )
            seen_ids = {row["word_id"] for row in await cursor.fetchall()}

            # 从词库中选未见过的词（按词频排名，核心词优先）
            cursor = await conn.execute(
                """SELECT id, word, meaning, frequency, rank_order
                   FROM word_bank
                   WHERE category = 'core'
                   ORDER BY rank_order ASC
                   LIMIT ?""",
                (remaining + len(seen_ids) + 50,),  # 多取一些用于过滤
            )
            candidates = [dict(row) for row in await cursor.fetchall()]
            for c in candidates:
                if c["id"] not in seen_ids and len(new_words) < remaining:
                    c["weight"] = 1.0
                    c["correct_streak"] = 0
                    new_words.append(c)

        all_words = review_words + new_words

        # 打乱顺序
        random.shuffle(all_words)
        return all_words[:count]


# ──────────────────────────────────────────────
# 状态更新
# ──────────────────────────────────────────────

async def _update_word_status(
    conn, uid: str, word_id: int, correct: bool, now: str
):
    """更新单个单词的学习状态"""
    # 检查是否已有记录
    cursor = await conn.execute(
        "SELECT id, weight, correct_streak, total_seen, total_correct, in_error_book "
        "FROM user_word_status WHERE user_id = ? AND word_id = ?",
        (uid, word_id),
    )
    row = await cursor.fetchone()

    if row:
        weight = row["weight"]
        streak = row["correct_streak"]
        total_seen = row["total_seen"] + 1
        total_correct = row["total_correct"]

        if correct:
            total_correct += 1
            streak += 1
            weight = max(0.1, weight - WEIGHT_RIGHT)
            # 连续答对 N 次，从错词本移出
            in_error = 0 if streak >= STREAK_THRESHOLD else row["in_error_book"]
        else:
            streak = 0
            weight += WEIGHT_WRONG
            in_error = 1  # 加入错词本

        await conn.execute(
            """UPDATE user_word_status
               SET weight = ?, correct_streak = ?, total_seen = ?,
                   total_correct = ?, in_error_book = ?, last_seen_at = ?
               WHERE id = ?""",
            (round(weight, 2), streak, total_seen, total_correct, in_error, now, row["id"]),
        )
    else:
        # 新记录
        if correct:
            weight = max(0.1, 1.0 - WEIGHT_RIGHT)
            streak = 1
            in_error = 0
            tc = 1
        else:
            weight = 1.0 + WEIGHT_WRONG
            streak = 0
            in_error = 1
            tc = 0

        await conn.execute(
            """INSERT INTO user_word_status
               (user_id, word_id, weight, correct_streak, total_seen,
                total_correct, in_error_book, last_seen_at)
               VALUES (?, ?, ?, ?, 1, ?, ?, ?)""",
            (uid, word_id, round(weight, 2), streak, tc, in_error, now),
        )


# ──────────────────────────────────────────────
# 消息格式化
# ──────────────────────────────────────────────

def _format_quiz(words: list[dict], round_num: int) -> str:
    """格式化推词测试消息"""
    lines = [f"📝 第 {round_num}/{TOTAL_ROUNDS} 轮（共 {len(words)} 词）", ""]
    for i, w in enumerate(words, 1):
        lines.append(f"  {i}. {w['word']}")
    lines.append("")
    lines.append("请回复你认识的词的序号（如 1 3 5）")
    lines.append("全都不认识回复 0，全都认识回复 全部")
    lines.append("回复「退出」结束推词")
    return "\n".join(lines)


def _format_results(results: list[dict], round_num: int) -> str:
    """格式化答题结果"""
    correct_count = sum(1 for r in results if r["correct"])
    wrong_count = len(results) - correct_count

    lines = [
        f"📋 第 {round_num} 轮结果  ✅{correct_count}  ❌{wrong_count}",
        "",
    ]
    for r in results:
        mark = "✅" if r["correct"] else "❌"
        lines.append(f"  {mark} {r['word']}  →  {r['meaning']}")
    return "\n".join(lines)


def _parse_answer(text: str, total: int) -> set[int]:
    """
    解析用户回复的序号。
    支持格式：'1 3 5'、'135'、'1,3,5'、'全部'、'0'
    """
    text = text.strip()

    if text == "0" or text == "无" or text == "都不认识":
        return set()

    if text in ("全部", "all", "都认识"):
        return set(range(1, total + 1))

    # 尝试用空格/逗号分隔
    indices = set()
    for sep in [" ", ",", "，", "、"]:
        if sep in text:
            for part in text.split(sep):
                part = part.strip()
                if part.isdigit():
                    n = int(part)
                    if 1 <= n <= total:
                        indices.add(n)
            return indices

    # 没有分隔符，尝试逐字符解析（如 "135"）
    for ch in text:
        if ch.isdigit():
            n = int(ch)
            if 1 <= n <= total:
                indices.add(n)
    return indices


# ──────────────────────────────────────────────
# #推词统计
# ──────────────────────────────────────────────

async def _show_stats(matcher: Matcher, uid: str):
    """显示推词统计"""
    async with get_db_conn() as conn:
        # 总学习词数
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM user_word_status WHERE user_id = ?",
            (uid,),
        )
        total = (await cursor.fetchone())["cnt"]

        # 已掌握（连续答对 >= 3 次）
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM user_word_status "
            "WHERE user_id = ? AND correct_streak >= ?",
            (uid, STREAK_THRESHOLD),
        )
        mastered = (await cursor.fetchone())["cnt"]

        # 错词本数量
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM user_word_status "
            "WHERE user_id = ? AND in_error_book = 1",
            (uid,),
        )
        error_count = (await cursor.fetchone())["cnt"]

        # 总正确率
        cursor = await conn.execute(
            "SELECT SUM(total_seen) as seen, SUM(total_correct) as correct "
            "FROM user_word_status WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        seen = row["seen"] or 0
        correct = row["correct"] or 0
        rate = round(correct / seen * 100) if seen > 0 else 0

        # 词库总量
        cursor = await conn.execute("SELECT COUNT(*) as cnt FROM word_bank")
        bank_total = (await cursor.fetchone())["cnt"]

    await matcher.finish(
        f"📊 推词学习统计\n"
        f"\n"
        f"词库总量：{bank_total} 词\n"
        f"已学习：{total} 词\n"
        f"已掌握：{mastered} 词（连续答对 ≥{STREAK_THRESHOLD} 次）\n"
        f"错词本：{error_count} 词\n"
        f"总正确率：{rate}%（{correct}/{seen}）\n"
        f"进度：{round(total / bank_total * 100) if bank_total else 0}%"
    )


# ──────────────────────────────────────────────
# 自动推词定时任务
# ──────────────────────────────────────────────

AUTO_PUSH_COUNT = 10  # 每次自动推送的单词数

# 星期映射：Python weekday() -> 课表中文
_WEEKDAY_MAP = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}

# 节次时间映射（常见大学作息）
_SLOT_TIME_RANGES = [
    ("第1", 8, 9),    # 第1-2节 ~8:00-9:35
    ("第2", 8, 9),
    ("第3", 10, 11),  # 第3-4节 ~10:00-11:35
    ("第4", 10, 11),
    ("第5", 14, 15),  # 第5-6节 ~14:00-15:35
    ("第6", 14, 15),
    ("第7", 16, 17),  # 第7-8节 ~16:00-17:35
    ("第8", 16, 17),
    ("第9", 19, 20),  # 第9-10节（晚课） ~19:00-20:35
    ("第10", 19, 20),
]


def _is_user_in_class(timetable_data: dict, now: datetime) -> bool:
    """
    根据课表判断用户当前是否在上课。
    """
    if not timetable_data:
        return False

    weekday_str = _WEEKDAY_MAP.get(now.weekday(), "")
    hour = now.hour
    busy = timetable_data.get("busy", [])

    for entry in busy:
        if entry.get("day") != weekday_str:
            continue
        slot = entry.get("slot", "")
        # 检查当前时间是否在该节次时间范围内
        for slot_prefix, start_h, end_h in _SLOT_TIME_RANGES:
            if slot.startswith(slot_prefix) and start_h <= hour <= end_h:
                return True
    return False


async def auto_push_words_job() -> None:
    """
    自动推词定时任务。
    每 45 分钟执行一次（8:00-22:00）：
      1. 遍历所有活跃用户
      2. 检查用户是否有推词订阅（非 basic 档位）
      3. 根据课表判断是否空闲
      4. 根据学习计划判断当前学习科目，调整推词策略
      5. 空闲则推送闪卡
    """
    import json as _json

    try:
        from nonebot import get_bot
        bot = get_bot()
    except (ImportError, ValueError):
        return  # Bot 未连接

    now = datetime.now()
    # 只在 8:00-22:00 之间推词
    if not (8 <= now.hour < 22):
        return

    from ..core.user_db import get_db_conn, get_all_user_ids

    async with get_db_conn() as conn:
        user_ids = await get_all_user_ids(conn)

    for uid in user_ids:
        try:
            async with get_db_conn() as conn:
                # 检查用户状态
                cursor = await conn.execute(
                    "SELECT init_complete, is_banned FROM users WHERE user_id = ?",
                    (uid,),
                )
                user = await cursor.fetchone()
                if not user or not user["init_complete"] or user["is_banned"]:
                    continue

                # 检查订阅和推词档位
                cursor = await conn.execute(
                    "SELECT subscription_active, word_tier FROM points_account WHERE user_id = ?",
                    (uid,),
                )
                account = await cursor.fetchone()
                if not account or not account["subscription_active"]:
                    continue

                word_tier = account.get("word_tier", "basic")
                if word_tier == "basic":
                    continue  # basic 档位不自动推词

                # 检查当前学习计划中的科目
                current_subject = await _get_current_study_subject(conn, uid)
                push_count, hint_style = _decide_push_strategy(current_subject)

                # 英语时段或无需推词
                if push_count == 0:
                    continue

                # 重科目时 30% 概率才推词（减少打扰）
                if hint_style == "heavy" and random.random() > 0.6:
                    continue

                # 选词并推送闪卡
                words = await _pick_words(uid, push_count)
                if not words:
                    continue

                # 标记这些词为已见过（被动复习）
                now_str = now.isoformat()
                for w in words:
                    await _mark_word_seen(conn, uid, w["id"], now_str)
                await conn.commit()

                # 格式化闪卡消息
                msg = _format_flashcard(words, hint_style, current_subject)

            # 发送消息
            from .schedule import _uid_to_qq
            qq = _uid_to_qq(uid)
            if qq:
                import asyncio
                await bot.send_private_msg(user_id=int(qq), message=msg)
                await asyncio.sleep(0.5)

        except Exception:
            pass  # 单个用户失败不影响其他用户


# 重科目：学习时需要高度集中注意力，尽量少打扰
_HEAVY_SUBJECTS = {"数学", "高数", "线代", "线性代数", "概率论", "概率", "计算机",
                    "数据结构", "操作系统", "组成原理", "计算机网络", "408",
                    "专业课", "政治"}

# 轻科目：背诵类、可以穿插推词
_LIGHT_SUBJECTS = {"英语", "英语一", "英语二", "单词", "阅读", "翻译", "写作"}


async def _get_current_study_subject(conn, uid: str) -> str | None:
    """从每日计划或周计划中推测用户当前在学什么科目"""
    today_str = datetime.now().date().isoformat()

    # 查询今日计划中的科目
    cursor = await conn.execute(
        """SELECT DISTINCT kp.subject_id, s.name as subject_name
           FROM daily_plan dp
           JOIN knowledge_points kp ON dp.kp_id = kp.id
           JOIN subjects s ON kp.subject_id = s.id
           WHERE dp.user_id = ? AND dp.plan_date = ? AND dp.status != 'done'
           LIMIT 3""",
        (uid, today_str),
    )
    rows = await cursor.fetchall()
    if rows:
        # 返回第一个未完成的科目
        return rows[0]["subject_name"]

    # 回退：查询周计划
    cursor = await conn.execute(
        """SELECT subject_name FROM weekly_plan
           WHERE user_id = ? AND plan_date = ?
           LIMIT 1""",
        (uid, today_str),
    )
    row = await cursor.fetchone()
    if row:
        return row["subject_name"]

    return None


def _decide_push_strategy(current_subject: str | None) -> tuple[int, str]:
    """
    根据当前学习科目决定推词策略。
    返回: (推送词数, 提示风格)
    """
    if not current_subject:
        return AUTO_PUSH_COUNT, "idle"  # 没有计划，正常推

    subject_lower = current_subject.strip()

    # 正在学英语 → 不推词，避免干扰
    for kw in _LIGHT_SUBJECTS:
        if kw in subject_lower:
            return 0, "skip"

    # 正在学重科目 → 减少推词量，用"换脑子"话术
    for kw in _HEAVY_SUBJECTS:
        if kw in subject_lower:
            return 5, "heavy"

    return AUTO_PUSH_COUNT, "normal"


# 各风格的引导话术
_HINTS = {
    "idle": [
        "💡 闲暇时间，看几个单词吧！",
        "📖 碎片时间也能背单词~",
        "✨ 每天多看几眼，考研单词不用愁！",
    ],
    "english": [
        "📚 英语学习时段，多记几个词！",
        "🔤 趁学英语的状态好，多看几个！",
        "💪 英语时段加推，冲！",
    ],
    "heavy": [
        "🧠 {subject}学累了？换换脑子背几个单词吧！",
        "☕ {subject}告一段落了？来看几个词放松下~",
        "🔄 学了会儿{subject}，切换下模式看几个词吧！",
        "💆 {subject}烧脑中？背单词换换脑子！",
    ],
    "normal": [
        "📖 学习间隙看几个词~",
        "💡 休息一下，顺便看几个单词！",
        "✨ 碎片时间不放过，看几个词吧！",
    ],
}


def _format_flashcard(words: list[dict], hint_style: str = "idle",
                      current_subject: str | None = None) -> str:
    """格式化闪卡消息（被动复习模式）"""
    hints = _HINTS.get(hint_style, _HINTS["idle"])
    hint = random.choice(hints)
    if "{subject}" in hint and current_subject:
        hint = hint.replace("{subject}", current_subject)

    lines = [
        hint,
        "",
    ]
    for i, w in enumerate(words, 1):
        lines.append(f"  {i}. {w['word']}  —  {w['meaning']}")
    lines.append("")
    lines.append("发 [#推词] 主动测试 · 发 [#推词统计] 看进度")
    return "\n".join(lines)


async def _mark_word_seen(conn, uid: str, word_id: int, now: str):
    """标记单词为已见过（被动复习，只增加 total_seen 不改权重）"""
    cursor = await conn.execute(
        "SELECT id, total_seen FROM user_word_status WHERE user_id = ? AND word_id = ?",
        (uid, word_id),
    )
    row = await cursor.fetchone()
    if row:
        await conn.execute(
            "UPDATE user_word_status SET total_seen = ?, last_seen_at = ? WHERE id = ?",
            (row["total_seen"] + 1, now, row["id"]),
        )
    else:
        await conn.execute(
            """INSERT INTO user_word_status
               (user_id, word_id, weight, correct_streak, total_seen,
                total_correct, in_error_book, last_seen_at)
               VALUES (?, ?, 1.0, 0, 1, 0, 0, ?)""",
            (uid, word_id, now),
        )


def _format_flashcard(words: list[dict]) -> str:
    """格式化闪卡消息（被动复习模式）"""
    lines = [
        f"📖 单词闪卡（{len(words)} 词）",
        "",
    ]
    for i, w in enumerate(words, 1):
        lines.append(f"  {i}. {w['word']}  —  {w['meaning']}")
    lines.append("")
    lines.append("💡 看一遍加深印象！发 [#推词] 主动测试")
    return "\n".join(lines)

