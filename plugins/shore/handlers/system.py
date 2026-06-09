"""
系统指令处理器（好友申请、初始化向导等）。
薄胶水层：解析输入 → 调用 core/ → 渲染结果。
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime

from nonebot import on_command, on_request
import nonebot.exception
from nonebot.adapters.onebot.v11 import (
    Bot,
    FriendRequestEvent,
    PrivateMessageEvent,
)
from nonebot.matcher import Matcher
from nonebot.params import ArgPlainText, CommandArg
from nonebot.adapters.onebot.v11 import Message

from ..core.security import (
    get_or_create_uid,
    sanitize_input,
    generate_invite_code,
)
from ..core.user_db import get_db_conn, UserDB
from ..core import points_service
from ..core import persona_engine
from ..core import ai_service
from .admin import check_banned


# ──────────────────────────────────────────────
# 1. 好友申请自动通过
# ──────────────────────────────────────────────

friend_request = on_request(priority=1, block=True)


@friend_request.handle()
async def handle_friend_request(bot: Bot, event: FriendRequestEvent):
    """自动通过所有好友申请，通过后发送欢迎消息"""
    # 自动通过
    await bot.set_friend_add_request(flag=event.flag, approve=True)

    # 等待好友关系生效
    await asyncio.sleep(1)

    # 发送欢迎消息并注册用户
    await _send_welcome(bot, str(event.user_id))


async def _send_welcome(bot: Bot, qq: str) -> None:
    """
    发送欢迎消息并在数据库中注册新用户。
    """
    uid = get_or_create_uid(qq)
    invite_code = generate_invite_code(uid)

    async with get_db_conn() as conn:
        # 检查是否已注册
        cursor = await conn.execute(
            "SELECT 1 FROM users WHERE user_id = ?", (uid,)
        )
        if await cursor.fetchone():
            # 已注册用户，发送简短提示
            await bot.send_private_msg(
                user_id=int(qq),
                message="欢迎回来！发送 [#帮助] 查看可用指令。",
            )
            return

        # 写入 users 表
        await conn.execute(
            """INSERT INTO users (user_id, invite_code)
               VALUES (?, ?)""",
            (uid, invite_code),
        )
        # 创建 persona_config
        await conn.execute(
            "INSERT INTO persona_config (user_id) VALUES (?)",
            (uid,),
        )
        # 创建 points_account
        await conn.execute(
            "INSERT INTO points_account (user_id) VALUES (?)",
            (uid,),
        )
        await conn.commit()

        # 发放注册积分
        db = UserDB(uid, conn)
        await points_service.register_bonus(uid, db)

    # 发送欢迎消息（简洁引导，先完成院校专业设置）
    welcome = (
        "【欢迎使用上岸考研陪伴机器人】\n"
        "\n"
        "✨ 用了上岸，你就上岸 ✨\n"
        "\n"
        "你好！我是你的备考伙伴 🎓\n"
        "\n"
        "🎁 新用户福利：注册即获 200 积分\n"
        "\n"
        "在使用之前，我需要先了解你的备考目标。\n"
        "请发送 #开始 启动初始化向导（约需2分钟），\n"
        "我会引导你完成以下设置：\n"
        "\n"
        "  ① 填写目标院校和报考专业\n"
        "  ② 确认专业课科目\n"
        "  ③ 设置考试日期\n"
        "  ④ 选择你的陪伴角色\n"
        "\n"
        "完成后即可开始你的备考之旅！\n"
        "\n"
        "👉 现在就发送 #开始 吧！"
    )
    await bot.send_private_msg(user_id=int(qq), message=welcome)


# ──────────────────────────────────────────────
# 2. #开始 指令 — 初始化向导
#    5分钟超时 / #取消 退出 / 非预期#指令提示
# ──────────────────────────────────────────────

WIZARD_TIMEOUT_SECONDS = 300  # 5分钟超时

init_wizard = on_command("开始", priority=5, block=True)


@init_wizard.handle()
async def wizard_start(bot: Bot, event: PrivateMessageEvent, matcher: Matcher):
    """初始化向导入口"""
    uid = get_or_create_uid(str(event.user_id))

    # 封禁检查
    if await check_banned(uid):
        await matcher.finish()

    # 确保用户记录存在（用户可能未通过好友申请流程进入）
    async with get_db_conn() as conn:
        cursor = await conn.execute(
            "SELECT init_complete FROM users WHERE user_id = ?", (uid,)
        )
        row = await cursor.fetchone()

        if not row:
            # 用户记录不存在，自动创建
            invite_code = generate_invite_code(uid)
            await conn.execute(
                """INSERT INTO users (user_id, invite_code)
                   VALUES (?, ?)""",
                (uid, invite_code),
            )
            await conn.execute(
                "INSERT INTO persona_config (user_id) VALUES (?)",
                (uid,),
            )
            await conn.execute(
                "INSERT INTO points_account (user_id) VALUES (?)",
                (uid,),
            )
            await conn.commit()

            # 发放注册积分
            db = UserDB(uid, conn)
            await points_service.register_bonus(uid, db)
        elif row["init_complete"]:
            await matcher.finish(
                "你已完成初始化。如需重新设置，请发送 [#重新初始化]。"
            )

    # 记录向导开始时间
    matcher.state["wizard_started_at"] = datetime.now().isoformat()

    # 第一步：院校专业
    step1_msg = (
        "【上岸 · 初始化 第1步 / 4】\n"
        "\n"
        "你好！在开始之前，我需要了解你的备考目标。\n"
        "\n"
        "请告诉我你的第一志愿：\n"
        "  院校名称：（如：北京大学）\n"
        "  报考专业：（如：计算机科学与技术）\n"
        "\n"
        "直接回复即可，格式：[院校名] [专业名]\n"
        "例如：北京大学 计算机科学与技术\n"
        "\n"
        "（发送 [#取消] 可随时退出向导）"
    )
    await matcher.pause(prompt=step1_msg)


@init_wizard.handle()
async def wizard_step1_school(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """第一步：接收院校和专业"""
    if await _wizard_guard(matcher, event):
        return
    uid = get_or_create_uid(str(event.user_id))
    raw = sanitize_input(event.get_plaintext(), max_length=200)

    # 解析院校和专业（空格分隔）
    parts = raw.split(maxsplit=1)
    if len(parts) < 2:
        await matcher.reject(
            "格式有误，请按格式回复：[院校名] [专业名]\n"
            "例如：北京大学 计算机科学与技术"
        )
        return

    school_name = parts[0]
    major_name = parts[1]

    # 存入 matcher 状态
    matcher.state["school_name"] = school_name
    matcher.state["major_name"] = major_name
    matcher.state["schools"] = [
        {"school": school_name, "major": major_name, "is_primary": True}
    ]

    # 写入数据库
    async with get_db_conn() as conn:
        await conn.execute(
            """INSERT INTO target_schools
               (user_id, school_name, major_name, is_primary, subjects)
               VALUES (?, ?, ?, 1, '[]')""",
            (uid, school_name, major_name),
        )
        await conn.commit()

    # 询问是否有备选院校
    confirm_msg = (
        f"已记录：{school_name} · {major_name} ✓\n"
        "\n"
        "你是否还有其他目标院校？（备选志愿）\n"
        "  · 有 → 回复院校名和专业名，格式同上\n"
        "  · 没有 → 回复 [完成]\n"
        "\n"
        "（最多支持3所目标院校，第一所为主目标）"
    )
    await matcher.pause(prompt=confirm_msg)


@init_wizard.handle()
async def wizard_step1_more_schools(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """第一步续：处理备选院校或进入第二步"""
    if await _wizard_guard(matcher, event):
        return
    uid = get_or_create_uid(str(event.user_id))
    raw = sanitize_input(event.get_plaintext(), max_length=200)
    schools = matcher.state.get("schools", [])

    if raw == "完成" or len(schools) >= 3:
        # fall through 到下面的 AI 检索
        pass
    else:
        # 添加备选院校
        parts = raw.split(maxsplit=1)
        if len(parts) < 2:
            await matcher.reject(
                "格式有误，请按格式回复：[院校名] [专业名]\n"
                "或回复 [完成] 进入下一步。"
            )
            return

        school_name = parts[0]
        major_name = parts[1]
        schools.append(
            {"school": school_name, "major": major_name, "is_primary": False}
        )
        matcher.state["schools"] = schools

        async with get_db_conn() as conn:
            await conn.execute(
                """INSERT INTO target_schools
                   (user_id, school_name, major_name, is_primary, subjects)
                   VALUES (?, ?, ?, 0, '[]')""",
                (uid, school_name, major_name),
            )
            await conn.commit()

        if len(schools) >= 3:
            await matcher.send(f"已记录：{school_name} · {major_name} ✓（已达上限）")
            # fall through 到下面的 AI 检索
        else:
            await matcher.reject(
                f"已记录：{school_name} · {major_name} ✓\n"
                "\n"
                "还有其他目标院校吗？\n"
                "  · 有 → 继续回复\n"
                "  · 没有 → 回复 [完成]"
            )
            return

    # ── 到这里说明用户输入了"完成"或达到上限，进入第二步 ──
    primary = schools[0]

    await matcher.send(
        "【上岸 · 初始化 第2步 / 4】\n"
        "\n"
        f"🔍 正在检索 {primary['school']} · {primary['major']} 的考研科目信息…\n"
        "请稍等，这可能需要 10-20 秒。"
    )

    # 调用 AI 检索（30秒超时）
    try:
        result = await asyncio.wait_for(
            ai_service.search_exam_subjects(
                primary["school"], primary["major"]
            ),
            timeout=30,
        )
        subjects = result.get("subjects", [])
        notes = result.get("notes", "")

        matcher.state["ai_subjects"] = subjects
        matcher.state["ai_notes"] = notes

        lines = ["✅ 检索完成！以下是你的考试科目：", ""]
        for i, subj in enumerate(subjects, 1):
            cat_icon = "📚" if subj.get("category") == "公共课" else "📖"
            code = f"({subj['code']})" if subj.get("code") else ""
            lines.append(f"  {_num_circle(i)} {cat_icon} {subj['name']} {code}")
            kps = subj.get("knowledge_points", [])
            if kps:
                shown = kps[:8]
                kp_str = "、".join(shown)
                if len(kps) > 8:
                    kp_str += f" 等共{len(kps)}个"
                lines.append(f"     知识点：{kp_str}")
            lines.append("")

        if notes:
            lines.append(f"📝 备注：{notes}")
            lines.append("")

        lines.append("请确认以上科目是否正确：")
        lines.append("  · 正确 → 回复 [确认]")
        lines.append("  · 需要修改 → 回复你的专业课科目名称（公共课保持不变）")
        lines.append("  · 跳过 → 回复 [#跳过]")

        await matcher.pause(prompt="\n".join(lines))

    except (
        # NoneBot 控制流异常必须穿透
        nonebot.exception.PausedException,
        nonebot.exception.RejectedException,
        nonebot.exception.FinishedException,
    ):
        raise
    except Exception as e:
        err_msg = "超时" if isinstance(e, asyncio.TimeoutError) else str(e)
        matcher.state["ai_subjects"] = None
        fallback_msg = (
            f"⚠️ AI 检索失败：{err_msg}\n"
            "\n"
            "请手动输入你的专业课科目名称：\n"
            "  常见情况：\n"
            "    · 408统考\n"
            "    · 院校自命题（请填写科目名称）\n"
            "\n"
            "  不确定？回复 [#跳过] 先继续。"
        )
        await matcher.pause(prompt=fallback_msg)


@init_wizard.handle()
async def wizard_step2_subject(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """第二步：确认 AI 检索结果或手动输入科目"""
    if await _wizard_guard(matcher, event):
        return
    uid = get_or_create_uid(str(event.user_id))
    raw = sanitize_input(event.get_plaintext(), max_length=200)

    if raw == "#跳过" or raw == "跳过":
        matcher.state["specialty_subject"] = None
    elif raw == "确认" and matcher.state.get("ai_subjects"):
        # 用户确认 AI 检索结果，写入数据库
        ai_subjects = matcher.state["ai_subjects"]
        matcher.state["specialty_subject"] = "、".join(
            s["name"] for s in ai_subjects if s.get("category") == "专业课"
        ) or "AI 检索"

        async with get_db_conn() as conn:
            for subj in ai_subjects:
                category = subj.get("category", "专业课")
                lib_type = "A" if category == "公共课" else "B"
                # 写入科目
                await conn.execute(
                    """INSERT OR IGNORE INTO subjects
                       (user_id, name, category, library_type)
                       VALUES (?, ?, ?, ?)""",
                    (uid, subj["name"], category, lib_type),
                )
                # 获取刚插入的 subject_id
                cursor = await conn.execute(
                    "SELECT id FROM subjects WHERE user_id = ? AND name = ?",
                    (uid, subj["name"]),
                )
                subj_row = await cursor.fetchone()
                if subj_row:
                    subj_id = subj_row["id"]
                    # 写入知识点
                    for kp_name in subj.get("knowledge_points", []):
                        await conn.execute(
                            """INSERT OR IGNORE INTO knowledge_points
                               (user_id, subject_id, topic_name, mastery_level)
                               VALUES (?, ?, ?, 1)""",
                            (uid, subj_id, kp_name),
                        )
            await conn.commit()

        # 统计写入结果
        total_kps = sum(len(s.get("knowledge_points", [])) for s in ai_subjects)
        await matcher.send(
            f"✅ 已写入 {len(ai_subjects)} 个科目、{total_kps} 个知识点"
        )
    else:
        # 手动输入专业课（AI 失败时的回退，或用户不确认 AI 结果）
        matcher.state["specialty_subject"] = raw

        async with get_db_conn() as conn:
            # 公共课（自动添加）
            for subj_name, category in [
                ("政治", "公共课"), ("英语", "公共课"), ("数学", "公共课")
            ]:
                await conn.execute(
                    """INSERT OR IGNORE INTO subjects
                       (user_id, name, category, library_type)
                       VALUES (?, ?, ?, 'A')""",
                    (uid, subj_name, category),
                )
            # 专业课
            lib_type = "A" if raw in ("408统考", "408") else "B"
            await conn.execute(
                """INSERT OR IGNORE INTO subjects
                   (user_id, name, category, library_type)
                   VALUES (?, ?, '专业课', ?)""",
                (uid, raw, lib_type),
            )
            await conn.commit()

    # 第三步：考试日期
    step3_msg = (
        "【上岸 · 初始化 第3步 / 4】\n"
        "\n"
        "请填写你的考试日期：\n"
        "\n"
        "  全国统考日期通常为每年12月第三周周六\n"
        "  （2025年为 2025-12-20，2026年为 2026-12-19）\n"
        "\n"
        "  直接回复日期，格式：YYYY-MM-DD\n"
        "  例如：2025-12-20\n"
        "\n"
        "  不确定具体日期？回复 [统考] 系统自动设置。"
    )
    await matcher.pause(prompt=step3_msg)


@init_wizard.handle()
async def wizard_step3_exam_date(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """第三步：接收考试日期"""
    if await _wizard_guard(matcher, event):
        return
    uid = get_or_create_uid(str(event.user_id))
    raw = sanitize_input(event.get_plaintext(), max_length=20)

    # 解析考试日期
    if raw == "统考":
        # 自动计算当年统考日期（12月第三周周六）
        year = date.today().year
        if date.today().month > 6:
            exam_date = _calc_exam_date(year)
        else:
            exam_date = _calc_exam_date(year)
        exam_str = exam_date.isoformat()
    else:
        # 手动输入日期
        try:
            exam_date = date.fromisoformat(raw)
            exam_str = exam_date.isoformat()
        except ValueError:
            await matcher.reject(
                "日期格式有误，请按 YYYY-MM-DD 格式回复。\n"
                "例如：2025-12-20\n"
                "或回复 [统考] 自动设置。"
            )
            return

    matcher.state["exam_date"] = exam_str
    days_left = (exam_date - date.today()).days

    # 写入数据库
    async with get_db_conn() as conn:
        await conn.execute(
            """INSERT OR REPLACE INTO exam_config (user_id, exam_date)
               VALUES (?, ?)""",
            (uid, exam_str),
        )
        await conn.commit()

    # 第四步：角色选择
    # 加载角色列表
    if not persona_engine.is_loaded():
        persona_engine.load_personas()

    persona_list = persona_engine.get_persona_list()
    lines = ["【上岸 · 初始化 第4步 / 4】", ""]
    lines.append(f"考试日期已设置：{exam_str}（距今 {days_left} 天）")
    lines.append("")
    lines.append("最后一步，选择你的陪伴角色：")
    lines.append("")

    for i, p in enumerate(persona_list, 1):
        lines.append(f"  {_num_circle(i)} {p['name']}（{p['archetype']}）")
        lines.append(f'     "{p["tagline"]}"')

    lines.append("")
    lines.append("回复角色名或 ID（如：咪咪 或 kitty）")
    lines.append("不确定？直接回复 [默认] 使用咪咪。")

    await matcher.pause(prompt="\n".join(lines))


@init_wizard.handle()
async def wizard_step4_persona(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """第四步：角色选择，完成初始化"""
    if await _wizard_guard(matcher, event):
        return
    uid = get_or_create_uid(str(event.user_id))
    raw = sanitize_input(event.get_plaintext(), max_length=50)

    # 匹配角色
    persona_id = _match_persona(raw)

    async with get_db_conn() as conn:
        # 更新角色
        await conn.execute(
            """UPDATE persona_config
               SET active_persona = ?, persona_since = ?
               WHERE user_id = ?""",
            (persona_id, datetime.now().isoformat(), uid),
        )

        # 标记初始化完成（订阅需用户手动开启）
        await conn.execute(
            "UPDATE users SET init_complete = 1 WHERE user_id = ?",
            (uid,),
        )
        await conn.commit()

    # 获取角色信息
    persona = persona_engine.get_persona(persona_id)
    persona_name = persona["name"] if persona else persona_id

    # 生成完成总结
    schools = matcher.state.get("schools", [])
    specialty = matcher.state.get("specialty_subject", "未设置")
    exam_str = matcher.state.get("exam_date", "未设置")
    days_left = 0
    try:
        days_left = (date.fromisoformat(exam_str) - date.today()).days
    except (ValueError, TypeError):
        pass

    from ..core.scheduler import get_study_phase
    phase = get_study_phase(days_left) if days_left > 0 else "基础期"
    phase_cn = {
        "foundation": "基础期",
        "intensify": "强化期",
        "sprint": "冲刺期",
        "sprint_final": "最后冲刺",
    }.get(phase, phase)

    summary_lines = ["🎉 初始化完成！", ""]
    summary_lines.append("你的备考配置：")
    summary_lines.append("")

    for i, s in enumerate(schools):
        prefix = "  主目标" if s["is_primary"] else "  备选  "
        summary_lines.append(f"{prefix}  {s['school']} · {s['major']}")

    if specialty:
        summary_lines.append(f"  专业课：{specialty}")

    summary_lines.append(f"  考试日期：{exam_str}（距今 {days_left} 天）")
    summary_lines.append(f"  备考阶段：{phase_cn}")
    summary_lines.append(f"  陪伴角色：{persona_name}")
    summary_lines.append("")
    summary_lines.append("接下来：")
    if matcher.state.get("ai_subjects"):
        summary_lines.append("  · 知识点已通过 AI 自动导入 ✓")
    summary_lines.append("")
    summary_lines.append("📋 推荐下一步：")
    summary_lines.append("  ① 发送 [#生成学期规划] → AI 生成月度备考目标")
    summary_lines.append("  ② 发送 [#订阅] → 开启每日学习提醒推送")
    summary_lines.append("     · 📖 按周计划自动提醒该学什么")
    summary_lines.append("     · ☀️ 每日早安推送 + 🌙 晚间复盘")
    summary_lines.append("")
    summary_lines.append("📝 更多功能：")
    summary_lines.append("  · 发送 [#今日计划] 查看今天的学习安排")
    summary_lines.append("  · 发送 [#推词] 开启考研英语单词推送")
    summary_lines.append("  · 发送 [#陪我聊] 找你的陪伴角色聊天")
    summary_lines.append("  · 发送 [#帮助] 查看所有可用指令")
    summary_lines.append("")
    summary_lines.append("准备好了吗？祝你备考顺利！🎓")

    await matcher.finish("\n".join(summary_lines))


# ──────────────────────────────────────────────
# 3. #重新初始化
# ──────────────────────────────────────────────

reinit = on_command("重新初始化", priority=5, block=True)


@reinit.handle()
async def handle_reinit(bot: Bot, event: PrivateMessageEvent, matcher: Matcher):
    """重置初始化状态，重新触发向导"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        await conn.execute(
            "UPDATE users SET init_complete = 0 WHERE user_id = ?",
            (uid,),
        )
        # 清除旧的 target_schools
        await conn.execute(
            "DELETE FROM target_schools WHERE user_id = ?",
            (uid,),
        )
        await conn.commit()

    await matcher.finish(
        "初始化状态已重置。\n发送 [#开始] 重新启动初始化向导。"
    )


# ──────────────────────────────────────────────
# 4. #填写邀请码
# ──────────────────────────────────────────────

fill_invite = on_command("填写邀请码", priority=5, block=True)


@fill_invite.handle()
async def handle_fill_invite(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher, args: Message = CommandArg()
):
    """填写邀请码"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()
    code = sanitize_input(args.extract_plain_text(), max_length=10).strip().upper()

    if not code or len(code) != 6:
        await matcher.finish("邀请码格式有误，请输入 6 位邀请码。")

    async with get_db_conn() as conn:
        # 查找邀请码对应的用户
        cursor = await conn.execute(
            "SELECT user_id FROM users WHERE invite_code = ?",
            (code,),
        )
        inviter = await cursor.fetchone()
        if not inviter:
            await matcher.finish("无效的邀请码，请确认后重试。")

        if inviter["user_id"] == uid:
            await matcher.finish("不能使用自己的邀请码。")

        # 检查是否已有邀请者
        cursor = await conn.execute(
            "SELECT invited_by FROM users WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        if row and row["invited_by"]:
            await matcher.finish("你已经填写过邀请码了。")

        # 记录邀请关系
        await conn.execute(
            "UPDATE users SET invited_by = ? WHERE user_id = ?",
            (inviter["user_id"], uid),
        )
        await conn.commit()

        # 尝试结算（如果已完成初始化）
        cursor = await conn.execute(
            "SELECT init_complete FROM users WHERE user_id = ?",
            (uid,),
        )
        init_row = await cursor.fetchone()
        if init_row and init_row["init_complete"]:
            db = UserDB(uid, conn)
            ok = await points_service.settle_invite(uid, db)
            if ok:
                await matcher.finish(
                    f"邀请码填写成功！✓\n"
                    f"你获得了 +{points_service.INVITE_BONUS_INVITEE} 积分奖励。"
                )
        
        await matcher.finish(
            "邀请码填写成功！✓\n"
            "完成初始化向导后将发放积分奖励。"
        )


# ──────────────────────────────────────────────
# 5. #我的邀请码
# ──────────────────────────────────────────────

my_invite = on_command("我的邀请码", priority=5, block=True)


@my_invite.handle()
async def handle_my_invite(bot: Bot, event: PrivateMessageEvent, matcher: Matcher):
    """查看个人邀请码"""
    uid = get_or_create_uid(str(event.user_id))
    code = generate_invite_code(uid)
    await matcher.finish(
        f"你的邀请码是 {code}\n"
        f"分享给朋友，让他们添加我为好友后发送：\n"
        f"  #填写邀请码 {code}"
    )


# ──────────────────────────────────────────────
# 辅助函数
# ──────────────────────────────────────────────

async def _wizard_guard(matcher: Matcher, event: PrivateMessageEvent) -> bool:
    """
    向导步骤入口守卫。检查超时、#取消、非预期#指令。
    返回 True 表示已经处理完毕（调用方应 return）。
    """
    raw = event.get_plaintext().strip()

    # #取消 — 主动退出向导
    if raw in ("#取消", "取消"):
        await matcher.finish("初始化向导已取消。\n随时发送 [#开始] 重新启动。")
        return True

    # 超时检查
    started_at = matcher.state.get("wizard_started_at")
    if started_at:
        try:
            start_dt = datetime.fromisoformat(started_at)
            elapsed = (datetime.now() - start_dt).total_seconds()
            if elapsed > WIZARD_TIMEOUT_SECONDS:
                await matcher.finish(
                    "向导已超时（5分钟无操作），已自动退出。\n"
                    "发送 [#开始] 重新启动初始化向导。"
                )
                return True
        except (ValueError, TypeError):
            pass

    # 非预期 #指令检测（向导期间收到其他 # 开头的指令）
    if raw.startswith("#") and raw not in ("#跳过", "#取消"):
        await matcher.reject(
            f"你正在进行初始化向导，请先完成当前步骤。\n"
            f"如需退出向导，发送 [#取消]。"
        )
        return True

    return False

def _calc_exam_date(year: int) -> date:
    """
    计算指定年份全国统考日期（12月第三周周六）。
    """
    # 找到12月1日是星期几
    dec_1 = date(year, 12, 1)
    # 星期六 = 5 (weekday())
    # 计算第一个周六
    first_sat = dec_1.day + (5 - dec_1.weekday()) % 7
    if first_sat == 0:
        first_sat = 7
    # 第三个周六
    third_sat = first_sat + 14
    return date(year, 12, third_sat)


def _num_circle(n: int) -> str:
    """返回带圈数字"""
    circles = {1: "①", 2: "②", 3: "③", 4: "④", 5: "⑤"}
    return circles.get(n, str(n))


def _match_persona(text: str) -> str:
    """
    从用户输入匹配角色 ID。
    支持角色名或英文 ID。
    """
    text = text.strip().lower()

    if text in ("默认", ""):
        return "kitty"

    # 名称到 ID 的映射
    name_map = {
        "咪咪": "kitty",
        "kitty": "kitty",
        "真学姐": "makoto",
        "makoto": "makoto",
        "卑弥呼": "himiko",
        "himiko": "himiko",
        "艾莉亚": "alya",
        "alya": "alya",
    }

    return name_map.get(text, "kitty")


# ──────────────────────────────────────────────
# 6. #订阅 — 开启定时推送（分周/月订阅）
# ──────────────────────────────────────────────

subscribe_cmd = on_command("订阅", priority=5, block=True)


@subscribe_cmd.handle()
async def handle_subscribe(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher,
    args: Message = CommandArg(),
):
    """开启推送订阅（需先有月计划）"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    arg_text = args.extract_plain_text().strip()

    async with get_db_conn() as conn:
        # 检查是否已完成初始化
        cursor = await conn.execute(
            "SELECT init_complete FROM users WHERE user_id = ?", (uid,),
        )
        user = await cursor.fetchone()
        if not user or not user["init_complete"]:
            await matcher.finish(
                "请先完成初始化向导。\n发送 [#开始] 启动初始化。"
            )

        # 检查是否有月计划（必须先生成学期规划）
        cursor = await conn.execute(
            "SELECT COUNT(*) as cnt FROM monthly_goals WHERE user_id = ?",
            (uid,),
        )
        row = await cursor.fetchone()
        if not row or row["cnt"] == 0:
            await matcher.finish(
                "⚠️ 你还没有生成月计划，无法订阅。\n"
                "请先发送 [#生成学期规划] 生成备考计划，\n"
                "然后再来订阅。"
            )

        # 检查当前订阅状态
        cursor = await conn.execute(
            "SELECT subscription_active FROM points_account WHERE user_id = ?",
            (uid,),
        )
        account = await cursor.fetchone()
        if account and account["subscription_active"]:
            await matcher.finish("你已经订阅了推送服务 ✓\n如需取消，发送 [#取消订阅]。")

        # 激活订阅
        await conn.execute(
            "UPDATE points_account SET subscription_active = 1 WHERE user_id = ?",
            (uid,),
        )
        await conn.commit()

    # 查找本周日程
    schedule_text = await _get_week_schedule_text(uid)

    sub_type = "周订阅" if arg_text in ("周", "周订阅") else "月订阅" if arg_text in ("月", "月订阅") else "推送订阅"

    lines = [
        f"✅ {sub_type}已开启！",
        "",
        "你将会收到以下推送：",
        "  · 📖 学习提醒（按周计划时间）",
        "  · ☀️ 早安推送（每日 07:30）",
        "  · 🌙 晚间复盘（每日 22:30）",
    ]

    if schedule_text:
        lines.append("")
        lines.append("📅 你本周的学习日程：")
        lines.append(schedule_text)

    lines.append("")
    lines.append("如需取消推送，发送 [#取消订阅]")

    await matcher.finish("\n".join(lines))


# ──────────────────────────────────────────────
# 7. #取消订阅
# ──────────────────────────────────────────────

unsubscribe_cmd = on_command("取消订阅", priority=5, block=True)


@unsubscribe_cmd.handle()
async def handle_unsubscribe(
    bot: Bot, event: PrivateMessageEvent, matcher: Matcher
):
    """取消推送订阅"""
    uid = get_or_create_uid(str(event.user_id))
    if await check_banned(uid):
        await matcher.finish()

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            "SELECT subscription_active FROM points_account WHERE user_id = ?",
            (uid,),
        )
        account = await cursor.fetchone()
        if not account or not account["subscription_active"]:
            await matcher.finish("你当前没有开启推送订阅。\n发送 [#订阅] 开启。")

        await conn.execute(
            "UPDATE points_account SET subscription_active = 0 WHERE user_id = ?",
            (uid,),
        )
        await conn.commit()

    await matcher.finish(
        "已取消推送订阅 ✓\n"
        "你将不再收到学习提醒、早安推送和晚间复盘。\n"
        "随时发送 [#订阅] 重新开启。"
    )


# ──────────────────────────────────────────────
# 辅助：获取本周日程文本
# ──────────────────────────────────────────────

async def _get_week_schedule_text(uid: str) -> str:
    """查询本周的 weekly_plan 并格式化为文本"""
    from datetime import timedelta

    today = date.today()
    # 本周一
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)

    async with get_db_conn() as conn:
        cursor = await conn.execute(
            """SELECT plan_date, subject_name, topic_name, estimated_minutes, notes
               FROM weekly_plan
               WHERE user_id = ?
                 AND plan_date BETWEEN ? AND ?
               ORDER BY plan_date, order_in_day""",
            (uid, monday.isoformat(), sunday.isoformat()),
        )
        rows = await cursor.fetchall()

    if not rows:
        return ""

    # 按日期分组
    day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    by_date: dict[str, list] = {}
    for r in rows:
        d = r["plan_date"]
        by_date.setdefault(d, []).append(r)

    lines = []
    for d in sorted(by_date.keys()):
        try:
            dt = date.fromisoformat(d)
            weekday = day_names[dt.weekday()]
            lines.append(f"\n  {weekday}（{d}）：")
        except ValueError:
            lines.append(f"\n  {d}：")

        for item in by_date[d]:
            mins = item["estimated_minutes"]
            subj = item["subject_name"]
            topic = item["topic_name"]
            lines.append(f"    · {subj}：{topic}（{mins}分钟）")

    return "\n".join(lines)
