import sqlite3

conn = sqlite3.connect('data/kaoyan.db')

migrations = [
    "ALTER TABLE users ADD COLUMN is_banned BOOLEAN NOT NULL DEFAULT 0",
    "ALTER TABLE users ADD COLUMN ban_reason TEXT",
    "ALTER TABLE users ADD COLUMN banned_at DATETIME",
    """CREATE TABLE IF NOT EXISTS admin_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_qq_suffix TEXT NOT NULL,
        action TEXT NOT NULL,
        target_user_id TEXT NOT NULL,
        detail TEXT,
        created_at DATETIME NOT NULL DEFAULT (datetime('now'))
    )""",
]

for sql in migrations:
    try:
        conn.execute(sql)
        print(f"OK: {sql[:40]}...")
    except Exception as e:
        print(f"跳过: {e}")

conn.commit()
conn.close()
print("迁移完成")