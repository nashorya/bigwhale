# 上岸 管理员后台设计
### 技术设计补充文档 v3.3 — 配套 v3.0 / v3.1 / v3.2 文档

---

## 1. 管理员身份验证

### 1.1 配置方式

管理员 QQ 号写入 `.env`，支持多个管理员（逗号分隔）：

```
ADMIN_QQ_LIST=123456789,987654321
```

### 1.2 验证机制

所有 `#admin` 指令在 handler 层入口处验证：

```python
def is_admin(qq: str) -> bool:
    admin_list = os.environ.get("ADMIN_QQ_LIST", "").split(",")
    return qq.strip() in [a.strip() for a in admin_list]
```

非管理员发送 `#admin` 指令时，**静默忽略**，不回复任何内容（防止暴露管理员指令存在）。

---

## 2. 管理员指令集

所有管理员指令以 `#admin` 开头，只在私聊中有效。

### 2.1 积分管理

| 指令 | 功能 | 示例 |
|------|------|------|
| `#admin 发放积分 [QQ号] [数量] [备注]` | 给指定用户发放积分 | `#admin 发放积分 123456 200 月度包充值` |
| `#admin 查积分 [QQ号]` | 查看指定用户积分余额和最近10条流水 | `#admin 查积分 123456` |

### 2.2 用户管理

| 指令 | 功能 | 示例 |
|------|------|------|
| `#admin 查用户 [QQ号]` | 查看用户注册信息（注册时间、初始化状态、邀请关系） | `#admin 查用户 123456` |
| `#admin 封禁 [QQ号] [原因]` | 封禁用户，封禁后 Bot 不再响应该用户消息 | `#admin 封禁 123456 违规使用` |
| `#admin 解封 [QQ号]` | 解除封禁 | `#admin 解封 123456` |

### 2.3 角色卡管理

| 指令 | 功能 | 示例 |
|------|------|------|
| `#admin 角色统计` | 查看所有用户的角色分布统计 | `#admin 角色统计` |
| `#admin 查角色卡 [角色id]` | 查看指定角色卡的 JSON 内容摘要 | `#admin 查角色卡 lingqi` |

---

## 3. 指令响应格式

### 3.1 发放积分

```
【管理员操作】积分发放

用户：[QQ号后4位]****（已隐藏）
发放：+200 积分
备注：月度包充值
操作后余额：520 积分
流水ID：#00042

操作成功 ✓
```

### 3.2 查积分

```
【积分查询】****[QQ号后4位]

当前余额：320 积分
预计可用：16 天

最近流水（最新10条）：
  +200  register_bonus     03-15 22:30
  -20   daily_subscription 03-16 00:01
  -50   plan_generation    03-16 09:15
  +100  invite_reward      03-16 10:00
  ...
```

### 3.3 查用户

```
【用户信息】****[QQ号后4位]

注册时间：2026-03-15 22:30
初始化：已完成
邀请者：有（积分已结算）
被邀请人数：3 人
当前角色：苏晚
封禁状态：正常
```

### 3.4 封禁/解封

```
【管理操作】封禁用户

用户：****[QQ号后4位]
原因：违规使用
状态：已封禁 ✓

该用户的消息将被静默忽略。
```

### 3.5 角色统计

```
【角色分布统计】

总用户数：128 人
当前激活角色：
  零绮（冷静理性）  ████████░░  42人  32.8%
  白泉（元气活泼）  ███████░░░  38人  29.7%
  苏晚（温柔治愈）  ██████░░░░  31人  24.2%
  纪律（毒舌激励）  ████░░░░░░  17人  13.3%

解锁情况：
  零绮  128人（默认解锁）
  白泉   89人（69.5%）
  苏晚   76人（59.4%）
  纪律   54人（42.2%）
```

### 3.6 查角色卡

```
【角色卡内容】零绮 (lingqi)

基本信息：
  风格：冷静理性系
  简介：说话简短，但每句话都算数。
  话量：low | 正式程度：neutral
  Emoji：无

口癖（catchphrase）：
  · 数据是这样说的。
  · 先把这一步做完。
  · 不用急，逐步来。
  · 这是可以解决的。

脚本覆盖：
  daily_scripts     ✓ 5个
  checkin_scripts   ✓ 6个
  emotion_scripts   ✓ 4类
  milestone_scripts ✓ 7个

文件路径：personas/builtin/lingqi.json
```

---

## 4. 数据库变更（v3.3）

### users 表新增字段

| 字段名 | 类型 | 说明 |
|--------|------|------|
| `is_banned` | BOOLEAN DEFAULT 0 | 是否被封禁 |
| `ban_reason` | TEXT NULLABLE | 封禁原因 |
| `banned_at` | DATETIME NULLABLE | 封禁时间 |

> 需在 `init.sql` 中补充这三个字段，并在 handler 层每条消息入口检查 `is_banned`。

---

## 5. 安全注意事项

- 管理员 QQ 号只存 `.env`，不落库，不在任何响应中暴露
- 响应中的 QQ 号一律显示为 `****后4位`，不显示完整 QQ 号
- 非管理员发送 `#admin` 指令时静默忽略，不回复
- 发放积分操作写入 `points_ledger`，`reason` 为 `admin_grant`，`ref_id` 记录操作者QQ号后4位
- 所有管理员操作写入独立的 `admin_log` 表（见下）

### admin_log 表

| 字段名 | 类型 | 说明 |
|--------|------|------|
| id | INTEGER PRIMARY KEY | 自增主键 |
| admin_qq_suffix | TEXT | 操作者QQ后4位（不存完整QQ） |
| action | TEXT | 操作类型 |
| target_user_id | TEXT | 目标用户 user_id（哈希值） |
| detail | TEXT | 操作详情JSON |
| created_at | DATETIME | 操作时间 |

---

## 6. 实现位置

```
plugins/上岸/
└── handlers/
    └── admin.py    ← 管理员后台所有指令的 handler
```

`admin.py` 的结构：

```python
# 入口验证装饰器
def admin_only(handler):
    """非管理员静默忽略"""
    ...

# 指令注册
@on_command("admin").handle()
async def handle_admin(bot, event):
    if not is_admin(str(event.user_id)):
        return  # 静默忽略
    # 解析子指令，分发到对应处理函数
    ...
```
