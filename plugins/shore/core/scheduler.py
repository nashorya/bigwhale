"""
Scheduler — 遗忘曲线调度，每日计划生成，定时内容生成。
core/ 层禁止直接 import nonebot 模块。

本模块只负责：
  1. 计算知识点优先级
  2. 生成每日学习计划（写入 daily_plan 表）
  3. 生成早安/晚间推送内容（返回结构化数据，不直接发消息）

消息推送由 handlers/schedule.py 中的定时任务负责调用 bot.send。
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any


# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

# 每个知识点按 importance 预估的复习时长（分钟）
ESTIMATED_MINUTES = {1: 10, 2: 15, 3: 20}

# 默认每日学习总容量（分钟）
DEFAULT_DAILY_CAPACITY = 360

# 艾宾浩斯间隔天数序列
EBBINGHAUS_INTERVALS = [1, 3, 7, 15, 30]

# 备考阶段阈值
PHASE_THRESHOLDS = {
    "sprint_final": 14,   # 最后冲刺：≤14天
    "sprint": 60,         # 冲刺期：≤60天
    "intensify": 120,     # 强化期：≤120天
    # > 120天 为基础期
}


# ──────────────────────────────────────────────
# 优先级计算（纯函数，无副作用）
# ──────────────────────────────────────────────

def calc_priority(kp: dict, days_left: int) -> float:
    """
    按 v3.1 文档公式计算单个知识点的当日优先级分数。

    公式：priority = base × forget_factor × overdue_factor × sprint_factor

    参数：
        kp: 知识点字典，需包含字段：
            - importance (int 1-3)
            - mastery_level (int 1-5)
            - last_review_at (str | None)
            - next_review_at (str | None)
        days_left: 距考试剩余天数

    返回：
        优先级分数（越高越应该优先复习）
    """
    importance = kp.get("importance", 2)
    mastery = kp.get("mastery_level", 1)

    # 基础分：重要程度越高、掌握度越低，分越高
    base = importance * (6 - mastery)

    # 遗忘系数：距上次复习越久，分越高（最大 2 倍）
    last_review = kp.get("last_review_at")
    if last_review:
        try:
            last_dt = datetime.fromisoformat(last_review)
            days_since = (datetime.now() - last_dt).days
        except (ValueError, TypeError):
            days_since = 30
    else:
        days_since = 30  # 从未复习过，按 30 天计算

    forget_factor = min(2.0, 1 + days_since / 14)

    # 逾期系数：next_review_at 已过期的知识点额外加权
    next_review = kp.get("next_review_at")
    overdue_factor = 1.0
    if next_review:
        try:
            next_dt = datetime.fromisoformat(next_review)
            if next_dt < datetime.now():
                overdue_factor = 1.5
        except (ValueError, TypeError):
            pass

    # 冲刺系数：考试 ≤30 天时，高频考点（importance=3）额外加权
    sprint_factor = 1.0
    if days_left <= 30 and importance == 3:
        sprint_factor = 1.3

    return base * forget_factor * overdue_factor * sprint_factor


def get_study_phase(days_left: int) -> str:
    """
    根据剩余天数判断备考阶段。

    返回：
        'sprint_final' / 'sprint' / 'intensify' / 'foundation'
    """
    if days_left <= PHASE_THRESHOLDS["sprint_final"]:
        return "sprint_final"
    elif days_left <= PHASE_THRESHOLDS["sprint"]:
        return "sprint"
    elif days_left <= PHASE_THRESHOLDS["intensify"]:
        return "intensify"
    else:
        return "foundation"


def calc_next_review_date(mastery_level: int) -> str:
    """
    根据掌握度计算下次复习日期（艾宾浩斯间隔）。

    mastery_level 1 -> 1天后
    mastery_level 2 -> 3天后
    mastery_level 3 -> 7天后
    mastery_level 4 -> 15天后
    mastery_level 5 -> 30天后

    返回：
        ISO 格式日期字符串 'YYYY-MM-DD'
    """
    from datetime import timedelta
    idx = min(mastery_level - 1, len(EBBINGHAUS_INTERVALS) - 1)
    idx = max(0, idx)
    days = EBBINGHAUS_INTERVALS[idx]
    return (date.today() + timedelta(days=days)).isoformat()


# ──────────────────────────────────────────────
# Scheduler 类
# ──────────────────────────────────────────────

class Scheduler:
    """
    遗忘曲线调度，每日计划生成。
    所有方法接收 UserDB 实例，不直接操作连接。
    不 import nonebot 模块，不调用 bot.send。
    """

    @staticmethod
    async def generate_daily_plan(
        db: Any,  # UserDB 实例（避免循环导入用 Any）
        daily_capacity: int = DEFAULT_DAILY_CAPACITY,
    ) -> list[dict]:
        """
        为指定用户生成当日学习计划。

        优先级：
          ① 检查 weekly_plan 表中今日是否有 AI 生成的周计划条目
          ② 若有，直接使用（不走遗忘曲线）
          ③ 若无，回退到遗忘曲线算法（原逻辑）

        流程（回退模式，对应 v3.1 文档 5.4）：
          ① 读取考试日期，计算 days_left
          ② 读取所有 active 科目的知识点
          ③ 为每个知识点计算优先级分数
          ④ 按备考阶段过滤知识点
          ⑤ 按科目均匀分配，写入 daily_plan 表
        """
        today_str = date.today().isoformat()

        # ── 优先：使用 AI 周计划 ──
        weekly_today = await db.get_today_from_weekly_plan(today_str)
        if weekly_today:
            # 同步写入 daily_plan 表（保持打卡逻辑兼容）
            await db.clear_daily_plan(today_str)
            plan_items = []
            for item in weekly_today:
                kp_id = item.get("kp_id")
                if kp_id:
                    await db.insert_daily_plan(
                        date=today_str,
                        kp_id=kp_id,
                        priority_score=10.0,  # 周计划固定高优先级
                        estimated_minutes=item.get("estimated_minutes", 60),
                    )
                plan_items.append(item)
            await db.commit()
            return plan_items

        # ── 回退：遗忘曲线算法 ──
        exam_date_str = await db.get_exam_date()
        if exam_date_str:
            try:
                exam_dt = date.fromisoformat(exam_date_str)
                days_left = (exam_dt - date.today()).days
            except ValueError:
                days_left = 180
        else:
            days_left = 180

        days_left = max(0, days_left)

        # 读取所有 active 科目的知识点
        all_kps = await db.get_all_knowledge_points()
        if not all_kps:
            return []

        # 获取备考阶段
        phase = get_study_phase(days_left)

        # 按备考阶段过滤知识点
        filtered_kps = _filter_by_phase(all_kps, phase)
        if not filtered_kps:
            filtered_kps = all_kps

        # 计算优先级
        for kp in filtered_kps:
            kp["_priority"] = calc_priority(kp, days_left)

        # 按优先级降序排列
        filtered_kps.sort(key=lambda x: x["_priority"], reverse=True)

        # 按科目分组
        by_subject: dict[int, list[dict]] = {}
        for kp in filtered_kps:
            sid = kp["subject_id"]
            by_subject.setdefault(sid, []).append(kp)

        # 按科目均匀分配每日容量
        n_subjects = len(by_subject) or 1
        minutes_per_subject = daily_capacity // n_subjects

        plan_items: list[dict] = []
        for subject_id, kps in by_subject.items():
            remaining = minutes_per_subject
            for kp in kps:
                importance = kp.get("importance", 2)
                est_min = ESTIMATED_MINUTES.get(importance, 15)
                if remaining < est_min:
                    break
                plan_items.append({
                    "kp_id": kp["id"],
                    "priority_score": round(kp["_priority"], 2),
                    "estimated_minutes": est_min,
                    "subject_id": subject_id,
                    "subject_name": kp.get("subject_name", ""),
                    "topic_name": kp.get("topic_name", ""),
                    "importance": importance,
                    "mastery_level": kp.get("mastery_level", 1),
                })
                remaining -= est_min

        # 清除旧计划，写入新计划
        await db.clear_daily_plan(today_str)
        for item in plan_items:
            await db.insert_daily_plan(
                date=today_str,
                kp_id=item["kp_id"],
                priority_score=item["priority_score"],
                estimated_minutes=item["estimated_minutes"],
            )
        # 批量提交
        await db.commit()

        return plan_items

    @staticmethod
    async def generate_weekly_plan_from_ai(
        db: Any,
        school: str,
        major: str,
    ) -> list[dict]:
        """
        调用 LLM 生成按先修关系排序的 7 天学习计划，
        并将结果写入 weekly_plan 表和 4 个 CSV 文件。

        返回：写入的周计划列表（直接来自 DB）
        """
        from datetime import timedelta
        from ..core import ai_service
        from ..core.csv_export import save_csv_files

        today = date.today()
        today_str = today.isoformat()

        # ① 读取考试日期
        exam_date_str = await db.get_exam_date()
        if exam_date_str:
            try:
                exam_dt = date.fromisoformat(exam_date_str)
                days_left = max(0, (exam_dt - today).days)
            except ValueError:
                exam_dt = None
                days_left = 180
        else:
            exam_dt = None
            days_left = 180

        # ② 读取所有 active 科目及其知识点
        subjects = await db.get_active_subjects()
        if not subjects:
            return []

        subjects_with_kps = []
        subject_id_map: dict[str, int] = {}  # topic_name -> subject_id (用于匹配)
        kp_name_map: dict[str, int] = {}      # topic_name -> kp_id

        for subj in subjects:
            sid = subj["id"]
            sname = subj["name"]
            kps = await db.get_knowledge_points(sid)
            kp_names = [kp["topic_name"] for kp in kps]
            subjects_with_kps.append({
                "name": sname,
                "category": subj.get("category", ""),
                "knowledge_points": kp_names,
                "subject_id": sid,
            })
            for kp in kps:
                kp_name_map[kp["topic_name"]] = kp["id"]
                subject_id_map[kp["topic_name"]] = sid

        # ③ 收集已掌握的知识点（mastery ≥ 3）
        mastered_kps: list[str] = []
        all_kps = await db.get_all_knowledge_points()
        for kp in all_kps:
            if kp.get("mastery_level", 0) >= 3:
                mastered_kps.append(kp["topic_name"])

        # ④ 第一步：LLM 生成月目标（确定本月聚焦的知识点子集）
        monthly_goals = await ai_service.generate_monthly_goals(
            school=school,
            major=major,
            days_left=days_left,
            subjects_with_kps=subjects_with_kps,
            mastered_kps=mastered_kps,
            today=today_str,
        )
        if not monthly_goals:
            return []

        # ⑤ 第二步：LLM 基于月目标生成7天周计划
        llm_plan = await ai_service.generate_ordered_weekly_plan(
            school=school,
            major=major,
            days_left=days_left,
            monthly_goals=monthly_goals,
            today=today_str,
        )
        if not llm_plan:
            return []

        # ④ 将 LLM 结果映射到 DB 数据（匹配 kp_id、subject_id）
        week_start = today_str
        db_items: list[dict] = []
        day_counters: dict[int, int] = {}  # day -> order_in_day

        for entry in llm_plan:
            topic = entry.get("topic", "")
            subject = entry.get("subject", "")
            day_num = entry.get("day", 1)
            plan_date_str = entry.get("date", "")

            # 推算日期（如果 LLM 没给日期）
            if not plan_date_str:
                plan_date_str = (today + timedelta(days=day_num - 1)).isoformat()

            day_idx = day_num - 1
            order = day_counters.get(day_idx, 0)
            day_counters[day_idx] = order + 1

            # 匹配 subject_id 和 kp_id（模糊匹配）
            kp_id = kp_name_map.get(topic)
            subject_id = subject_id_map.get(topic)
            if subject_id is None:
                # 按科目名回退匹配
                for s in subjects:
                    if s["name"] == subject or subject in s["name"] or s["name"] in subject:
                        subject_id = s["id"]
                        break

            db_items.append({
                "plan_date": plan_date_str,
                "day_index": day_idx,
                "subject_id": subject_id,
                "kp_id": kp_id,
                "topic_name": topic,
                "subject_name": subject,
                "order_in_day": order,
                "estimated_minutes": entry.get("estimated_minutes", 60),
                "scheduled_time": entry.get("time", ""),
                "notes": entry.get("notes", ""),
            })

        # ⑤ 写入 DB
        await db.save_weekly_plan(week_start, db_items)

        # ⑥ 生成并保存 4 个 CSV 文件
        import os
        csv_dir = os.environ.get("CSV_OUTPUT_DIR", ".")
        subject_names = [s["name"] for s in subjects]
        save_csv_files(
            output_dir=csv_dir,
            subject_names=subject_names,
            start_date=today,
            exam_date=exam_dt or (today + timedelta(days=days_left)),
            weekly_plan_items=db_items,
        )

        return await db.get_weekly_plan(week_start)

    @staticmethod
    async def save_weekly_plan_from_ai(
        db: Any,
        llm_plan: list[dict],
    ) -> list[dict]:
        """
        将级联生成的 LLM 周计划结果映射并写入 weekly_plan 表。
        用于 generate_cascaded_weekly_plan 返回的原始数据。

        参数：
            db: UserDB 实例
            llm_plan: LLM 返回的周计划列表

        返回：
            写入到 DB 后的周计划列表
        """
        from datetime import timedelta

        today = date.today()
        today_str = today.isoformat()
        week_start = today_str

        # 读取知识点映射
        subjects = await db.get_active_subjects()
        kp_name_map: dict[str, int] = {}
        subject_id_map: dict[str, int] = {}
        subject_name_map: dict[str, int] = {}

        for subj in subjects:
            sid = subj["id"]
            subject_name_map[subj["name"]] = sid
            kps = await db.get_knowledge_points(sid)
            for kp in kps:
                kp_name_map[kp["topic_name"]] = kp["id"]
                subject_id_map[kp["topic_name"]] = sid

        db_items: list[dict] = []
        day_counters: dict[int, int] = {}

        for entry in llm_plan:
            topic = entry.get("topic", "")
            subject = entry.get("subject", "")
            day_num = entry.get("day", 1)
            plan_date_str = entry.get("date", "")

            if not plan_date_str:
                plan_date_str = (today + timedelta(days=day_num - 1)).isoformat()

            day_idx = day_num - 1
            order = day_counters.get(day_idx, 0)
            day_counters[day_idx] = order + 1

            # 匹配 kp_id 和 subject_id
            kp_id = kp_name_map.get(topic)
            subject_id = subject_id_map.get(topic)
            if subject_id is None:
                subject_id = subject_name_map.get(subject)
                if subject_id is None:
                    for sname, sid in subject_name_map.items():
                        if subject in sname or sname in subject:
                            subject_id = sid
                            break

            db_items.append({
                "plan_date": plan_date_str,
                "day_index": day_idx,
                "subject_id": subject_id,
                "kp_id": kp_id,
                "topic_name": topic,
                "subject_name": subject,
                "order_in_day": order,
                "estimated_minutes": entry.get("estimated_minutes", 60),
                "scheduled_time": entry.get("time", ""),
                "notes": entry.get("notes", ""),
            })

        await db.save_weekly_plan(week_start, db_items)
        return await db.get_weekly_plan(week_start)



    @staticmethod
    async def generate_morning_content(db: Any) -> dict[str, Any]:
        """
        生成早安推送的结构化内容（07:30 调用）。
        不发送消息，只返回数据字典供 handler 层渲染。

        返回字典包含：
            - days_left: 距考试剩余天数
            - phase: 备考阶段
            - subject_summary: 各科进度摘要文本
            - suggestion: 今日建议文本
            - plan_items: 今日计划列表
            - plan_by_subject: 按科目分组的计划
        """
        today_str = date.today().isoformat()

        # 考试日期
        exam_date_str = await db.get_exam_date()
        if exam_date_str:
            try:
                days_left = (date.fromisoformat(exam_date_str) - date.today()).days
            except ValueError:
                days_left = 180
        else:
            days_left = 180

        days_left = max(0, days_left)
        phase = get_study_phase(days_left)

        # 今日计划
        plan = await db.get_daily_plan(today_str)

        # 按科目分组计划
        plan_by_subject: dict[str, list[dict]] = {}
        total_minutes = 0
        for item in plan:
            # 从知识点表查找科目名和知识点名
            kp_id = item.get("kp_id")
            est = item.get("estimated_minutes", 15)
            total_minutes += est
            # 暂时使用 kp_id 作为索引
            sid_key = str(item.get("subject_id", "未分类"))
            plan_by_subject.setdefault(sid_key, []).append(item)

        # 各科掌握度摘要
        all_kps = await db.get_all_knowledge_points()
        subject_stats = _calc_subject_stats(all_kps)

        # 找出最薄弱科目
        weakest = None
        lowest_avg = 999.0
        for name, stats in subject_stats.items():
            if stats["avg_mastery"] < lowest_avg:
                lowest_avg = stats["avg_mastery"]
                weakest = name

        # 生成摘要文本
        summary_parts = []
        for name, stats in subject_stats.items():
            summary_parts.append(
                f"{name} 均分 {stats['avg_mastery']:.1f}"
            )
        subject_summary = "、".join(summary_parts) if summary_parts else "暂无科目数据"

        suggestion = ""
        if weakest and lowest_avg < 3.0:
            suggestion = f"{weakest} 有点落后，建议今天多给它一些时间"
        else:
            suggestion = "各科进度均衡，继续保持"

        return {
            "days_left": days_left,
            "phase": phase,
            "subject_summary": subject_summary,
            "suggestion": suggestion,
            "plan_items": plan,
            "plan_by_subject": plan_by_subject,
            "subject_stats": subject_stats,
            "total_minutes": total_minutes,
        }

    @staticmethod
    async def generate_evening_content(db: Any) -> dict[str, Any]:
        """
        生成晚间复盘推送的结构化内容（22:30 调用）。
        不发送消息，只返回数据字典供 handler 层渲染。

        返回字典包含：
            - rate: 今日完成率百分比
            - done_count: 已完成数
            - total_count: 总计划数
            - missed: 未完成知识点列表
            - improved_list: 今日掌握度提升的知识点
            - streak: 连续打卡天数
            - top3: 明日建议 Top3 知识点
        """
        today_str = date.today().isoformat()

        # 今日计划
        plan = await db.get_daily_plan(today_str)
        total = len(plan)
        done = sum(1 for p in plan if p.get("status") == "done")
        rate = round(done / total * 100) if total > 0 else 0

        # 未完成和已完成知识点
        missed = [p for p in plan if p.get("status") != "done"]
        improved = [
            p for p in plan
            if p.get("mastery_after") is not None
            and p.get("mastery_before") is not None
            and p["mastery_after"] > p["mastery_before"]
        ]

        # 连续打卡
        streak_data = await db.get_checkin_streak()

        # 明日建议 Top3：取当前所有知识点中优先级最高的3个
        exam_date_str = await db.get_exam_date()
        if exam_date_str:
            try:
                days_left = (date.fromisoformat(exam_date_str) - date.today()).days
            except ValueError:
                days_left = 180
        else:
            days_left = 180

        all_kps = await db.get_all_knowledge_points()
        for kp in all_kps:
            kp["_priority"] = calc_priority(kp, max(0, days_left - 1))
        all_kps.sort(key=lambda x: x["_priority"], reverse=True)
        top3 = [kp.get("topic_name", "") for kp in all_kps[:3]]

        return {
            "rate": rate,
            "done_count": done,
            "total_count": total,
            "missed": [p.get("kp_id") for p in missed],
            "missed_names": [],  # 需要 handler 层查询知识点名
            "improved_list": [
                f"{p.get('kp_id')}({p.get('mastery_before')}->{p.get('mastery_after')})"
                for p in improved
            ],
            "streak": streak_data.get("current_streak", 0),
            "top3": "、".join(top3) if top3 else "暂无建议",
            "days_left": days_left,
        }


# ──────────────────────────────────────────────
# 定时任务注册（供 handler 层调用）
# ──────────────────────────────────────────────

def register_scheduled_jobs(scheduler_instance: Any) -> None:
    """
    注册 APScheduler 定时任务。
    由 handler 层在启动时调用，传入 AsyncIOScheduler 实例。

    注册的任务：
      - 00:05  重新生成所有用户的每日计划
      - 07:30  早安推送（handler 层实现推送逻辑）
      - 22:30  晚间复盘（handler 层实现推送逻辑）

    注意：这里只注册任务触发时间和回调函数名 ID。
    实际的回调函数需要在 handler 层定义（因为需要访问 bot 实例）。
    """
    # 00:05 — 每日计划重新生成
    scheduler_instance.add_job(
        _placeholder_daily_plan_job,
        "cron",
        hour=0,
        minute=5,
        id="daily_plan_generation",
        replace_existing=True,
    )

    # 07:30 — 早安推送触发
    scheduler_instance.add_job(
        _placeholder_morning_push_job,
        "cron",
        hour=7,
        minute=30,
        id="morning_push",
        replace_existing=True,
    )

    # 22:30 — 晚间复盘触发
    scheduler_instance.add_job(
        _placeholder_evening_summary_job,
        "cron",
        hour=22,
        minute=30,
        id="evening_summary",
        replace_existing=True,
    )

    # 每30分钟 — 清理过期 Session
    from .session import SessionManager
    scheduler_instance.add_job(
        SessionManager.evict_idle,
        "interval",
        minutes=30,
        id="session_evict",
        replace_existing=True,
    )


async def _placeholder_daily_plan_job() -> None:
    """
    占位回调：每日计划生成。
    实际逻辑应在 handler 层覆盖此 job，
    因为需要遍历所有用户并使用数据库连接。
    """
    pass


async def _placeholder_morning_push_job() -> None:
    """占位回调：早安推送。实际逻辑在 handler 层。"""
    pass


async def _placeholder_evening_summary_job() -> None:
    """占位回调：晚间复盘。实际逻辑在 handler 层。"""
    pass


# ──────────────────────────────────────────────
# 内部辅助函数
# ──────────────────────────────────────────────

def _filter_by_phase(kps: list[dict], phase: str) -> list[dict]:
    """
    按备考阶段过滤知识点。

    - foundation（基础期 >120天）：所有知识点
    - intensify（强化期 60-120天）：加大 importance>=2 比例
    - sprint（冲刺期 <=60天）：importance=3 优先，停止引入新知识点
    - sprint_final（最后冲刺 <=14天）：只复习 mastery_level <= 3 的
    """
    if phase == "sprint_final":
        return [kp for kp in kps if kp.get("mastery_level", 1) <= 3]
    elif phase == "sprint":
        # 冲刺期：不引入 mastery_level=1 且 importance=1 的新知识点
        return [
            kp for kp in kps
            if not (kp.get("mastery_level", 1) == 1 and kp.get("importance", 2) == 1)
        ]
    elif phase == "intensify":
        # 强化期：所有知识点，但 importance>=2 的权重已在 calc_priority 中体现
        return kps
    else:
        # 基础期：所有知识点
        return kps


def _calc_subject_stats(all_kps: list[dict]) -> dict[str, dict]:
    """
    计算各科目的掌握度统计数据。

    返回：{科目名: {avg_mastery, total, mastered_count}}
    """
    by_subject: dict[str, list[int]] = {}
    for kp in all_kps:
        name = kp.get("subject_name", "未知")
        mastery = kp.get("mastery_level", 1)
        by_subject.setdefault(name, []).append(mastery)

    stats = {}
    for name, levels in by_subject.items():
        # 加权均分（按 importance 加权）
        avg = sum(levels) / len(levels) if levels else 0
        mastered = sum(1 for l in levels if l >= 4)
        stats[name] = {
            "avg_mastery": round(avg, 1),
            "total": len(levels),
            "mastered_count": mastered,
        }
    return stats
