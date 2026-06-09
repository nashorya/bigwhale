"""
智谱 embedding-3 封装模块。
提供文本向量化能力，用于 Memory 系统的语义搜索。

技术指标（已验证）：
  模型：embedding-3
  维度：2048
  延迟：~600ms / 4条文本
  费用：<1分钱/月（考研场景）
"""

import os
import struct
import logging
from typing import Optional

import httpx

logger = logging.getLogger("shore.zhipu_embedding")

# ──────────────────────────────────────────────
# 配置
# ──────────────────────────────────────────────

ZHIPU_API_URL = "https://open.bigmodel.cn/api/paas/v4/embeddings"
EMBEDDING_MODEL = "embedding-3"
EMBEDDING_DIM = 2048


def _get_api_key() -> str:
    """从环境变量获取智谱 API Key。"""
    key = os.environ.get("ZHIPU_API_KEY")
    if not key:
        raise RuntimeError(
            "环境变量 ZHIPU_API_KEY 未设置。"
            "请在 .env 中配置后重启 Bot。"
        )
    return key


# ──────────────────────────────────────────────
# 核心 API
# ──────────────────────────────────────────────

async def get_embedding(text: str) -> list[float]:
    """
    获取单条文本的 embedding 向量。

    参数：
        text: 待向量化的文本
    返回：
        2048 维 float 列表
    """
    result = await get_embeddings([text])
    return result[0]


async def get_embeddings(texts: list[str]) -> list[list[float]]:
    """
    批量获取文本的 embedding 向量。

    参数：
        texts: 文本列表（建议单次不超过 16 条）
    返回：
        对应的 2048 维向量列表
    """
    if not texts:
        return []

    api_key = _get_api_key()
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": EMBEDDING_MODEL,
        "input": texts,
    }

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            resp = await client.post(ZHIPU_API_URL, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            # 按 index 排序，确保顺序与输入一致
            embeddings = sorted(data["data"], key=lambda x: x["index"])
            return [item["embedding"] for item in embeddings]

        except httpx.HTTPStatusError as e:
            logger.error("智谱 API 请求失败: %s %s", e.response.status_code, e.response.text)
            raise RuntimeError(f"智谱 embedding 请求失败: {e.response.status_code}") from e
        except Exception as e:
            logger.error("智谱 API 调用异常: %s", e)
            raise RuntimeError(f"智谱 embedding 调用异常: {e}") from e


# ──────────────────────────────────────────────
# 向量序列化（存入 SQLite BLOB）
# ──────────────────────────────────────────────

def vector_to_blob(vec: list[float]) -> bytes:
    """
    将 float 列表序列化为 bytes，用于存入 SQLite BLOB 字段。
    使用 struct 打包 float32，比 JSON 节省 4 倍存储空间。
    """
    return struct.pack(f"{len(vec)}f", *vec)


def blob_to_vector(blob: bytes) -> list[float]:
    """从 SQLite BLOB 反序列化为 float 列表。"""
    count = len(blob) // 4  # float32 = 4 bytes
    return list(struct.unpack(f"{count}f", blob))


# ──────────────────────────────────────────────
# 余弦相似度
# ──────────────────────────────────────────────

def cosine_similarity(a: list[float], b: list[float]) -> float:
    """
    计算两个向量的余弦相似度。
    不依赖 numpy，纯 Python 实现，对 2048 维向量足够快。
    """
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(x * x for x in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)
