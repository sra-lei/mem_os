"""v0.1: BM25 全文检索对话文本（单用户规模足够）

修正要点：
- 词频用 split 后的词列表 count（原 doc.count(w) 是子串计数，"my" 会命中 "myself"）
- 查询与文档统一小写，避免大小写不匹配
- idf 用标准 log 形式，避免无界放大
"""
from typing import List
import math

from os_mem.infra.logger import get_logger
_logger = get_logger("os_mem.retriever.simple_bm25")
# 英文常用停用词（查询与文档统一过滤，避免 what/my/it/set 等词干扰关键词权重）
_STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "if", "then", "else", "for",
    "to", "in", "on", "at", "by", "with", "of", "from", "up", "out",
    "off", "over", "under", "again", "once", "here", "there", "when",
    "where", "why", "how", "all", "any", "both", "each", "few", "more",
    "most", "other", "some", "such", "no", "nor", "not", "only", "own",
    "same", "so", "than", "too", "very", "just", "now", "is", "are",
    "was", "were", "be", "been", "being", "have", "has", "had", "do",
    "does", "did", "will", "would", "can", "could", "should", "may",
    "might", "must", "i", "you", "he", "she", "it", "we", "they", "me",
    "him", "her", "us", "them", "my", "your", "his", "its", "our",
    "their", "what", "which", "who", "whom", "this", "that", "these",
    "those", "yes", "ok", "okay", "please", "thanks", "thank", "also",
    "need", "want", "like", "get", "got", "let", "know", "right", "well",
    "really", "actually", "sure", "please", "would", "can", "will",
}

# 简陋的 BM25 实现（单用户规模足够）
class SimpleBM25:
    def __init__(self, documents: List[str]) -> None:
        self.documents = documents
        self._build_index()
        _logger.info("SimpleBM25 初始化完成")

    @staticmethod
    def _tokenize(text: str) -> List[str]:
        return [w for w in text.lower().split() if w not in _STOPWORDS]

    def _build_index(self) -> None:
        # 简单词频统计
        self.doc_freq = {}
        self.doc_lengths = []
        self.corpus_size = len(self.documents)

        for doc in self.documents:
            tokens = self._tokenize(doc)
            words = set(tokens)
            for w in words:
                self.doc_freq[w] = self.doc_freq.get(w, 0) + 1
            self.doc_lengths.append(len(tokens))

        self.avg_doc_len = sum(self.doc_lengths) / self.corpus_size if self.corpus_size else 1

    def retrieve(self, query: str, top_k: int = 3) -> List[tuple]:
        query_words = self._tokenize(query)
        scores = []

        for i, doc in enumerate(self.documents):
            score = 0.0
            doc_len = self.doc_lengths[i]
            tokens = self._tokenize(doc)
            for w in query_words:
                df = self.doc_freq.get(w, 0)
                if df == 0:
                    continue
                # 标准 BM25 idf：ln(1 + (N - df + 0.5) / (df + 0.5))
                idf = math.log(1 + (self.corpus_size - df + 0.5) / (df + 0.5))
                tf = tokens.count(w)  # 词频（split 后计数，非子串）
                score += idf * (tf * 1.5 / (tf + 0.5 + 1.5 * (doc_len / self.avg_doc_len)))
            scores.append((i, self.documents[i], score))

        scores.sort(key=lambda x: x[2], reverse=True)
        return scores[:top_k]
