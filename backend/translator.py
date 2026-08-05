"""使用 DeepSeek API 将台词翻译成日语。"""

import json
from concurrent.futures import ThreadPoolExecutor
from .ai_client import MAX_AI_ATTEMPTS, MAX_SINGLE_RETRIES_PER_RUN, EmptyAIResponseError, create_ai_client
from .ai_ops import estimate_tokens, estimate_tokens_from_usage, extract_json_array, extract_single_translation, extract_text_results, make_cache_key

BATCH_SIZE = 80

def _chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _is_soft_parse_error(exc):
    """只有 AI 返回内容解析失败才允许继续兜底，API/网络错误立即停止。"""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, ValueError):
            return True
        exc = exc.__cause__ if exc.__cause__ is not None else exc.__context__
    return False


def _extract_json_array(content):
    """优先解析纯文本行式结果，兼容旧 JSON 缓存返回。"""
    plain = extract_text_results(content)
    if plain:
        return plain
    return extract_json_array(content)

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
        output_format="text",
    )
    user_message = "请严格按上面的格式逐行输出，不要输出其他内容。"
    last_error = None
    for attempt in range(attempts):
        kwargs = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": 0.4,
            "max_tokens": 32768,
        }
        if model.startswith("deepseek-v4"):
            kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
        try:
            response = client.chat.completions.create(**kwargs)
            finish_reason = str(getattr(response.choices[0], "finish_reason", "") or "").lower()
            content = (response.choices[0].message.content or "").strip()
            if not content and finish_reason == "stop":
                content = str(getattr(response.choices[0].message, "reasoning_content", "") or "").strip()
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
            if not content:
                raise EmptyAIResponseError("模型返回内容为空，输出额度可能被思考过程耗尽，请检查模型设置或改用非思考模型")
            try:
                return _extract_json_array(content)
            except Exception as parse_err:
                print("[translate] parse failed: " + str(parse_err)[:120] + " | content head: " + content[:300].replace("\n", "\\n"))
                if len(batch) == 1:
                    return [{"index": batch[0].get("index"), "translation": extract_single_translation(content)}]
                raise
        except EmptyAIResponseError:
            raise
        except Exception as e:
            last_error = e
    raise RuntimeError("日语翻译调用已连续失败 " + str(attempts) + " 次，已停止调用 API: " + str(last_error)) from last_error


def _retry_single_translate(lines, client, model, name_readings, results, failed_out, cache, usage, prices, budget=None):
    """整批失败或批内缺失时，对失败句单独重试一次；budget 限制整个任务的总单句重试数。"""
    for i, line in enumerate(lines):
        if line.get("index") is None:
            continue
        if budget is not None and budget[0] <= 0:
            if failed_out is not None:
                failed_out.extend(l.get("index") for l in lines[i:] if l.get("index") is not None)
            break
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
        if budget is not None:
            budget[0] -= 1
        try:
            one = _translate_batch(single, client, model, name_readings, usage=usage, prices=prices, attempts=1)
            if cache is not None:
                cache.store(key, one)
            results.extend(one)
        except Exception:
            if failed_out is not None:
                failed_out.append(line.get("index"))

def _handle_translate_chunk_retry(chunk, client, model, name_readings, out, failed_out, cache, usage, prices, budget, depth):
    """批量结果缺行或解析失败时，先拆半重试，最后才逐句兜底。"""
    if len(chunk) > SPLIT_MIN_LINES and depth < MAX_BATCH_SPLIT_DEPTH:
        mid = len(chunk) // 2
        for part in (chunk[:mid], chunk[mid:]):
            out.extend(_process_translate_batch(part, client, model, name_readings, cache, usage, failed_out, prices, budget, _depth=depth + 1))
    else:
        out.extend(_process_translate_batch(chunk, client, model, name_readings, cache, usage, failed_out, prices, budget, _depth=depth, _final=True))


def _process_translate_batch(batch, client, model, name_readings, cache=None, usage=None, failed_out=None, prices=None, budget=None, _depth=0, _final=False):
    """处理一个批次：缓存命中 / 整批调用；解析类失败才做单句重试，API/网络错误整批标记失败。"""
    norm_items = [
        {"index": line.get("index"), "character": line.get("character"), "text": line.get("text")}
        for line in batch
    ]
    key = make_cache_key("translate", model, "ja", [], name_readings, norm_items)
    if cache is not None:
        cached = cache.lookup(key)
        if cached is not None:
            if usage:
                usage.record("translate", model, cache_hit=True, prices=prices)
            return list(cached)
    out = []
    batch_results = None
    batch_error = None
    try:
        batch_results = _translate_batch(batch, client, model, name_readings, usage=usage, prices=prices)
    except Exception as e:
        batch_results = None
        batch_error = e
    if batch_results is not None:
        out.extend(batch_results)
        returned_idx = {r.get("index") for r in batch_results}
        missing = [line for line in batch if line.get("index") not in returned_idx]
        if missing:
            if _final or _depth >= MAX_BATCH_SPLIT_DEPTH:
                _retry_single_translate(missing, client, model, name_readings, out, failed_out, cache, usage, prices, budget)
            else:
                _handle_translate_chunk_retry(missing, client, model, name_readings, out, failed_out, cache, usage, prices, budget, _depth)
        elif cache is not None:
            cache.store(key, batch_results)
    else:
        if _is_soft_parse_error(batch_error):
            if _final or _depth >= MAX_BATCH_SPLIT_DEPTH:
                _retry_single_translate(batch, client, model, name_readings, out, failed_out, cache, usage, prices, budget)
            else:
                _handle_translate_chunk_retry(batch, client, model, name_readings, out, failed_out, cache, usage, prices, budget, _depth)
        elif failed_out is not None:
            failed_out.extend(l.get("index") for l in batch if l.get("index") is not None)
    return out


ONE_SHOT_MAX_LINES = 120
MAX_BATCH_SPLIT_DEPTH = 2
SPLIT_MIN_LINES = 4


def _try_translate_one_shot(lines, client, model, name_readings, cache=None, usage=None, prices=None):
    """先整本一次翻译，失败只返回已解析部分，不做逐句兜底。"""
    norm_items = [
        {"index": line.get("index"), "character": line.get("character"), "text": line.get("text")}
        for line in lines
    ]
    key = make_cache_key("translate", model, "ja", [], name_readings, norm_items)
    if cache is not None:
        cached = cache.lookup(key)
        if cached is not None:
            if usage:
                usage.record("translate", model, cache_hit=True, prices=prices)
            return list(cached)
    try:
        out = _translate_batch(lines, client, model, name_readings, usage=usage, prices=prices)
    except Exception as e:
        if _is_soft_parse_error(e):
            return []
        raise
    if cache is not None and len(out) >= len(lines):
        cache.store(key, out)
    return out


def _run_translate_batches(batch_lines, client, model, name_readings, cache=None, usage=None, failed_out=None, prices=None, batch_size=80, budget=None):
    """兜底路径：按批处理（并行），带缓存与失败句单独重试（单句重试有总预算）。"""
    if budget is None:
        budget = [MAX_SINGLE_RETRIES_PER_RUN]
    batches = list(_chunks(batch_lines, batch_size))
    outputs = []
    if len(batches) > 1:
        workers = min(3, len(batches))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = [
                executor.submit(
                    _process_translate_batch,
                    b, client, model, name_readings,
                    cache, usage, failed_out, prices, budget,
                )
                for b in batches
            ]
            outputs = [f.result() for f in futures]
    else:
        for b in batches:
            outputs.append(
                _process_translate_batch(
                    b, client, model, name_readings,
                    cache, usage, failed_out, prices, budget,
                )
            )
    out = []
    for o in outputs:
        out.extend(o)
    return out


def translate_lines(
    lines: list[dict],
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-v4-pro",
    name_readings: list = None,
    cache=None,
    usage=None,
    failed_out=None,
    prices=None,
    batch_size: int = 80,
) -> list[dict]:
    """Translate script lines to Japanese via DeepSeek API (分批 + 缓存 + 失败句单独重试)."""
    if not lines:
        return []

    client = create_ai_client(api_key, base_url)
    raw_items = []
    hard_failed = False
    if len(lines) <= ONE_SHOT_MAX_LINES:
        try:
            raw_items.extend(_try_translate_one_shot(lines, client, model, name_readings, cache, usage, prices))
        except Exception:
            hard_failed = True
        if not hard_failed:
            covered = {r.get("index") for r in raw_items}
            missing = [line for line in lines if line.get("index") not in covered]
            if missing:
                raw_items.extend(_run_translate_batches(missing, client, model, name_readings, cache, usage, failed_out, prices, batch_size))
    else:
        raw_items.extend(_run_translate_batches(lines, client, model, name_readings, cache, usage, failed_out, prices, batch_size))
    if hard_failed and failed_out is not None:
        failed_out.extend(l.get("index") for l in lines if l.get("index") is not None)

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
