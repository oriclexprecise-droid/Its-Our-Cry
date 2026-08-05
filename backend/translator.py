"""使用 DeepSeek API 将台词翻译成日语。"""

import json
from .ai_client import MAX_AI_ATTEMPTS, create_ai_client
from .ai_ops import estimate_tokens, estimate_tokens_from_usage, make_cache_key

BATCH_SIZE = 40

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

def _translate_batch(batch, client, model, name_readings=None, usage=None, prices=None, attempts=None):
    # 与客户端模式共用同一套提示词，保证角色名单、纠音参考与翻译要求一致
    from .manual_ai import build_client_prompt

    attempts = MAX_AI_ATTEMPTS if attempts is None else max(1, int(attempts))
    system_prompt = build_client_prompt(
        batch,
        [],
        lang="ja",
        mode="translate",
        name_readings=name_readings,
    )
    user_message = "请直接输出 JSON 数组，不要输出其他内容。"
    last_error = None
    for attempt in range(attempts):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
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
            if usage:
                in_tokens, out_tokens = estimate_tokens_from_usage(
                    getattr(response, "usage", None),
                    len(system_prompt) + len(user_message),
                    len(content),
                )
                usage.record(
                    "translate", model,
                    input_chars=len(system_prompt) + len(user_message),
                    output_chars=len(content),
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    prices=prices,
                )
            return _extract_json_array(content)
        except Exception as e:
            last_error = e
    raise RuntimeError("日语翻译调用已连续失败 " + str(attempts) + " 次，已停止调用 API: " + str(last_error))


def _retry_single_translate(lines, client, model, name_readings, results, failed_out, cache, usage, prices):
    """整批失败或批内缺失时，对失败句单独重试一次并收集仍失败的 index。"""
    for line in lines:
        if line.get("index") is None:
            continue
        single = [line]
        norm = [
            {"index": line.get("index"), "character": line.get("character"), "text": line.get("text")}
        ]
        key = make_cache_key("translate", model, "ja", [], name_readings, norm)
        if cache is not None:
            cached = cache.lookup(key)
            if cached is not None:
                results.extend(cached)
                if usage:
                    usage.record("translate", model, cache_hit=True, prices=prices)
                continue
        try:
            one = _translate_batch(single, client, model, name_readings, usage=usage, prices=prices, attempts=1)
            if cache is not None:
                cache.store(key, one)
            results.extend(one)
        except Exception:
            if failed_out is not None:
                failed_out.append(line.get("index"))

def translate_lines(
    lines: list[dict],
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-v4-flash",
    name_readings: list = None,
    cache=None,
    usage=None,
    failed_out=None,
    prices=None,
    batch_size: int = 40,
) -> list[dict]:
    """Translate script lines to Japanese via DeepSeek API (分批 + 缓存 + 失败句单独重试)."""
    if not lines:
        return []

    client = create_ai_client(api_key, base_url)
    raw_items = []
    for batch in _chunks(lines, batch_size):
        norm_items = [
            {"index": line.get("index"), "character": line.get("character"), "text": line.get("text")}
            for line in batch
        ]
        key = make_cache_key("translate", model, "ja", [], name_readings, norm_items)
        if cache is not None:
            cached = cache.lookup(key)
            if cached is not None:
                raw_items.extend(cached)
                if usage:
                    usage.record("translate", model, cache_hit=True, prices=prices)
                continue
        batch_results = None
        try:
            batch_results = _translate_batch(batch, client, model, name_readings, usage=usage, prices=prices)
        except Exception:
            batch_results = None
        if batch_results is not None:
            if cache is not None:
                cache.store(key, batch_results)
            raw_items.extend(batch_results)
            returned_idx = {r.get("index") for r in batch_results}
            missing = [line for line in batch if line.get("index") not in returned_idx]
            if missing:
                _retry_single_translate(missing, client, model, name_readings, raw_items, failed_out, cache, usage, prices)
        else:
            _retry_single_translate(batch, client, model, name_readings, raw_items, failed_out, cache, usage, prices)

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
