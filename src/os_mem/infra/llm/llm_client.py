

from openai import OpenAI
from os_mem.configs.mem_settings import memory_settings

SYSTEM_PROMPT = """
你是一个信息提取助手。从以下对话中提取值得长期记忆的事实。

## 对话内容
{dialog_text}

## 提取规则
1. 每条事实独立成句，格式为 "用户 ..."
2. category 必须从以下列表中选取：
   personal, contact, preference, health, travel, work, finance, family, education, other
3. key 是字段名（如 'email', 'seat_preference', 'checking_account_number'）
4. 最多提取 10 条

## 输出格式（必须输出 JSON 对象，facts 为数组）
{
    "facts": [
        {
            "fact": "用户支票账户号码是 4429853327",
            "category": "finance",
            "key": "checking_account_number",
            "value": "4429853327",
            "confidence": 0.9
        }
    ]
}
"""

class LLMClient:
    def __init__(self):
        self.client = OpenAI(   
            api_key=memory_settings.DEEPSEEK_API_KEY,
            base_url=memory_settings.DEEPSEEK_BASE_URL,
        )

    def complete(self, dialog_text: str, retries: int = 3) -> str:
        """调用 DeepSeek 提取结构化事实。

        模型偶发返回空 content（deepseek-v4-flash 对长文本不稳定），
        空返回时等待后重试（指数等待 1.5s/3s/4.5s），仍空则返回 "" 交给上层。
        """
        import time

        for attempt in range(retries):
            resp = self.client.chat.completions.create(
                model=memory_settings.DEEPSEEK_MODEL,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": f"请从以下对话中提取结构化事实：\n\n{dialog_text}"}
                ],
                response_format={"type": "json_object"},
                temperature=memory_settings.DEEPSEEK_TEMPERATURE,
                max_tokens=memory_settings.DEEPSEEK_MAX_TOKENS,
                timeout=memory_settings.DEEPSEEK_TIMEOUT,
            )
            content = resp.choices[0].message.content
            if content:
                return content
            # 模型/API 偶发返回空 content（限流或模型不稳定），
            # 拉长退避时间等待恢复：3s / 6s / 9s
            if attempt < retries - 1:
                wait = 3.0 * (attempt + 1)
                time.sleep(wait)
        return ""


_llm_client = None
def get_llm_client():
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
