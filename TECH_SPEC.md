# 上岸 技术栈与项目结构
### 给 AI 的开发参考文件 — 请在每次对话开始时附上此文件

---

## 技术栈

| 层级 | 选型 | 版本要求 | 说明 |
|------|------|---------|------|
| 语言 | Python | 3.10+ | 全程异步，使用 asyncio |
| Bot 框架 | NoneBot2 | 最新稳定版 | 事件驱动，插件化 |
| QQ 协议端 | NapCat | 最新版 | 独立进程，与 Bot 通过 WebSocket 通信 |
| NoneBot 适配器 | nonebot-adapter-onebot | v11 | aiocqhttp 兼容 |
| 数据库 | SQLite3 | 3.38+ | 使用 aiosqlite 异步驱动 |
| 定时任务 | APScheduler | 3.x | AsyncIOScheduler |
| 环境变量 | python-dotenv | 任意版本 | 读取 .env 文件 |
| 代码格式化 | ruff | 最新版 | 提交前必须格式化 |

---

## 核心约束（AI 必须遵守）

1. **所有数据库操作必须通过 `UserDB` 类进行**，禁止在业务代码中直接拼接 SQL
2. **所有用户输入必须经过 `sanitize_input()` 清洗**后再进入业务逻辑
3. **数据库中不存储原始 QQ 号**，只存 `hash_user_id(qq)` 的结果
4. **所有查询必须带 `WHERE user_id = ?`**，禁止裸查
5. **只处理私聊消息**，群消息一律忽略
6. **人物卡内容从 `/personas/builtin/*.json` 读取**，不硬编码在代码里
7. **所有数据库表结构以 `init.sql` 为准**，不自行创建新表

---

## 目录结构

```
上岸/
│
├── main.py                     # NoneBot2 入口，加载适配器和插件
├── .env                        # 环境变量（不提交 git）
├── .env.example                # 环境变量模板（提交 git）
├── requirements.txt
├── init.sql                    # 数据库建表语句（唯一事实来源）
│
├── /plugins
│   └── 上岸/               # 上岸 NoneBot2 插件
│       ├── __init__.py         # 插件入口，注册所有 handler
│       │
│       ├── /handlers           # NoneBot2 事件处理器（薄胶水层，不含业务逻辑）
│       │   ├── checkin.py      # 打卡相关指令
│       │   ├── schedule.py     # 日程相关指令
│       │   ├── persona.py      # 人物卡相关指令
│       │   ├── emotion.py      # 情绪陪伴相关指令
│       │   ├── points.py       # 积分相关指令
│       │   ├── school.py       # 院校配置相关指令
│       │   ├── words.py        # 推词相关指令
│       │   └── system.py       # 系统指令（好友申请、初始化向导等）
│       │
│       └── /core               # 核心业务逻辑（不依赖任何 NoneBot2 API）
│           ├── user_db.py      # UserDB 封装类，所有数据库操作入口
│           ├── session.py      # SessionManager + UserSession
│           ├── security.py     # hash_user_id + sanitize_input
│           ├── manager.py      # Manager：跨科权重调度，掌握度计算
│           ├── scheduler.py    # Scheduler：遗忘曲线，每日计划生成
│           ├── persona_engine.py # PersonaEngine：角色渲染，模板填充
│           ├── emotion_detector.py # EmotionDetector：情绪信号检测
│           └── points_service.py  # 积分系统：扣费、充值、查询
│
├── /personas
│   ├── personas_index.json     # 角色索引
│   └── /builtin
│       ├── lingqi.json         # 零绮·冷静理性系
│       ├── baiquan.json        # 白泉·元气活泼系
│       ├── suwan.json          # 苏晚·温柔治愈系
│       └── jilv.json           # 纪律·毒舌激励系
│
└── /data
    ├── kaoyan.db               # SQLite 数据库（不提交 git）
    └── /logs
```

---

## 模块职责边界

### handlers/ （薄胶水层）
- 只做三件事：① 解析用户输入 ② 调用 core/ 的业务方法 ③ 返回渲染结果
- 不含任何业务判断逻辑
- 不直接操作数据库

```python
# 正确示例
@on_command("打卡").handle()
async def handle_checkin(bot: Bot, event: PrivateMessageEvent):
    uid = security.get_or_create_uid(str(event.user_id))
    topic = sanitize_input(str(event.get_message()).replace("#打卡", "").strip())
    result = await manager.handle_checkin(uid, topic)
    response = persona_engine.render(uid, "checkin", result)
    await bot.send(event, response)
```

### core/ （业务逻辑层）
- 不 import 任何 `nonebot.*` 模块
- 所有数据库操作通过 `UserDB` 实例进行
- 返回结构化数据，不返回最终消息字符串（渲染由 PersonaEngine 负责）

---

## 环境变量（.env.example）

```
# QQ Bot 配置
ONEBOT_ACCESS_TOKEN=your_access_token_here

# 安全配置
上岸_USER_SALT=your_random_32_char_salt_here

# 数据库路径
DB_PATH=data/kaoyan.db

# 积分配置
REGISTER_BONUS=200
INVITE_REWARD_INVITER=100
INVITE_REWARD_INVITEE=50
DAILY_SUBSCRIPTION_COST=20
PERSONA_UNLOCK_COST=80
```

---

## IDE 规则文件（阶段 0 必须创建）

> 这两个文件是 vibe coding 最重要的基础设施。
> Claude Code 自动读取 `CLAUDE.md`，Cursor / Antigravity 自动读取 `.cursor/rules`。
> **在阶段 0 搭骨架时，让 AI 根据本文件生成这两个文件。**

### CLAUDE.md（Claude Code 专用）

放在项目根目录，Claude Code 每次启动自动加载。内容应包含：

```markdown
# 上岸 项目规则

## 项目简介
基于 NoneBot2 的 QQ 私聊考研陪伴机器人，只处理私聊消息。

## 核心约束（每次生成代码前必须检查）
1. 所有数据库操作必须通过 `UserDB` 类，禁止直接使用 conn.execute
2. 所有用户输入必须经过 `sanitize_input()` 清洗
3. 数据库不存储原始 QQ 号，只存 hash_user_id(qq) 的结果
4. 所有查询必须带 WHERE user_id = ?，禁止裸查
5. 只处理 PrivateMessageEvent，群消息一律忽略
6. 人物卡从 /personas/builtin/*.json 读取，不硬编码
7. 表结构以 init.sql 为准，不自行创建新表
8. core/ 目录下不得 import 任何 nonebot.* 模块
9. handlers/ 只做：解析输入 → 调用 core/ → 渲染结果，不含业务判断

## 开发记录规范
- 每次完成一个函数或模块后，在 DEVLOG.md 末尾追加记录，格式：
  ```
  ### [日期] [模块名]
  - 完成内容：
  - 遇到的问题及解法：
  - 设计决策及理由：
  - 遗留事项：
  ```
- 遇到以下情况，必须写入 QUESTIONS.md，不得自行假设后继续：
  · 文档描述模糊或有歧义
  · 需要在两种实现方案中选择
  · 发现文档之间存在矛盾
  · 需要新增文档未提及的功能
  格式：`- [ ] [模块名] 问题描述，待确认后继续`
- bug 修复后在 DEVLOG.md 记录：原因 + 解法 + 影响范围

## 数据库表结构
见 init.sql（项目根目录）

## 目录结构
见 TECH_SPEC.md

## 常用命令
# 初始化数据库（Windows）
python -c "import sqlite3; conn=sqlite3.connect('data/kaoyan.db'); conn.executescript(open('init.sql', encoding='utf-8').read()); conn.close()"

# 运行测试
python -m pytest tests/ -v

# 格式化代码
ruff format .
ruff check . --fix

# 启动 Bot
python main.py
```

### .cursor/rules（Cursor / Antigravity 专用）

放在 `.cursor/rules` 路径（新版 Cursor）或项目根目录 `.cursorrules`（旧版）。内容与 CLAUDE.md 保持一致，但格式更简短：

```
You are working on 上岸, a NoneBot2 QQ private-chat bot for graduate exam preparation.

STRICT RULES - check before every code generation:
- All DB operations via UserDB class only. No direct conn.execute in business logic.
- Never store raw QQ numbers. Always use hash_user_id(qq).
- All queries must include WHERE user_id = ?
- Only handle PrivateMessageEvent. Ignore group messages.
- core/ must NOT import any nonebot.* modules
- handlers/ is glue only: parse input → call core/ → render result
- Table schema is defined in init.sql. Do not create new tables.
- Persona content loaded from /personas/builtin/*.json only.

DOCUMENTATION RULES:
- After completing each function or module, append a record to DEVLOG.md:
  ### [date] [module]
  - Done:
  - Issues & solutions:
  - Design decisions:
  - Remaining:
- If anything is ambiguous, conflicting, or requires a design choice,
  write it to QUESTIONS.md as: - [ ] [module] description
  DO NOT assume and continue. Wait for confirmation.
- After fixing a bug, log it in DEVLOG.md: cause + fix + affected scope.

TECH STACK: Python 3.10+, NoneBot2, aiosqlite, APScheduler, python-dotenv
PROJECT STRUCTURE: See TECH_SPEC.md
```

### 生成这两个文件的 Prompt

在阶段 0 骨架生成时，加入以下指令：

```
同时生成以下两个 IDE 规则文件：
1. CLAUDE.md（项目根目录）：包含项目简介、核心约束列表、常用命令
2. .cursor/rules（.cursor/ 目录下）：同样的约束，英文简短格式

内容以 TECH_SPEC.md 中的核心约束为准，确保两个文件内容一致。
```

---

## 关键数据流

### 一条消息的完整处理链

```
QQ 用户发私聊消息
  ↓
NoneBot2 事件分发
  ↓
handlers/ 中对应的 handler
  ↓
security.get_or_create_uid(qq)     → 得到 user_id（哈希值）
security.sanitize_input(text)      → 清洗用户输入
  ↓
SessionManager.get(user_id)        → 取出内存 Session
UserDB(user_id, conn)              → 绑定数据库操作
  ↓
core/ 业务逻辑处理
  ↓
PersonaEngine.render(uid, key, data) → 渲染为角色口吻的消息
  ↓
bot.send(event, response)          → 发送给用户
```

### 定时任务链（每日）

```
00:05  Scheduler.calc_daily_plan()    → 计算所有用户当日知识点优先级
00:01  PointsService.daily_deduct()   → 扣除当日订阅费用
07:30  Scheduler.morning_push()       → 早安提醒 + 今日计划
22:30  Scheduler.evening_summary()    → 晚间复盘推送
```
