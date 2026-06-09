# 上岸 开发日志

---

### 2026-03-15 core/security.py

- **完成内容**：
  - `hash_user_id(qq: str) -> str`：SHA-256 哈希，SALT 从环境变量 `上岸_USER_SALT` 延迟加载
  - `get_or_create_uid(qq: str) -> str`：内存字典缓存 `qq → uid` 映射，不落库
  - `sanitize_input(text: str, max_length: int = 100) -> str`：strip → 移除控制字符 → 截断
- **设计决策及理由**：
  - SALT 采用延迟加载（`_get_salt()`），而非模块级常量。原因：模块导入时 `.env` 可能尚未被 `python-dotenv` 加载，延迟到首次调用时读取更安全
  - `_qq_to_uid` 映射仅存内存，Bot 重启后从下一条消息自动重建，符合 v3.2 文档设计
- **遇到的问题及解法**：无
- **遗留事项**：无

---

### 2026-03-15 core/session.py

- **完成内容**：
  - `UserSession` dataclass：包含 v3.2 文档定义的全部字段（`user_id`, `active_persona`, `companion_mode`, `immersive_mode`, `immersive_subject`, `last_word_push`, `emotion_cooldown_until`, `last_active`）
  - `SessionManager.get(user_id, load_persona_fn=)`：异步获取/创建 Session，支持注入回调加载角色
  - `SessionManager.evict_idle(idle_minutes=120)`：清理超时 Session，返回被卸载的 uid 列表
  - `SessionManager.remove()` / `active_count()` / `clear_all()`：辅助方法
- **设计决策及理由**：
  - `get()` 接受 `load_persona_fn` 回调而非直接接收 `UserDB` 实例，避免 `session.py` 依赖 `user_db.py`，保持 core/ 模块间解耦
  - v3.2 文档中 `SessionManager` 是类方法风格（`@classmethod`），保持一致
- **遇到的问题及解法**：无
- **遗留事项**：`evict_idle` 需在 APScheduler 定时任务中注册调用（后续实现 scheduler 时处理）

---

### 2026-03-15 core/user_db.py

- **完成内容**：
  - `get_db_conn()` 异步上下文管理器：从 `DB_PATH` 环境变量读取路径，启用 WAL + 外键约束 + Row 工厂
  - `UserDB` 类：绑定 `user_id`，所有查询自动携带 `WHERE user_id = ?`
  - 实现 8 个异步方法：`get_active_persona`, `get_knowledge_points`, `update_mastery`, `get_points_balance`, `deduct_points`, `get_daily_plan`, `get_user_schedule`, `get_exam_date`
- **设计决策及理由**：
  - `deduct_points` 先查余额再扣除（非原子），当前单用户单连接场景下无并发风险；如未来需要支持并发，可改用 `UPDATE ... WHERE balance >= ?` 原子操作
  - `update_mastery` 同时更新 `last_review_at` 为当前时间，符合遗忘曲线复习记录需求
  - 所有返回列表的方法统一返回 `list[dict]`，方便上层业务处理
- **遇到的问题及解法**：无
- **遗留事项**：
  - 后续可能需要追加更多方法（如 `add_points`, `get_checkin_streak` 等），按需扩展
  - `deduct_points` 的并发安全性需在多用户压测时验证

---

### 2026-03-15 personas/ JSON 文件

- **完成内容**：
  - `personas/builtin/lingqi.json`：零绮·冷静理性系（low verbosity，无 emoji）
  - `personas/builtin/baiquan.json`：白泉·元气活泼系（high verbosity，✨🎉💪🌟🥹）
  - `personas/builtin/suwan.json`：苏晚·温柔治愈系（mid verbosity，🌿🍵🌙💙）
  - `personas/builtin/jilv.json`：纪律·毒舌激励系（low verbosity，无 emoji）
  - `personas/personas_index.json`：角色索引文件
  - 每张卡包含完整字段：`tone_profile`, `daily_scripts`, `checkin_scripts`, `emotion_scripts`, `milestone_scripts`
- **设计决策及理由**：
  - JSON 结构严格对照 v3.1 文档 9.2 节的模板，所有 script key 命名一致
  - 各角色脚本内容来自 v3.0 文档 5.3 节的示例话术
- **遇到的问题及解法**：无
- **遗留事项**：`/personas/custom/` 目录为 v3.3 预留，暂未创建

---

### 2026-03-15 core/persona_engine.py

- **完成内容**：
  - `load_personas(personas_dir)`：启动时从 JSON 文件加载全部人物卡到内存
  - `render(persona_id, script_key, data)`：核心渲染方法，支持点号路径查找、模板变量填充、20% catchphrase、30% emoji 追加
  - `get_persona_list()`：返回角色列表（用于 #选择角色 展示）
  - `get_persona(persona_id)`：获取完整角色数据
  - `is_loaded()`：判断是否已加载
- **设计决策及理由**：
  - `render()` 接受 `persona_id` 而非从数据库读取 `active_persona`，由 handler 层传入。避免 persona_engine 依赖 user_db
  - 使用 `SafeDict` 处理模板中缺失的变量，不抛异常而是保留原始占位符 `{key}`
  - 回退机制：找不到角色卡 -> 尝试 lingqi -> 找不到脚本 -> fallback 渲染
  - emoji 追加前检查是否已存在于消息中，避免重复
- **遇到的问题及解法**：无
- **遗留事项**：
  - verbosity 字段（low/mid/high）目前仅作为元数据存在，未用于动态截断/扩展内容
  - 后续如需按 verbosity 自动调整消息长度，可在 `_apply_tone` 中扩展

---

### 2026-03-16 core/scheduler.py

- **完成内容**：
  - `calc_priority(kp, days_left)`：按 v3.1 文档公式 `base × forget_factor × overdue_factor × sprint_factor` 计算优先级
  - `get_study_phase(days_left)`：返回备考阶段 foundation/intensify/sprint/sprint_final
  - `calc_next_review_date(mastery_level)`：艾宾浩斯间隔（1/3/7/15/30 天）
  - `Scheduler.generate_daily_plan(db, capacity=360)`：按科目均匀分配知识点，写入 daily_plan 表
  - `Scheduler.generate_morning_content(db)`：生成早安推送结构化数据（各科进度摘要、建议、计划）
  - `Scheduler.generate_evening_content(db)`：生成晚间复盘数据（完成率、提升列表、连击、明日 Top3）
  - `register_scheduled_jobs(scheduler)`：注册 APScheduler 定时任务（00:05/07:30/22:30 + 30min session清理）
- **设计决策及理由**：
  - 定时任务使用占位回调函数，由 handler 层在启动时替换为实际回调。原因：core/ 不能访问 bot 实例
  - `generate_morning/evening_content` 返回结构化 dict 而非最终消息字符串，由 PersonaEngine 在 handler 层渲染
  - 备考阶段过滤使用独立函数 `_filter_by_phase`，sprint_final 阶段只看 mastery<=3 的知识点
- **遇到的问题及解法**：无
- **遗留事项**：
  - 科目时间分配比例目前为均分，后续可由 Manager 基于掌握度动态调整权重
  - `generate_evening_content` 中 `missed_names` 需要 handler 层从知识点表查询填充

---

### 2026-03-16 core/user_db.py 扩展

- **完成内容**（为 scheduler 追加的方法）：
  - `get_active_subjects()`：查 subjects + subject_status，回退兼容无 status 数据
  - `get_all_knowledge_points()`：获取所有 active 科目知识点并附加 subject_name
  - `get_checkin_streak()`：获取打卡连击数据
  - `clear_daily_plan(date)`：清除指定日期计划
  - `insert_daily_plan(date, kp_id, score, minutes)`：插入计划记录
  - `get_all_user_ids(conn)`：模块级函数，获取所有用户 ID
- **遗留事项**：无

---

### 2026-03-16 core/emotion_detector.py

- **完成内容**：
  - `EMOTION_KEYWORDS` 词库：焦虑类(10) / 沮丧类(10) / 疲惫类(7) / 求陪伴类(6)，求陪伴类全部为强信号词
  - `detect(text)` → `(bool, str|None)`：≥2 弱信号词或 1 强信号词触发，宁漏检不误触发
  - `detect_detailed(text)`：返回详细匹配信息（用于写入 mood_signal）
  - `check_system_anomaly(user_id, db)`：检测熔断/缺卡/深夜/离线
  - `start_session(user_id, triggered_by, db)`：写入 emotion_log，companion_mode = True
  - `end_session(user_id, db)`：更新 session_end，companion_mode = False
  - `is_in_companion_mode(db)`：查询当前陪伴模式状态
- **设计决策及理由**：
  - 强信号词集合包含求陪伴类全部 + 沮丧类中最严重的几个词（"放弃""不想学了""好绝望""考不上了""坚持不住了"）
  - `_check_meltdown` 简化实现：查 user_word_status 中高权重错词数 ≥5 作为熔断指标
  - 冷却期检查放在 `check_system_anomaly` 入口，30 分钟内不重复触发
- **遇到的问题及解法**：
  - SQLite 不支持 `UPDATE ... ORDER BY ... LIMIT`，改用子查询 `WHERE id = (SELECT id ... ORDER BY ... LIMIT 1)`
- **遗留事项**：
  - `_check_meltdown` 的逻辑较简化，后续推词系统实现后需要根据实际正确率数据细化
  - `check_system_anomaly` 中 "长时间离线" 检测需要 handler 层提供最后消息时间戳

---

### 2026-03-16 core/points_service.py

- **完成内容**：
  - `register_bonus(user_id, db)` → 注册赠送 200 积分
  - `daily_deduct(user_id, db)` → 每日订阅扣费（20 + 推词档位附加费），余额<10 不扣费返回 False
  - `spend(user_id, amount, reason, db)` → 通用扣费，余额不足返回 False
  - `grant(user_id, amount, reason, db)` → 积分发放
  - `get_account_summary(user_id, db)` → 余额/预估天数/最近5条流水/解锁角色/余额状态
  - `settle_invite(invitee_uid, db)` → 邀请结算：被邀请者+50，邀请者+100，标记 settled
  - `_modify_balance(db, delta, reason)` → 内部统一变动方法，自动写 points_ledger 流水
- **设计决策及理由**：
  - 所有积分变动通过 `_modify_balance` 集中处理，确保每次变动都有流水记录
  - `daily_deduct` 读取 word_tier 计算额外推词费用（basic=0, enhanced=+5, sprint=+10）
  - `settle_invite` 在同一事务内给双方发放积分并标记 settled，防止部分成功
  - `get_account_summary` 从 `persona_config.unlocked_personas` 读取 JSON 数组获取解锁状态
- **遇到的问题及解法**：
  - 测试时外键约束要求先插入邀请者再插入被邀请者，调整了 INSERT 顺序
- **遗留事项**：无

---

### 2026-03-16 handlers/system.py

- **完成内容**：
  - 好友申请自动通过（`on_request`）→ 1秒后发送欢迎消息
  - 欢迎消息注册流程：写入 users / persona_config / points_account → 调用 `register_bonus`
  - `#开始` 初始化向导（4步多轮对话，NoneBot2 `pause/reject` 模式）：
    1. 院校+专业（支持最多3所，写入 `target_schools`）
    2. 专业课科目（写入 `subjects`，自动添加公共课）
    3. 考试日期（支持回复"统考"自动计算，写入 `exam_config`）
    4. 角色选择（更新 `persona_config.active_persona`，标记 `init_complete=1`）
  - `#重新初始化`：重置 `init_complete` 并清空 `target_schools`
  - `#填写邀请码 XXXXXX`：验证邀请码 → 记录邀请关系 → 初始化完成后结算积分
  - `#我的邀请码`：展示用户邀请码
- **设计决策及理由**：
  - 使用 NoneBot2 的 `matcher.pause()` + 多个 `@init_wizard.handle()` 实现多轮对话状态机
  - `_send_welcome` 检查用户是否已注册，避免重复注册
  - 角色匹配支持中文名和英文 ID，未匹配时默认零绮
  - 统考日期计算使用 12 月第三周周六推算
- **遇到的问题及解法**：无
- **遗留事项**：
  - 向导流程无法通过单元测试验证（依赖 NoneBot2 运行时），需集成测试
  - 公共课科目自动识别较简化（固定为政治/英语/数学），后续需按院校专业匹配

---

### 2026-03-16 core/security.py 扩展

- **完成内容**：
  - 新增 `generate_invite_code(user_id)` → 基于 MD5 生成 6 位大写邀请码
- **遗留事项**：无

---

### 2026-03-16 plugins/上岸/__init__.py

- **完成内容**：
  - 更新为显式导入 `handlers.system`，确保 NoneBot2 事件处理器被注册
- **遗留事项**：后续新 handler 模块实现后需追加导入

---

### 2026-03-16 handlers/admin.py（v3.3 管理员后台）

- **完成内容**：
  - `is_admin(qq)` → 从 `ADMIN_QQ_LIST` 环境变量读取，非管理员静默忽略
  - `check_banned(uid)` → 封禁检查，供所有 handler 入口调用
  - `_mask_qq(qq)` → QQ 号脱敏只显示后4位
  - `_log_admin_action(conn, admin_qq, action, target_uid, detail)` → 写入 admin_log 表
  - 7 个管理员指令：
    1. `#admin 发放积分 [QQ] [数量] [备注]` → 调用 `points_service.grant`
    2. `#admin 查积分 [QQ]` → 余额 + 最近10条流水
    3. `#admin 查用户 [QQ]` → 注册信息、邀请关系、角色、封禁状态
    4. `#admin 封禁 [QQ] [原因]` → 设置 is_banned=1 + 写日志
    5. `#admin 解封 [QQ]` → 清除封禁
    6. `#admin 角色统计` → 角色分布进度条 + 解锁统计
    7. `#admin 查角色卡 [角色id]` → 角色卡内容摘要
- **设计决策及理由**：
  - 所有指令通过单一 `on_command("admin")` 入口分发，子指令按前缀匹配
  - 封禁检查 `check_banned` 独立为公共函数，已在 system.py 的所有命令入口调用
  - QQ 号脱敏在所有响应中统一使用 `_mask_qq`
- **遇到的问题及解法**：无
- **遗留事项**：其余 handler（checkin/schedule/persona/emotion/points）实现后也需添加 `check_banned` 调用

---

### 2026-03-16 init.sql v3.3 变更

- **完成内容**：
  - `users` 表新增 `is_banned`、`ban_reason`、`banned_at` 三个字段
  - 新增 `admin_log` 表（管理员操作日志）
  - 用户已创建 `migrate.py` 用于存量数据库迁移

---

### 2026-03-16 handlers/checkin.py

- **完成内容**：
  - `#打卡 [知识点名]` — 自动 mastery+1，写 checkin_history，更新 daily_plan.status
  - `#打卡 [知识点名] [1-5]` — 直接给分
  - `#完成 [科目名]` — 批量标记科目今日计划全部完成
  - `#今日计划` — 按科目分组展示计划，含 ✅/⬜/⏭️ 状态图标
  - `#跳过 [知识点名]` — 标记 skipped，排入明日
  - `#连续打卡` — 展示当前/最长连续天数 + 下一里程碑
  - `_update_streak` 自动维护 checkin_streak 表（连续/断连判定）
  - 打卡后通过 PersonaEngine 渲染单次反馈 + 每5个连击提示
  - 全计划完成时提示日终总结将在晚间推送
- **设计决策**：
  - 知识点匹配使用 SQL LIKE 模糊匹配，优先从当日计划中查找
  - `_update_streak` 通过 last_complete_date 与昨日比较判定连续/中断
  - 所有入口含 `check_banned` 封禁检查
- **遗留事项**：里程碑反馈（7/30/60/100天）需配合 PersonaEngine 的 milestone_scripts 渲染

---

### 2026-03-16 handlers/schedule.py

- **完成内容**：
  - `#生成计划` 手动触发当日计划生成
  - `daily_plan_job` — 00:05 为所有用户重新生成每日计划
  - `morning_push_job` — 07:30 早安推送（检查 init_complete + is_banned + subscription_active）
  - `evening_summary_job` — 22:30 晚间复盘推送
  - `register_real_jobs()` — 用 `replace_existing=True` 替换 scheduler.py 占位回调
  - `@driver.on_startup` — Bot 启动时注册定时任务 + 加载 PersonaEngine
  - `_uid_to_qq()` — 从 security 内存映射反查 QQ 号
  - 早安/晚间 fallback 格式化（PersonaEngine 不可用时）
- **设计决策**：
  - 推送前检查 3 条件：init_complete + 非封禁 + 订阅中
  - 每条推送间 0.5s 间隔，避免 QQ 频率限制
  - 单用户失败不影响其他用户（try/except 包裹）
- **遗留事项**：无

---

### 2026-03-16 core/security.py (QQ映射持久化)

- **完成内容**：将 `_qq_to_uid` 内存映射补充了本地持久化（`data/.qq_map.json`），解决 Bot 重启后 `_uid_to_qq` 无法找人导致无法定时推送的问题。

---

### 2026-03-16 handlers/persona.py

- **完成内容**：
  - `#选择角色` — 无参数时展示角色列表（含 archetype / tagline）；有参数时切换角色（检查已解锁）
  - `#当前角色` — 展示当前角色名、风格、tagline、已解锁列表
  - `#解锁角色 [名称]` — 花费 80 积分解锁新角色，更新 `persona_config.unlocked_personas`（JSON 数组）
  - `#角色商店` — 展示所有角色的价格与解锁状态（✅/🔒）
  - `_NAME_TO_ID` 映射：支持中文名和英文 ID 两种输入
  - `_get_unlocked(conn, uid)` 辅助函数：读取 `persona_config.unlocked_personas` JSON 数组
- **设计决策及理由**：
  - `lingqi` 永远免费（`_FREE_PERSONAS`），其他角色 80 积分
  - 解锁后将新 persona_id append 到 JSON 数组并写回数据库，不另建表
  - 切换时同步更新 `persona_config.active_persona`，不走 PersonaEngine 内部
- **遇到的问题及解法**：无
- **遗留事项**：自定义角色（`/personas/custom/`）留 v3.3 版本实现

---

### 2026-03-16 handlers/points.py

- **完成内容**：
  - `#积分` — 展示余额、预估可用天数、订阅状态、推词档位、余额预警提示
  - `#积分明细` — 最近 10 条 `points_ledger` 流水，含中文原因翻译
  - `#充值` — 静态说明文案（内测阶段，暂无在线充值，联系管理员）
  - `#积分说明` — 静态积分制度说明（获取/消耗/预警阈值）
  - `#推词档位 [档位]` — 无参数展示当前档位和说明；有参数时更新 `points_account.word_tier`
  - `_reason_to_zh()` 辅助函数：枚举原因 → 中文描述
- **设计决策及理由**：
  - `#充值` 和 `#积分说明` 采用静态文案，内测阶段无需动态数据
  - 推词档位直接更新 DB，不需要 `points_service` 方法（该方法只管费用，不管 tier 字段）
  - 余额状态 normal/low/urgent/empty 直接从 `get_account_summary` 返回的字段读取
- **遇到的问题及解法**：无
- **遗留事项**：正式上线后 `#充值` 需对接实际支付系统

---

### 2026-03-16 handlers/words.py

- **完成内容**：
  - `#错题本` / `#错题本 [科目名]` — 英语查 `user_word_status`（in_error_book=1）；其他科目查 `knowledge_points`（mastery_level≤2）；全科汇总按科目分组展示
  - `#掌握 [知识点] [1-5]` — 手动设置掌握度，写 `checkin_history`，与 `#打卡` 逻辑一致
  - `#关联 [知识点A] [知识点B]` — 双向更新 `knowledge_points.cross_links`（JSON 数组），防止重复追加
  - `#添加科目 [名称] [类型]` — 插入 `subjects`（library_type='B'）+ `subject_status`（active）
  - `#同步` — Stub，返回开发中提示及当前可用替代操作
  - `_add_cross_link()` 辅助函数：原子追加 cross_link，读取-检查-写回
- **设计决策及理由**：
  - `#错题本` 按"英语"关键词判断是否查词库，其他统一查知识点表
  - `#添加科目` 默认 category="专业课"，最后一个 token 若为"专业课"/"公共课"则覆盖
  - `#同步` 暂做 Stub，文件解析逻辑复杂，记入 QUESTIONS.md
- **遇到的问题及解法**：无
- **遗留事项**：`#同步` CSV 文件上传功能待后续实现（详见 QUESTIONS.md）

---

### 2026-03-16 handlers/emotion.py

- **完成内容**：
  - `#陪我聊 [内容]` — 主动开启陪伴会话（5 积分），已在陪伴模式时直接继续对话
  - `#结束聊天` — 调用 `emotion_detector.end_session()`，渲染告别语
  - `emotion_listener` (on_message, priority=10, block=False) — 被动检测私聊情绪信号词：跳过 `#` 开头的指令；触发后扣 5 积分、调用 `start_session()`；使用 `bot.send(event, response)` 主动发送（不用 `matcher.finish()`，因为 block=False 不结束事件链）
- **设计决策及理由**：
  - priority=10 低于命令处理器（priority=5），确保指令类消息不被情绪监听器拦截
  - block=False 使后续处理器仍可运行（如日志、统计等）
  - 被动触发静默跳过余额不足的情况（不发警告），避免打扰用户正常学习流
  - 已在陪伴模式中收到 `#陪我聊` 时直接响应内容，无需重复扣费
- **遇到的问题及解法**：无
- **遗留事项**：`emotion_scripts.in_session` 脚本 key 需确认各角色 JSON 文件中是否已定义

---

### 2026-03-16 handlers/school.py

- **完成内容**：
  - `#查看院校配置` — 展示 `target_schools` + `subjects`/`subject_status`，含状态图标（✅/⏸️/❌）
  - `#停用科目 [名称]` — `INSERT OR REPLACE` 设置 `subject_status.status='suspended'`
  - `#激活科目 [名称]` — 设置 `status='active'`，清空 `suspended_at`
  - `#删除科目 [名称]` — 记录到模块级 `_pending_delete[uid]`，展示待删除知识点数量，等待确认
  - `#确认删除` — pop `_pending_delete`，按顺序删除 `subject_status` → `knowledge_points` → `subjects`
  - `#倒计时` — 从 `exam_config` 读取考试日期，显示剩余天数及备考阶段
  - `#设置考试日期 [YYYY-MM-DD]` — `INSERT OR REPLACE` 更新 `exam_config`
  - `#学情 [科目名]` / `#学情` — 消耗 5 积分；全科返回各科平均掌握度+今日进度；单科返回掌握度分布直方图
- **设计决策及理由**：
  - 删除操作用内存字典 `_pending_delete` 存待确认请求，重启后清空（可接受，防止误删更重要）
  - `subject_status` 使用 `INSERT … ON CONFLICT DO UPDATE` 语法（SQLite upsert），兼容首次插入和后续更新
  - `#学情` 先扣费再查询，与其他消耗积分指令保持一致
  - `_mastery_bar()` 统一使用 ⭐/☆ 符号，与打卡模块一致
- **遇到的问题及解法**：无
- **遗留事项**：`_pending_delete` 为内存存储，Bot 重启后未确认的删除请求会丢失（设计如此，安全第一）

---

### 2026-03-16 plugins/上岸/__init__.py 更新

- **完成内容**：追加导入 `persona`、`points`、`words`、`emotion`、`school` 五个 handler 模块，确保 NoneBot2 事件处理器被注册。
