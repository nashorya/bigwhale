# 上岸 安全机制 · Session 管理 · 积分系统
### 技术设计补充文档 v3.2 — 配套 v3.0 / v3.1 文档

> 本文档聚焦四个模块：
> **① 数据安全（用户隔离 · 哈希 · 参数化查询）**  ·  **② Session 管理**  ·  **③ 好友申请自动处理**  ·  **④ 积分系统**

---

## 目录

1. [数据安全设计](#1-数据安全设计)
2. [Session 管理](#2-session-管理)
3. [好友申请自动处理](#3-好友申请自动处理)
4. [积分系统](#4-积分系统)
5. [数据库变更（v3.2）](#5-数据库变更)
6. [新增指令集](#6-新增指令集)

---

## 1. 数据安全设计

### 1.1 用户标识哈希

QQ 号是明文的公开信息，不应直接作为数据库主键存储。系统在首次接收到用户消息时，对 QQ 号进行**单向哈希**，后续所有表的 `user_id` 字段均存储哈希值，原始 QQ 号不落库。

#### 哈希方案

```python
import hashlib
import os

# 系统启动时从环境变量读取，不硬编码在代码里
SALT = os.environ.get("上岸_USER_SALT")

def hash_user_id(qq_number: str) -> str:
    """
    将 QQ 号转换为不可逆的用户标识。
    同一 QQ 号 + 同一 SALT 始终得到相同结果，
    但无法从结果反推出原始 QQ 号。
    """
    raw = f"{SALT}:{qq_number}"
    return hashlib.sha256(raw.encode()).hexdigest()
```

#### SALT 管理规则

- SALT 通过环境变量 `上岸_USER_SALT` 注入，不写入代码仓库
- 部署时手动生成一次（建议 32 位随机字符串），之后**永不更换**
- 更换 SALT 会导致所有用户的 `user_id` 变更，等同于清空所有用户数据
- `.env` 文件加入 `.gitignore`，不上传到版本控制

```bash
# .env 示例（不提交到 git）
上岸_USER_SALT=your_random_32_char_string_here

# .gitignore
.env
/data/
*.db
```

#### 内存中的 QQ 号映射

Session 存活期间，内存里维护一份 `qq → user_id` 的映射，用于消息路由。这份映射**只存在内存，不落库**，Bot 重启后从下一条消息重新建立：

```python
# 内存映射，仅用于消息路由，不持久化
_qq_to_uid: dict[str, str] = {}

def get_or_create_uid(qq: str) -> str:
    if qq not in _qq_to_uid:
        _qq_to_uid[qq] = hash_user_id(qq)
    return _qq_to_uid[qq]
```

---

### 1.2 参数化查询强制规范

所有数据库操作必须通过 `UserDB` 类进行，**禁止在业务代码中直接拼接 SQL 字符串**。

#### UserDB 封装类

```python
class UserDB:
    """
    绑定 user_id 的数据库操作类。
    实例化后，所有查询自动携带 WHERE user_id = ?，
    上层代码无需手动传入 user_id，从根源防止串用户。
    """

    def __init__(self, user_id: str, conn: sqlite3.Connection):
        self._uid = user_id
        self._conn = conn

    def get_active_persona(self) -> str:
        # ✅ 正确：参数化查询
        row = self._conn.execute(
            "SELECT active_persona FROM persona_config WHERE user_id = ?",
            (self._uid,)
        ).fetchone()
        return row["active_persona"] if row else "lingqi"

    def get_knowledge_points(self, subject_id: int) -> list:
        # ✅ 正确：参数化查询，user_id 自动绑定
        return self._conn.execute(
            "SELECT * FROM knowledge_points WHERE user_id = ? AND subject_id = ?",
            (self._uid, subject_id)
        ).fetchall()

    def update_mastery(self, kp_id: int, new_level: int):
        # ✅ 正确：参数化查询
        self._conn.execute(
            "UPDATE knowledge_points SET mastery_level = ? WHERE id = ? AND user_id = ?",
            (new_level, kp_id, self._uid)
        )
        self._conn.commit()
```

#### 禁止模式（代码审查必须拦截）

```python
# ❌ 禁止：字符串拼接，存在 SQL 注入风险
user_input = "#打卡 二叉树的遍历"
topic = user_input.split(" ")[1]
conn.execute(f"SELECT * FROM knowledge_points WHERE topic_name = '{topic}'")

# ❌ 禁止：裸查询不带 user_id
conn.execute("SELECT * FROM knowledge_points WHERE subject_id = 1")

# ✅ 正确：参数化 + UserDB 封装
db = UserDB(uid, conn)
results = db.get_knowledge_points(subject_id=1)
```

#### 用户输入清洗

所有来自 QQ 消息的文本输入，在进入业务逻辑前统一清洗：

```python
import re

def sanitize_input(text: str, max_length: int = 100) -> str:
    """
    清洗用户输入：
    · 截断超长输入
    · 移除控制字符
    · 去除首尾空白
    """
    text = text.strip()
    text = re.sub(r'[\x00-\x1f\x7f]', '', text)  # 移除控制字符
    return text[:max_length]
```

---

## 2. Session 管理

### 2.1 Session 结构

每个用户在内存中维护一个独立的 Session 对象，存储当前会话的轻量状态：

```python
from dataclasses import dataclass, field
from datetime import datetime

@dataclass
class UserSession:
    user_id: str                          # 哈希后的用户标识
    active_persona: str = "lingqi"        # 当前激活角色
    companion_mode: bool = False          # 是否处于情绪陪伴模式
    immersive_mode: bool = False          # 是否处于沉浸学习模式（#开始学习）
    immersive_subject: str | None = None  # 沉浸模式当前科目
    last_word_push: datetime | None = None # 上次推词时间（用于8分钟间隔检查）
    emotion_cooldown_until: datetime | None = None  # 情绪熔断冷却到期时间
    last_active: datetime = field(default_factory=datetime.now)
```

### 2.2 Session 生命周期

```
用户发送消息
  → get_or_create_uid(qq) 取得 user_id
  → SessionManager.get(user_id) 取出 Session（不存在则从数据库恢复）
  → 业务逻辑处理（读写 Session 状态）
  → Session 写回内存
  → 关键状态变更（如角色切换、熔断触发）同步持久化到数据库

Session 超过 2 小时无活动
  → 从内存中卸载（节省内存）
  → 下次消息到来时从数据库重新加载
```

```python
class SessionManager:
    _sessions: dict[str, UserSession] = {}

    @classmethod
    def get(cls, user_id: str, db: UserDB) -> UserSession:
        if user_id not in cls._sessions:
            # 从数据库恢复持久化状态
            cls._sessions[user_id] = cls._load_from_db(user_id, db)
        session = cls._sessions[user_id]
        session.last_active = datetime.now()
        return session

    @classmethod
    def _load_from_db(cls, user_id: str, db: UserDB) -> UserSession:
        persona = db.get_active_persona()
        return UserSession(user_id=user_id, active_persona=persona)

    @classmethod
    def evict_idle(cls, idle_minutes: int = 120):
        """定期清理超时 Session，释放内存"""
        cutoff = datetime.now() - timedelta(minutes=idle_minutes)
        to_remove = [
            uid for uid, s in cls._sessions.items()
            if s.last_active < cutoff
        ]
        for uid in to_remove:
            del cls._sessions[uid]
```

### 2.3 用户隔离保证

每条消息的完整处理链：

```
OneBot 消息事件（携带 QQ 号）
  ↓
get_or_create_uid(qq)          → 得到 user_id（哈希值）
  ↓
UserDB(user_id, conn)          → 所有数据库操作自动绑定该用户
  ↓
SessionManager.get(user_id)    → 取出该用户的内存 Session
  ↓
业务逻辑（Scheduler / Manager / Persona Engine）
  ↓
响应消息通过 OneBot 发回给原 QQ 号
```

不同用户的 `UserDB` 实例绑定不同 `user_id`，`SessionManager` 按 `user_id` 索引，**在任何环节都不可能读取到其他用户的数据**。

---

## 3. 好友申请自动处理

### 3.1 设计原则

- Bot 只工作在**私聊**场景，不处理群消息
- 好友申请**全部自动通过**，通过后立即发送欢迎消息并引导注册
- 不需要验证码或人工审核，降低用户进入门槛

### 3.2 好友申请事件处理

```python
from nonebot import on_request
from nonebot.adapters.onebot.v11 import FriendRequestEvent, Bot

friend_request_handler = on_request()

@friend_request_handler.handle()
async def handle_friend_request(bot: Bot, event: FriendRequestEvent):
    # 自动通过所有好友申请
    await bot.set_friend_add_request(flag=event.flag, approve=True)

    # 通过后稍等 1 秒再发欢迎消息（等待好友关系生效）
    await asyncio.sleep(1)
    await send_welcome(bot, str(event.user_id))
```

### 3.3 欢迎消息与注册引导

好友添加成功后，Bot 发送欢迎消息，同时完成用户初始化（写入数据库）：

```
【欢迎使用 上岸 考研陪伴机器人】

你好！我是你的备考伙伴。

在我们开始之前，有几件事需要确认——
发送 [#开始] 启动初始化向导（约需2分钟）

🎁 新用户福利：
  注册即获得 200 积分，包含：
  · 一次完整学习计划生成
  · 7天学习提醒订阅
  · 每日推词（基础档：10词/天）

想了解更多？发送 [#积分说明]
```

### 3.4 邀请注册流程

每个已注册用户拥有唯一邀请码（基于 user_id 生成，不暴露 QQ 号）：

```python
def generate_invite_code(user_id: str) -> str:
    """生成 6 位大写邀请码，与 user_id 一一对应"""
    raw = hashlib.md5(f"{user_id}:invite".encode()).hexdigest()
    return raw[:6].upper()
```

邀请流程：

```
邀请者发送 [#我的邀请码]
  → Bot 回复：你的邀请码是 A3F9K2，分享给朋友添加我时使用

被邀请者添加 Bot 好友
  → 欢迎消息中提示：如果有邀请码，发送 [#填写邀请码 XXXXXX]

被邀请者发送 [#填写邀请码 A3F9K2]
  → 系统验证邀请码有效性
  → 邀请者 +100 积分
  → 被邀请者 +50 积分（叠加在注册初始 200 积分上，共 250 积分）
  → 双方收到积分到账通知
```

邀请码有效性规则：
- 邀请码**永久有效**，不过期
- 同一被邀请者只能填写一次邀请码（注册后7天内有效填写窗口）
- 邀请码在新用户**完成初始化向导后**才正式结算积分，防止恶意刷注册

---

## 4. 积分系统

### 4.1 积分定位

积分是 上岸 的**使用凭证**，不是虚荣货币。核心逻辑：

```
基础功能（初始积分覆盖）    →  所有用户都能体验完整功能
高级功能（消耗积分解锁）    →  持续使用需要充值或邀请好友
```

### 4.2 积分获取途径

| 来源 | 积分数 | 说明 |
|------|--------|------|
| 新用户注册 | +200 | 一次性发放，足够完整体验7天 |
| 填写邀请码（被邀请者） | +50 | 注册完成初始化后发放 |
| 邀请好友注册成功（邀请者） | +100 | 被邀请者完成初始化后结算 |
| 充值 | 按套餐 | 见 4.5 充值套餐 |

### 4.3 积分消耗规则

#### 功能分级

| 功能 | 免费/消耗 | 消耗详情 |
|------|-----------|---------|
| 系统初始化（目标院校、科目配置） | 免费 | 永久免费，不消耗积分 |
| 生成一次完整学习计划 | 消耗 | 50积分/次 |
| 学习提醒订阅（早安/打卡提醒/晚间复盘） | 消耗 | 20积分/天 |
| 英语推词·基础档（10词/天） | 消耗 | 包含在订阅内 |
| 英语推词·加强档（20词/天） | 消耗 | 额外 +5积分/天 |
| 英语推词·冲刺档（30词/天） | 消耗 | 额外 +10积分/天 |
| 情绪陪伴（#陪我聊） | 消耗 | 5积分/次会话 |
| 基础人物卡（零绮） | 免费 | 默认解锁 |
| 扩展人物卡（白泉/苏晚/纪律） | 消耗 | 80积分/张，永久解锁 |
| #学情 报告生成 | 消耗 | 5积分/次 |
| CSV 知识点同步 | 免费 | 永久免费 |

> **设计说明**：初始 200 积分的覆盖范围——生成计划（50）+ 7天订阅（140）+ 基础推词（含在订阅内）= 190积分，恰好够7天完整体验，略有余量。

#### 订阅模式

学习提醒采用**按天订阅**而非按次收费，用户每天凌晨 00:01 自动扣除当日订阅费用：

```
订阅检查（每日 00:01）：
  · 余额 ≥ 20积分 → 正常扣除，当日服务正常
  · 余额 10–19积分 → 扣除，但推送余额预警（"积分还剩X天，记得充值"）
  · 余额 < 10积分 → 不扣除，当日提醒服务暂停，推送余额不足通知
```

余额不足时，系统**不立即停止所有功能**，只暂停主动推送类服务（提醒、推词）。用户仍可主动发送任何指令查询数据，`#打卡`、`#同步` 等操作不受影响。

### 4.4 积分余额通知策略

| 余额状态 | 通知时机 | 通知内容 |
|----------|----------|---------|
| 首次低于50积分 | 当日晚间复盘时附带 | 轻提示，不打断 |
| 低于20积分（约1天） | 早安提醒时附带 | 明确提醒，附充值入口 |
| 余额耗尽，服务暂停 | 即时推送 | 说明哪些服务暂停，如何恢复 |
| 充值到账 | 即时推送 | 到账积分数，当前余额，可用天数 |

各角色余额不足通知示例（低于20积分）：

**零绮**
```
提示：积分余额不足20，约剩1天服务。
发送 [#充值] 查看套餐。
```

**白泉**
```
哎，积分快不够了！还剩不到1天～🥹
快发送 [#充值] 看看套餐，不然我就联系不到你了！
```

**苏晚**
```
积分快用完了，还剩不到一天。🌿
不想中断的话，发送 [#充值] 看看，我在这里等你。
```

**纪律**
```
积分还剩不到1天。
[#充值] 查套餐。别拖。
```

### 4.5 充值套餐

充值通过外部渠道完成（微信/支付宝），管理员手动或通过脚本发放积分。Bot 本身不处理支付，只处理积分发放和查询。

| 套餐名 | 积分数 | 建议售价 | 对应天数（基础订阅） |
|--------|--------|---------|------------------|
| 体验包 | 200积分 | ¥6 | 约10天 |
| 月度包 | 700积分 | ¥18 | 约35天 |
| 季度包 | 2000积分 | ¥45 | 约100天 |
| 备考包 | 4000积分 | ¥78 | 约200天（覆盖完整备考周期） |

> **备考包说明**：200天覆盖从备考启动到考试前一天，定价相当于全程备考每天不到4毛钱，是价格最合适的选择，在套餐列表中重点展示。

### 4.6 积分查询与明细

用户发送 `#积分` 查看当前状态：

```
【积分账户】

当前余额：320 积分

预计可用：
  · 基础订阅（20积分/天）→ 约16天
  · 含加强推词（25积分/天）→ 约12天

最近消耗：
  · 今日订阅  -20积分   剩余 320
  · 学情报告  -5积分    剩余 340
  · 昨日订阅  -20积分   剩余 345

人物卡解锁状态：
  · 零绮  ✅ 免费
  · 白泉  ✅ 已解锁
  · 苏晚  ❌ 未解锁（80积分）
  · 纪律  ❌ 未解锁（80积分）

发送 [#充值] 查看套餐
发送 [#积分明细] 查看完整消耗记录
```

---

## 5. 数据库变更（v3.2）

### 新增表：users（用户注册信息）

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| user_id | TEXT | PRIMARY KEY | SHA-256 哈希后的用户标识 |
| invite_code | TEXT | UNIQUE | 该用户的邀请码（6位大写） |
| invited_by | TEXT | NULLABLE | 邀请者的 user_id；自然注册为 NULL |
| registered_at | DATETIME | NOT NULL | 注册时间（首次发消息时间） |
| init_complete | BOOLEAN | DEFAULT FALSE | 是否完成初始化向导 |
| invite_settled | BOOLEAN | DEFAULT FALSE | 邀请积分是否已结算 |

### 新增表：points_account（积分账户）

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| user_id | TEXT | PRIMARY KEY | FK → users.user_id |
| balance | INTEGER | DEFAULT 0 | 当前积分余额 |
| total_earned | INTEGER | DEFAULT 0 | 历史累计获得积分 |
| total_spent | INTEGER | DEFAULT 0 | 历史累计消耗积分 |
| subscription_active | BOOLEAN | DEFAULT FALSE | 当日订阅是否已扣费激活 |
| word_tier | TEXT | DEFAULT 'basic' | 推词档位：basic / enhanced / sprint |
| unlocked_personas | TEXT | DEFAULT '["lingqi"]' | JSON 数组，已解锁人物卡 id 列表 |

### 新增表：points_ledger（积分流水）

| 字段名 | 类型 | 约束 | 说明 |
|--------|------|------|------|
| id | INTEGER | PRIMARY KEY | 自增主键 |
| user_id | TEXT | FK → users.user_id | 用户标识 |
| delta | INTEGER | NOT NULL | 变动量，正数为收入，负数为支出 |
| balance_after | INTEGER | NOT NULL | 变动后余额快照 |
| reason | TEXT | NOT NULL | 变动原因（枚举值，见下） |
| ref_id | TEXT | NULLABLE | 关联业务 id（如邀请码、订单号） |
| created_at | DATETIME | NOT NULL | 流水时间 |

`reason` 枚举值：

| 值 | 说明 |
|----|------|
| `register_bonus` | 注册赠送 |
| `invite_reward_inviter` | 邀请好友奖励（邀请者） |
| `invite_reward_invitee` | 填写邀请码奖励（被邀请者） |
| `recharge` | 充值到账 |
| `daily_subscription` | 每日订阅扣费 |
| `word_tier_upgrade` | 推词档位升级扣费 |
| `plan_generation` | 生成学习计划扣费 |
| `persona_unlock` | 解锁人物卡扣费 |
| `emotion_session` | 情绪陪伴会话扣费 |
| `report_generation` | 学情报告生成扣费 |
| `admin_grant` | 管理员手动发放 |
| `admin_deduct` | 管理员手动扣除 |

---

## 6. 新增指令集

### 积分与账户类

| 指令 | 功能 | 说明 |
|------|------|------|
| `#积分` | 查看当前积分余额与账户状态 | 含解锁人物卡列表 |
| `#积分明细` | 查看完整积分流水 | 最近20条 |
| `#充值` | 查看充值套餐说明 | Bot 不处理支付，展示联系方式 |
| `#积分说明` | 查看积分获取与消耗规则 | — |
| `#我的邀请码` | 查看个人邀请码 | — |
| `#填写邀请码 [CODE]` | 填写邀请者邀请码 | 注册7天内有效 |

### 推词档位类

| 指令 | 功能 | 积分消耗 |
|------|------|---------|
| `#推词档位` | 查看当前档位与可用档位 | — |
| `#推词档位 基础` | 切换到基础档（10词/天） | 含在订阅内 |
| `#推词档位 加强` | 切换到加强档（20词/天） | +5积分/天 |
| `#推词档位 冲刺` | 切换到冲刺档（30词/天） | +10积分/天 |

### 人物卡解锁类

| 指令 | 功能 | 说明 |
|------|------|------|
| `#解锁角色 [角色名]` | 消耗80积分解锁指定人物卡 | 需二次确认 |
| `#角色商店` | 查看所有人物卡及解锁状态 | — |

---

*上岸 · 安全机制与积分系统设计 · v3.2*  
*配套 v3.0 / v3.1 文档使用*
