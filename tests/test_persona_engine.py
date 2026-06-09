"""
core/persona_engine.py 验证脚本
"""
import os
import sys
import random

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugins", "shore"))

from core import persona_engine


def test():
    # 设置随机种子保证可复现
    random.seed(42)

    personas_dir = os.path.join(os.path.dirname(__file__), "..", "personas")

    # 1. 加载人物卡
    count = persona_engine.load_personas(personas_dir)
    assert count == 4, f"应加载4个人物卡，实际: {count}"
    assert persona_engine.is_loaded()
    print(f"  load_personas OK: 加载了 {count} 个人物卡")

    # 2. get_persona_list
    persona_list = persona_engine.get_persona_list()
    assert len(persona_list) == 4
    ids = [p["id"] for p in persona_list]
    assert "kitty" in ids
    assert "makoto" in ids
    assert "himiko" in ids
    assert "alya" in ids
    print(f"  get_persona_list OK: {[p['name'] for p in persona_list]}")

    # 3. get_persona
    kitty = persona_engine.get_persona("kitty")
    assert kitty is not None
    assert kitty["name"] == "咪咪"
    assert kitty["tone_profile"]["verbosity"] == "mid"
    assert len(kitty["tone_profile"]["emoji_set"]) > 0
    print(f"  get_persona OK: 咪咪 verbosity={kitty['tone_profile']['verbosity']}")

    makoto = persona_engine.get_persona("makoto")
    assert makoto is not None
    assert len(makoto["tone_profile"]["emoji_set"]) > 0
    print(f"  get_persona OK: 真学姐 emoji_set={makoto['tone_profile']['emoji_set']}")

    # 4. render 基本功能 - 打卡反馈
    result = persona_engine.render(
        "kitty",
        "checkin_scripts.single_immediate",
        {"kp_name": "B树与B+树", "before": 3, "after": 4, "next_review": "7天后"},
        catchphrase_chance=0,
    )
    assert "B树与B+树" in result
    assert "3" in result
    assert "4" in result
    print(f"  render (咪咪打卡) OK: {result[:60]}...")

    # 5. render 真学姐打卡
    result_bq = persona_engine.render(
        "makoto",
        "checkin_scripts.single_immediate",
        {"kp_name": "极限", "before": 2, "after": 3},
        catchphrase_chance=0,
    )
    assert "极限" in result_bq
    assert "2" in result_bq
    print(f"  render (真学姐打卡) OK: {result_bq[:60]}...")

    # 6. render 情绪脚本（列表类型，应随机选一条）
    random.seed(0)
    result_emo = persona_engine.render(
        "himiko",
        "emotion_scripts.accept",
        {},
        catchphrase_chance=0,
    )
    assert isinstance(result_emo, str)
    assert len(result_emo) > 0
    print(f"  render (卑弥呼情绪接纳) OK: {result_emo[:60]}...")

    # 7. render 艾莉亚日常
    result_jl = persona_engine.render(
        "alya",
        "daily_scripts.study_reminder",
        {"subject": "408"},
        catchphrase_chance=0,
    )
    assert "408" in result_jl
    print(f"  render (艾莉亚提醒) OK: {result_jl}")

    # 8. render 熔断（字符串类型）
    result_melt = persona_engine.render(
        "makoto",
        "emotion_scripts.meltdown",
        {},
        catchphrase_chance=0,
    )
    assert len(result_melt) > 0
    print(f"  render (真学姐熔断) OK: {result_melt[:50]}...")

    # 9. render 缺失角色 - 回退到第一个可用角色
    result_fallback = persona_engine.render(
        "nonexistent_persona",
        "daily_scripts.study_reminder",
        {"subject": "数学"},
        catchphrase_chance=0,
    )
    assert "数学" in result_fallback
    print(f"  render (回退角色) OK: {result_fallback}")

    # 10. render 缺失脚本键 - 回退渲染
    result_missing = persona_engine.render(
        "kitty",
        "nonexistent.key",
        {"foo": "bar"},
    )
    assert "foo" in result_missing
    print(f"  render (回退脚本) OK: {result_missing}")

    # 11. render 缺失模板变量 - 应安全处理
    result_safe = persona_engine.render(
        "kitty",
        "checkin_scripts.single_immediate",
        {"kp_name": "B树"},  # 缺少 before, after, next_review
        catchphrase_chance=0,
    )
    assert "B树" in result_safe
    assert "{before}" in result_safe  # 未提供的变量保留原始占位符
    print(f"  render (安全填充) OK: {result_safe[:60]}...")

    # 12. catchphrase 概率测试
    random.seed(42)
    results_with_catch = []
    for _ in range(100):
        r = persona_engine.render(
            "kitty",
            "daily_scripts.study_reminder",
            {"subject": "数学"},
            catchphrase_chance=0.2,
        )
        results_with_catch.append(r)
    has_catchphrase = sum(1 for r in results_with_catch if "\n" in r)
    # 20% 概率，100次应该有大约 10-30 次包含口癖
    assert 5 < has_catchphrase < 40, f"口癖出现次数异常: {has_catchphrase}/100"
    print(f"  catchphrase 概率 OK: {has_catchphrase}/100 次（预期~20%）")

    # 13. 里程碑脚本
    result_ms = persona_engine.render(
        "makoto",
        "milestone_scripts.mastery_5",
        {"kp_name": "进程调度算法"},
        catchphrase_chance=0,
    )
    assert "进程调度算法" in result_ms
    print(f"  render (里程碑) OK: {result_ms[:60]}...")

    print()
    print("✅ persona_engine.py 所有测试通过！")


if __name__ == "__main__":
    test()
