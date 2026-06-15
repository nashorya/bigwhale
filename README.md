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
# 编辑项目根目录下的 .env
```

关键配置：

| 变量 | 必填 | 用途 |
|------|------|------|
| `ONEBOT_ACCESS_TOKEN` | QQ Bot 必填 | NapCat / Lagrange 的 Access Token |
| `ONEBOT_WS_URL` | QQ Bot 必填 | OneBot WebSocket 地址，默认 `ws://127.0.0.1:3001` |
| `SHORE_USER_SALT` | 必填 | 用户 ID 哈希盐值，部署后不要更换 |
| `API_KEY` | AI 必填 | OpenAI 兼容协议 API Key |
| `BASE_URL` | AI 必填 | OpenAI 兼容协议 Base URL |
| `CHAT_MODEL` | AI 必填 | 所有 AI 能力统一使用的模型名 |
| `TAVILY_API_KEY` | 可选 | 知识点搜索 |
| `ZHIPU_API_KEY` | 可选 | 语义记忆 / embedding |
| `DB_PATH` | 可选 | SQLite 数据库路径，默认 `data/kaoyan.db` |
| `ADMIN_QQ_LIST` | 可选 | 管理员 QQ 号列表 |

示例：

```env
SHORE_USER_SALT=用随机字符串替换
API_KEY=你的 API Key
BASE_URL=https://你的兼容协议地址/v1
CHAT_MODEL=你的模型名
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
│   │   ├── web_api.py          # 官网 API（聊天、网页计划）
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
├── web/                        # React + Vite 官网前端
│   ├── src/
│   └── public/
│
└── tests/                      # 单元测试
```

---

## 🌐 官网（Web 前端）

`web/` 目录是项目官网（React + Vite），支持在网页上直接和 Bot 对话，也支持网页用户维护自己的学习计划。

```bash
# 1. 启动 Bot 后端（自带 /api/chat 等接口，监听 127.0.0.1:8080）
python main.py

# 2. 启动官网开发服务器
cd web
npm install
npm run dev       # 打开 http://localhost:5173

# 生产构建
npm run build     # 产物在 web/dist/
```

Web API（注册在 NoneBot2 内置 FastAPI 上，见 `plugins/shore/handlers/web_api.py`）：

| 接口 | 说明 |
|------|------|
| `GET /api/personas` | 角色列表 |
| `POST /api/chat` | 网页聊天 `{session_id, persona_id, message}` |
| `POST /api/chat/reset` | 清空会话历史 |
| `GET /api/plan?session_id=...` | 读取当前网页用户最近一轮学习计划 |
| `POST /api/plan/generate` | 根据用户输入的学习目标，由 AI 生成 7 天计划并保存 |
| `PUT /api/plan` | 保存网页用户本周计划 `{session_id, week_start, items}` |
| `POST /api/plan/status` | 更新单条计划状态 `{session_id, plan_id, status}` |

`POST /api/plan/generate` 会调用后端 AI 服务。请先在项目根目录 `.env` 中配置 `API_KEY`、`BASE_URL`、`CHAT_MODEL`；后端按 OpenAI 兼容协议调用，具体中转站和模型由你自己配置。

网页用户以浏览器生成的随机 `session_id` 标识。后端会先清洗输入，再用 `hash_user_id("web:<session_id>")` 得到不可逆用户 ID：

- 聊天历史仍只存在内存中，Bot 重启即清空。
- 网页学习计划会落库到现有 `weekly_plan` 表，`subject_id/kp_id` 可为空，用于网页自定义计划。
- 数据库不存原始 `session_id`，只存哈希后的 `user_id`。
- 网页计划读写同样通过 `UserDB`，并自动携带 `WHERE user_id = ?` 用户隔离。

网页计划页主流程是：用户输入想学习的内容 → AI 生成 7 天计划 → 表格化渲染 → 用户微调后保存。也支持新增、删除本地条目，以及标记完成/待办。保存时限制在当前 `week_start` 起 7 天内，避免网页端把周计划表当作无限笔记存储。

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
