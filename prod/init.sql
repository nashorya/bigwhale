-- 上岸 考研全科陪伴机器人
-- 数据库初始化脚本 v3.2
-- 执行方式：python -c "import sqlite3; sqlite3.connect('data/kaoyan.db').executescript(open('init.sql').read())"

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ─────────────────────────────────────────────
-- 用户层
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS users (
    user_id         TEXT PRIMARY KEY,           -- SHA-256 哈希后的用户标识
    invite_code     TEXT UNIQUE NOT NULL,        -- 6位大写邀请码
    invited_by      TEXT,                        -- 邀请者 user_id，自然注册为 NULL
    registered_at   DATETIME NOT NULL DEFAULT (datetime('now')),
    init_complete   BOOLEAN NOT NULL DEFAULT 0,  -- 是否完成初始化向导
    invite_settled  BOOLEAN NOT NULL DEFAULT 0,  -- 邀请积分是否已结算
    is_banned       BOOLEAN NOT NULL DEFAULT 0,  -- v3.3: 是否被封禁
    ban_reason      TEXT,                        -- v3.3: 封禁原因
    banned_at       DATETIME,                    -- v3.3: 封禁时间
    FOREIGN KEY (invited_by) REFERENCES users(user_id)
);

-- ─────────────────────────────────────────────
-- 目标院校与科目
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS target_schools (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    school_name     TEXT NOT NULL,
    major_name      TEXT NOT NULL,
    is_primary      BOOLEAN NOT NULL DEFAULT 0,
    subjects        TEXT NOT NULL,              -- JSON 数组，该院校考试科目列表
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS subjects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    name            TEXT NOT NULL,              -- 科目名称
    category        TEXT NOT NULL,              -- 公共课 / 专业课
    library_type    TEXT NOT NULL DEFAULT 'B',  -- A=统考预置 / B=用户自定义
    syllabus_source TEXT,                       -- A类：考纲来源；B类：'user_upload'
    user_warned     BOOLEAN NOT NULL DEFAULT 0, -- B类是否已推送过大纲提示
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS subject_status (
    user_id         TEXT NOT NULL,
    subject_id      INTEGER NOT NULL,
    school_id       INTEGER,                    -- 关联院校，NULL 表示公共课
    status          TEXT NOT NULL DEFAULT 'active', -- active / suspended / deleted
    suspended_at    DATETIME,
    suspend_reason  TEXT,
    PRIMARY KEY (user_id, subject_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id),
    FOREIGN KEY (school_id) REFERENCES target_schools(id)
);

-- ─────────────────────────────────────────────
-- 知识点层
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS syllabus_nodes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_id      INTEGER NOT NULL,
    parent_id       INTEGER,                    -- NULL 为一级章节
    node_name       TEXT NOT NULL,
    node_level      INTEGER NOT NULL,           -- 1=章节，2=考点
    is_system       BOOLEAN NOT NULL DEFAULT 1, -- 1=系统预置只读，0=用户添加
    importance_hint INTEGER NOT NULL DEFAULT 2, -- 1-3，系统建议重要程度
    FOREIGN KEY (subject_id) REFERENCES subjects(id),
    FOREIGN KEY (parent_id) REFERENCES syllabus_nodes(id)
);

CREATE TABLE IF NOT EXISTS knowledge_points (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    subject_id      INTEGER NOT NULL,
    topic_name      TEXT NOT NULL,
    mastery_level   INTEGER NOT NULL DEFAULT 1, -- 1-5
    last_review_at  DATETIME,
    importance      INTEGER NOT NULL DEFAULT 2, -- 1-3
    next_review_at  DATETIME,
    cross_links     TEXT,                       -- JSON 数组，关联知识点 id
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id)
);

-- ─────────────────────────────────────────────
-- 日程层
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS user_schedule (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    day_of_week     INTEGER NOT NULL,           -- 1-7
    time_slot       TEXT NOT NULL,              -- 如 "08:55-09:40"
    subject_id      INTEGER,
    is_idle         BOOLEAN NOT NULL DEFAULT 0,
    override_until  DATETIME,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id)
);

CREATE TABLE IF NOT EXISTS exam_config (
    user_id         TEXT PRIMARY KEY,
    exam_date       DATE NOT NULL,              -- 考试日期
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    updated_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS daily_plan (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date           DATE NOT NULL,
    user_id             TEXT NOT NULL,
    kp_id               INTEGER NOT NULL,
    priority_score      REAL NOT NULL,
    estimated_minutes   INTEGER NOT NULL,
    status              TEXT NOT NULL DEFAULT 'pending', -- pending / done / skipped
    completed_at        DATETIME,
    mastery_before      INTEGER,
    mastery_after       INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (kp_id) REFERENCES knowledge_points(id)
);

-- AI 驱动的周计划（按先修顺序排列）
CREATE TABLE IF NOT EXISTS weekly_plan (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             TEXT NOT NULL,
    week_start          DATE NOT NULL,              -- 本轮计划开始日期
    plan_date           DATE NOT NULL,              -- 具体日期
    day_index           INTEGER NOT NULL,           -- 0-6，从 week_start 起第几天
    subject_id          INTEGER,                    -- 关联科目（可为 NULL 如单词）
    kp_id               INTEGER,                    -- 关联知识点（可为 NULL）
    topic_name          TEXT NOT NULL,              -- 知识点名称快照
    subject_name        TEXT NOT NULL,              -- 科目名称快照
    order_in_day        INTEGER NOT NULL DEFAULT 0, -- 同一天内的排列顺序
    estimated_minutes   INTEGER NOT NULL DEFAULT 60,
    status              TEXT NOT NULL DEFAULT 'pending', -- pending / done / skipped
    completed_at        DATETIME,
    notes               TEXT,                       -- LLM 生成的学习建议
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (subject_id) REFERENCES subjects(id),
    FOREIGN KEY (kp_id) REFERENCES knowledge_points(id)
);


-- ─────────────────────────────────────────────
-- 打卡层
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS checkin_streak (
    user_id             TEXT PRIMARY KEY,
    current_streak      INTEGER NOT NULL DEFAULT 0,
    longest_streak      INTEGER NOT NULL DEFAULT 0,
    last_complete_date  DATE,
    total_checkins      INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS checkin_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    kp_id           INTEGER NOT NULL,
    kp_name         TEXT NOT NULL,              -- 快照，防止删除后丢失
    subject_name    TEXT NOT NULL,              -- 快照
    checkin_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    mastery_before  INTEGER,
    mastery_after   INTEGER,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- ─────────────────────────────────────────────
-- 英语推词层
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS word_bank (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word        TEXT NOT NULL UNIQUE,
    meaning     TEXT NOT NULL,
    phase       TEXT NOT NULL DEFAULT 'base',   -- base / intensive / sprint
    frequency   INTEGER NOT NULL DEFAULT 2      -- 1-3，词频重要性
);

CREATE TABLE IF NOT EXISTS user_word_status (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    word_id         INTEGER NOT NULL,
    weight          REAL NOT NULL DEFAULT 1.0,  -- 错题本权重
    correct_streak  INTEGER NOT NULL DEFAULT 0,
    last_pushed_at  DATETIME,
    in_error_book   BOOLEAN NOT NULL DEFAULT 0,
    UNIQUE (user_id, word_id),
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (word_id) REFERENCES word_bank(id)
);

-- ─────────────────────────────────────────────
-- 人物卡与情绪层
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS persona_config (
    user_id             TEXT PRIMARY KEY,
    active_persona      TEXT NOT NULL DEFAULT 'kitty',
    persona_since       DATETIME NOT NULL DEFAULT (datetime('now')),
    companion_mode      BOOLEAN NOT NULL DEFAULT 0,
    last_emotion_at     DATETIME,
    unlocked_personas   TEXT NOT NULL DEFAULT '["kitty"]', -- JSON 数组
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS emotion_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    triggered_by    TEXT NOT NULL,  -- user_confide / system_detect / command
    trigger_detail  TEXT,
    persona_used    TEXT NOT NULL,
    session_start   DATETIME NOT NULL DEFAULT (datetime('now')),
    session_end     DATETIME,
    mood_signal     TEXT,           -- JSON，检测到的情绪关键词
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- ─────────────────────────────────────────────
-- 管理员操作日志（v3.3）
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS admin_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    admin_qq_suffix TEXT NOT NULL,               -- 操作者QQ后4位（不存完整QQ）
    action          TEXT NOT NULL,               -- 操作类型
    target_user_id  TEXT NOT NULL,               -- 目标用户 user_id（哈希值）
    detail          TEXT,                        -- 操作详情JSON
    created_at      DATETIME NOT NULL DEFAULT (datetime('now'))
);

-- ─────────────────────────────────────────────
-- 积分层
-- ─────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS points_account (
    user_id                 TEXT PRIMARY KEY,
    balance                 INTEGER NOT NULL DEFAULT 0,
    total_earned            INTEGER NOT NULL DEFAULT 0,
    total_spent             INTEGER NOT NULL DEFAULT 0,
    subscription_active     BOOLEAN NOT NULL DEFAULT 0,
    word_tier               TEXT NOT NULL DEFAULT 'basic', -- basic / enhanced / sprint
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS points_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    delta           INTEGER NOT NULL,           -- 正数收入，负数支出
    balance_after   INTEGER NOT NULL,           -- 变动后余额快照
    reason          TEXT NOT NULL,              -- 见文档枚举值
    ref_id          TEXT,                       -- 关联业务 id
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- ─────────────────────────────────────────────
-- Memory 系统（语义记忆 + embedding 搜索）
-- ─────────────────────────────────────────────

-- 每日记忆：短期学习事件记录
CREATE TABLE IF NOT EXISTS user_memory_daily (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    log_date        DATE NOT NULL,                  -- 记忆日期
    category        TEXT NOT NULL,                  -- checkin/emotion/plan/study
    content         TEXT NOT NULL,                  -- 记忆内容
    embedding       BLOB,                           -- 智谱 embedding-3 向量（2048维 float32）
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 长期记忆：沉淀后的用户事实
CREATE TABLE IF NOT EXISTS user_memory_long (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    fact            TEXT NOT NULL,                   -- 事实描述
    embedding       BLOB,                           -- 智谱 embedding-3 向量
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- ─────────────────────────────────────────────
-- 学期备考规划（AI 生成）
-- ─────────────────────────────────────────────

-- 月度备考目标
CREATE TABLE IF NOT EXISTS monthly_goals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    plan_version    INTEGER NOT NULL DEFAULT 1,     -- 规划版本（重新生成时递增）
    month           TEXT NOT NULL,                  -- 月份 YYYY-MM
    subject_name    TEXT NOT NULL,                  -- 科目名称
    goal_title      TEXT NOT NULL,                  -- 月度目标标题
    goal_detail     TEXT,                           -- 目标详情/关键知识点
    priority        INTEGER NOT NULL DEFAULT 2,     -- 优先级 1-3
    status          TEXT NOT NULL DEFAULT 'pending', -- pending/in_progress/done
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 学期规划元信息
CREATE TABLE IF NOT EXISTS study_plan (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    plan_version    INTEGER NOT NULL DEFAULT 1,     -- 规划版本
    plan_json       TEXT NOT NULL,                  -- 完整规划 JSON（Claude 生成的原始结果）
    exam_date       DATE NOT NULL,                  -- 生成时的目标考试日期
    total_months    INTEGER NOT NULL,               -- 规划覆盖的总月数
    water_courses   TEXT,                           -- JSON 数组：水课列表 [{name, day_of_week, time_slot}]
    timetable_image TEXT,                           -- 课表图片路径（如果有）
    model_used      TEXT,                           -- 生成时使用的 AI 模型
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 用户课表（大学上课时间约束）
CREATE TABLE IF NOT EXISTS user_timetable (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    timetable_months TEXT,                           -- 课表适用月份（如 '3-6月'）
    timetable_json  TEXT NOT NULL,                   -- 结构化课表 JSON {headers, slots, busy}
    free_desc       TEXT,                            -- 空闲时段描述（供 prompt 注入）
    source_type     TEXT DEFAULT 'xlsx',             -- 'xlsx' 或 'image'
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- 核心知识清单（AI 生成的科目×知识点×重要度）
CREATE TABLE IF NOT EXISTS knowledge_checklist (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    plan_version    INTEGER DEFAULT 1,
    subject         TEXT NOT NULL,
    topic           TEXT NOT NULL,
    importance      TEXT DEFAULT 'medium',        -- high / medium / low
    mastery         INTEGER DEFAULT 0,            -- 1-5，初始为 0
    suggested_month TEXT,                         -- 建议学习月份
    notes           TEXT,
    created_at      DATETIME NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

-- ─────────────────────────────────────────────
-- 英语推词
-- ─────────────────────────────────────────────

-- 考研英语词库（全局共享，不区分用户）
CREATE TABLE IF NOT EXISTS word_bank (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    word        TEXT NOT NULL UNIQUE,              -- 英文单词
    meaning     TEXT NOT NULL,                     -- 中文释义
    frequency   INTEGER DEFAULT 0,                 -- 考研真题词频
    rank_order  INTEGER DEFAULT 0,                 -- 词频排名（1=最高频）
    category    TEXT DEFAULT 'core'                -- core/basic（超高频词如 the/a 标为 basic）
);

-- 用户单词学习状态
CREATE TABLE IF NOT EXISTS user_word_status (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         TEXT NOT NULL,
    word_id         INTEGER NOT NULL REFERENCES word_bank(id),
    weight          REAL DEFAULT 1.0,              -- 推送权重，越高越优先推
    correct_streak  INTEGER DEFAULT 0,             -- 连续答对次数
    total_seen      INTEGER DEFAULT 0,             -- 总见面次数
    total_correct   INTEGER DEFAULT 0,             -- 总答对次数
    in_error_book   INTEGER DEFAULT 0,             -- 是否在错词本中
    last_seen_at    TEXT,                           -- 上次见面时间
    created_at      TEXT DEFAULT (datetime('now')),
    UNIQUE(user_id, word_id)
);

-- ─────────────────────────────────────────────
-- 索引
-- ─────────────────────────────────────────────

CREATE INDEX IF NOT EXISTS idx_kp_user_subject ON knowledge_points(user_id, subject_id);
CREATE INDEX IF NOT EXISTS idx_kp_next_review ON knowledge_points(user_id, next_review_at);
CREATE INDEX IF NOT EXISTS idx_daily_plan_date ON daily_plan(user_id, plan_date);
CREATE INDEX IF NOT EXISTS idx_checkin_history_user ON checkin_history(user_id, checkin_at);
CREATE INDEX IF NOT EXISTS idx_points_ledger_user ON points_ledger(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_emotion_log_user ON emotion_log(user_id, session_start);
CREATE INDEX IF NOT EXISTS idx_memory_daily_user_date ON user_memory_daily(user_id, log_date);
CREATE INDEX IF NOT EXISTS idx_memory_long_user ON user_memory_long(user_id);
CREATE INDEX IF NOT EXISTS idx_monthly_goals_user_month ON monthly_goals(user_id, month);
CREATE INDEX IF NOT EXISTS idx_study_plan_user ON study_plan(user_id, plan_version);
CREATE INDEX IF NOT EXISTS idx_word_bank_rank ON word_bank(rank_order);
CREATE INDEX IF NOT EXISTS idx_word_bank_category ON word_bank(category);
CREATE INDEX IF NOT EXISTS idx_user_word_status_user ON user_word_status(user_id, weight DESC);
CREATE INDEX IF NOT EXISTS idx_user_word_status_error ON user_word_status(user_id, in_error_book);
