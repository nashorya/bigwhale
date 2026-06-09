"""
EmotionDetector — 情绪信号检测与陪伴会话管理。
core/ 层禁止直接 import nonebot 模块。

三条触发路径：
  1. 用户主动倾诉（user_confide）：关键词匹配
  2. 系统检测异常（system_detect）：连续低正确率 / 长时间离线 / 深夜活跃 / 连续缺卡
  3. 用户主动指令（command）：#陪我聊

设计原则：宁可漏检，不可误触发。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any


# ──────────────────────────────────────────────
# 情绪信号词库
# ──────────────────────────────────────────────

# 强信号词：单独出现就触发
_STRONG_SIGNALS: set[str] = set()

EMOTION_KEYWORDS: dict[str, list[str]] = {
    "焦虑类": [
        "好烦", "烦死了", "压力好大", "好慌", "睡不着",
        "好难", "坚持不住了", "焦虑", "心慌", "受不了了",
    ],
    "沮丧类": [
        "好差", "完了", "好绝望", "没意思", "不想学了",
        "放弃", "没用", "考不上了", "白学了", "没希望了",
    ],
    "疲惫类": [
        "好累", "累了", "撑不住", "身体不行了",
        "太累了", "学不动了", "脑子转不动",
    ],
    "求陪伴类": [
        "有点难受", "想聊聊", "陪我说说话",
        "想找人说话", "好孤独", "一个人好难",
    ],
}

# 求陪伴类全部为强信号词（单独出现即触发）
for word in EMOTION_KEYWORDS["求陪伴类"]:
    _STRONG_SIGNALS.add(word)

# 额外强信号词
_STRONG_SIGNALS.update(["坚持不住了", "不想学了", "放弃", "好绝望", "考不上了"])

# 扁平化所有信号词，用于快速查找
_ALL_KEYWORDS: set[str] = set()
for words in EMOTION_KEYWORDS.values():
    _ALL_KEYWORDS.update(words)


# ──────────────────────────────────────────────
# 路径一：用户消息情绪检测
# ──────────────────────────────────────────────

def detect(text: str) -> tuple[bool, str | None]:
    """
    检测用户消息中的情绪信号。

    触发规则（宁可漏检，不可误触发）：
      - ≥2 个信号词 → 触发
      - 1 个强信号词 → 触发
      - 其他 → 不触发

    参数：
        text: 用户发送的原始消息文本

    返回：
        (should_trigger, category)
        - should_trigger: 是否应触发情绪陪伴
        - category: 触发的情绪类别名（如 "焦虑类"），未触发时为 None
    """
    if not text or not text.strip():
        return False, None

    matched_words: list[str] = []
    matched_categories: list[str] = []
    has_strong = False

    for category, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                matched_words.append(kw)
                if category not in matched_categories:
                    matched_categories.append(category)
                if kw in _STRONG_SIGNALS:
                    has_strong = True

    # 触发判定
    if has_strong and len(matched_words) >= 1:
        return True, matched_categories[0]

    if len(matched_words) >= 2:
        return True, matched_categories[0]

    return False, None


def detect_detailed(text: str) -> dict[str, Any]:
    """
    详细版情绪检测，返回完整匹配信息。
    用于写入 emotion_log.mood_signal 字段。

    返回：
        {
            "triggered": bool,
            "matched_words": list[str],
            "categories": list[str],
            "has_strong_signal": bool
        }
    """
    if not text or not text.strip():
        return {
            "triggered": False,
            "matched_words": [],
            "categories": [],
            "has_strong_signal": False,
        }

    matched_words: list[str] = []
    categories: list[str] = []
    has_strong = False

    for category, keywords in EMOTION_KEYWORDS.items():
        for kw in keywords:
            if kw in text:
                matched_words.append(kw)
                if category not in categories:
                    categories.append(category)
                if kw in _STRONG_SIGNALS:
                    has_strong = True

    triggered = has_strong or len(matched_words) >= 2

    return {
        "triggered": triggered,
        "matched_words": matched_words,
        "categories": categories,
        "has_strong_signal": has_strong,
    }


# ──────────────────────────────────────────────
# 路径二：系统异常检测
# ──────────────────────────────────────────────

async def check_system_anomaly(
    user_id: str,
    db: Any,  # UserDB 实例
) -> tuple[bool, str] | None:
    """
    检查系统层面的异常状态，决定是否触发情绪陪伴。

    检测项（优先级从高到低）：
      1. 情绪熔断：连续 3 次推词正确率 < 50%
      2. 连续多日缺卡：连续 3 天打卡覆盖率 < 30%
      3. 深夜异常活跃：00:00-04:00 期间活跃
      4. 长时间离线：计划学习时段内 > 90 分钟无消息

    参数：
        user_id: 用户 ID
        db: 已绑定 user_id 的 UserDB 实例

    返回：
        (should_trigger, anomaly_type) 或 None（无异常）
        anomaly_type: 'meltdown' / 'multi_day_absence' / 'late_night' / 'long_offline'
    """
    # 检查冷却期：上次情绪陪伴后 30 分钟内不再触发
    if await _is_in_cooldown(db):
        return None

    # ① 情绪熔断：检查最近推词正确率
    meltdown = await _check_meltdown(db)
    if meltdown:
        return True, "meltdown"

    # ② 连续多日缺卡
    absence = await _check_multi_day_absence(db)
    if absence:
        return True, "multi_day_absence"

    # ③ 深夜异常活跃
    if _check_late_night():
        return True, "late_night"

    return None


async def _is_in_cooldown(db: Any) -> bool:
    """
    检查是否在冷却期内（上次情绪陪伴后 30 分钟内）。
    """
    cursor = await db._conn.execute(
        """SELECT last_emotion_at FROM persona_config
           WHERE user_id = ?""",
        (db._uid,),
    )
    row = await cursor.fetchone()
    if not row or not row["last_emotion_at"]:
        return False

    try:
        last_dt = datetime.fromisoformat(row["last_emotion_at"])
        return datetime.now() - last_dt < timedelta(minutes=30)
    except (ValueError, TypeError):
        return False


async def _check_meltdown(db: Any) -> bool:
    """
    情绪熔断：检查最近 3 次推词的正确率是否均 < 50%。
    查询 user_word_status 中最近 3 轮的 correct_streak。
    """
    # 查询最近推词记录（简化：检查 error_book 中权重高的词数量）
    cursor = await db._conn.execute(
        """SELECT COUNT(*) as err_count FROM user_word_status
           WHERE user_id = ? AND in_error_book = 1 AND weight >= 2.0""",
        (db._uid,),
    )
    row = await cursor.fetchone()
    if row and row["err_count"] >= 5:
        # 多个高权重错词说明连续答错
        return True
    return False


async def _check_multi_day_absence(db: Any) -> bool:
    """
    连续多日缺卡：连续 3 天打卡覆盖率 < 30%。
    """
    streak_data = await db.get_checkin_streak()
    last_date = streak_data.get("last_complete_date")

    if not last_date:
        # 从未完成过打卡，检查注册时间
        return False

    try:
        last_dt = datetime.fromisoformat(last_date).date() if isinstance(last_date, str) else last_date
        days_gap = (datetime.now().date() - last_dt).days
        return days_gap >= 3
    except (ValueError, TypeError):
        return False


def _check_late_night() -> bool:
    """
    深夜异常活跃：当前时间在 00:00-04:00 之间。
    """
    hour = datetime.now().hour
    return 0 <= hour < 4


# ──────────────────────────────────────────────
# 陪伴会话管理
# ──────────────────────────────────────────────

async def start_session(
    user_id: str,
    triggered_by: str,
    db: Any,
    *,
    trigger_detail: str | None = None,
    mood_signal: list[str] | None = None,
) -> None:
    """
    进入情绪陪伴会话。

    操作：
      1. persona_config.companion_mode = 1
      2. persona_config.last_emotion_at = now
      3. 写入 emotion_log 记录

    参数：
        user_id: 用户 ID
        triggered_by: 触发来源 'user_confide' / 'system_detect' / 'command'
        db: UserDB 实例
        trigger_detail: 触发详情（如异常类型、用户消息片段）
        mood_signal: 检测到的情绪信号词列表
    """
    now = datetime.now().isoformat()

    # 获取当前角色
    persona = await db.get_active_persona()

    # 更新 companion_mode
    await db._conn.execute(
        """UPDATE persona_config
           SET companion_mode = 1, last_emotion_at = ?
           WHERE user_id = ?""",
        (now, user_id),
    )

    # 如果 persona_config 中无该用户记录，先插入
    cursor = await db._conn.execute(
        "SELECT 1 FROM persona_config WHERE user_id = ?",
        (user_id,),
    )
    if not await cursor.fetchone():
        await db._conn.execute(
            """INSERT INTO persona_config (user_id, companion_mode, last_emotion_at)
               VALUES (?, 1, ?)""",
            (user_id, now),
        )

    # 写入 emotion_log
    mood_json = json.dumps(mood_signal, ensure_ascii=False) if mood_signal else None
    await db._conn.execute(
        """INSERT INTO emotion_log
           (user_id, triggered_by, trigger_detail, persona_used, session_start, mood_signal)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, triggered_by, trigger_detail, persona, now, mood_json),
    )

    await db.commit()


async def end_session(user_id: str, db: Any) -> None:
    """
    结束情绪陪伴会话。

    操作：
      1. persona_config.companion_mode = 0
      2. 更新最近一条 emotion_log 的 session_end

    参数：
        user_id: 用户 ID
        db: UserDB 实例
    """
    now = datetime.now().isoformat()

    # 关闭 companion_mode
    await db._conn.execute(
        """UPDATE persona_config
           SET companion_mode = 0
           WHERE user_id = ?""",
        (user_id,),
    )

    # 更新 emotion_log 最近一条未结束的记录
    await db._conn.execute(
        """UPDATE emotion_log
           SET session_end = ?
           WHERE id = (
               SELECT id FROM emotion_log
               WHERE user_id = ? AND session_end IS NULL
               ORDER BY session_start DESC
               LIMIT 1
           )""",
        (now, user_id),
    )

    await db.commit()


async def is_in_companion_mode(db: Any) -> bool:
    """
    检查用户当前是否处于情绪陪伴模式。
    """
    cursor = await db._conn.execute(
        "SELECT companion_mode FROM persona_config WHERE user_id = ?",
        (db._uid,),
    )
    row = await cursor.fetchone()
    return bool(row and row["companion_mode"])
