"""
doc-kit - 纯向量化能力：文本 → embedding 向量
不涉及任何存储，独立可复用。
"""
from typing import List

from openai import OpenAI, BadRequestError
from loguru import logger

from os_mem.configs.mem_settings import memory_settings

class Vectorizer:
    """文本向量化器（基于阿里百炼 text-embedding-v2）"""

    def __init__(
        self,
        model: str = None,
        api_key: str = None,
        base_url: str = None,
    ):
        self.model = model or memory_settings.EMBEDDING_MODEL
        self.client = OpenAI(
            api_key=api_key or memory_settings.DASHSCOPE_API_KEY,
            base_url=base_url or memory_settings.DASHSCOPE_BASE_URL,
        )

    def embed(self, text: str) -> List[float]:
        """获取单条文本的 embedding 向量"""
        text = text.replace("\n", " ")
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            logger.error(f"向量化文本失败: {e}")
            raise

    def embed_batch(self, texts: List[str], batch_size: int = 10) -> List[List[float]]:
        """批量获取 embedding 向量，按 batch_size 分批请求。

        注意：阿里百炼 text-embedding-v2 单批上限 10 条，超限会返回 400；
        即使调用方传入更大的 batch_size，_embed_batch_once 也会自动折半重试。
        """
        all_embeddings: List[List[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch = [t.replace("\n", " ") for t in batch]
            logger.info(
                f"向量化 {i + 1}-{min(i + batch_size, len(texts))}/{len(texts)}"
            )
            all_embeddings.extend(self._embed_batch_once(batch))
        return all_embeddings

    def _embed_batch_once(self, batch: List[str]) -> List[List[float]]:
        """单次请求一批文本；若服务端拒绝批量大小，则折半递归重试"""
        try:
            response = self.client.embeddings.create(
                model=self.model,
                input=batch,
            )
            return [d.embedding for d in response.data]
        except BadRequestError as e:
            if len(batch) <= 1:
                raise
            logger.warning(f"批量过大被服务端拒绝（{len(batch)} 条），折半重试: {e}")
            mid = (len(batch) + 1) // 2
            return self._embed_batch_once(batch[:mid]) + self._embed_batch_once(batch[mid:])


# 全局单例
_vectorizer: Vectorizer = None


def get_vectorizer() -> Vectorizer:
    """获取全局 Vectorizer 单例"""
    global _vectorizer
    if _vectorizer is None:
        _vectorizer = Vectorizer(
            model=memory_settings.EMBEDDING_MODEL,
            api_key=memory_settings.DASHSCOPE_API_KEY,
            base_url=memory_settings.DASHSCOPE_BASE_URL,
        )
    return _vectorizer
