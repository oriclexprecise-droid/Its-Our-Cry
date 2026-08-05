"""使用 DeepSeek API 将台词翻译成日语。"""

import json
from .ai_client import MAX_AI_ATTEMPTS, create_ai_client


SYSTEM_PROMPT = """你是一个专业的中文到日语翻译助手，专门翻译 MyGO!!!!! 同人剧本中的台词。

请把每句台词翻译成自然、口语化、符合角色语气的日语。无论文本多长多短，都要在能力范围内完整翻译，不得省略、截断或拒绝。要求：
1. 保留原句的完整含义、语气和标点风格
2. 不要翻译角色名，只翻译台词文本
3. 不要添加解释、注音或额外内容
4. 严格按以下 JSON 格式返回，不要返回其他内容：
[
  {"index": 0, "translation": "おはよう"},
  {"index": 1, "translation": "..."}
]
"""

BATCH_SIZE = 40


def build_translate_prompt(lines: list[dict]) -> str:
    script_lines = []
    for line in lines:
        script_lines.append(f"[{line['index']}] {line['character']}：{line['text']}")
    return "\n".join(script_lines)


def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _extract_json_array(content):
    """兼容代码块、数组、对象包裹数组、单条对象四种返回。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("AI returned empty content")
    text = text.replace("```json", "").replace("```", "").strip()
    start = text.find("[")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            if isinstance(obj, list):
                return obj
        except Exception:
            pass
    try:
        obj = json.loads(text)
    except Exception as e:
        raise ValueError("AI returned no JSON array: " + str(e))
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("result", "translations", "data", "items", "lines"):
            if isinstance(obj.get(key), list):
                return obj[key]
        for v in obj.values():
            if isinstance(v, list):
                return v
        if "translation" in obj:
            return [obj]
    raise ValueError("AI returned no JSON array")


def _translate_batch(batch, client, model, name_readings=None):
    # 与客户端模式共用同一套提示词，保证角色名单、纠音参考与翻译要求一致
    from .manual_ai import build_client_prompt

    system_prompt = build_client_prompt(
        batch,
        [],
        lang="ja",
        mode="translate",
        name_readings=name_readings,
    )
    last_error = None
    for attempt in range(MAX_AI_ATTEMPTS):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": "请直接输出 JSON 数组，不要输出其他内容。"},
            ],
            "temperature": 0.4,
            "max_tokens": 4096,
        }
        # 第一次优先 JSON 模式，失败时第二次退回普通模式
        if attempt == 0:
            kwargs["response_format"] = {"type": "json_object"}
        try:
            response = client.chat.completions.create(**kwargs)
            content = (response.choices[0].message.content or "").strip()
            return _extract_json_array(content)
        except Exception as e:
            last_error = e
    raise RuntimeError("日语翻译调用已连续失败 2 次，已停止调用 API: " + str(last_error or "unknown error"))


def translate_lines(
    lines: list[dict],
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-v4-flash",
    name_readings: list = None,
) -> list[dict]:
    """Translate script lines to Japanese via DeepSeek API."""
    if not lines:
        return []

    client = create_ai_client(api_key, base_url)
    raw_items = []
    for batch in _chunks(lines, BATCH_SIZE):
        raw_items.extend(_translate_batch(batch, client, model, name_readings))

    cleaned = []
    for item in raw_items:
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
