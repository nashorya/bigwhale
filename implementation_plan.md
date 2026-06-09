# kaoyan_bot Vibe Coding 方案

> 今日验证成果 → 明日 Bot 集成路线

## 已验证能力

| 能力 | 状态 | 关键发现 |
|------|------|---------|
| 级联生成（月→周） | ✅ | Claude vision 效果最佳，暂缓科目正确排除 |
| 本周计划（非模板） | ✅ | 每周具体任务，与月度目标一致 |
| 单词碎片化 | ✅ | 英语整块只安排精读/翻译，单词交 Bot 推送 |
| 课表视觉识别 | ✅ Claude / ⚠️ Gemini | Claude 稳定，Gemini 中转站传图不稳定 |
| 智谱 Embedding | ✅ | `embedding-3`，2048维，598ms，语义区分优秀 |
| Gemini Embedding | ❌ | 中转站不支持 |
| 水课识别 | 📝 设计完成 | flash-lite 识别 → 用户确认 → prompt 注入 |

## 技术栈决定

| 组件 | 选型 | 理由 |
|------|------|------|
| 月度规划生成 | Claude Sonnet | 视觉稳定，格式好 |
| 周计划级联 | Claude Sonnet | 同上，视觉+级联一次搞定 |
| 水课识别 | Gemini flash-lite | 便宜快，轻量判断 |
| Memory Embedding | **智谱 embedding-3** | 零安装，2048维，<1分钱/月 |
| Memory 搜索 | 余弦相似度 | numpy 即可 |

---

## P0：Memory 系统

### 新增文件
- `core/memory_store.py` — 记忆读写 + 智谱 embedding 搜索
- `core/zhipu_embedding.py` — 智谱 embedding-3 封装

### 新增 DB 表
```sql
CREATE TABLE user_memory_daily (
    id INTEGER PRIMARY KEY, user_id TEXT, log_date DATE,
    category TEXT, content TEXT, embedding BLOB,
    created_at DATETIME DEFAULT (datetime('now'))
);
CREATE TABLE user_memory_long (
    id INTEGER PRIMARY KEY, user_id TEXT,
    fact TEXT, embedding BLOB,
    created_at DATETIME DEFAULT (datetime('now'))
);
```

### 写入时机
- 打卡 → "完成 B树，掌握度 3→4"
- 情绪 → "用户说压力大"
- 周计划 → "本周聚焦：极限、数据结构"

### 读取时机
- 早安推送 → 注入昨天+前天记忆
- 打卡反馈 → 搜索该知识点历史
- 周计划 → 注入上周学况

---

## P1：DB 扩展 + 迁移

- `study_plan` 表（月度规划 JSON + 水课列表）
- [.env](file:///c:/Users/nashorya/Desktop/kaoyan_bot/.env) 新增 `ZHIPU_API_KEY`

---

## P2：AI 层

### ai_service.py 新增
- `identify_water_courses(timetable)` — flash-lite 判水课
- `generate_semester_plan(image, subjects, exam_date)` — 月度规划

### 复用
- [generate_schedule_csv.py](file:///c:/Users/nashorya/Documents/Downloads/generate_schedule_csv.py) 的级联 prompt 逻辑移植

---

## P3：Handler

### [NEW] study_plan_handler.py
```
#生成备考计划
  → 上传课表(可选) → 水课确认 → Claude 级联生成
  → 写入 study_plan + monthly_goals
```

### [MODIFY] weekly_plan.py
- 读 monthly_goals 当前周 → 级联 prompt
- 单词不占整块 → 碎片推送

### [MODIFY] checkin.py
- 打卡写 memory

### [MODIFY] schedule.py
- 早安/晚间注入 memory
- 水课时段推词

---

## P4：Soul 联动

角色卡（`personas/builtin/*.json`）已有完整 scripts，增加 `{memory_hint}` 变量：
- 早安推送："你昨天在极限那卡了一下，今天换个角度试试"
- 打卡反馈："这个知识点你已经复习3次啦，越来越熟练了"

---

## P5：测试

- Memory 读写 + embedding 搜索
- 级联生成端到端
- 完整用户流程 walkthrough

---

## 明日执行顺序

1. **P0** [zhipu_embedding.py](file:///tmp/test_zhipu_embedding.py) + `memory_store.py`（~30min）
2. **P1** DB 迁移（~10min）
3. **P2** [ai_service.py](file:///c:/Users/nashorya/Desktop/kaoyan_bot/plugins/openclaw/core/ai_service.py) 新增函数（~30min）
4. **P3** Handler 改造（~1h）
5. **P4** Soul 联动（~20min）
6. **P5** 测试（~30min）
