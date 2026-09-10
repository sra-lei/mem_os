"""确定性正则提取器（RegularExtractor）—— 不依赖 LLM 的纯规则提取。

归属：``os_mem.extractor`` 记忆提取域。职责：

- ``fallback_numeric_facts`` / ``prune_redundant_verbatim``（原挂在
  FactExtractor 上的静态方法）：含金额/编号/日期/百分比等精确 token 的原文
  句子 verbatim 兜底（layer1 精确回忆防线：结构化提取改写会丢数字），以及
  「token 全被结构化覆盖的兜底句不存」的 R1 剪枝。

数值 token 抽取口径（``fact_tokens`` / ``norm_token``）的唯一实现源在
``common.py``——它同时被检索侧 ``core.retrieval_strategies`` 共享；本模块只 import
使用，不再持有副本。

边界：本模块无 DB / LLM / 网络依赖；结构化（LLM）提取链路仍在
``fact_extractor.py``。判分侧与审计工具各保留独立副本（分属 tests / 工具，
注明同步义务）。
"""

from __future__ import annotations

import hashlib
import json
import re

from os_mem.extractor.common import fact_tokens
from os_mem.models.mem_models import MemoryFact

__all__ = ['RegularExtractor', 'MAX_FALLBACK_FACTS']

# --------------------------------------------------------------------------- #
#  兜底句识别正则（verbatim 提取专属；共享数值 token 口径见 common.fact_tokens）
# --------------------------------------------------------------------------- #
# 精确信息兜底：即便 LLM 提取遗漏，也要把含金额/编号/日期/百分比的原文句子捞进库。
# 这些 token 正是 layer1 精确回忆类问题的答案核心（金额、编号、时间等）。
_NUMERIC_TOKENS = re.compile(
    r'\$\s?\d[\d,]*(?:\.\d+)?|'  # $2,400 / $1,017.50
    r'\d{1,2}%|'  # 20%
    r'\b\d{1,2}/\d{1,2}/\d{2,4}\b|'  # 11/21/2024
    r'\b\d{1,2}[:：]\d{2}\s*[APap]\.?[Mm]\.?|'  # 2:30 PM
    r'\b\d{1,2}[:：]\d{2}\b|'  # 14:35
    r'\b[A-Z]{2,}-\d{2,}[A-Z0-9-]*\b|'  # CLM-2024-894327 / PAC-778K4M
    r'\b\d{3}-\d{3}-\d{4}\b|'  # 电话 916-555-8899
    r'\b\d{4}-\d{4}-\d{4}-\d{4}\b|'  # 卡号 4532-8876-9901-3345
    # 产品名+年份/代号：Freedom 2045（裸 4 位易噪，仅收"大写词 4 位"形态）
    r'\b[A-Z][a-z]+ \d{4}\b'
)
# 兜底句归入 finance 类的提示（金额/百分比/卡号）；其余精确句归 other。
_FINANCE_HINT_RE = re.compile(r'\$\s?\d|%|\b\d{4}-\d{4}-\d{4}-\d{4}\b')
# 句末标点拆句（与兜底/AB 保真率口径一致）。
_SENTENCE_SPLIT_RE = re.compile(r'(?<=[.!?。！？])\s+')

MAX_FALLBACK_FACTS = 60


class RegularExtractor:
    """确定性正则提取：verbatim 数字句兜底 + token 覆盖剪枝（无 LLM/DB 依赖）。"""

    @staticmethod
    def fallback_numeric_facts(
        dialog_text: str,
        max_facts: int = MAX_FALLBACK_FACTS,
    ) -> list[MemoryFact]:
        """把原文中带金额/编号/日期/百分比等精确信息的短句原样入库。

        结构化提取会把对话"翻译/压缩"成语义事实，金额、编号这类精确 token 容易被
        改写或省略（layer1 失败的主因）。这里用正则从原文把含关键 token 的句子
        捞出来作为 verbatim 事实，保证数字类信息不因提取遗漏而丢失。
        """
        facts: list[MemoryFact] = []
        seen_prefixes: set[str] = set()
        for line in dialog_text.split('\n'):
            line = line.strip()
            if not line:
                continue
            message_content = line
            try:
                message_obj = json.loads(line)
                if isinstance(message_obj, dict) and message_obj.get('content'):
                    message_content = message_obj['content']
                elif isinstance(message_obj, list):
                    message_content = ' '.join(
                        str(item.get('content', ''))
                        for item in message_obj
                        if isinstance(item, dict)
                    )
            except Exception:
                pass
            # 按句末标点拆句，逐句判断是否含关键数值 token
            sentences = _SENTENCE_SPLIT_RE.split(message_content)
            for sentence in sentences:
                sentence = sentence.strip()
                if len(sentence) < 8 or len(sentence) > 600:
                    continue
                if not _NUMERIC_TOKENS.search(sentence):
                    continue
                signature = sentence[:120]
                if signature in seen_prefixes:
                    continue
                seen_prefixes.add(signature)
                category = (
                    'finance' if _FINANCE_HINT_RE.search(sentence) else 'other'
                )
                # key 用内容指纹而非聚合的 'verbatim_record'：多条兜底句共享一个
                # key 会在 (user, key) 冲突 upsert 时互相覆盖（库里只留最后一条，
                # 其余全部丢失——曾导致 13/17/20 的关键数字句在入库后被消灭）。
                # 内容指纹：同句重跑同 key（幂等覆盖），异句不同 key（互不踩踏）。
                digest = hashlib.sha1(sentence.encode('utf-8')).hexdigest()[:12]
                facts.append(
                    MemoryFact(
                        fact=sentence,
                        category=category,
                        key=f'verbatim_{digest}',
                        value=sentence,
                        confidence=0.85,
                    )
                )
                if len(facts) >= max_facts:
                    return facts
        return facts

    @staticmethod
    def prune_redundant_verbatim(
        fallback_facts: list[MemoryFact],
        llm_facts: list[MemoryFact],
    ) -> list[MemoryFact]:
        """R1 覆盖去重：verbatim 数值 token 全被 LLM 结构化事实覆盖 → 不存。

        兜底是"结构化漏提数字"的保险——只应保结构化**没覆盖**的信息。若一句兜底
        句里的数值全部已由结构化事实表达（同一 token 集合），该句不提供新信息，
        丢弃（安全：删的只是重复信息，唯一载体不受影响，无负收益）。

        降级保护：LLM 提取整体失败时 llm_facts 是 ``raw_conversation`` 原文降级
        （value=整段对话，token 覆盖一切）——此时不做剪枝，兜底照存（保险语义）。
        """
        if not llm_facts:
            return fallback_facts
        if any(fact.key.startswith('raw_conversation') for fact in llm_facts):
            return fallback_facts
        structured_tokens: set[str] = set()
        for fact in llm_facts:
            structured_tokens |= fact_tokens(f'{fact.fact} {fact.value or ""}')
        kept_facts: list[MemoryFact] = []
        for fact in fallback_facts:
            tokens = fact_tokens(f'{fact.fact} {fact.value or ""}')
            if tokens and tokens <= structured_tokens:
                continue
            kept_facts.append(fact)
        return kept_facts
