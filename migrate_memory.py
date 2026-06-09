"""
Memory 系统数据库迁移脚本。
在已有数据库上新增 user_memory_daily 和 user_memory_long 表。
适用于不想重建数据库的场景。

用法：python migrate_memory.py
"""

import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "data/kaoyan.db")

MIGRATION_SQL = """
-- Memory 系统表
CREATE TABLE IF NOT EXISTS user_memory_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    log_date        DATE NOT NULL,
    category        TEXT NOT NULL,
    content         TEXT NOT NULL,
    embedding       BLOB,
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS user_memory_long (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    fact            TEXT NOT NULL,
    embedding       BLOB,
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_memory_daily_user_date ON user_memory_daily(user_id, log_date);
CREATE INDEX IF NOT EXISTS idx_memory_long_user ON user_memory_long(user_id);
"""


def main():
    if not os.path.exists(DB_PATH):
        print(f"数据库文件不存在: {DB_PATH}")
        print("请先执行 init.sql 初始化数据库。")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(MIGRATION_SQL)
    conn.close()
    print("✅ Memory 系统表迁移完成！")
    print("   - user_memory_daily")
    print("   - user_memory_long")


if __name__ == "__main__":
    main()
