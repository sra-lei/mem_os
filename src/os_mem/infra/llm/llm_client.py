

from openai import OpenAI
from os_mem.configs.mem_settings import memory_settings

SYSTEM_PROMPT = """
你是一个信息提取助手。从以下对话中提取值得长期记忆的事实。

## 提取标准（什么值得提取）
只提取**用户明确陈述的、持久的、对未来交互有价值**的信息，例如：
- 身份与联系方式：姓名、生日、地址、电话、邮箱
- 账户/财务：账号、卡号、路由号、余额、转账设置（如"用户支票账户号码是 4429853327"）
- 偏好：座位、饮食、沟通方式、旅行习惯
- 健康、工作、家庭、教育等长期事实

**不要提取**：
- 客服客套话（"好的，我记下了"、"还有什么需要帮助吗"）
- 瞬时/无长期价值的信息（"今天天气不错"、临时决定）
- 与用户无关的信息

## 提取规则
1. 每条事实独立成句，格式为 "用户 ..."
2. category 必须从以下列表中选取：
   personal, contact, preference, health, travel, work, finance, family, education, other
3. key 是字段名（如 'email', 'seat_preference', 'checking_account_number'）
4. 按上述标准尽量提取（宁多勿漏），不要遗漏关键信息；最多 {max_facts} 条（防失控保险）
5. 金额、编号、日期、时间、百分比、账号、余额、号码等**精确值必须原样保留**
   （含 $、千分位逗号、小数、连字符格式），不得省略、改写、四舍五入或合并进其他条目。
   对话中**新产生/变更的精确信息**（如刚分配的理赔编号、刚确认的预约时间、
   刚计算的退款金额、刚报价的总价与分期金额、刚告知的学费单价）与既有资料同等重要，
   必须逐条提取，例如：
   - "用户本次理赔编号是 CLM-2024-894327"
   - "理赔专员 Patricia Wong 将在 24-48 小时内来电"
   - "原始 24 次课程套餐价格为 $2,400"
   - "退款金额 $1,600，扣除 20% 行政费 $320，净退款 $1,280"
   - "每周学费 $617.50（Emma $325 + Olivia $292.50）"

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
    def __init__(self) -> None:
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
                    {"role": "system", "content": SYSTEM_PROMPT.replace(
                        "{max_facts}", str(memory_settings.DEEPSEEK_EXTRACT_MAX_FACTS),
                    )},
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
def get_llm_client() -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient()
    return _llm_client
