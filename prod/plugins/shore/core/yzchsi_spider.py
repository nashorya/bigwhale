"""
研招网爬虫模块 — 通过 yz.chsi.com.cn 的 JSON API 获取考研科目。

参考 https://github.com/freecho/yzw 项目的 API 逆向工程。
API 链路：
  1. POST /zsml/rs/dws.do    → 搜索院校（获取 dwdm）
  2. POST /zsml/rs/dwzys.do  → 院校专业列表（获取 zydm）
  3. POST /zsml/rs/yjfxs.do  → 专业详情（考试科目）

注意: 第 2、3 步需要学信网登录 session。
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import aiohttp
from bs4 import BeautifulSoup

logger = logging.getLogger("shore.yzchsi_spider")

# ──────────────────────────────────────────────
# 常量
# ──────────────────────────────────────────────

BASE_URL = "https://yz.chsi.com.cn"
LOGIN_URL = (
    "https://account.chsi.com.cn/passport/login"
    "?entrytype=yzgr"
    "&service=https%3A%2F%2Fyz.chsi.com.cn%2Fj_spring_cas_security_check"
)

# API 端点
SCHOOL_SEARCH_URL = f"{BASE_URL}/zsml/rs/dws.do"
MAJOR_SEARCH_URL = f"{BASE_URL}/zsml/rs/dwzys.do"
MAJOR_DETAIL_URL = f"{BASE_URL}/zsml/rs/yjfxs.do"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Referer": f"{BASE_URL}/zsml/",
    "Origin": "https://account.chsi.com.cn",
}

TIMEOUT = aiohttp.ClientTimeout(total=20)


# ──────────────────────────────────────────────
# 公开接口
# ──────────────────────────────────────────────


async def fetch_exam_subjects(
    school_name: str, major_name: str
) -> dict[str, Any] | None:
    """
    从研招网获取指定院校+专业的考试科目（需要学信网登录）。

    参数：
        school_name: 院校名称（如"北京大学"）
        major_name: 专业名称（如"计算机科学与技术"）

    返回：
        包含科目信息的字典，失败时返回 None。
    """
    username = os.environ.get("CHSI_USERNAME", "")
    password = os.environ.get("CHSI_PASSWORD", "")

    if not username or not password:
        logger.info("未配置 CHSI_USERNAME/CHSI_PASSWORD，跳过研招网爬取")
        return None

    try:
        session = await _login(username, password)
        if not session:
            return None

        async with session:
            # 第一步：搜索院校
            school_code = await _search_school(session, school_name)
            if not school_code:
                logger.warning("未找到院校: %s", school_name)
                return None
            logger.info("找到院校: %s -> %s", school_name, school_code)

            # 第二步：获取专业列表，匹配目标专业
            major_info = await _search_major(
                session, school_code, school_name, major_name
            )
            if not major_info:
                logger.warning("未找到专业: %s %s", school_name, major_name)
                return None
            logger.info(
                "找到专业: %s %s (zydm=%s)",
                school_name, major_info.get("zymc"), major_info.get("zydm"),
            )

            # 第三步：获取专业详情（考试科目）
            subjects = await _fetch_detail(
                session, school_code, major_info
            )
            if not subjects:
                logger.warning("未获取到科目: %s %s", school_name, major_name)
                return None

            return {
                "school": school_name,
                "major": major_info.get("zymc", major_name),
                "subjects": subjects,
                "directions": [],
                "notes": "数据来自研招网",
            }

    except Exception as e:
        logger.error("研招网爬取异常: %s", e)
        return None


# ──────────────────────────────────────────────
# 登录
# ──────────────────────────────────────────────


async def _login(
    username: str, password: str
) -> aiohttp.ClientSession | None:
    """
    登录学信网，返回带 cookie 的 session。

    CAS 登录流程：
    1. GET 登录页 → 提取 lt、execution 隐藏字段
    2. POST 登录 → 自动重定向完成认证
    """
    session = aiohttp.ClientSession(headers=HEADERS, timeout=TIMEOUT)
    try:
        # 获取登录页，提取 CSRF token
        async with session.get(LOGIN_URL) as resp:
            if resp.status != 200:
                logger.error("获取登录页失败: HTTP %d", resp.status)
                await session.close()
                return None
            html = await resp.text()

        soup = BeautifulSoup(html, "html.parser")
        lt_input = soup.find("input", {"name": "lt"})
        exec_input = soup.find("input", {"name": "execution"})
        lt = lt_input["value"] if lt_input else ""
        execution = exec_input["value"] if exec_input else ""

        # 提交登录
        form_data = {
            "username": username,
            "password": password,
            "lt": lt,
            "execution": execution,
            "_eventId": "submit",
        }
        async with session.post(LOGIN_URL, data=form_data) as resp:
            if resp.status == 200:
                logger.info("学信网登录完成")
            else:
                logger.error("学信网登录失败: HTTP %d", resp.status)
                await session.close()
                return None

        # 访问研招网首页激活 session
        await session.get(f"{BASE_URL}/zsml/")
        await asyncio.sleep(0.5)

        return session

    except Exception as e:
        logger.error("学信网登录异常: %s", e)
        await session.close()
        return None


# ──────────────────────────────────────────────
# API 调用
# ──────────────────────────────────────────────


async def _search_school(
    session: aiohttp.ClientSession, school_name: str
) -> str | None:
    """搜索院校，返回院校代码 dwdm。"""
    data = {
        "dwmc": school_name,
        "curPage": "1",
        "pageSize": "10",
    }
    result = await _api_call(session, SCHOOL_SEARCH_URL, data)
    if not result:
        return None

    school_list = result.get("msg", {}).get("list", [])
    if not school_list:
        return None

    # 优先精确匹配
    for item in school_list:
        if item.get("dwmc", "").strip() == school_name:
            return item.get("dwdm", "")

    # 回退到第一个
    return school_list[0].get("dwdm", "")


async def _search_major(
    session: aiohttp.ClientSession,
    school_code: str,
    school_name: str,
    major_name: str,
) -> dict | None:
    """搜索院校的专业，返回匹配的专业信息字典。"""
    # 先按专业名称搜索
    data = {
        "dwdm": school_code,
        "dwmc": school_name,
        "zymc": major_name,
        "curPage": "1",
        "pageSize": "10",
    }
    result = await _api_call(session, MAJOR_SEARCH_URL, data)
    if not result:
        return None

    msg = result.get("msg", {})
    if isinstance(msg, str):
        # 可能返回 "请登录"
        logger.warning("专业搜索返回: %s", msg)
        return None

    major_list = msg.get("list", [])
    if not major_list:
        # 尝试不带专业名称搜索全部
        data["zymc"] = ""
        result = await _api_call(session, MAJOR_SEARCH_URL, data)
        if result:
            msg = result.get("msg", {})
            if isinstance(msg, dict):
                major_list = msg.get("list", [])

    if not major_list:
        return None

    # 精确匹配
    for item in major_list:
        if item.get("zymc", "").strip() == major_name:
            return item

    # 模糊匹配
    for item in major_list:
        item_name = item.get("zymc", "")
        if major_name in item_name or item_name in major_name:
            return item

    # 回退到第一个
    return major_list[0] if major_list else None


async def _fetch_detail(
    session: aiohttp.ClientSession,
    school_code: str,
    major_info: dict,
) -> list[dict[str, str]]:
    """获取专业详情（研究方向和考试科目）。"""
    data = {
        "zydm": major_info.get("zydm", ""),
        "zymc": major_info.get("zymc", ""),
        "dwdm": school_code,
        "xxfs": "",
        "dwlxs": "",
        "tydxs": "",
        "jsggjh": "",
        "start": "0",
        "pageSize": "20",
        "totalCount": "0",
    }
    result = await _api_call(session, MAJOR_DETAIL_URL, data)
    if not result:
        return []

    msg = result.get("msg", {})
    if isinstance(msg, str):
        logger.warning("详情查询返回: %s", msg)
        return []

    detail_list = msg.get("list", [])
    if not detail_list:
        return []

    # 从第一条详情记录中提取考试科目
    # 数据结构：detail.kskmz = [{"km1Vo": {"kskmdm": "101", "kskmmc": "思想政治理论"}, ...}]
    detail = detail_list[0]
    kskmz = detail.get("kskmz", [])

    subjects = []
    if kskmz and isinstance(kskmz, list) and len(kskmz) > 0:
        km_group = kskmz[0]  # 取第一组科目组合
        for i in range(1, 5):
            km_key = f"km{i}Vo"
            km_vo = km_group.get(km_key, {})
            if km_vo and isinstance(km_vo, dict):
                code = km_vo.get("kskmdm", "")
                name = km_vo.get("kskmmc", "")
                if code and name:
                    subjects.append({
                        "name": name,
                        "code": code,
                        "category": _categorize_subject(code),
                    })

    return subjects


# ──────────────────────────────────────────────
# 通用 API 调用
# ──────────────────────────────────────────────


async def _api_call(
    session: aiohttp.ClientSession,
    url: str,
    data: dict,
    retry: int = 0,
) -> dict | None:
    """通用 API 调用，带重试和速率限制。"""
    if retry > 3:
        logger.error("API 重试次数过多: %s", url)
        return None

    try:
        async with session.post(url, data=data) as resp:
            if resp.status != 200:
                logger.error("API 返回 HTTP %d: %s", resp.status, url)
                return None
            result = await resp.json(content_type=None)

        # 检查是否需要登录
        if not result.get("flag"):
            msg = result.get("msg", "")
            if msg == "请登录":
                logger.warning("API 返回 '请登录'，session 可能已过期")
                return None
            if msg == "访问太频繁":
                wait = (retry + 1) * 2
                logger.warning("访问太频繁，%d 秒后重试", wait)
                await asyncio.sleep(wait)
                return await _api_call(session, url, data, retry + 1)

        return result

    except Exception as e:
        logger.error("API 调用异常 (%s): %s", url, e)
        if retry < 2:
            await asyncio.sleep(2)
            return await _api_call(session, url, data, retry + 1)
        return None


# ──────────────────────────────────────────────
# 数据解析
# ──────────────────────────────────────────────


def _extract_subjects_from_detail(detail: dict) -> list[dict[str, str]]:
    """从 yjfxs.do 返回的详情中提取考试科目。"""
    subjects = []

    # yzw 项目中详情记录的典型字段结构
    # 遍历所有字段，查找科目代码和名称
    for key, value in detail.items():
        if not value or not isinstance(value, str):
            continue
        # 查找格式如 "101 思想政治理论" 或 "①101思想政治理论"
        found = _parse_subject_text(str(value))
        subjects.extend(found)

    # 如果字段中没找到，尝试从特定字段名提取
    # 常见字段名: kskm1, kskm2, kskm3, kskm4, kskmmc, kskmdm 等
    for i in range(1, 5):
        code_key = f"kskmdm{i}"
        name_key = f"kskmmc{i}"
        code = detail.get(code_key, "")
        name = detail.get(name_key, "")
        if code and name:
            category = _categorize_subject(str(code))
            subjects.append({
                "name": str(name),
                "code": str(code),
                "category": category,
            })

    # 去重
    seen = set()
    unique = []
    for s in subjects:
        if s["code"] not in seen:
            seen.add(s["code"])
            unique.append(s)

    return unique


def _parse_subject_text(text: str) -> list[dict[str, str]]:
    """从文本中提取考试科目。"""
    subjects = []

    # 按序号字符或换行分割
    segments = re.split(r"[①②③④⑤⑥⑦⑧⑨⑩\n、,，;；]", text)

    code_pattern = re.compile(r"(\d{3})\s*([^\d\n①②③④⑤⑥⑦⑧⑨⑩]{2,20})")

    for seg in segments:
        seg = seg.strip()
        if not seg:
            continue
        m = code_pattern.search(seg)
        if m:
            code = m.group(1)
            name = m.group(2).strip()
            category = _categorize_subject(code)
            subjects.append({
                "name": name,
                "code": code,
                "category": category,
            })

    return subjects


def _categorize_subject(code: str) -> str:
    """根据科目代码判断公共课或专业课。"""
    if not code:
        return "专业课"
    first_digit = code[0] if isinstance(code, str) else str(code)[0]
    if first_digit in ("1", "2", "3"):
        return "公共课"
    return "专业课"
