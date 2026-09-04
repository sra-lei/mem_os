import jieba
from typing import List
from rank_bm25 import BM25Okapi
from os_mem.infra.logger import get_logger

# 加载 jieba（首次约 0.5 秒）
_jieba_loaded = False
_logger = get_logger("os_mem.retriever.rank_bm25")

class RankBM25:
    def __init__(self, convs: List[str]):
        self.convs = convs
        self._load_jieba()
        _logger.info("RankBM25 初始化完成")

    def _load_jieba(self):
        global _jieba_loaded
        if not _jieba_loaded:
            #jieba.load_user_dict("data/user_dict.txt")
            _jieba_loaded = True

    def _tokenize(self, text: str) -> List[str]:
        """混合分词：中文用 jieba，英文按空格"""
        self._load_jieba()
        
        # 检测是否包含中文
        has_chinese = any('\u4e00' <= c <= '\u9fff' for c in text)
        
        if has_chinese:
            # 精确模式，默认
            words = jieba.cut(text, cut_all=False)
            # 过滤空白字符
            return [w.strip() for w in words if len(w.strip()) > 0]
        else:
            # 英文：按空格分词，去除空白
            return [w.strip() for w in text.split() if len(w.strip()) > 0]

    def _build_index(self):
        self.all_documents = []  # 原始文本
        self.all_tokenized = []  # 分词后的文本（用于 BM25）
        
        for record in self.convs:
            # 对每条文档做分词（rank_bm25 需要传入分词后的列表）
            tokenized_doc = self._tokenize(record)
            if tokenized_doc:  # 过滤空文档
                self.all_documents.append(record)
                self.all_tokenized.append(tokenized_doc)
       
    def retrieve(self, query: str, top_k: int = 3) -> List[tuple]:
        self._build_index()
         # 2. 提取所有回合
        if not self.all_tokenized:
            return []
        # 3. 构建 BM25 索引
        bm25 = BM25Okapi(self.all_tokenized)
        # 4. 查询分词
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []
        # 5. 计算得分
        scores = bm25.get_scores(query_tokens)
        # 6. 取 Top-K
        # 获取得分最高的 top_k 个索引
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        # 7. 组装返回结果
        response = []
        for idx in top_indices:
            if scores[idx] > 0:  # 只返回有得分的
                response.append((idx, self.all_documents[idx], scores[idx]))
        return response

def __main__():
    # 英文（保持不变）
    print(RankBM25Retriever._tokenize("What is my account number?"))
    # → ['What', 'is', 'my', 'account', 'number?']

    # 中文（jieba 切分）
    print(RankBM25Retriever._tokenize("我的支票账户号码是多少？"))
    # → ['我的', '支票', '账户', '号码', '是多少？']

    # 中英混合（各自处理）
    print(RankBM25Retriever._tokenize("我的账号是 ABC123，请帮我查一下余额。"))
    # → ['我的', '账号', '是', 'ABC123', '请', '帮', '我', '查', '一下', '余额。']