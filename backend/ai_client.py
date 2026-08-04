"""Shared DeepSeek/OpenAI client policy: max 2 total attempts, then stop."""

from openai import OpenAI

MAX_AI_ATTEMPTS = 2


def create_ai_client(api_key, base_url="https://api.deepseek.com", timeout=60.0):
    """Create a client that never auto-retries; retry policy is controlled here."""
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout, max_retries=0)
