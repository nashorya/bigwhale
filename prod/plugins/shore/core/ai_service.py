"""
AI 服务模块 — 封装 OpenAI 兼容协议与外部搜索调用。

提供考研科目检索、知识点生成等 AI 能力。
策略：研招网爬取科目 → Tavily 搜索知识点 → LLM 结构化/回退。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

logger = logging.getLogger("shore.ai_service")

# ──────────────────────────────────────────────
# OpenAI 兼容协议客户端配置
# ──────────────────────────────────────────────

_client = None


def _get_client():
    """获取或创建 OpenAI 兼容协议客户端（延迟初始化）。"""
    global _client
    if _client is None:
        from openai import AsyncOpenAI

        api_key = os.environ.get("API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "未配置 API_KEY 环境变量。请在 .env 文件中添加 API_KEY。"
            )
        base_url = os.environ.get("BASE_URL", "")
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        _client = AsyncOpenAI(**kwargs)
    return _client


def _get_model() -> str:
    """获取通用聊天模型名。"""
    return _get_chat_model()


# 重试配置
_MAX_RETRIES = 3
_RETRY_DELAYS = [2, 5, 10]  # 每次重试的等待秒数


async def _generate_with_retry(
    prompt: str,
    temperature: float = 0.3,
    response_mime_type: str | None = None,
    system_instruction: str | None = None,
    max_tokens: int = 8000,
) -> str | None:
    """
    带重试的 OpenAI 兼容协议调用。

    最多重试 3 次，每次间隔递增。
    """
    client = _get_client()
    model = _get_model()

    messages: list[dict[str, str]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": prompt})

    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            last_error = e
            err_str = str(e)
            # 仅对 401/429/5xx 等暂时性错误重试
            if (
                "401" in err_str
                or "429" in err_str
                or "500" in err_str
                or "503" in err_str
            ):
                delay = _RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS) - 1)]
                logger.warning(
                    "AI API 暂时性错误 (第%d次), %d秒后重试: %s",
                    attempt + 1,
                    delay,
                    err_str[:100],
                )
                await asyncio.sleep(delay)
            else:
                # 非暂时性错误，直接抛出
                raise

    logger.error("AI API 重试 %d 次后仍失败: %s", _MAX_RETRIES, last_error)
    raise last_error  # type: ignore


# ──────────────────────────────────────────────
# 计划生成
# ──────────────────────────────────────────────


async def _generate_with_plan_model(
    prompt: str,
    temperature: float = 0.3,
    system_instruction: str | None = None,
    response_format: str | None = None,
    max_tokens: int = 8000,
) -> str | None:
    """
    计划生成统一复用 CHAT_MODEL。
    保留函数名是为了让既有调用点表达语义清晰。
    """
    return await _generate_with_retry(
        prompt=prompt,
        temperature=temperature,
        system_instruction=system_instruction,
        max_tokens=max_tokens,
    )


# ──────────────────────────────────────────────
# 陪聊多轮对话（使用轻量模型）
# ──────────────────────────────────────────────


def _get_chat_model() -> str:
    """获取通用聊天模型名。"""
    model = os.environ.get("CHAT_MODEL", "")
    if not model:
        raise RuntimeError("环境变量 CHAT_MODEL 未设置，请在 .env 中配置模型名。")
    return model


async def generate_chat_response(
    system_prompt: str,
    history: list[dict[str, str]],
    user_message: str,
) -> str:
    """
    多轮对话生成（陪聊模式专用）。
    使用 OpenAI 兼容协议。

    参数：
        system_prompt: 角色卡 system instruction
        history: 历史消息列表 [{"role": "user"/"model", "text": "..."}]
        user_message: 当前用户消息

    返回：
        AI 回复文本
    """
    client = _get_client()
    model = _get_chat_model()

    # 构建 OpenAI 格式的 messages
    messages = [{"role": "system", "content": system_prompt}]
    for msg in history:
        # 兼容数据库中既有的 "model" 角色名
        role = "assistant" if msg["role"] == "model" else "user"
        messages.append({"role": role, "content": msg["text"]})
    messages.append({"role": "user", "content": user_message})

    import time as _time

    t0 = _time.monotonic()
    print(f"[陪聊] LLM 调用开始 (model={model}, history={len(history)}条)")
    try:
        response = await asyncio.wait_for(
            client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.8,
                max_tokens=2048,
            ),
            timeout=30,  # 30秒超时，防止代理挂起
        )
        elapsed = _time.monotonic() - t0
        finish_reason = (
            response.choices[0].finish_reason if response.choices else "unknown"
        )
        full_text = (
            response.choices[0].message.content or "" if response.choices else ""
        )
        print("\n===== 陪聊 LLM 诊断 =====")
        print(f"耗时: {elapsed:.1f}秒")
        print(f"finish_reason: {finish_reason}")
        print(f"回复长度: {len(full_text)} 字符")
        print(f"完整回复: 【{full_text}】")
        print("=========================\n")
        return full_text or "……"
    except asyncio.TimeoutError:
        elapsed = _time.monotonic() - t0
        print(f"[陪聊] LLM 调用超时 ({elapsed:.1f}秒)")
        return "喵…网络好像卡住了，你再说一次好吗？"
    except Exception as e:
        elapsed = _time.monotonic() - t0
        print(f"[陪聊] LLM 调用失败 ({elapsed:.1f}秒): {e}")
        return "抱歉，我现在有点恍惚…再说一次好吗？"


# ──────────────────────────────────────────────
# Tavily Search 客户端
# ──────────────────────────────────────────────

_tavily_client = None


def _get_tavily_client():
    """获取或创建 Tavily 客户端（延迟初始化）"""
    global _tavily_client
    if _tavily_client is None:
        api_key = os.environ.get("TAVILY_API_KEY", "")
        if not api_key:
            logger.warning("未配置 TAVILY_API_KEY，知识点搜索将回退到纯 LLM")
            return None
        try:
            from tavily import AsyncTavilyClient

            _tavily_client = AsyncTavilyClient(api_key=api_key)
        except ImportError:
            logger.warning("tavily-python 未安装，知识点搜索将回退到纯 LLM")
            return None
    return _tavily_client


# ──────────────────────────────────────────────
# 考研科目检索（主入口）
# ──────────────────────────────────────────────

_LLM_SUBJECT_PROMPT = """\
你是一个考研信息助手。用户想报考 {school} 的 {major} 专业（硕士研究生）。

请根据你的知识，返回该院校该专业的考研初试科目信息。

要求：
1. 必须包含所有初试科目（公共课 + 专业课）
2. 公共课通常包含政治、英语、数学（视专业而定）
3. 专业课请根据该校该专业的常见考试科目填写
4. 如果是统考科目（如408计算机学科专业基础），请标注

请严格返回以下 JSON 格式，不要包含任何其他文字：
{{
  "school": "{school}",
  "major": "{major}",
  "subjects": [
    {{
      "name": "科目名称",
      "category": "公共课 或 专业课",
      "code": "科目代码（如101、201等）"
    }}
  ],
  "notes": "补充说明"
}}
"""


async def search_exam_subjects(school: str, major: str) -> dict[str, Any]:
    """
    检索考研科目信息（双层策略）。

    策略：
    1. 优先从研招网爬取真实数据
    2. 爬取失败则回退到纯 LLM（不使用 Google Search）

    获取科目后，使用 Tavily Search 补充知识点。

    参数：
        school: 目标院校名称
        major: 报考专业名称

    返回：
        包含科目和知识点的字典

    异常：
        RuntimeError: 所有策略均失败
    """
    # ── 第一层：研招网爬取 ──
    spider_result = None
    try:
        from . import yzchsi_spider

        chsi_user = os.environ.get("CHSI_USERNAME", "")
        logger.info(
            "准备爬取研招网: school=%s, major=%s, CHSI_USERNAME=%s",
            school,
            major,
            f"{chsi_user[:3]}***" if chsi_user else "(未配置)",
        )
        spider_result = await yzchsi_spider.fetch_exam_subjects(school, major)
        if spider_result and spider_result.get("subjects"):
            logger.info(
                "研招网爬取成功: %s %s, %d 个科目",
                school,
                major,
                len(spider_result["subjects"]),
            )
        else:
            logger.info("研招网爬取返回空结果")
    except Exception as e:
        logger.warning("研招网爬取失败，回退到 LLM: %s", e, exc_info=True)
        spider_result = None

    # ── 第二层：LLM 回退（无 Google Search，15秒超时）──
    if not spider_result or not spider_result.get("subjects"):
        logger.info("使用 LLM 生成科目信息: %s %s", school, major)
        try:
            spider_result = await asyncio.wait_for(
                _llm_get_subjects(school, major), timeout=15
            )
        except asyncio.TimeoutError:
            logger.warning("LLM 科目生成超时(15s)")
            spider_result = None
        except Exception as e:
            logger.warning("LLM 科目生成失败: %s", e)
            spider_result = None

    if not spider_result or not spider_result.get("subjects"):
        raise RuntimeError("未能获取考试科目信息（研招网和 LLM 均失败）")

    # ── 第三层：Tavily 搜索知识点（可选，10秒超时，失败不影响结果）──
    subjects = spider_result["subjects"]
    try:
        subjects_with_kps = await asyncio.wait_for(
            _enrich_with_knowledge_points(school, major, subjects),
            timeout=10,
        )
        spider_result["subjects"] = subjects_with_kps
        logger.info("知识点补充完成")
    except asyncio.TimeoutError:
        logger.warning("Tavily 知识点补充超时(10s)，跳过")
    except Exception as e:
        logger.warning("知识点补充失败: %s，跳过", e)

    return spider_result


# ──────────────────────────────────────────────
# LLM 科目生成（不使用 Google Search）
# ──────────────────────────────────────────────


async def _llm_get_subjects(school: str, major: str) -> dict[str, Any] | None:
    """使用纯 LLM 生成科目信息（无外部搜索工具，带重试）。"""
    prompt = _LLM_SUBJECT_PROMPT.format(school=school, major=major)

    try:
        text = await _generate_with_retry(
            prompt=prompt,
            temperature=0.1,
            response_mime_type="application/json",
        )
        if not text:
            return None

        text = _clean_json_text(text)
        result = json.loads(text)
        logger.info(
            "LLM 科目生成成功: %s %s, %d 个科目",
            school,
            major,
            len(result.get("subjects", [])),
        )
        return result

    except json.JSONDecodeError as e:
        logger.error("LLM 响应 JSON 解析失败: %s", e)
        return None
    except Exception as e:
        logger.error("LLM 科目生成失败（重试后）: %s", e)
        return None


# ──────────────────────────────────────────────
# Tavily 搜索知识点
# ──────────────────────────────────────────────

_KP_SEARCH_TEMPLATE = "{school} {major} 考研 {subject_name} 考试大纲 知识点 重点章节"

_KP_LLM_PROMPT = """\
你是一个考研信息助手。以下是关于考研科目的搜索结果摘要。
请根据这些信息，为 {school} {major} 专业的 "{subject_name}" 科目整理出 5-15 个核心知识点或重点章节。

搜索结果：
{search_context}

要求：
1. 知识点要具体、实用，覆盖考试重点
2. 使用简短的条目名称（不超过20字）
3. 按重要性或章节顺序排列

请严格返回 JSON 数组格式，不要包含任何其他文字：
["知识点1", "知识点2", "知识点3", ...]
"""

_KP_PURE_LLM_PROMPT = """\
你是一个考研信息助手。请为 {school} {major} 专业的 "{subject_name}" 科目列出 5-15 个核心知识点或重点章节。

要求：
1. 知识点要具体、实用，覆盖考试重点
2. 如果是统考科目（如 408），列出各子科目的核心内容
3. 使用简短的条目名称（不超过20字）

请严格返回 JSON 数组格式，不要包含任何其他文字：
["知识点1", "知识点2", "知识点3", ...]
"""


async def _enrich_with_knowledge_points(
    school: str, major: str, subjects: list[dict]
) -> list[dict]:
    """为每个科目补充知识点（Tavily 搜索 + LLM 整理）。"""
    enriched = []
    for subj in subjects:
        subject_name = subj.get("name", "")
        if not subject_name:
            enriched.append(subj)
            continue

        # 如果已有知识点，跳过
        if subj.get("knowledge_points"):
            enriched.append(subj)
            continue

        kps = await _get_knowledge_points(school, major, subject_name)
        subj["knowledge_points"] = kps
        enriched.append(subj)

    return enriched


async def _get_knowledge_points(
    school: str, major: str, subject_name: str
) -> list[str]:
    """获取单个科目的知识点列表。"""
    # 1. 尝试 Tavily 搜索
    tavily = _get_tavily_client()
    search_context = ""
    if tavily:
        try:
            query = _KP_SEARCH_TEMPLATE.format(
                school=school,
                major=major,
                subject_name=subject_name,
            )
            result = await tavily.search(
                query=query,
                search_depth="basic",
                max_results=5,
                include_answer=True,
            )
            # 拼接搜索结果
            parts = []
            if result.get("answer"):
                parts.append(f"总结：{result['answer']}")
            for r in result.get("results", []):
                title = r.get("title", "")
                content = r.get("content", "")
                if content:
                    parts.append(f"- {title}: {content[:300]}")
            search_context = "\n".join(parts)
            logger.info(
                "Tavily 搜索完成: %s, 获取 %d 条结果",
                subject_name,
                len(result.get("results", [])),
            )
        except Exception as e:
            logger.warning("Tavily 搜索失败 (%s): %s", subject_name, e)
            search_context = ""

    # 2. 用 LLM 结构化（有搜索结果时用搜索增强提示词，否则用纯 LLM 提示词）
    if search_context:
        prompt = _KP_LLM_PROMPT.format(
            school=school,
            major=major,
            subject_name=subject_name,
            search_context=search_context,
        )
    else:
        prompt = _KP_PURE_LLM_PROMPT.format(
            school=school,
            major=major,
            subject_name=subject_name,
        )

    try:
        text = await _generate_with_retry(
            prompt=prompt,
            temperature=0.3,
            response_mime_type="application/json",
        )
        if not text:
            return _default_knowledge_points(subject_name)

        text = _clean_json_text(text)
        kps = json.loads(text)
        if isinstance(kps, list) and kps:
            return kps[:15]  # 最多15个

    except Exception as e:
        logger.warning("知识点 LLM 生成失败 (%s): %s", subject_name, e)

    return _default_knowledge_points(subject_name)


def _default_knowledge_points(subject_name: str) -> list[str]:
    """兜底的默认知识点（当所有方法失败时）。"""
    defaults = {
        "思想政治理论": [
            "马克思主义基本原理",
            "毛泽东思想",
            "中国特色社会主义理论体系",
            "中国近现代史纲要",
            "思想道德与法治",
            "形势与政策",
        ],
        "英语": [
            "完形填空",
            "阅读理解",
            "新题型",
            "翻译",
            "写作",
        ],
    }
    # 模糊匹配默认知识点
    for key, kps in defaults.items():
        if key in subject_name:
            return kps
    return [f"{subject_name}核心内容"]


# ──────────────────────────────────────────────
# 第一步：月目标生成（决定本月聚焦哪些知识点）
# ──────────────────────────────────────────────

_MONTHLY_GOALS_PROMPT = """\
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


async def generate_monthly_goals(
    school: str,
    major: str,
    days_left: int,
    subjects_with_kps: list[dict],
    mastered_kps: list[str],
    today: str,
) -> list[dict] | None:
    """
    调用 LLM 生成月度学习目标（本月应聚焦哪些知识点）。

    返回：
        月目标列表，每项含 subject/topics/priority/reason
        失败时返回 None
    """
    import json as _json

    subjects_summary = []
    for s in subjects_with_kps:
        kps = s.get("knowledge_points", [])
        subjects_summary.append(
            {
                "name": s["name"],
                "category": s.get("category", ""),
                "knowledge_points": kps[:30],
            }
        )

    months_left = max(1, round(days_left / 30, 1))

    prompt = _MONTHLY_GOALS_PROMPT.format(
        school=school,
        major=major,
        days_left=days_left,
        months_left=months_left,
        today=today,
        subjects_json=_json.dumps(subjects_summary, ensure_ascii=False, indent=2),
        mastered_json=_json.dumps(mastered_kps, ensure_ascii=False)
        if mastered_kps
        else "无",
    )

    try:
        text = await _generate_with_retry(
            prompt=prompt,
            temperature=0.2,
            response_mime_type="application/json",
        )
        if not text:
            return None

        text = _clean_json_text(text)
        goals = _json.loads(text)
        if isinstance(goals, list) and goals:
            logger.info("LLM 月目标生成成功：%d 个科目", len(goals))
            return goals

    except _json.JSONDecodeError as e:
        logger.error("LLM 月目标 JSON 解析失败: %s", e)
    except Exception as e:
        logger.error("LLM 月目标生成失败: %s", e)

    return None


# ──────────────────────────────────────────────
# 第二步：周计划生成（基于月目标子集排7天日程）
# ──────────────────────────────────────────────

_WEEKLY_PLAN_PROMPT = """\
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


async def generate_ordered_weekly_plan(
    school: str,
    major: str,
    days_left: int,
    monthly_goals: list[dict],
    today: str,
) -> list[dict] | None:
    """
    基于月目标子集，调用 LLM 生成 7 天学习日程。

    参数：
        monthly_goals: generate_monthly_goals() 的返回值
        其他参数同前

    返回：
        周计划列表，每项含 day/date/time/subject/topic/estimated_minutes/notes
    """
    import json as _json

    prompt = _WEEKLY_PLAN_PROMPT.format(
        school=school,
        major=major,
        days_left=days_left,
        today=today,
        monthly_goals_json=_json.dumps(monthly_goals, ensure_ascii=False, indent=2),
    )

    try:
        text = await _generate_with_retry(
            prompt=prompt,
            temperature=0.2,
            response_mime_type="application/json",
        )
        if not text:
            return None

        text = _clean_json_text(text)
        plan = _json.loads(text)
        if isinstance(plan, list) and plan:
            logger.info("LLM 周计划生成成功：%d 条任务", len(plan))
            return plan

    except _json.JSONDecodeError as e:
        logger.error("LLM 周计划响应 JSON 解析失败: %s", e)
    except Exception as e:
        logger.error("LLM 周计划生成失败: %s", e)

    return None


# ──────────────────────────────────────────────
# 网页端通用学习计划生成
# ──────────────────────────────────────────────

_WEB_STUDY_PLAN_PROMPT = """\
你是一位学习计划助手。请根据用户想学习的内容，为用户生成接下来7天的学习计划。

## 用户想学的内容
{goal}

## 生成要求
1. 计划必须围绕用户输入，不要扩展到无关主题。
2. 每天安排 1-4 条任务，任务之间体现递进关系。
3. 每条任务要具体、可执行，topic 不要写成空泛口号。
4. 如果用户没有指定科目，请根据内容推断一个简短 subject。
5. 每天总时长建议控制在 {daily_minutes} 分钟左右。
6. 日期从 {today} 开始连续 7 天。
7. 不要使用“考研”“备考”等特定考试口径，除非用户输入明确提到。

## 输出格式
只返回 JSON 数组，不要包含任何其他文字：
[
  {{
    "day": 1,
    "date": "YYYY-MM-DD",
    "time": "09:00",
    "subject": "科目/领域",
    "topic": "具体学习任务",
    "estimated_minutes": 45,
    "notes": "10字以内学习重点"
  }}
]
"""


async def generate_web_study_plan(
    goal: str,
    today: str,
    daily_minutes: int = 120,
) -> list[dict] | None:
    """
    根据网页用户输入的学习目标生成 7 天通用学习计划。

    返回项字段与 weekly_plan 映射兼容：
    day/date/time/subject/topic/estimated_minutes/notes
    """
    prompt = _WEB_STUDY_PLAN_PROMPT.format(
        goal=goal,
        today=today,
        daily_minutes=daily_minutes,
    )

    try:
        text = await _generate_with_plan_model(
            prompt=prompt,
            temperature=0.25,
            response_format="json",
            max_tokens=6000,
        )
        if not text:
            return None

        plan = await _load_json_with_repair(text, expected="array")
        if isinstance(plan, list) and plan:
            logger.info("网页学习计划生成成功：%d 条任务", len(plan))
            return plan

    except json.JSONDecodeError as e:
        logger.error("网页学习计划 JSON 解析失败: %s", e)
    except Exception as e:
        logger.error("网页学习计划生成失败: %s", e)

    return None


# ──────────────────────────────────────────────
# 通用 AI 对话（后续扩展用）
# ──────────────────────────────────────────────


async def chat(prompt: str, system_instruction: str = "") -> str:
    """
    通用 AI 对话接口。

    参数：
        prompt: 用户输入
        system_instruction: 系统指令

    返回：
        AI 回复文本
    """
    return (
        await _generate_with_retry(
            prompt=prompt,
            temperature=0.7,
            system_instruction=system_instruction or None,
        )
        or ""
    )


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────


def _clean_json_text(text: str) -> str:
    """清理 LLM 返回的 JSON 文本（去除 markdown 代码块标记等）。"""
    text = text.replace("\r\n", "\n").strip()
    # 去除 ```json ... ``` 包裹
    if text.startswith("```"):
        # 去掉第一行（```json 或 ```）
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
    if text.rstrip().endswith("```"):
        text = text.rstrip()[:-3]
    return text.strip()


def _extract_json_candidate(text: str, expected: str) -> str:
    """从模型回复中截取最外层 JSON，兼容前后夹带说明文字。"""
    cleaned = _clean_json_text(text)
    opening, closing = ("[", "]") if expected == "array" else ("{", "}")
    start = cleaned.find(opening)
    end = cleaned.rfind(closing)
    if start >= 0 and end > start:
        return cleaned[start : end + 1]
    return cleaned


async def _load_json_with_repair(text: str, expected: str) -> Any:
    """解析模型 JSON；语法错误时使用同一个 CHAT_MODEL 修复一次。"""
    candidate = _extract_json_candidate(text, expected)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as first_error:
        logger.warning("AI JSON 首次解析失败，尝试修复: %s", first_error)

    expected_name = "JSON 数组" if expected == "array" else "JSON 对象"
    repair_prompt = (
        f"下面内容原本应该是合法的{expected_name}，但存在 JSON 语法错误。"
        "请只修复引号、逗号、转义或括号等语法问题，不改动字段和值，"
        "不要解释，不要使用 Markdown，只返回修复后的 JSON。\n\n"
        f"{candidate}"
    )
    repaired = await _generate_with_retry(
        prompt=repair_prompt,
        temperature=0,
        max_tokens=6000,
    )
    if not repaired:
        raise json.JSONDecodeError("AI 未返回修复结果", candidate, 0)
    return json.loads(_extract_json_candidate(repaired, expected))


# ──────────────────────────────────────────────
# P2: 水课识别（flash-lite 轻量判断）
# ──────────────────────────────────────────────

_WATER_COURSE_PROMPT = """\
你是一位大学生学习顾问。以下是一份课程表中的课程列表。
请判断哪些是"水课"（不需要认真听讲、可以自己复习考研内容的课）。

判断标准：
1. 选修课、通识课、体育课通常是水课
2. 专业核心课、实验课、设计课通常不是水课
3. 需要点名但内容简单的课也算水课
4. 如果不确定，标记为 "uncertain"

课程列表：
{courses_json}

请返回 JSON 数组，每项包含：
[
  {{
    "name": "课程名",
    "day_of_week": "周几",
    "time_slot": "时间段",
    "is_water": true/false,
    "confidence": "high/medium/low",
    "reason": "10字以内理由"
  }}
]

只返回 JSON 数组，不要包含其他文字。
"""


async def identify_water_courses(
    courses: list[dict],
) -> list[dict] | None:
    """
    使用轻量模型识别课程表中的水课。

    参数：
        courses: 课程列表，每项含 name, day_of_week, time_slot

    返回：
        标注了 is_water 的课程列表，失败返回 None
    """
    if not courses:
        return []

    prompt = _WATER_COURSE_PROMPT.format(
        courses_json=json.dumps(courses, ensure_ascii=False, indent=2),
    )

    try:
        text = await _generate_with_retry(
            prompt=prompt,
            temperature=0.1,
            response_mime_type="application/json",
            max_tokens=4000,
        )
        if not text:
            return None
        text = _clean_json_text(text)
        result = json.loads(text)

        if isinstance(result, list):
            logger.info(
                "水课识别完成：%d 门课，%d 门水课",
                len(result),
                sum(1 for c in result if c.get("is_water")),
            )
            return result

    except json.JSONDecodeError as e:
        logger.error("水课识别 JSON 解析失败: %s", e)
    except Exception as e:
        logger.error("水课识别失败: %s", e)

    return None


# ──────────────────────────────────────────────
# P2: 学期备考规划生成（级联入口）
# ──────────────────────────────────────────────

_SEMESTER_PLAN_PROMPT = """\
你是一位专业考研规划师。请根据以下信息，生成从现在到考试的完整学期备考规划。

## 用户信息
- 目标院校：{school}
- 报考专业：{major}
- 考试日期：{exam_date}
- 距考试天数：{days_left} 天（约 {months_left} 个月）
- 今天日期：{today}

## 考试科目
{subjects_json}

{water_courses_section}

## 规划要求
1. 按月划分阶段（基础→强化→冲刺→模考）
2. 每月为每个科目设定1-2个具体目标
3. 前期（>150天）：每月每科只深入1个方向
4. 中期（60-150天）：加大强度，每科2-3个方向
5. 后期（<60天）：全面冲刺，侧重薄弱点和真题
6. 英语单词由碎片时间推送，不占整块学习时间
7. 政治考前2个月开始即可
{timetable_section}

## 输出格式
返回 JSON 对象：
{{
  "phases": [
    {{
      "name": "基础阶段",
      "months": ["2026-04", "2026-05"],
      "focus": "打牢基础，不赶进度",
      "goals": [
        {{
          "month": "2026-04",
          "subject": "科目名",
          "title": "月度目标",
          "detail": "具体内容",
          "priority": 1
        }}
      ]
    }}
  ],
  "summary": "整体规划概述（100字以内）"
}}

只返回 JSON 对象，不要包含其他文字。
"""


# ──────────────────────────────────────────────
# P1.5: 置信度自评 + 核心知识清单（从脚本移植）
# ──────────────────────────────────────────────


async def check_subject_confidence(
    subjects: list[str],
    school: str,
) -> dict[str, str]:
    """
    让计划生成模型自评能否准确给出每个科目的知识点。

    返回：{"数据结构": "high", "某冷门": "low"}
    high = 统考/常见专业课，把握充足
    low = 冷门/高度自命题，极不确定
    """
    subj_list = "\n".join(f"  - {s}" for s in subjects)
    prompt = (
        f"评估你能否准确列出以下科目的考研核心知识点？目标院校：{school}\n"
        f"科目列表：\n{subj_list}\n\n"
        "high=统考/常见专业课把握充足；low=冷门/高度自命题极不确定。\n"
        '只返回 JSON 对象，示例：{"数据结构": "high", "某冷门": "low"}'
    )

    try:
        text = await _generate_with_plan_model(
            prompt=prompt,
            temperature=0.1,
            system_instruction="你是考研专家。诚实评估，不加任何废话。",
            response_format="json",
        )
        if text:
            text = _clean_json_text(text)
            return json.loads(text)
    except Exception as e:
        logger.error("置信度自评失败: %s", e)

    # 失败时默认全部 high
    return {s: "high" for s in subjects}


async def generate_knowledge_checklist(
    subjects: list[str],
    school: str,
    syllabi: dict[str, str] | None = None,
) -> list[dict] | None:
    """
    生成核心知识清单（科目×知识点×重要度×建议月份）。

    参数：
        subjects: 科目列表（如 ["数据结构", "操作系统"]）
        school: 目标院校
        syllabi: 低置信科目的大纲文本 {科目名: 大纲文本}

    返回：
        知识点清单列表
    """
    syllabi_section = ""
    if syllabi:
        parts = [f"  ── {s} ──\n{t}" for s, t in syllabi.items()]
        syllabi_section = (
            "\n【务必严格依据以下大纲内容提取考点，禁止幻觉生造】\n"
            + "\n\n".join(parts)
            + "\n\n"
        )

    subjects_str = "、".join(subjects)
    prompt = (
        f"输出 {subjects_str}、英语考研核心考点。\n"
        f"目标院校：{school}\n"
        f"{syllabi_section}"
        "JSON 数组字段：序号, 科目, 知识点或技能, 重要程度_高中低, "
        "掌握度_1到5, 建议学习月份, 备注。掌握度留空。只返回JSON数组。"
    )

    try:
        text = await _generate_with_plan_model(
            prompt=prompt,
            temperature=0.2,
            system_instruction=(
                f"你是专业考研规划师。目标院校：{school}。\n"
                "输出 JSON 数组，禁止多余文字。"
            ),
        )
        if not text:
            return None

        text = _clean_json_text(text)
        data = json.loads(text)
        if isinstance(data, list) and data:
            logger.info("知识清单生成成功：%d 个知识点", len(data))
            return data

    except json.JSONDecodeError as e:
        logger.error("知识清单 JSON 解析失败: %s", e)
    except Exception as e:
        logger.error("知识清单生成失败: %s", e)

    return None


async def generate_semester_plan(
    school: str,
    major: str,
    exam_date: str,
    days_left: int,
    subjects: list[dict],
    water_courses: list[dict] | None = None,
    timetable_desc: str | None = None,
    today: str | None = None,
) -> dict | None:
    """
    生成完整学期备考规划（级联生成的第一步）。

    整合课表信息和水课标记，调用 LLM 生成分阶段月度目标。
    返回的规划可存入 study_plan 表，月目标可写入 monthly_goals 表。

    参数：
        school: 目标院校
        major: 报考专业
        exam_date: 考试日期字符串
        days_left: 距考试天数
        subjects: 科目列表（含知识点）
        water_courses: 水课列表（可选）
        timetable_desc: 课表空闲时间描述（可选）
        today: 今天日期（默认自动获取）

    返回：
        包含 phases 和 summary 的规划字典，失败返回 None
    """
    from datetime import date as _date

    if today is None:
        today = _date.today().isoformat()

    months_left = max(1, round(days_left / 30, 1))

    # 构建科目摘要
    subjects_summary = []
    for s in subjects:
        kps = s.get("knowledge_points", [])
        subjects_summary.append(
            {
                "name": s.get("name", ""),
                "category": s.get("category", ""),
                "knowledge_points": kps[:20],
            }
        )

    # 构建水课信息
    water_section = ""
    if water_courses:
        water_names = [c["name"] for c in water_courses if c.get("is_water")]
        if water_names:
            water_section = (
                "\n## 水课时段（可用于碎片复习）\n"
                f"以下课程为水课，可在上课时同步复习：{'、'.join(water_names)}\n"
            )

    # 构建课表信息
    timetable_section = ""
    if timetable_desc:
        timetable_section = (
            f"\n8. 课表空闲时段：\n{timetable_desc}\n请基于空闲时段合理安排。\n"
        )

    prompt = _SEMESTER_PLAN_PROMPT.format(
        school=school,
        major=major,
        exam_date=exam_date,
        days_left=days_left,
        months_left=months_left,
        today=today,
        subjects_json=json.dumps(subjects_summary, ensure_ascii=False, indent=2),
        water_courses_section=water_section,
        timetable_section=timetable_section,
    )

    try:
        text = await _generate_with_plan_model(
            prompt=prompt,
            temperature=0.2,
            response_format="json",
        )
        if not text:
            return None

        text = _clean_json_text(text)
        plan = json.loads(text)

        if isinstance(plan, dict) and plan.get("phases"):
            total_goals = sum(len(p.get("goals", [])) for p in plan["phases"])
            logger.info(
                "学期规划生成成功：%d 个阶段，%d 个月目标",
                len(plan["phases"]),
                total_goals,
            )
            return plan

    except json.JSONDecodeError as e:
        logger.error("学期规划 JSON 解析失败: %s", e)
    except Exception as e:
        logger.error("学期规划生成失败: %s", e)

    return None


# ──────────────────────────────────────────────
# P2: 月度日程生成（级联：学期计划 → 月目标 → 28天日程）
# ──────────────────────────────────────────────

_MONTHLY_SCHEDULE_PROMPT = """\
你是一位考研学习规划专家。请根据**月度目标**，为用户安排本月4周（28天）的详细学习日程。

⚠️ 核心原则：
1. 本月目标中的每个知识点在28天中**反复出现、螺旋上升**
2. 每周内同一知识点每天都要触及，逐步深入
3. 4周之间体现递进关系（第1周入门→第4周强化训练）

## 用户信息
- 目标院校：{school}
- 报考专业：{major}
- 距考试天数：{days_left} 天
- 当前阶段：{phase}（{phase_focus}）
- 今天日期：{today}
- 本月范围：{month_start} 至 {month_end}

## 本月学习目标（严格限定，不得添加其他知识点）
{current_goals_json}

{suspended_section}
{timetable_section}
## 单词策略
英语单词由 Bot 在碎片时间（课间、走路、排队、睡前）自动推送，
不要在日程中安排整块的背单词时段。
英语的整块时间只安排阅读精读、长难句分析、翻译或写作。

## 排课规则
1. 严禁添加月目标以外的知识点
2. 暂缓科目绝对不要出现
3. 有课表约束时，大学上课时段绝对不安排考研复习
4. 同一知识点每天都要安排，逐步深入
5. 每天总学习时间 300-480 分钟（不含大学课程时间）
6. 每天涉及月目标中的所有活跃科目
7. 4 周体现递进：第1周理解概念 → 第2周做基础题 → 第3周做真题 → 第4周查漏补缺

## 输出格式
返回 JSON 数组（28天，每天多条）：
[
  {{
    "day": 1,
    "week": 1,
    "date": "YYYY-MM-DD",
    "time": "09:00",
    "subject": "科目名称",
    "topic": "知识点名称",
    "estimated_minutes": 60,
    "notes": "当日学习重点（体现递进）"
  }}
]

只返回 JSON 数组。
"""


def _extract_active_subjects(month_goals: list[dict]) -> tuple[list[str], list[str]]:
    """
    从月目标中提取活跃科目和暂缓科目。
    复用 generate_schedule_csv.py 的 _extract_current_subjects 逻辑。

    返回：(活跃科目描述列表, 暂缓科目名列表)
    """
    active = []
    suspended = []
    for g in month_goals:
        title = g.get("goal_title", "")
        detail = g.get("goal_detail", "")
        subject = g.get("subject_name", "")
        if "暂缓" in title or "暂缓" in detail:
            suspended.append(subject)
        else:
            desc = f"{subject}：{title}"
            if detail:
                desc += f"（{detail[:30]}）"
            active.append(desc)
    return active, suspended


def _get_phase_info(days_left: int) -> tuple[str, str]:
    """根据剩余天数返回阶段名称和重点说明"""
    if days_left <= 14:
        return "最终冲刺期", "只复习薄弱点和高频考点，不引入新内容"
    elif days_left <= 60:
        return "冲刺期", "侧重真题训练和模拟考试，查漏补缺"
    elif days_left <= 120:
        return "强化期", "以做题为主，巩固知识框架，攻克难点"
    else:
        return "基础期", "全面覆盖知识点，建立学科框架，打牢基础"


async def generate_monthly_schedule(
    school: str,
    major: str,
    days_left: int,
    current_month_goals: list[dict],
    today: str,
    semester_plan_phase: str | None = None,
    timetable_prompt: str = "",
) -> list[dict] | None:
    """
    基于当前月目标生成 28 天月度日程（一次 LLM 调用）。

    参数：
        current_month_goals: monthly_goals 表记录列表
        semester_plan_phase: 学期计划中当前阶段描述（可选）
        其他参数同 generate_ordered_weekly_plan

    返回：
        28天日程列表，或 None
    """
    from datetime import date as dt_date

    # 提取活跃/暂缓科目
    active, suspended = _extract_active_subjects(current_month_goals)

    # 备考阶段
    phase, phase_focus = _get_phase_info(days_left)
    if semester_plan_phase:
        phase_focus = semester_plan_phase  # 用学期计划的阶段说明覆盖

    # 计算月份范围
    today_dt = dt_date.fromisoformat(today)
    month_start = today_dt.replace(day=1).isoformat()
    import calendar

    last_day = calendar.monthrange(today_dt.year, today_dt.month)[1]
    month_end = today_dt.replace(day=last_day).isoformat()

    # 构建目标 JSON
    goals_for_prompt = []
    for g in current_month_goals:
        if g.get("subject_name", "") not in suspended:
            goals_for_prompt.append(
                {
                    "subject": g.get("subject_name", ""),
                    "topics": [g.get("goal_title", "")],
                    "detail": g.get("goal_detail", ""),
                    "priority": {1: "high", 2: "medium", 3: "low"}.get(
                        g.get("priority", 2), "medium"
                    ),
                }
            )

    # 暂缓科目段落
    suspended_section = ""
    if suspended:
        suspended_section = f"## 暂缓科目（绝对不要安排！）\n{'、'.join(suspended)}\n"

    prompt = _MONTHLY_SCHEDULE_PROMPT.format(
        school=school,
        major=major,
        days_left=days_left,
        phase=phase,
        phase_focus=phase_focus,
        today=today,
        month_start=month_start,
        month_end=month_end,
        current_goals_json=json.dumps(goals_for_prompt, ensure_ascii=False, indent=2),
        suspended_section=suspended_section,
        timetable_section=timetable_prompt,
    )

    try:
        text = await _generate_with_plan_model(
            prompt=prompt,
            temperature=0.2,
            response_format="json",
            max_tokens=16000,
        )
        if not text:
            logger.error("月度日程: 计划生成模型返回空响应")
            return None

        # 调试：保存原始响应
        logger.info("月度日程原始响应长度: %d 字符", len(text))
        try:
            with open("data/debug_monthly_raw.txt", "w", encoding="utf-8") as f:
                f.write(text)
            logger.info("原始响应已保存到 data/debug_monthly_raw.txt")
        except Exception:
            pass

        text = _clean_json_text(text)
        plan = json.loads(text)
        if isinstance(plan, list) and plan:
            logger.info(
                "月度日程生成成功：%d 条任务（覆盖 %d 天）",
                len(plan),
                len(set(e.get("day", 0) for e in plan)),
            )
            return plan
        else:
            logger.error("月度日程: 解析结果不是非空列表, type=%s", type(plan).__name__)

    except json.JSONDecodeError as e:
        logger.error("月度日程 JSON 解析失败: %s", e)
        logger.error("清理后文本前100字: %s", text[:100] if text else "(empty)")
        logger.error("清理后文本后100字: %s", text[-100:] if text else "(empty)")
    except Exception as e:
        logger.error("月度日程生成失败: %s", e)

    return None
