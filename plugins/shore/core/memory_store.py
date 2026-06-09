"""
Memory 系统 — 用户记忆的读写与语义搜索。

记忆分两层：
  - daily（短期）：每日学习事件，如打卡、情绪记录、周计划摘要
  - long（长期）：经过沉淀的用户事实，如学习偏好、持续困难点

写入时机：打卡、情绪检测、周计划生成
读取时机：早安推送、打卡反馈、周计划生成（注入 prompt）
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

from .zhipu_embedding import (
    get_embedding,
    vector_to_blob,
    blob_to_vector,
    cosine_similarity,
)

logger = logging.getLogger("shore.memory_store")


# ──────────────────────────────────────────────
# 记忆分类常量
# ──────────────────────────────────────────────

# daily 类别
CAT_CHECKIN = "checkin"       # 打卡记录："完成 B树，掌握度 3→4"
CAT_EMOTION = "emotion"       # 情绪记录："用户说压力大，进入陪伴模式"
CAT_PLAN = "plan"             # 计划摘要："本周聚焦：极限、数据结构"
CAT_STUDY = "study"           # 学习行为："连续3天复习线性代数"

# long 类别（未来扩展用）
FACT_PREFERENCE = "preference"  # 学习偏好
FACT_WEAKNESS = "weakness"      # 持续薄弱点
FACT_MILESTONE = "milestone"    # 里程碑事件


# ──────────────────────────────────────────────
# 写入记忆
# ──────────────────────────────────────────────

async def write_daily_memory(
    conn,
    user_id: str,
    category: str,
    content: str,
    log_date: str | None = None,
) -> int:
    """
    写入一条每日记忆。

    参数：
        conn: aiosqlite 连接
        user_id: 用户哈希 ID
        category: 记忆类别（checkin/emotion/plan/study）
        content: 记忆内容文本
        log_date: 日期（默认今天），格式 YYYY-MM-DD
    返回：
        插入的记录 ID
    """
    if log_date is None:
        log_date = date.today().isoformat()

    # 获取 embedding
    try:
        vec = await get_embedding(content)
        embedding_blob = vector_to_blob(vec)
    except Exception as e:
        logger.warning("获取 embedding 失败，记忆将不支持语义搜索: %s", e)
        embedding_blob = None

    cursor = await conn.execute(
        """INSERT INTO user_memory_daily
           (user_id, log_date, category, content, embedding)
           VALUES (?, ?, ?, ?, ?)""",
        (user_id, log_date, category, content, embedding_blob),
    )
    await conn.commit()
    return cursor.lastrowid


async def write_long_memory(
    conn,
    user_id: str,
    fact: str,
) -> int:
    """
    写入一条长期记忆。

    参数：
        conn: aiosqlite 连接
        user_id: 用户哈希 ID
        fact: 事实描述文本
    返回：
        插入的记录 ID
    """
    try:
        vec = await get_embedding(fact)
        embedding_blob = vector_to_blob(vec)
    except Exception as e:
        logger.warning("获取 embedding 失败: %s", e)
        embedding_blob = None

    cursor = await conn.execute(
        """INSERT INTO user_memory_long
           (user_id, fact, embedding)
           VALUES (?, ?, ?)""",
        (user_id, fact, embedding_blob),
    )
    await conn.commit()
    return cursor.lastrowid


# ──────────────────────────────────────────────
# 读取记忆（按日期）
# ──────────────────────────────────────────────

async def get_daily_memories(
    conn,
    user_id: str,
    log_date: str,
    category: str | None = None,
) -> list[dict]:
    """
    获取指定日期的记忆列表。

    参数：
        conn: aiosqlite 连接
        user_id: 用户哈希 ID
        log_date: 日期字符串 YYYY-MM-DD
        category: 可选，筛选指定类别
    返回：
        记忆字典列表（不含 embedding BLOB）
    """
    if category:
        cursor = await conn.execute(
            """SELECT id, log_date, category, content, created_at
               FROM user_memory_daily
               WHERE user_id = ? AND log_date = ? AND category = ?
               ORDER BY created_at""",
            (user_id, log_date, category),
        )
    else:
        cursor = await conn.execute(
            """SELECT id, log_date, category, content, created_at
               FROM user_memory_daily
               WHERE user_id = ? AND log_date = ?
               ORDER BY created_at""",
            (user_id, log_date),
        )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


async def get_recent_memories(
    conn,
    user_id: str,
    days: int = 3,
    limit: int = 20,
) -> list[dict]:
    """
    获取最近 N 天的记忆（用于注入推送 prompt）。

    参数：
        conn: aiosqlite 连接
        user_id: 用户哈希 ID
        days: 回溯天数（默认 3 天）
        limit: 最大返回条数
    返回：
        按时间降序排列的记忆列表
    """
    cursor = await conn.execute(
        """SELECT id, log_date, category, content, created_at
           FROM user_memory_daily
           WHERE user_id = ?
             AND log_date >= date('now', ? || ' days')
           ORDER BY created_at DESC
           LIMIT ?""",
        (user_id, f"-{days}", limit),
    )
    rows = await cursor.fetchall()
    return [dict(row) for row in rows]


# ──────────────────────────────────────────────
# 语义搜索
# ──────────────────────────────────────────────

async def search_memories(
    conn,
    user_id: str,
    query: str,
    top_k: int = 5,
    min_score: float = 0.5,
    table: str = "daily",
) -> list[dict]:
    """
    语义搜索用户记忆。
    将 query 向量化后，与存储的 embedding 计算余弦相似度，
    返回相似度最高的 top_k 条记忆。

    参数：
        conn: aiosqlite 连接
        user_id: 用户哈希 ID
        query: 搜索文本
        top_k: 返回前 N 条
        min_score: 最低相似度阈值
        table: "daily" 或 "long"
    返回：
        含 similarity 字段的记忆列表，按相似度降序
    """
    # 获取查询向量
    query_vec = await get_embedding(query)

    # 从 DB 取出所有有 embedding 的记忆
    if table == "long":
        cursor = await conn.execute(
            """SELECT id, fact AS content, embedding, created_at
               FROM user_memory_long
               WHERE user_id = ? AND embedding IS NOT NULL""",
            (user_id,),
        )
    else:
        cursor = await conn.execute(
            """SELECT id, log_date, category, content, embedding, created_at
               FROM user_memory_daily
               WHERE user_id = ? AND embedding IS NOT NULL""",
            (user_id,),
        )

    rows = await cursor.fetchall()

    # 计算相似度并排序
    scored = []
    for row in rows:
        row_dict = dict(row)
        blob = row_dict.pop("embedding")
        stored_vec = blob_to_vector(blob)
        sim = cosine_similarity(query_vec, stored_vec)
        if sim >= min_score:
            row_dict["similarity"] = round(sim, 4)
            scored.append(row_dict)

    scored.sort(key=lambda x: x["similarity"], reverse=True)
    return scored[:top_k]


# ──────────────────────────────────────────────
# 辅助：格式化记忆为 prompt 注入文本
# ──────────────────────────────────────────────

def format_memories_for_prompt(
    memories: list[dict],
    max_chars: int = 500,
) -> str:
    """
    将记忆列表格式化为可注入 LLM prompt 的文本。
    每条记忆一行，超过 max_chars 截断。

    示例输出：
        [3/17 打卡] 完成 B树，掌握度 3→4
        [3/16 情绪] 用户说压力大，进入陪伴模式
        [3/16 计划] 本周聚焦：极限、数据结构
    """
    if not memories:
        return ""

    # 类别中文映射
    cat_map = {
        "checkin": "打卡",
        "emotion": "情绪",
        "plan": "计划",
        "study": "学习",
    }

    lines = []
    total = 0
    for m in memories:
        log_date = m.get("log_date", "")
        # 简化日期：2026-03-17 → 3/17
        short_date = ""
        if log_date and len(log_date) >= 10:
            short_date = f"{int(log_date[5:7])}/{int(log_date[8:10])}"

        cat = cat_map.get(m.get("category", ""), m.get("category", ""))
        content = m.get("content", m.get("fact", ""))

        line = f"[{short_date} {cat}] {content}" if short_date else f"[{cat}] {content}"
        total += len(line)
        if total > max_chars:
            break
        lines.append(line)

    return "\n".join(lines)
