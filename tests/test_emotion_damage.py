import sys
import os
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "shore"))

os.environ["SHORE_USER_SALT"] = "test_salt_for_unit_test_only"
os.environ["DB_PATH"] = ":memory:"

from core.emotion_detector import detect, EMOTION_KEYWORDS

# ── detect 基本逻辑 ──────────────────────────────

def test_no_trigger_on_normal_text():
    """正常消息不触发"""
    assert detect("今天天气不错") == (False, None)
    assert detect("我去吃饭了") == (False, None)
    assert detect("408复习进度怎么样") == (False, None)

def test_strong_signal_triggers():
    """单个强信号词触发"""
    triggered, category = detect("我想聊聊")
    assert triggered is True
    assert category is not None

def test_two_weak_signals_trigger():
    """两个弱信号词触发"""
    triggered, _ = detect("好烦啊，感觉好累")
    assert triggered is True

def test_one_weak_signal_no_trigger():
    """单个弱信号词不触发"""
    triggered, _ = detect("好烦")
    assert triggered is False

def test_all_categories_covered():
    """四个分类都有词"""
    assert "焦虑" in EMOTION_KEYWORDS or len(EMOTION_KEYWORDS) >= 4

def test_detect_returns_category():
    """触发时返回分类名"""
    triggered, category = detect("陪我说说话")
    assert triggered is True
    assert isinstance(category, str)

def test_empty_string():
    """空字符串不崩溃"""
    assert detect("") == (False, None)

def test_no_false_positive_on_partial_match():
    """包含信号词子串的正常词不误触发"""
    # '累' 是信号词，但 '积累' 不应该触发
    triggered, _ = detect("知识在积累")
    assert triggered is False