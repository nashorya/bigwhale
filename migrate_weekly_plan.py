"""
数据库迁移脚本：新增 weekly_plan 表
"""
import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "data/kaoyan.db")

def migrate():
    if not os.path.exists(DB_PATH):
        print(f"DB 不存在: {DB_PATH}")
        return
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
CREATE TABLE IF NOT EXISTS weekly_plan (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL,
    week_start          DATE NOT NULL,
    plan_date           DATE NOT NULL,
    day_index           INTEGER NOT NULL,
    subject_id          INTEGER,
    kp_id               INTEGER,
    topic_name          TEXT NOT NULL,
    subject_name        TEXT NOT NULL,
    order_in_day        INTEGER NOT NULL DEFAULT 0,
    estimated_minutes   INTEGER NOT NULL DEFAULT 60,
    scheduled_time      TEXT,
    reminder_sent       INTEGER NOT NULL DEFAULT 0,
    status              TEXT NOT NULL DEFAULT 'pending',
    completed_at        DATETIME,
    notes               TEXT
);
CREATE INDEX IF NOT EXISTS idx_weekly_plan_user_week ON weekly_plan(user_id, week_start);
CREATE INDEX IF NOT EXISTS idx_weekly_plan_date ON weekly_plan(user_id, plan_date);
"""
    )
    # 如果表已存在，尝试添加新列（旧表兼容）
    try:
        conn.execute("ALTER TABLE weekly_plan ADD COLUMN scheduled_time TEXT")
    except Exception:
        pass
    try:
        conn.execute("ALTER TABLE weekly_plan ADD COLUMN reminder_sent INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass
    conn.commit()
    conn.commit()
    conn.close()
    print("✅ 迁移完成：weekly_plan 表已创建")

if __name__ == "__main__":
    migrate()
