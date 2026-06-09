"""
安全工具 — hash_user_id + sanitize_input + get_or_create_uid。
core/ 层禁止直接 import nonebot 模块。
"""

import hashlib
import os
import re

# 系统启动时从环境变量读取 SALT，不硬编码在代码里
_SALT: str | None = None


def _get_salt() -> str:
    """
    延迟加载 SALT。
    首次调用时从环境变量 SHORE_USER_SALT 读取并缓存，
    避免模块导入时 .env 尚未加载的问题。
    """
    global _SALT
    if _SALT is None:
        _SALT = os.environ.get("SHORE_USER_SALT")
        if not _SALT:
            raise RuntimeError(
                "环境变量 SHORE_USER_SALT 未设置。"
                "请在 .env 中配置后重启 Bot。"
            )
    return _SALT


def hash_user_id(qq: str) -> str:
    """
    将原始 QQ 号转换为不可逆的用户标识（SHA-256）。
    同一 QQ 号 + 同一 SALT 始终得到相同结果，
    但无法从结果反推出原始 QQ 号。
    数据库中只存储此哈希值，不存储原始 QQ 号。
    """
    salt = _get_salt()
    raw = f"{salt}:{qq}"
    return hashlib.sha256(raw.encode()).hexdigest()


# ──────────────────────────────────────────────
# QQ -> user_id 内存映射（持久化以支持定时推送）
# ──────────────────────────────────────────────

import json
from pathlib import Path

MAPPING_FILE = Path("data/.qq_map.json")


def _load_mapping() -> dict[str, str]:
    if MAPPING_FILE.exists():
        try:
            with open(MAPPING_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_mapping(mapping: dict[str, str]) -> None:
    MAPPING_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(MAPPING_FILE, "w", encoding="utf-8") as f:
            json.dump(mapping, f, ensure_ascii=False)
    except Exception:
        pass


_qq_to_uid: dict[str, str] = _load_mapping()


def get_or_create_uid(qq: str) -> str:
    """
    获取或创建用户的哈希 ID。
    维护 qq -> user_id 的映射，并持久化到 data/.qq_map.json。
    以支持定时任务即使在 Bot 重启后也能向用户推送消息。
    """
    if qq not in _qq_to_uid:
        _qq_to_uid[qq] = hash_user_id(qq)
        _save_mapping(_qq_to_uid)
    return _qq_to_uid[qq]


# ──────────────────────────────────────────────
# 用户输入清洗
# ──────────────────────────────────────────────

def sanitize_input(text: str, max_length: int = 100) -> str:
    """
    清洗用户输入：
    · 去除首尾空白
    · 移除控制字符（ASCII 0x00-0x1f 和 0x7f）
    · 截断超长输入（默认 100 字符）
    所有来自 QQ 消息的文本输入，必须在进入业务逻辑前经过此函数。
    """
    text = text.strip()
    text = re.sub(r'[\x00-\x1f\x7f]', '', text)  # 移除控制字符
    return text[:max_length]


# ──────────────────────────────────────────────
# 邀请码生成
# ──────────────────────────────────────────────

def generate_invite_code(user_id: str) -> str:
    """
    生成 6 位大写邀请码，与 user_id 一一对应。
    基于 user_id 的 MD5 哈希前 6 位，不暴露 QQ 号。
    """
    raw = hashlib.md5(f"{user_id}:invite".encode()).hexdigest()
    return raw[:6].upper()
