"""
独立测试脚本：测试月目标 + 周计划两步 LLM 提示词效果。
支持切换不同模型对比，无需启动 NoneBot。

用法：
  python test_plan_prompt.py                          # 默认 gemini-2.5-flash
  python test_plan_prompt.py --model gemini-2.5-pro   # 指定模型
  python test_plan_prompt.py --step monthly           # 只测月目标
  python test_plan_prompt.py --step weekly             # 只测周计划（用硬编码月目标）
  python test_plan_prompt.py --step both               # 两步都测（默认）
"""

import argparse
import asyncio
import json
import os
import time

from dotenv import load_dotenv

load_dotenv(override=True)

# ──────────────────────────────────────────────
# 模拟用户数据（可按需修改）
# ──────────────────────────────────────────────

SCHOOL = "杭州电子科技大学"
MAJOR = "计算机科学与技术"
DAYS_LEFT = 270  # 距考试天数，修改这个值测试不同阶段
TODAY = "2026-03-17"

# 模拟科目和知识点（来自真实数据库）
SUBJECTS_WITH_KPS = [
    {
        "name": "数学（一）",
        "category": "公共课",
        "knowledge_points": [
            "实数集与函数", "极限与连续", "导数与微分", "一元函数微分学",
            "微分中值定理", "一元函数积分学", "实数的完备性",
            "不定积分", "定积分及应用", "无穷级数",
            "多元函数微分学", "多元函数积分学", "线性代数模块",
            "向量与线性方程组", "矩阵与变换", "特征值与二次型",
            "概率论基础", "随机变量", "数理统计"
        ],
    },
    {
        "name": "英语（一）",
        "category": "公共课",
        "knowledge_points": [
            "考研英语核心 5500 词汇深度记忆",
            "英语长难句分析与翻译",
            "阅读理解精练",
            "完形填空与新题型",
            "写作模板与高分句型"
        ],
    },
    {
        "name": "思想政治理论",
        "category": "公共课",
        "knowledge_points": [
            "马克思主义基本原理概论",
            "唯物辩证法及其核心规律",
            "认识论与真理观",
            "唯物史观与社会发展规律",
            "毛泽东思想及其历史地位",
            "中国特色社会主义理论",
            "思想道德与法治"
        ],
    },
    {
        "name": "计算机学科专业基础",
        "category": "专业课",
        "knowledge_points": [
            "数据结构基本概念与算法复杂度分析",
            "线性表、栈和队列的存储与运算",
            "树与二叉树的遍历及应用",
            "图的存储结构与最短路径算法",
            "各种排序算法的原理与性能比较",
            "查找算法（顺序/折半/哈希）",
            "计算机系统组成与冯·诺依曼体系",
            "进位计数制转换与BCD码表示",
            "定点数与浮点数的表示及运算",
            "存储器层次结构与Cache映射方式",
            "指令系统与CPU设计基础",
            "进程管理与调度算法",
            "内存管理与虚拟存储",
            "文件系统与I/O管理",
            "计算机网络体系结构",
            "TCP/IP协议与网络安全"
        ],
    },
]

# 已掌握的知识点（mastery >= 3）
MASTERED_KPS = []  # 空列表 = 全都没学过

# ──────────────────────────────────────────────
# 提示词（和 ai_service.py 保持一致）
# ──────────────────────────────────────────────

MONTHLY_GOALS_PROMPT = """\
你是一位考研学习规划专家。请根据备考周期，从知识点列表中**严格筛选出极少数**本月应聚焦的知识点。

⚠️ 核心原则：宁可少不可多。前期备考一个月只深入吃透1-2个知识点。

## 用户信息
- 目标院校：{school}
- 报考专业：{major}
- 距考试天数：{days_left} 天（约 {months_left} 个月）
- 今天日期：{today}

## 全部科目及知识点
{subjects_json}

## 已掌握的知识点（mastery ≥ 3，应跳过）
{mastered_json}

## 硬性数量上限（违反即为错误）

| 阶段 | 距考试天数 | 每个科目本月最多选几个知识点 | 全部科目合计上限 |
|------|-----------|------------------------|-------------|
| 前期 | >150天    | **1个**                | **总共不超过5个** |
| 中期 | 60-150天  | 2个                    | 总共不超过8个 |
| 后期 | <60天     | 3-4个                  | 总共不超过15个 |

### 正确示例（距考试250天，前期）
✅ 数学：只选「极限与连续」（1个）— 本月整月深入学习极限
✅ 计算机：只选「数据结构基本概念」（1个）— 本月打基础
✅ 英语：只选「词汇记忆」（1个）
✅ 政治：只选「马原概论」（1个）
合计 = 4个知识点 ✅

### 错误示例（切勿这样做）
❌ 数学：选「极限、导数、微分、积分、级数」（5个）— 严重违规！前期每科只能1个！
❌ 计算机：选「数据结构、计组、操作系统」（3个）— 违规！前期只能1个！

## 其他规则
1. 先修顺序：必须先选前置知识点（数学先极限再导数，计算机先数据结构再计组）
2. 已掌握(mastery≥3)的跳过，选下一个未掌握的
3. 英语前期(>100天)只选词汇，中期加阅读，后期加写作

## 输出格式
返回 JSON 数组（数组长度严格遵守上限）：
[
  {{
    "subject": "科目名称",
    "topics": ["知识点1"],
    "priority": "high/medium/low",
    "reason": "10字以内理由"
  }}
]

不要包含任何其他文字，只返回 JSON 数组。
"""

WEEKLY_PLAN_PROMPT = """\
你是一位考研学习规划专家。请根据**本月学习目标**，为用户安排本周7天的详细学习日程。

⚠️ 核心原则：月目标中的每个知识点应该在7天中**反复出现**，每天都深入学习，而不是一天学一个新的。

## 用户信息
- 目标院校：{school}
- 报考专业：{major}
- 距考试天数：{days_left} 天
- 今天日期：{today}

## 本月学习目标（只安排以下知识点，严禁添加其他知识点）
{monthly_goals_json}

## 排课规则（必须严格遵守）

1. **严禁添加月目标以外的知识点**：
   - topic 字段必须和月目标中 topics 数组的值完全一致
   - 如果月目标只有"极限与连续"，那7天都只能安排"极限与连续"

2. **同一知识点每天都要安排**（这是最重要的规则）：
   - 月目标中的每个知识点应该在 Day 1 到 Day 7 **每天都出现**
   - 每天同一知识点安排 60-120 分钟，学习不同的子内容
   - 示例：数学"极限与连续"→ Day1:理解定义, Day2:计算方法, Day3:连续性, Day4:习题, Day5:进阶, Day6:总结, Day7:测试

3. **每天分配合理**：
   - 每天总学习时间 300-480 分钟
   - 每天涉及月目标中的所有科目

4. **时间段分配**：
   - 上午：09:00、10:00、11:00（数学、专业课）
   - 下午：14:00、15:00、16:00（英语、政治）
   - 晚上：19:00、20:00、21:00（复习、练习）
   - 每个时段只安排一个知识点

### 正确排课（月目标=数学"极限与连续"+计算机"数据结构基本概念"+英语"词汇"）
✅ Day1: 09:00数学极限与连续, 14:00英语词汇, 19:00计算机数据结构基本概念
✅ Day2: 09:00数学极限与连续, 14:00英语词汇, 19:00计算机数据结构基本概念
（7天都是这3个知识点反复深入）

### 错误排课
❌ Day1:极限, Day2:导数, Day3:微分, Day4:积分 — 每天换新知识点是严重错误！

## 输出格式
返回 JSON 数组：
[
  {{
    "day": 1,
    "date": "YYYY-MM-DD",
    "time": "09:00",
    "subject": "科目名称",
    "topic": "知识点名称（必须与月目标完全一致）",
    "estimated_minutes": 60,
    "notes": "10字以内的当日学习重点（如：理解ε-δ定义）"
  }}
]

不要包含任何其他文字，只返回 JSON 数组。
"""

# 硬编码的月目标（--step weekly 时使用）
DEFAULT_MONTHLY_GOALS = [
    {"subject": "数学（一）", "topics": ["极限与连续"], "priority": "high", "reason": "数学基础核心"},
    {"subject": "计算机学科专业基础", "topics": ["数据结构基本概念与算法复杂度分析"], "priority": "high", "reason": "408第一章"},
    {"subject": "英语（一）", "topics": ["考研英语核心 5500 词汇深度记忆"], "priority": "high", "reason": "前期词汇为主"},
    {"subject": "思想政治理论", "topics": ["马克思主义基本原理概论"], "priority": "medium", "reason": "政治入门"},
]


# ──────────────────────────────────────────────
# LLM 调用
# ──────────────────────────────────────────────

async def call_llm(prompt: str, model: str) -> str:
    """调用 Gemini API 生成响应"""
    import google.generativeai as genai

    api_key = os.environ.get("GEMINI_API_KEY", "")
    base_url = os.environ.get("GEMINI_BASE_URL", "")

    if base_url:
        genai.configure(api_key=api_key, transport="rest",
                        client_options={"api_endpoint": base_url})
    else:
        genai.configure(api_key=api_key)

    gen_model = genai.GenerativeModel(model)
    config = genai.GenerationConfig(
        temperature=0.2,
        response_mime_type="application/json",
    )

    print(f"\n⏳ 调用模型 {model}...")
    t0 = time.time()
    response = await gen_model.generate_content_async(prompt, generation_config=config)
    elapsed = time.time() - t0
    print(f"✅ 响应完成（{elapsed:.1f}秒）")

    return response.text


def clean_json(text: str) -> str:
    """清理 Markdown 代码块标记"""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


# ──────────────────────────────────────────────
# 主流程
# ──────────────────────────────────────────────

async def test_monthly(model: str) -> list[dict]:
    """测试月目标生成"""
    months_left = max(1, round(DAYS_LEFT / 30, 1))

    prompt = MONTHLY_GOALS_PROMPT.format(
        school=SCHOOL,
        major=MAJOR,
        days_left=DAYS_LEFT,
        months_left=months_left,
        today=TODAY,
        subjects_json=json.dumps(SUBJECTS_WITH_KPS, ensure_ascii=False, indent=2),
        mastered_json=json.dumps(MASTERED_KPS, ensure_ascii=False) if MASTERED_KPS else "无",
    )

    print("\n" + "=" * 60)
    print("📋 第一步：月目标生成")
    print(f"   模型: {model}")
    print(f"   距考试: {DAYS_LEFT} 天（{'前期' if DAYS_LEFT > 150 else '中期' if DAYS_LEFT > 60 else '后期'}）")
    print("=" * 60)

    raw = await call_llm(prompt, model)
    cleaned = clean_json(raw)

    try:
        goals = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON 解析失败: {e}")
        print(f"原始响应:\n{raw}")
        return []

    # 统计分析
    total_topics = sum(len(g.get("topics", [])) for g in goals)
    print(f"\n📊 月目标结果:")
    print(f"   科目数: {len(goals)}")
    print(f"   知识点总数: {total_topics}")

    if DAYS_LEFT > 150 and total_topics > 5:
        print(f"   ⚠️ 警告：前期知识点超过5个上限！({total_topics})")
    elif DAYS_LEFT > 150 and total_topics <= 5:
        print(f"   ✅ 符合前期上限（≤5个）")

    for g in goals:
        topics = g.get("topics", [])
        priority = g.get("priority", "?")
        reason = g.get("reason", "")
        print(f"\n   [{priority}] {g['subject']}:")
        for t in topics:
            print(f"      · {t}")
        if reason:
            print(f"      理由: {reason}")

    print(f"\n📝 原始 JSON:\n{json.dumps(goals, ensure_ascii=False, indent=2)}")
    return goals


async def test_weekly(model: str, monthly_goals: list[dict]) -> list[dict]:
    """测试周计划生成"""
    prompt = WEEKLY_PLAN_PROMPT.format(
        school=SCHOOL,
        major=MAJOR,
        days_left=DAYS_LEFT,
        today=TODAY,
        monthly_goals_json=json.dumps(monthly_goals, ensure_ascii=False, indent=2),
    )

    print("\n" + "=" * 60)
    print("📅 第二步：周计划生成")
    print(f"   模型: {model}")
    print("=" * 60)

    raw = await call_llm(prompt, model)
    cleaned = clean_json(raw)

    try:
        plan = json.loads(cleaned)
    except json.JSONDecodeError as e:
        print(f"\n❌ JSON 解析失败: {e}")
        print(f"原始响应:\n{raw}")
        return []

    # 统计分析
    # 获取月目标中的合法 topic 集合
    valid_topics = set()
    for g in monthly_goals:
        valid_topics.update(g.get("topics", []))

    actual_topics = set(item.get("topic", "") for item in plan)
    illegal = actual_topics - valid_topics

    print(f"\n📊 周计划结果:")
    print(f"   总任务数: {len(plan)}")
    print(f"   不同知识点: {len(actual_topics)}")

    if illegal:
        print(f"   ❌ 非法知识点（不在月目标中）: {illegal}")
    else:
        print(f"   ✅ 所有知识点均在月目标范围内")

    # 按天展示
    day_map: dict[int, list] = {}
    for item in plan:
        d = item.get("day", 0)
        day_map.setdefault(d, []).append(item)

    weekday_zh = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    for day_num in sorted(day_map.keys()):
        items = day_map[day_num]
        date_str = items[0].get("date", "?")
        print(f"\n   【Day {day_num} · {date_str}】")
        for item in items:
            t = item.get("time", "??:??")
            subj = item.get("subject", "?")
            topic = item.get("topic", "?")
            mins = item.get("estimated_minutes", "?")
            notes = item.get("notes", "")
            marker = "✅" if topic in valid_topics else "❌"
            print(f"      {marker} [{t}] {subj}：{topic}（{mins}分钟）{notes}")

    # 检查每个月目标知识点是否每天都出现
    print(f"\n📊 知识点覆盖分析:")
    for topic in valid_topics:
        days_with_topic = set()
        for item in plan:
            if item.get("topic") == topic:
                days_with_topic.add(item.get("day"))
        coverage = len(days_with_topic)
        status = "✅" if coverage >= 5 else "⚠️" if coverage >= 3 else "❌"
        print(f"   {status} {topic}: 出现在 {coverage}/7 天")

    return plan


async def main():
    parser = argparse.ArgumentParser(description="测试月目标+周计划提示词效果")
    parser.add_argument("--model", default="gemini-2.5-flash",
                        help="模型名称（默认 gemini-2.5-flash）")
    parser.add_argument("--step", choices=["monthly", "weekly", "both"], default="both",
                        help="测试步骤：monthly/weekly/both（默认 both）")
    parser.add_argument("--days", type=int, default=None,
                        help="覆盖距考试天数（测试不同阶段）")
    args = parser.parse_args()

    global DAYS_LEFT
    if args.days is not None:
        DAYS_LEFT = args.days

    if args.step in ("monthly", "both"):
        goals = await test_monthly(args.model)
    else:
        goals = DEFAULT_MONTHLY_GOALS
        print(f"\n📋 使用硬编码月目标（{len(goals)} 个科目）")

    if args.step in ("weekly", "both"):
        if not goals:
            print("\n❌ 月目标为空，无法生成周计划")
            return
        await test_weekly(args.model, goals)

    print("\n" + "=" * 60)
    print("🎉 测试完成！")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
