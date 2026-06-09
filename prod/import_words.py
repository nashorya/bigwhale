"""
考研英语词库导入脚本。
数据来源：https://github.com/exam-data/NETEMVocabulary

用法：
  python import_words.py

功能：
  1. 从 GitHub 下载 netem_full_list.json
  2. 过滤掉超基础词（the, a, is 等）
  3. 导入到 data/kaoyan.db 的 word_bank 表
"""

import json
import os
import sqlite3
import urllib.request

# 词库下载地址
JSON_URL = "https://raw.githubusercontent.com/exam-data/NETEMVocabulary/master/netem_full_list.json"

# 超基础词列表（不需要推送的词）
SKIP_WORDS = {
    "the", "be", "a", "to", "of", "and", "in", "have", "that", "it",
    "for", "on", "they", "you", "with", "as", "their", "by", "not",
    "he", "from", "at", "will", "more", "do", "we", "this", "or",
    "can", "I", "but", "if", "all", "so", "what", "about", "which",
    "when", "would", "make", "like", "no", "just", "him", "know",
    "take", "into", "year", "some", "could", "them", "see", "other",
    "than", "then", "now", "look", "only", "come", "its", "over",
    "also", "after", "use", "two", "how", "our", "work", "well",
    "way", "even", "new", "want", "because", "any", "these", "give",
    "day", "most", "us", "she", "her", "his", "my", "an", "who",
    "been", "said", "had", "was", "were", "did", "got", "may",
    "shall", "should", "must", "get", "go", "say", "me", "very",
    "up", "out", "there", "where", "here", "each", "every", "much",
    "many", "such", "both", "own", "still", "too",
}

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "kaoyan.db")


def download_json() -> list[dict]:
    """从 GitHub 下载词库 JSON"""
    print(f"正在下载词库: {JSON_URL}")
    req = urllib.request.Request(JSON_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    words = data.get("5530考研词汇词频排序表", [])
    print(f"下载完成，共 {len(words)} 词")
    return words


def import_to_db(words: list[dict]) -> int:
    """将词汇导入到 word_bank 表"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 确保表存在
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS word_bank (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            word        TEXT NOT NULL UNIQUE,
            meaning     TEXT NOT NULL,
            frequency   INTEGER DEFAULT 0,
            rank_order  INTEGER DEFAULT 0,
            category    TEXT DEFAULT 'core'
        )
    """)

    imported = 0
    skipped = 0
    for item in words:
        word = item.get("单词", "").strip()
        meaning = item.get("释义", "").strip()
        freq = item.get("词频", 0)
        rank = item.get("序号", 0)

        if not word or not meaning:
            continue

        # 跳过超基础词
        if word.lower() in SKIP_WORDS:
            skipped += 1
            continue

        # 判断类别：前 2444 个为高频核心词
        category = "core" if rank <= 2444 else "advanced"

        try:
            cursor.execute(
                """INSERT OR IGNORE INTO word_bank (word, meaning, frequency, rank_order, category)
                   VALUES (?, ?, ?, ?, ?)""",
                (word, meaning, freq, rank, category),
            )
            if cursor.rowcount > 0:
                imported += 1
        except sqlite3.Error as e:
            print(f"  跳过 {word}: {e}")

    conn.commit()
    conn.close()
    print(f"导入完成：成功 {imported} 词，跳过基础词 {skipped} 个")
    return imported


def main():
    if not os.path.exists(DB_PATH):
        print(f"数据库不存在: {DB_PATH}")
        print("请先执行 init.sql 初始化数据库")
        return

    words = download_json()
    count = import_to_db(words)

    # 验证
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM word_bank")
    total = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM word_bank WHERE category = 'core'")
    core = cursor.fetchone()[0]
    conn.close()

    print(f"\n词库统计：")
    print(f"  总词数: {total}")
    print(f"  核心词: {core}")
    print(f"  进阶词: {total - core}")


if __name__ == "__main__":
    main()
