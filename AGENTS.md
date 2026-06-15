# 上岸 项目规则

## 项目简介

基于 NoneBot2 的 QQ 私聊考研陪伴机器人，只处理私聊消息。

## 核心约束（每次生成代码前必须检查）

1. 所有数据库操作必须通过 `UserDB` 类，禁止直接使用 conn.execute
2. 所有用户输入必须经过 `sanitize_input()` 清洗
3. 数据库不存储原始 QQ 号，只存 hash_user_id(qq) 的结果
4. 所有查询必须带 WHERE user_id = ?，禁止裸查
5. 只处理 PrivateMessageEvent，群消息一律忽略
6. 人物卡从 /personas/builtin/\*.json 读取，不硬编码
7. 表结构以 init.sql 为准，不自行创建新表
8. core/ 目录下不得 import 任何 nonebot.\* 模块
9. handlers/ 只做：解析输入 → 调用 core/ → 渲染结果，不含业务判断

## 技术栈

- Python 3.10+，全程异步（asyncio）
- NoneBot2 + nonebot-adapter-onebot（v11）
- aiosqlite（异步 SQLite 驱动）
- APScheduler 3.x（AsyncIOScheduler）
- python-dotenv（读取 .env）
- ruff（代码格式化，提交前必须格式化）

## 数据库表结构

见 init.sql（项目根目录）

## 目录结构

见 TECH_SPEC.md

## 常用命令

```bash
# 初始化数据库
python -c "import sqlite3; conn=sqlite3.connect('data/kaoyan.db'); conn.executescript(open('init.sql').read()); conn.close()"

# 运行测试
python -m pytest tests/ -v

# 格式化代码
ruff format .
ruff check . --fix

# 启动 Bot
python main.py
```
