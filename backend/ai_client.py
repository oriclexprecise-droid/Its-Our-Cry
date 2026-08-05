"""Shared DeepSeek/OpenAI client policy: max 2 total attempts, then stop."""

from openai import OpenAI

MAX_AI_ATTEMPTS = 2
MAX_SINGLE_RETRIES_PER_RUN = 10


class EmptyAIResponseError(RuntimeError):
    """模型返回内容为空（输出额度可能被思考过程耗尽），不再重试，直接停止。"""


def create_ai_client(api_key, base_url="https://api.deepseek.com", timeout=60.0):
    """Create a client that never auto-retries; retry policy is controlled here."""
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
