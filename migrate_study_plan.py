"""
P1 数据库迁移脚本：新增学期备考规划相关表。
- monthly_goals：月度备考目标
- study_plan：学期规划元信息

用法：python migrate_study_plan.py
"""

import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "data/kaoyan.db")

MIGRATION_SQL = """
-- 月度备考目标
CREATE TABLE IF NOT EXISTS monthly_goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    plan_version    INTEGER NOT NULL DEFAULT 1,
    month           TEXT NOT NULL,
    subject_name    TEXT NOT NULL,
    goal_title      TEXT NOT NULL,
    goal_detail     TEXT,
    priority        INTEGER NOT NULL DEFAULT 2,
    status          TEXT NOT NULL DEFAULT 'pending',
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 学期规划元信息
CREATE TABLE IF NOT EXISTS study_plan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    plan_version    INTEGER NOT NULL DEFAULT 1,
    plan_json       TEXT NOT NULL,
    exam_date       DATE NOT NULL,
    total_months    INTEGER NOT NULL,
    water_courses   TEXT,
    timetable_image TEXT,
    model_used      TEXT,
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_monthly_goals_user_month ON monthly_goals(user_id, month);
CREATE INDEX IF NOT EXISTS idx_study_plan_user ON study_plan(user_id, plan_version);
"""


def main():
    if not os.path.exists(DB_PATH):
        print(f"数据库文件不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    conn.executescript(MIGRATION_SQL)
    conn.close()
    print("✅ 学期备考规划表迁移完成！")
    print("   - monthly_goals")
    print("   - study_plan")


if __name__ == "__main__":
    main()
