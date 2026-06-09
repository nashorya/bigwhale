"""
CSV 导出模块 — 生成 GBK 编码的学习日程表。

生成的文件格式与参考文件保持一致：
  #, 月/日, 星期, [科目1]完成情况, ..., 单词打卡(?/?), 备注/完成度

支持四种视图：
  week.csv     — 当前周（7天）
  month.csv    — 整个备考周期（到考试日）
  list.csv     — 知识点清单（整个备考周期，含计划内容）
  clock_in.csv — 每日打卡记录（含完成情况）
"""

from __future__ import annotations

import csv
import io
import os
from datetime import date, timedelta
from typing import Any

# 星期中文映射
_WEEKDAY_ZH = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 是否包含英语单词打卡列（科目名含"英语"时自动添加）
_WORD_CHECKIN_SUBJECT = "英语"


def _get_output_dir() -> str:
    """获取输出目录路径（项目根目录）。"""
    return os.environ.get("CSV_OUTPUT_DIR", ".")


def _detect_has_english(subject_names: list[str]) -> bool:
    """判断是否有英语科目（控制是否显示单词打卡列）。"""
    return any("英语" in s or "英一" in s or "英二" in s for s in subject_names)


def _format_date(d: date) -> str:
    """格式化日期为 月/日 格式（如 3/11）。"""
    return f"{d.month}/{d.day}"


def _build_header_row(subject_names: list[str], has_english: bool) -> list[str]:
    """构建表头行。"""
    cols = ["#", "月/日", "星期"]
    for name in subject_names:
        cols.append(f"{name}\n完成情况")
    if has_english:
        cols.append("单词打卡\n(?/?)")
    cols.append("备注/完成度")
    return cols


def _build_data_row(
    idx: int,
    d: date,
    subject_names: list[str],
    has_english: bool,
    day_plan: dict[str, str],  # {subject_name: 完成情况文本}
    word_checkin: str = "",
    note: str = "",
) -> list[str]:
    """构建单行数据。"""
    weekday_zh = _WEEKDAY_ZH[d.weekday()]
    row = [str(idx), _format_date(d), weekday_zh]
    for name in subject_names:
        row.append(day_plan.get(name, ""))
    if has_english:
        row.append(word_checkin)
    row.append(note)
    return row


def _write_csv_gbk(rows: list[list[str]], title: str = "") -> bytes:
    """
    将二维列表写为 GBK 编码的 CSV 字节流。
    如果提供标题，在第一行写标题。
    """
    buf = io.StringIO()
    writer = csv.writer(buf)
    if title:
        writer.writerow([title])
    for row in rows:
        writer.writerow(row)
    return buf.getvalue().encode("gbk", errors="replace")


def generate_week_csv(
    week_start: date,
    subject_names: list[str],
    weekly_plan_items: list[dict],
    exam_date: date | None = None,
    title: str = "",
) -> bytes:
    """
    生成当周日程 CSV（7天）。

    参数：
        week_start: 本周开始日期
        subject_names: 所有科目名列表（顺序决定列顺序）
        weekly_plan_items: DB 中的 weekly_plan 行（list[dict]）
        exam_date: 考试日期（用于标题）
        title: 自定义表头文字
    """
    has_english = _detect_has_english(subject_names)
    exam_str = f"，共?天" if not exam_date else f"，共{(exam_date - date.today()).days}天"
    auto_title = title or f"每周典型备考日程（至{exam_date or '?'}{exam_str}）"

    # 按 plan_date 聚合每天各科目内容
    day_map: dict[str, dict[str, str]] = {}
    for item in weekly_plan_items:
        pd = item["plan_date"]
        sn = item["subject_name"]
        topic = item["topic_name"]
        day_map.setdefault(pd, {})
        prev = day_map[pd].get(sn, "")
        day_map[pd][sn] = (prev + "\n" + topic).strip() if prev else topic

    header = _build_header_row(subject_names, has_english)
    rows: list[list[str]] = [header]
    for i in range(7):
        d = week_start + timedelta(days=i)
        date_str = d.isoformat()
        day_plan = day_map.get(date_str, {})
        row = _build_data_row(i + 1, d, subject_names, has_english, day_plan)
        rows.append(row)

    return _write_csv_gbk(rows, auto_title)


def generate_month_csv(
    start_date: date,
    exam_date: date,
    subject_names: list[str],
    weekly_plan_items: list[dict],
    title: str = "",
) -> bytes:
    """
    生成整个备考周期日程 CSV（从 start_date 到 exam_date）。
    """
    has_english = _detect_has_english(subject_names)
    total_days = (exam_date - start_date).days + 1
    auto_title = title or f"备考全程日程（{_format_date(start_date)}—{_format_date(exam_date)}，共{total_days}天）"

    day_map: dict[str, dict[str, str]] = {}
    for item in weekly_plan_items:
        pd = item["plan_date"]
        sn = item["subject_name"]
        topic = item["topic_name"]
        day_map.setdefault(pd, {})
        prev = day_map[pd].get(sn, "")
        day_map[pd][sn] = (prev + "\n" + topic).strip() if prev else topic

    header = _build_header_row(subject_names, has_english)
    rows: list[list[str]] = [header]
    num_days = (exam_date - start_date).days + 1
    for i in range(num_days):
        d = start_date + timedelta(days=i)
        date_str = d.isoformat()
        day_plan = day_map.get(date_str, {})
        row = _build_data_row(i + 1, d, subject_names, has_english, day_plan)
        rows.append(row)

    return _write_csv_gbk(rows, auto_title)


def generate_list_csv(
    start_date: date,
    exam_date: date,
    subject_names: list[str],
    weekly_plan_items: list[dict],
    title: str = "",
) -> bytes:
    """
    生成知识点清单 CSV，与 month.csv 格式相同，
    区别是标题不同，用于展示"将要学习的知识点列表"。
    """
    has_english = _detect_has_english(subject_names)
    auto_title = title or f"重点知识点清单（{_format_date(start_date)}—{_format_date(exam_date)}）"
    total_days = (exam_date - start_date).days + 1

    day_map: dict[str, dict[str, str]] = {}
    for item in weekly_plan_items:
        pd = item["plan_date"]
        sn = item["subject_name"]
        topic = item["topic_name"]
        day_map.setdefault(pd, {})
        prev = day_map[pd].get(sn, "")
        day_map[pd][sn] = (prev + "\n" + topic).strip() if prev else topic

    header = _build_header_row(subject_names, has_english)
    rows: list[list[str]] = [header]
    for i in range(total_days):
        d = start_date + timedelta(days=i)
        date_str = d.isoformat()
        day_plan = day_map.get(date_str, {})
        row = _build_data_row(i + 1, d, subject_names, has_english, day_plan)
        rows.append(row)

    return _write_csv_gbk(rows, auto_title)


def generate_clock_in_csv(
    start_date: date,
    exam_date: date,
    subject_names: list[str],
    checkin_data: list[dict],  # [{plan_date, subject_name, topic_name, status, word_count}]
    title: str = "",
) -> bytes:
    """
    生成每日打卡记录 CSV。

    checkin_data 为实际打卡记录（来自 weekly_plan 表 status='done'）。
    """
    has_english = _detect_has_english(subject_names)
    total_days = (exam_date - start_date).days + 1
    auto_title = title or f"每日打卡记录（{_format_date(start_date)}—{_format_date(exam_date)}，共{total_days}天）"

    # 聚合打卡 — only done items
    day_map: dict[str, dict[str, str]] = {}
    word_map: dict[str, str] = {}
    for item in checkin_data:
        if item.get("status") != "done":
            continue
        pd = item["plan_date"]
        sn = item.get("subject_name", "")
        topic = item.get("topic_name", "")
        day_map.setdefault(pd, {})
        prev = day_map[pd].get(sn, "")
        day_map[pd][sn] = (prev + "\n" + topic).strip() if prev else topic
        # 单词打卡格式：已打/总数，简单用✓标记
        if "英语" in sn or "英一" in sn or "英二" in sn:
            wc = item.get("word_count", "")
            if wc:
                word_map[pd] = str(wc)

    header = _build_header_row(subject_names, has_english)
    rows: list[list[str]] = [header]
    for i in range(total_days):
        d = start_date + timedelta(days=i)
        date_str = d.isoformat()
        day_plan = day_map.get(date_str, {})
        word_str = word_map.get(date_str, "")
        note = "✅" if day_plan else ""
        row = _build_data_row(i + 1, d, subject_names, has_english, day_plan, word_str, note)
        rows.append(row)

    return _write_csv_gbk(rows, auto_title)


def save_csv_files(
    output_dir: str,
    subject_names: list[str],
    start_date: date,
    exam_date: date,
    weekly_plan_items: list[dict],
    checkin_data: list[dict] | None = None,
) -> dict[str, str]:
    """
    一次性生成并保存所有 4 个 CSV 文件。

    返回：{filename: 绝对路径} 的字典
    """
    os.makedirs(output_dir, exist_ok=True)
    week_start = date.today()

    files: dict[str, bytes] = {
        "week.csv": generate_week_csv(week_start, subject_names, weekly_plan_items, exam_date),
        "month.csv": generate_month_csv(start_date, exam_date, subject_names, weekly_plan_items),
        "list.csv": generate_list_csv(start_date, exam_date, subject_names, weekly_plan_items),
        "clock_in.csv": generate_clock_in_csv(
            start_date, exam_date, subject_names,
            checkin_data or weekly_plan_items,
        ),
    }

    saved: dict[str, str] = {}
    for fname, content in files.items():
        path = os.path.join(output_dir, fname)
        with open(path, "wb") as f:
            f.write(content)
        saved[fname] = os.path.abspath(path)

    return saved
