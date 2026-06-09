# 🦀 上岸 — 考研全科陪伴机器人

基于 **NoneBot2 + OneBot v11** 的 QQ 私聊考研陪伴 Bot。

> ⚠️ 本项目仅处理**私聊消息**，群消息一律忽略。

---

## ✨ 功能一览

| 模块 | 核心功能 |
|------|----------|
| 📋 系统 | 好友自动通过、初始化向导（`#开始`）、邀请码 |
| 🏫 院校 | 目标院校配置、倒计时、学情报告 |
| ✅ 打卡 | 知识点打卡、今日计划、连续打卡统计 |
| 📅 计划 | 基于遗忘曲线的智能学习计划、自动早晚推送 |
| 🎭 角色 | 四种陪伴角色切换（零绮/白泉/苏晚/纪律） |
| 📚 推词 | 知识点同步、错题本、掌握度追踪 |
| 💬 情绪 | 自动情绪检测、陪伴模式、情绪熔断 |
| 💰 积分 | 积分系统、邀请奖励、推词档位 |
| 🔧 管理 | 管理员指令面板 |

发送 `#帮助` 查看完整指令列表。

---

## 🛠 技术栈

- **Python 3.10+**（全程异步 asyncio）
- **NoneBot2** + nonebot-adapter-onebot（v11）
- **aiosqlite**（异步 SQLite 驱动）
- **APScheduler 3.x**（AsyncIOScheduler 定时任务）
- **python-dotenv**（.env 配置管理）
- **QQ 协议端**：NapCat / Lagrange（独立进程）

---

## 🚀 快速开始

### 1. 创建虚拟环境

```bash
cd kaoyan_bot
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境变量

```bash
cp env.example .env
# 编辑 .env，填入以下关键配置：
#   ONEBOT_ACCESS_TOKEN  — NapCat 的 Access Token
#   ONEBOT_WS_URL        — NapCat 的 WebSocket 地址
#   上岸_USER_SALT   — 用户 ID 哈希盐值（部署后永不更换）
#   ADMIN_QQ_LIST        — 管理员 QQ 号
```

### 4. 初始化数据库

```bash
mkdir data
python -c "import sqlite3; conn=sqlite3.connect('data/kaoyan.db'); conn.executescript(open('init.sql', encoding='utf-8').read()); conn.close(); print('数据库初始化完成')"
```

### 5. 安装 QQ 协议端

安装 [NapCat](https://napcat.napneko.icu/) 并配置：
- WebSocket 地址与 `.env` 中的 `ONEBOT_WS_URL` 一致
- Access Token 与 `.env` 中的 `ONEBOT_ACCESS_TOKEN` 一致

### 6. 启动

```bash
# 第一步：启动 NapCat → QQ 扫码登录
# 第二步：启动 Bot
python main.py
```

---

## 📁 项目结构

```
上岸/
├── main.py                     # NoneBot2 入口
├── .env                        # 环境变量（不提交 git）
├── init.sql                    # 数据库建表语句
├── requirements.txt
│
├── plugins/上岸/           # NoneBot2 插件
│   ├── handlers/               # 事件处理器（薄胶水层）
│   │   ├── system.py           # 好友申请、初始化向导
│   │   ├── school.py           # 院校配置
│   │   ├── checkin.py          # 打卡
│   │   ├── schedule.py         # 学习计划
│   │   ├── persona.py          # 角色切换
│   │   ├── words.py            # 推词与知识点
│   │   ├── emotion.py          # 情绪陪伴
│   │   ├── points.py           # 积分系统
│   │   ├── help.py             # 帮助指令
│   │   └── admin.py            # 管理员指令
│   │
│   └── core/                   # 核心业务逻辑（不依赖 NoneBot2）
│       ├── user_db.py          # 数据库操作封装
│       ├── security.py         # 用户 ID 哈希、输入清洗
│       ├── session.py          # Session 管理
│       ├── scheduler.py        # 学习计划生成、定时任务
│       ├── persona_engine.py   # 角色渲染引擎
│       ├── emotion_detector.py # 情绪信号检测
│       └── points_service.py   # 积分服务
│
├── personas/                   # 角色数据
│   ├── personas_index.json
│   └── builtin/                # 内置角色 JSON
│
├── data/
│   ├── kaoyan.db               # SQLite 数据库（不提交 git）
│   └── logs/
│
└── tests/                      # 单元测试
```

---

## 🧪 开发命令

```bash
# 运行测试
python -m pytest tests/ -v

# 格式化代码
ruff format .
ruff check . --fix

# 启动 Bot
python main.py
```

---

## 📝 核心约束

1. 所有数据库操作通过 `UserDB` 类，禁止直接拼 SQL
2. 不存储原始 QQ 号，只存 `hash_user_id(qq)` 的结果
3. `core/` 目录不依赖任何 `nonebot.*` 模块
4. `handlers/` 只做解析输入 → 调用 core → 渲染结果
5. 表结构以 `init.sql` 为准

---

## 📄 License

MIT
