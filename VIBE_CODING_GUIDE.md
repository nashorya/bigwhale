# 上岸 Vibe Coding 完整流程
### 开发前请完整阅读此文件

---

## 准备工作

### 需要给 AI 的文件清单

每次开始新的开发会话时，按以下顺序附上文件：

| 文件 | 说明 | 是否每次都附 |
|------|------|------------|
| `TECH_SPEC.md` | 技术栈 + 目录结构 + 约束规则 | ✅ 每次必附 |
| `init.sql` | 数据库表结构 | ✅ 每次必附 |
| `v3.0 主文档` | 架构 + 核心功能 + 人物卡 + 情绪陪伴 | 按需附 |
| `v3.1 补充文档` | 知识点库 + 日程分布 + 打卡反馈 | 按需附 |
| `v3.2 补充文档` | 安全机制 + Session + 积分系统 | 按需附 |

> **原则**：每次只附当前模块相关的文档，避免上下文过长导致 AI 混乱。
> `TECH_SPEC.md` 和 `init.sql` 是例外，每次都要附。

---

## 开发阶段与顺序

### 阶段 0：环境搭建（不需要 AI，自己做）

```bash
# 1. 创建项目目录
mkdir 上岸 && cd 上岸

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install nonebot2 nonebot-adapter-onebot aiosqlite apscheduler python-dotenv ruff

# 4. 初始化数据库
mkdir data
python -c "
import sqlite3
conn = sqlite3.connect('data/kaoyan.db')
conn.executescript(open('init.sql').read())
conn.close()
print('数据库初始化完成')
"

# 5. 复制 .env.example 为 .env 并填写
cp .env.example .env
# 编辑 .env，填入 SALT 和 TOKEN

# 6. 安装 NapCat（单独进程，参考 NapCat 官方文档）
```

---

### 阶段 1：项目骨架

**附上文件**：`TECH_SPEC.md` + `init.sql`

**给 AI 的 Prompt**：
```
请根据 TECH_SPEC.md 的目录结构，帮我创建 上岸 的项目骨架。

需要完成：
1. main.py（NoneBot2 入口，配置 OneBot v11 适配器，只处理私聊）
2. requirements.txt
3. .env.example
4. plugins/上岸/__init__.py（插件入口，暂时为空）
5. 所有 core/ 和 handlers/ 的空文件（只有 import 和空类定义）
6. CLAUDE.md（项目根目录，Claude Code 规则文件）
7. .cursor/rules（Cursor / Antigravity 规则文件）

CLAUDE.md 和 .cursor/rules 的内容以 TECH_SPEC.md 中的核心约束为准，
确保两个文件约束内容一致，CLAUDE.md 中文，.cursor/rules 英文。

注意：
- 只处理 PRIVATE_MESSAGE，群消息一律忽略
- core/ 目录下不得 import 任何 nonebot 模块
```

**验收标准**：`python main.py` 能启动，NoneBot2 正常运行，无报错。

---

### 阶段 2：安全层与 Session

**附上文件**：`TECH_SPEC.md` + `init.sql` + `v3.2 安全机制章节`

**给 AI 的 Prompt**：
```
请实现 core/security.py 和 core/session.py。

security.py 需要：
- hash_user_id(qq: str) -> str：从环境变量读取 SALT，SHA-256 哈希
- get_or_create_uid(qq: str) -> str：内存映射，不落库
- sanitize_input(text: str, max_length: int = 100) -> str：清洗用户输入

session.py 需要：
- UserSession dataclass（字段见 v3.2 文档）
- SessionManager：get / evict_idle 方法
- 超过 120 分钟无活动自动卸载 Session

所有代码必须是异步的，不得使用任何 nonebot 模块。
```

**验收标准**：写单元测试，验证同一 QQ 号始终得到相同 hash，不同 QQ 号得到不同 hash。

---

### 阶段 3：数据库层

**附上文件**：`TECH_SPEC.md` + `init.sql` + `v3.2 UserDB 章节`

**给 AI 的 Prompt**：
```
请实现 core/user_db.py。

要求：
- 使用 aiosqlite 异步驱动
- UserDB 类实例化时绑定 user_id，所有方法自动携带 WHERE user_id = ?
- 实现以下方法（全部参数化查询，禁止字符串拼接）：
  · get_active_persona(self) -> str
  · get_knowledge_points(self, subject_id: int) -> list
  · update_mastery(self, kp_id: int, new_level: int) -> None
  · get_points_balance(self) -> int
  · deduct_points(self, amount: int, reason: str, ref_id: str = None) -> bool
  · get_daily_plan(self, date: str) -> list
  · get_user_schedule(self) -> list
  · get_exam_date(self) -> str | None

同时实现一个 get_db_conn() 异步上下文管理器，用于获取数据库连接。
```

**验收标准**：写单元测试，用测试数据库验证每个方法，确认不同 user_id 的数据完全隔离。

---

### 阶段 4：人物卡系统

**附上文件**：`TECH_SPEC.md` + `v3.0 人物卡章节` + `v3.1 人物卡文件系统章节`

**给 AI 的 Prompt**：
```
请完成两件事：

1. 生成四张人物卡的完整 JSON 文件（参考 v3.0 文档的 tone_profile 和脚本内容）：
   - personas/builtin/lingqi.json
   - personas/builtin/baiquan.json
   - personas/builtin/suwan.json
   - personas/builtin/jilv.json
   - personas/personas_index.json

2. 实现 core/persona_engine.py：
   - 启动时加载所有 builtin JSON 到内存
   - render(user_id, script_key, data: dict) -> str
     · 从数据库读取该用户的 active_persona
     · 查找对应角色的 script_key 模板
     · 填充 data 中的变量（{kp_name} 等）
     · 20% 概率随机插入 catchphrase
     · 按 emoji_set 决定是否附加 emoji
   - get_persona_list() -> list（用于 #选择角色 指令）
```

**验收标准**：同一个 script_key，用不同角色渲染，输出风格明显不同。

---

### 阶段 5：调度核心

**附上文件**：`TECH_SPEC.md` + `init.sql` + `v3.1 每日学习计划自动分布算法章节`

**给 AI 的 Prompt**：
```
请实现 core/scheduler.py。

需要：
1. calc_priority(kp, days_left) 函数：
   按 v3.1 文档的公式计算知识点优先级（base × forget_factor × overdue_factor × sprint_factor）

2. generate_daily_plan(user_id) 异步方法：
   - 读取用户考试日期，计算 days_left
   - 读取所有 active 科目的知识点
   - 按优先级排序，按科目时间比例分配
   - 写入 daily_plan 表

3. APScheduler 定时任务注册：
   - 00:05 → 所有用户重新生成每日计划
   - 07:30 → 早安推送（暂时只生成内容，推送在 handler 层调用）
   - 22:30 → 晚间复盘（同上）

注意：scheduler.py 不得直接调用 bot.send，只负责生成内容和写数据库。
推送由 handlers/schedule.py 中的定时任务调用。
```

**验收标准**：手动调用 `generate_daily_plan`，检查 daily_plan 表数据是否符合优先级排序。

---

### 阶段 6：情绪检测

**附上文件**：`TECH_SPEC.md` + `v3.0 情绪陪伴模块章节`

**给 AI 的 Prompt**：
```
请实现 core/emotion_detector.py。

需要：
1. EMOTION_KEYWORDS 词库字典（按 v3.0 文档分类：焦虑/沮丧/疲惫/求陪伴）
2. detect(text: str) -> tuple[bool, str | None]：
   - 返回 (是否触发, 触发类型)
   - 阈值：≥2 个信号词，或 1 个强信号词（求陪伴类全部为强信号词）
   - 宁可漏检不可误触发
3. check_system_anomaly(user_id: str, db: UserDB) -> tuple[bool, str] | None：
   检查系统异常触发条件（连续低正确率/长时间未打卡/深夜活跃/连续缺卡）
4. start_session(user_id: str, triggered_by: str, db: UserDB) -> None：
   写入 emotion_log，更新 Session 的 companion_mode = True
5. end_session(user_id: str, db: UserDB) -> None：
   更新 emotion_log.session_end，companion_mode = False
```

**验收标准**：用不同文本测试 detect()，确认阈值正确，不会对普通消息误触发。

---

### 阶段 7：积分系统

**附上文件**：`TECH_SPEC.md` + `init.sql` + `v3.2 积分系统章节`

**给 AI 的 Prompt**：
```
请实现 core/points_service.py。

需要：
1. register_bonus(user_id: str, db: UserDB)：注册赠送 200 积分
2. daily_deduct(user_id: str, db: UserDB) -> bool：
   每日扣除订阅费（20积分基础 + 推词档位附加）
   余额不足时返回 False，不扣费
3. spend(user_id: str, amount: int, reason: str, db: UserDB) -> bool：
   通用扣费，余额不足返回 False
4. grant(user_id: str, amount: int, reason: str, db: UserDB, ref_id=None)：
   积分发放，写入 points_ledger
5. get_account_summary(user_id: str, db: UserDB) -> dict：
   返回余额、预计可用天数、最近5条流水、人物卡解锁状态
6. settle_invite(invitee_uid: str, db: UserDB)：
   被邀请者完成初始化后，给双方结算积分

所有方法必须在单个数据库事务内完成，防止积分丢失。
```

**验收标准**：模拟一个完整的注册→消费→余额不足流程，检查 points_ledger 流水是否正确。

---

### 阶段 8：NoneBot2 Handler 层

**附上文件**：`TECH_SPEC.md` + 对应功能的文档章节

**给 AI 的 Prompt 模板**：
```
请实现 handlers/[模块名].py。

handlers 层只做三件事：
1. 用 @on_command 注册指令
2. 调用 security 层处理用户标识
3. 调用 core/ 的业务方法，用 persona_engine 渲染结果后发送

不含任何业务判断逻辑，所有判断在 core/ 层完成。

本次实现以下指令：
[列出本模块的指令，参考 v3.x 文档的指令集章节]
```

**建议实现顺序**：
1. `system.py`：好友申请自动通过 + 初始化向导
2. `school.py`：目标院校配置
3. `checkin.py`：打卡指令
4. `schedule.py`：今日计划 + 定时推送
5. `persona.py`：人物卡选择
6. `words.py`：推词
7. `emotion.py`：情绪陪伴
8. `points.py`：积分查询

---

### 阶段 9：联调与测试

**给 AI 的 Prompt**：
```
请帮我写一个 tests/test_integration.py，模拟以下完整流程：

1. 新用户添加好友 → 系统自动通过 → 发送欢迎消息
2. 用户完成初始化向导（填写院校、专业课、考试日期）
3. 上传知识点 CSV
4. 生成今日学习计划
5. 打卡3个知识点，检查掌握度更新
6. 触发情绪熔断，检查推词停止
7. 查询积分余额

每个步骤都要有断言，验证数据库状态是否符合预期。
```

---

## 常见问题与注意事项

**Q：AI 生成的代码直接操作了数据库，没有通过 UserDB？**
在 Prompt 里加：「所有数据库操作必须通过 UserDB 类，不得直接使用 conn.execute」

**Q：AI 把业务逻辑写进了 handler？**
在 Prompt 里加：「handler 只调用 core/ 的方法，不包含任何 if/else 业务判断」

**Q：AI 生成的 SQL 和 init.sql 不一致？**
在 Prompt 里加：「所有表结构以附件 init.sql 为准，不得自行修改表结构」

**Q：AI 忘记了某个约束？**
在对话开头加：「请先复述 TECH_SPEC.md 中的核心约束，再开始写代码」

**Q：生成的代码太长一次看不完？**
要求 AI 每次只实现一个方法，写完一个测试通过后再继续

---

## 每次会话开始的标准 Prompt 模板

```
我正在开发 上岸 考研全科陪伴机器人，一个基于 NoneBot2 的 QQ 私聊 Bot。

请先阅读以下附件：
- TECH_SPEC.md：技术栈、目录结构、核心约束
- init.sql：数据库表结构（所有表以此为准）
- [本次相关的文档章节]

本次任务：[具体任务描述]

开始前请确认你理解了以下约束：
1. 所有数据库操作通过 UserDB 类
2. 不存储原始 QQ 号
3. core/ 不依赖任何 nonebot 模块
4. 只处理私聊消息
```
