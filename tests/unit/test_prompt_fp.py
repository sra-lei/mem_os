"""prompt 内容指纹（os_mem.utils.prompt_fp）离线单测。

验证指纹函数性质，并锁定评测 4 处 prompt（提取 system/repair、回答、
判分）都导出指纹常量——prompt 后续每次迭代都会使指纹变化，因此
任何改动 prompt 的提交，这里记录的指纹也随之改变（无需手动维护版本号）。
"""

from __future__ import annotations

from os_mem.utils.prompt_fp import fingerprint


def test_fingerprint_deterministic_and_hex_length() -> None:
    fp = fingerprint('# 角色\n你是客服助手')
    assert len(fp) == 12
    int(fp, 16)  # 纯十六进制
    assert fp == fingerprint('# 角色\n你是客服助手')


def test_fingerprint_changes_on_content_edit() -> None:
    assert fingerprint('# 角色\n你是客服助手') != fingerprint(
        '# 角色\n你是一名客服助手'
    )


def _all_fingerprints() -> dict[str, str]:
    """评测 4 处 prompt 的指纹常量（模块引用，避免常量改名冲突）。"""
    import eval.judge as judge_mod
    import eval.llm as llm_mod
    import os_mem.extractor.prompt as extract_mod

    return {
        'extract.system': extract_mod.SYSTEM_PROMPT_FINGERPRINT,
        'extract.repair': extract_mod.REPAIR_PROMPT_FINGERPRINT,
        'answer.system': llm_mod.SYSTEM_PROMPT_FINGERPRINT,
        'judge.system': judge_mod.SYSTEM_PROMPT_FINGERPRINT,
    }


def test_all_prompt_fingerprints_are_valid_hex() -> None:
    for name, fp in _all_fingerprints().items():
        assert len(fp) == 12, f'{name} 指纹长度异常: {fp}'
        int(fp, 16)  # 必须为十六进制


def test_all_prompts_are_distinct() -> None:
    """四份 prompt 内容各异，指纹互不相同（防止误用同一模板）。"""
    fps = set(_all_fingerprints().values())
    assert len(fps) == 4


def test_extract_prompt_template_fingerprint_independent_of_max_facts() -> None:
    """指纹作用于模板本身：{max_facts} 占位不参与指纹（配置值变化不误判为迭代）。"""
    from os_mem.extractor import prompt as extract_prompt

    assert '{max_facts}' in extract_prompt.SYSTEM_PROMPT
    # 直接计算原文模板指纹应与模块常量一致
    assert fingerprint(extract_prompt.SYSTEM_PROMPT) == (
        extract_prompt.SYSTEM_PROMPT_FINGERPRINT
    )
