"""
PersonaEngine — 角色渲染引擎。
core/ 层禁止直接 import nonebot 模块。

启动时从 /personas/ 目录加载所有内置人物卡 JSON 到内存。
所有对外推送消息在发出前，统一经过本引擎的风格渲染层。
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any


# ──────────────────────────────────────────────
# 全局状态：内存中的人物卡数据
# ──────────────────────────────────────────────

# persona_id -> 完整 JSON 数据
_personas: dict[str, dict[str, Any]] = {}

# 索引列表缓存（用于 #选择角色 展示）
_persona_index: list[dict[str, str]] = []

# 人物卡目录路径
_PERSONAS_DIR: Path | None = None


# ──────────────────────────────────────────────
# 初始化：加载人物卡
# ──────────────────────────────────────────────

def load_personas(personas_dir: str | Path | None = None) -> int:
    """
    启动时调用，从 personas 目录加载所有内置人物卡 JSON 到内存。
    返回加载成功的人物卡数量。

    参数：
        personas_dir: personas 目录路径。默认为项目根目录下的 personas/
    """
    global _personas, _persona_index, _PERSONAS_DIR

    if personas_dir is None:
        # 默认路径：项目根目录/personas/
        _PERSONAS_DIR = Path(__file__).resolve().parent.parent.parent.parent / "personas"
    else:
        _PERSONAS_DIR = Path(personas_dir)

    index_path = _PERSONAS_DIR / "personas_index.json"
    if not index_path.exists():
        raise FileNotFoundError(
            f"角色索引文件不存在: {index_path}\n"
            f"请确认 personas/personas_index.json 已正确创建。"
        )

    with open(index_path, "r", encoding="utf-8") as f:
        index_data = json.load(f)

    _personas.clear()
    _persona_index.clear()

    # 加载内置人物卡
    for entry in index_data.get("builtin", []):
        card_path = _PERSONAS_DIR / entry["file"]
        if not card_path.exists():
            continue

        with open(card_path, "r", encoding="utf-8") as f:
            card_data = json.load(f)

        _personas[card_data["id"]] = card_data
        _persona_index.append({
            "id": entry["id"],
            "name": entry["name"],
            "archetype": entry["archetype"],
            "tagline": entry["tagline"],
        })

    return len(_personas)


# ──────────────────────────────────────────────
# 核心渲染方法
# ──────────────────────────────────────────────

def render(
    persona_id: str,
    script_key: str,
    data: dict[str, Any] | None = None,
    *,
    catchphrase_chance: float = 0.2,
) -> str:
    """
    使用指定角色渲染模板消息。

    渲染流程（对应 v3.1 文档 9.4）：
      ① 从 _personas 中取出角色卡
      ② 从角色卡中查找对应的 script_key 模板
      ③ 填充 data 中的变量（{kp_name} 等）
      ④ 20% 概率随机追加 catchphrase
      ⑤ 按 emoji_set 决定是否附加 emoji

    参数：
        persona_id: 角色 ID（如 'kitty'）
        script_key: 脚本键，支持点号分隔的嵌套路径
                    如 'checkin_scripts.single_immediate'
                    或 'emotion_scripts.accept'
        data: 模板变量字典，如 {'kp_name': 'B树', 'before': 3, 'after': 4}
        catchphrase_chance: 插入口癖的概率（默认 0.2 即 20%）

    返回：
        渲染后的最终消息字符串
    """
    if data is None:
        data = {}

    card = _personas.get(persona_id)
    if card is None:
        # 回退到第一个可用角色
        if _personas:
            card = next(iter(_personas.values()))
        else:
            return _fallback_render(script_key, data)

    # 查找模板文本
    template = _resolve_script(card, script_key)
    if template is None:
        return _fallback_render(script_key, data)

    # 如果模板是列表，随机选一条
    if isinstance(template, list):
        template = random.choice(template)

    # 填充模板变量
    message = _fill_template(str(template), data)

    # 注入 tone_profile 修饰
    tone = card.get("tone_profile", {})
    message = _apply_tone(message, tone, catchphrase_chance)

    return message


def _resolve_script(card: dict, script_key: str) -> Any | None:
    """
    根据点号分隔的键路径，从角色卡中查找对应脚本。

    支持的 script_key 格式：
      - 'daily_scripts.morning_greeting'
      - 'checkin_scripts.single_immediate'
      - 'emotion_scripts.accept'
      - 'milestone_scripts.streak_30'
    """
    parts = script_key.split(".")
    current = card

    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None

    return current


def _fill_template(template: str, data: dict[str, Any]) -> str:
    """
    填充模板中的变量占位符。
    使用 str.format_map 进行安全替换，
    未提供的变量会保留原始占位符（不抛异常）。
    """

    class SafeDict(dict):
        """缺失键时返回原始占位符，避免 KeyError"""
        def __missing__(self, key: str) -> str:
            return f"{{{key}}}"

    try:
        return template.format_map(SafeDict(data))
    except (ValueError, IndexError):
        # 模板格式异常时返回原始模板
        return template


def _apply_tone(
    message: str,
    tone: dict[str, Any],
    catchphrase_chance: float,
) -> str:
    """
    按照 tone_profile 对消息进行修饰：
      - 20% 概率追加 catchphrase（口癖）
      - 按 emoji_set 决定是否附加 emoji
    """
    # 随机插入 catchphrase（概率 20%，避免每次都出现）
    catchphrases = tone.get("catchphrase", [])
    if catchphrases and random.random() < catchphrase_chance:
        phrase = random.choice(catchphrases)
        message = f"{message}\n{phrase}"

    # 按 emoji_set 决定是否附加 emoji
    # 如果角色有 emoji_set，30% 概率在末尾追加一个随机 emoji
    emoji_set = tone.get("emoji_set", [])
    if emoji_set and random.random() < 0.3:
        # 不重复添加已存在的 emoji
        emoji = random.choice(emoji_set)
        if emoji not in message:
            message = f"{message} {emoji}"

    return message


def _fallback_render(script_key: str, data: dict[str, Any]) -> str:
    """
    当找不到角色卡或脚本时的回退渲染。
    直接返回变量数据的简单格式化。
    """
    parts = script_key.split(".")
    key_name = parts[-1] if parts else script_key
    if data:
        content = "、".join(f"{k}={v}" for k, v in data.items())
        return f"[{key_name}] {content}"
    return f"[{key_name}]"


# ──────────────────────────────────────────────
# 查询方法
# ──────────────────────────────────────────────

def get_persona_list() -> list[dict[str, str]]:
    """
    获取所有可用角色的列表信息，用于 #选择角色 指令展示。

    返回：
        列表，每项包含 id, name, archetype, tagline。
    """
    return list(_persona_index)


def get_persona(persona_id: str) -> dict[str, Any] | None:
    """
    获取指定角色的完整数据。

    参数：
        persona_id: 角色 ID

    返回：
        角色卡字典，不存在时返回 None
    """
    return _personas.get(persona_id)


def is_loaded() -> bool:
    """判断人物卡是否已加载到内存。"""
    return len(_personas) > 0
