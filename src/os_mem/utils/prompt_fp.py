"""prompt 内容指纹 —— 迭代中人工 prompt 的版本标识。

背景：评测三处人工 prompt（事实提取 system/repair、回答 system、判分 system）
处于持续迭代期。为把「某次跑分」与「当时 prompt 的具体内容」挂钩，对每个
prompt 模板计算内容指纹（SHA-1 前 12 位，文本一变指纹即变），随
``--record-db`` 的 ``config_snapshot`` 落库，并在 LLM 调用观测日志中可按
模块追溯——无需手工维护版本号（人工版本号极易漏改）。

依赖方向：由 ``os_mem`` 提供实现；评测侧（``tests/eval``）允许 import 复用
（依赖单向：eval → os_mem）。本模块只依赖标准库，import 无任何副作用。
"""

from __future__ import annotations

import hashlib

__all__ = ['fingerprint']


def fingerprint(text: str, length: int = 12) -> str:
    """返回 prompt 模板文本的短内容指纹（SHA-1 十六进制前 length 位）。"""
    return hashlib.sha1(text.encode('utf-8')).hexdigest()[:length]
