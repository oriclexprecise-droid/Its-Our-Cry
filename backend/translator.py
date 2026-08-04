"""使用 DeepSeek API 将台词翻译成日语。"""

import json
import re
from openai import OpenAI


SYSTEM_PROMPT = """你是一个专业的中文到日语翻译助手，专门翻译 MyGO!!!!! 同人剧本中的台词。

请把每句台词翻译成自然、口语化、符合角色语气的日语。要求：
1. 保留原句的完整含义、语气和标点风格
2. 不要翻译角色名，只翻译台词文本
3. 不要添加解释、注音或额外内容
4. 严格按以下 JSON 格式返回，不要返回其他内容：
[
  {"index": 0, "translation": "おはよう"},
  {"index": 1, "translation": "..."}
]
"""


def build_translate_prompt(lines: list[dict]) -> str:
    script_lines = []
    for line in lines:
        script_lines.append(f"[{line['index']}] {line['character']}：{line['text']}")
    return "\n".join(script_lines)


def _extract_json_array(content):
    start = content.find("[")
    if start == -1:
        raise ValueError("AI returned no JSON array")
    obj, _ = json.JSONDecoder().raw_decode(content[start:])
    if not isinstance(obj, list):
        raise ValueError("AI did not return a JSON list")
    return obj


def translate_lines(
    lines: list[dict],
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-v4-flash",
) -> list[dict]:
    """Translate script lines to Japanese via DeepSeek API."""
    if not lines:
        return []

    client = OpenAI(api_key=api_key, base_url=base_url, timeout=60.0, max_retries=1)
    user_prompt = build_translate_prompt(lines)

    results = None
    last_error = None
    for attempt in range(2):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=0.4,
                max_tokens=4096,
            )
            content = (response.choices[0].message.content or "").strip()
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE).strip()
            results = _extract_json_array(content)
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_error = e
    if results is None:
        raise ValueError("AI returned empty or invalid JSON: " + str(last_error or "unknown error"))

    cleaned = []
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        translation = str(item.get("translation") or "").strip()
        if translation:
            cleaned.append({"index": idx, "translation": translation})

    # 保证顺序并按 index 排序，重复 index 时保留最后一条
    cleaned.sort(key=lambda x: x["index"])
    return cleaned
