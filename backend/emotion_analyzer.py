"""使用 DeepSeek API 分析每句台词的纯本地逻辑。"""

import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor
from .ai_client import MAX_AI_ATTEMPTS, MAX_SINGLE_RETRIES_PER_RUN, EmptyAIResponseError, create_ai_client
from .ai_ops import estimate_tokens, estimate_tokens_from_usage, extract_json_array, make_cache_key

DEFAULT_EMOTIONS = [
    "生气", "告别", "哭泣", "感动", "决心",
    "悲伤", "认真", "害羞", "微笑", "惊讶", "思考",
]

DEFAULT_EMOTION_DESC = {
    "生气": "愤怒、不满、指责",
    "告别": "道别、分离、离开",
    "哭泣": "流泪、悲痛、崩溃",
    "感动": "被触动、温暖、欣慰",
    "决心": "坚定、发誓、目标明确",
    "悲伤": "忧郁、低落、遗憾",
    "认真": "严肃、专注、正经",
    "害羞": "羞涩、不好意思、扭捏",
    "微笑": "开心、温暖笑容、愉悦",
    "惊讶": "震惊、意外、没想到",
    "思考": "思索、犹豫、自言自语式思考",
}

PARAM_KEYS = ("temperature", "top_k", "top_p", "speed_factor")

GENERIC_DEFAULTS = {
    "temperature": 1.0,
    "top_k": 15,
    "top_p": 1.0,
    "speed_factor": 1.0,
    "seed": -1,
}

DEFAULT_EMOTION_PARAMS = {
    "生气": {"temperature": 1.05, "top_k": 20, "top_p": 0.95, "speed_factor": 1.1, "seed": -1},
    "告别": {"temperature": 0.8, "top_k": 12, "top_p": 0.9, "speed_factor": 0.95, "seed": -1},
    "哭泣": {"temperature": 0.6, "top_k": 8, "top_p": 0.8, "speed_factor": 0.85, "seed": -1},
    "感动": {"temperature": 0.75, "top_k": 12, "top_p": 0.9, "speed_factor": 0.9, "seed": -1},
    "决心": {"temperature": 0.85, "top_k": 15, "top_p": 0.92, "speed_factor": 1.0, "seed": -1},
    "悲伤": {"temperature": 0.65, "top_k": 10, "top_p": 0.85, "speed_factor": 0.88, "seed": -1},
    "认真": {"temperature": 0.7, "top_k": 10, "top_p": 0.85, "speed_factor": 0.95, "seed": -1},
    "害羞": {"temperature": 0.7, "top_k": 9, "top_p": 0.85, "speed_factor": 0.9, "seed": -1},
    "微笑": {"temperature": 0.85, "top_k": 14, "top_p": 0.92, "speed_factor": 1.0, "seed": -1},
    "惊讶": {"temperature": 1.1, "top_k": 25, "top_p": 0.95, "speed_factor": 1.15, "seed": -1},
    "思考": {"temperature": 0.75, "top_k": 12, "top_p": 0.88, "speed_factor": 0.95, "seed": -1},
}

_FALLBACK_PARAMS = {"temperature": 0.8, "top_k": 12, "top_p": 0.9, "speed_factor": 1.0, "seed": -1}

def _merge_param_suggestion(name, ai_params):
    """Curated base overlaid only by AI values that differ from generic defaults."""
    base = dict(DEFAULT_EMOTION_PARAMS.get(name) or _FALLBACK_PARAMS)
    for key in base:
        v = ai_params.get(key)
        if v is not None and v != GENERIC_DEFAULTS.get(key):
            base[key] = v
    return base

def build_param_prompt(emotions, lines=None):
    """构建让 AI 推荐 SoVITS 情绪参数的提示词。"""
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    parts = [
        "你是一个 GPT-SoVITS 语音合成参数调优助手，熟悉文字冒险/二创配音场景。",
        "请为以下每种情绪推荐一组语音合成参数，让语气更贴合情绪。",
        "无论是否有剧本文本、文本多长多短，都必须在能力范围内给出建议：文本为空时按该情绪典型语气给出合理参数；文本过长时按已有片段概括判断，不得拒绝、跳过或返回空结果。",
        "只输出一个 JSON 对象，键必须严格等于下方情绪列表中的情绪名，值是该情绪的参数字典；不得新增、遗漏、合并或改用同义词。不要输出其他内容。",
        "可调参数及范围：",
        "- temperature：0.1-1.5，越大表现力越强但越不稳定",
        "- top_k：1-50，越小发音越稳定",
        "- top_p：0.1-1.0，概率截断阈值",
        "- speed_factor：0.5-1.5，1.0 为正常语速",
        "- seed：固定随机种子，-1 表示随机；对比试听时可给固定值",
        "默认值：temperature=1、top_k=15、top_p=1、speed_factor=1、seed=-1",
        "调参原则：",
        "- 哭泣/悲伤/害羞：语速略慢（0.85-0.95），温度偏低（0.5-0.7），top_k 偏小保持稳定",
        "- 生气/惊讶：语速略快（1.05-1.15），温度稍高（0.9-1.1）",
        "- 微笑/决心/认真：正常偏稳，温度 0.7-0.9，top_k 10-20",
        "- 告别/感动/思考：中速、稳定，温度 0.7-0.9",
        "- 情绪列表中未列出具体原则的情绪，按该情绪的字面语义合理推断语气强度，不得偷懒返回默认值或通用值",
        "严格要求：",
        "- 每个情绪都必须给出全部 5 个参数，值必须在范围内",
        "- 每个情绪都必须与默认值组合有实质差异：至少 temperature、speed_factor 中有一项明显偏离默认值",
        "- 不同情绪至少要在 temperature 或 speed_factor 上体现出明显差异",
        "- 禁止所有情绪返回同一组参数，禁止原样返回默认值组合",
        "示例：",
        '{"哭泣": {"temperature": 0.6, "top_k": 8, "top_p": 0.8, "speed_factor": 0.9, "seed": -1}}',
    ]
    if lines:
        parts.append("以下为剧本片段（情绪已标注），请逐条阅读并判断每种情绪在本剧本中的实际语气强度，再据此调整参数；片段中未出现的情绪按典型语气给出合理参数：")
        for line in lines[:60]:
            parts.append("- {character}（{emotion}）：{text}".format(
                character=line.get("character", ""),
                emotion=line.get("emotion", ""),
                text=(line.get("text") or "").replace("\n", " ")[:120],
            ))
    parts.append("情绪列表：" + "、".join(emotions))
    return "\n".join(parts)

def _params_are_degenerate(data, emotions):
    """AI 偷懒检测：缺失情绪、全部相同或全部等于默认值。"""
    names = [str(e).strip() for e in emotions if str(e).strip()]
    missing = [n for n in names if n not in data or not isinstance(data.get(n), dict)]
    if missing:
        return "缺少情绪：" + "、".join(missing)
    combos = set()
    all_default = True
    for n in names:
        p = data[n]
        temperature = _param_float(p.get("temperature"), 0.1, 1.5, 1.0)
        top_k = _param_int(p.get("top_k"), 1, 50, 15)
        top_p = _param_float(p.get("top_p"), 0.1, 1.0, 1.0)
        speed_factor = _param_float(p.get("speed_factor"), 0.5, 1.5, 1.0)
        combos.add((round(temperature, 3), round(speed_factor, 3)))
        if (
            abs(temperature - GENERIC_DEFAULTS["temperature"]) > 0.001
            or top_k != GENERIC_DEFAULTS["top_k"]
            or abs(top_p - GENERIC_DEFAULTS["top_p"]) > 0.001
            or abs(speed_factor - GENERIC_DEFAULTS["speed_factor"]) > 0.001
        ):
            all_default = False
    if all_default:
        return "所有情绪都等于默认参数组合"
    if len(combos) < 2:
        return "所有情绪的 temperature/speed_factor 完全相同"
    return None

def _extract_json_object(content):
    """从 AI 返回文本中提取第一个 JSON 对象，容忍代码块和前后多余文字。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("AI 返回内容为空，没有可解析的 JSON")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("{")
    if start == -1:
        raise ValueError("AI 返回内容中没有 JSON 对象")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except Exception:
        try:
            obj = ast.literal_eval(text[start:])
        except Exception as e:
            raise ValueError("JSON 解析失败：" + str(e))
    if not isinstance(obj, dict):
        raise ValueError("AI 返回的不是 JSON 对象")
    return obj

def _extract_result_list(content):
    """兼容代码块、数组、对象包裹数组、单条对象四种返回。"""
    return extract_json_array(content)

def suggest_params(emotions, api_key, base_url="https://api.deepseek.com", model="deepseek-v4-flash", lines=None, cache=None, usage=None, prices=None):
    """调用 DeepSeek API 为情绪列表推荐 SoVITS 合成参数（带缓存与用量统计）。"""
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    if not emotions:
        return {}
    line_items = [
        {"index": l.get("index"), "character": l.get("character"), "emotion": l.get("emotion"), "text": l.get("text")}
        for l in (lines or [])[:60]
    ]
    key = make_cache_key("params", model, "zh", emotions, [], line_items)
    if cache is not None:
        cached = cache.lookup(key)
        if cached is not None:
            if usage:
                usage.record("params", model, cache_hit=True, prices=prices)
            return cached
    client = create_ai_client(api_key, base_url)
    last_error = None
    user_message = "请为这些情绪给出参数建议。"
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": build_param_prompt(emotions, lines if attempt == 0 else None)},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 2500,
            }
            # 第一次优先用 JSON 模式，第二次退回普通模式
            if attempt == 0:
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
            content = (response.choices[0].message.content or "").strip()
            if usage:
                system_len = len(kwargs["messages"][0]["content"])
                in_tokens, out_tokens = estimate_tokens_from_usage(
                    getattr(response, "usage", None),
                    system_len + len(user_message),
                    len(content),
                )
                usage.record(
                    "params", model,
                    input_chars=system_len + len(user_message),
                    output_chars=len(content),
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    prices=prices,
                )
            if not content:
                print("[suggest_params] API 返回内容为空 finish_reason=" + str(response.choices[0].finish_reason))
            data = _extract_json_object(content)
            issue = _params_are_degenerate(data, emotions)
            if issue:
                raise ValueError("AI 返回参数不合格（" + issue + "）")
            cleaned = {}
            for name, p in data.items():
                if not isinstance(p, dict):
                    continue
                cleaned[str(name).strip()] = _merge_param_suggestion(str(name).strip(), {
                    "temperature": _param_float(p.get("temperature"), 0.1, 1.5, 1.0),
                    "top_k": _param_int(p.get("top_k"), 1, 50, 15),
                    "top_p": _param_float(p.get("top_p"), 0.1, 1.0, 1.0),
                    "speed_factor": _param_float(p.get("speed_factor"), 0.5, 1.5, 1.0),
                    "seed": _param_int(p.get("seed"), -1, 2147483647, -1),
                })
            if cache is not None:
                cache.store(key, cleaned)
            return cleaned
        except Exception as e:
            last_error = e
    raise RuntimeError("参数建议调用已连续失败 2 次，已停止调用 API: " + str(last_error))

def _param_float(value, lo, hi, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))

def _param_int(value, lo, hi, default):
    try:
        v = int(float(value))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, v))

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


def _analyze_batch(batch, client, model, lang, emotions, name_readings, usage=None, prices=None, attempts=None):
    """分析一个批次；attempts 默认沿用 2 次策略，单句重试时传 1。"""
    from .manual_ai import build_client_prompt

    attempts = MAX_AI_ATTEMPTS if attempts is None else max(1, int(attempts))
    system_prompt = build_client_prompt(
        batch,
        emotions,
        lang=lang,
        mode="analyze",
        name_readings=name_readings,
    )
    user_message = "请直接输出 JSON 数组，不要输出其他内容。"
    last_error = None
    for attempt in range(attempts):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 8192,
            }
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
                    "analyze", model,
                    input_chars=len(system_prompt) + len(user_message),
                    output_chars=len(content),
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    prices=prices,
                )
            if not content:
                raise EmptyAIResponseError("模型返回内容为空，输出额度可能被思考过程耗尽，请检查模型设置或改用非思考模型")
            return _extract_result_list(content)
        except EmptyAIResponseError:
            raise
        except Exception as e:
            last_error = e
    raise RuntimeError("情绪分析调用已连续失败 " + str(attempts) + " 次，已停止调用 API: " + str(last_error)) from last_error


def _retry_single_lines(lines, client, model, lang, emotions, name_readings, results, failed_out, cache, usage, prices, budget=None):
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
        key = make_cache_key("analyze", model, lang, emotions, name_readings, norm)
        if cache is not None:
            cached = cache.lookup(key)
            if cached is not None:
                results.extend(cached)
                if usage:
                    usage.record("analyze", model, cache_hit=True, prices=prices)
                continue
        if budget is not None:
            budget[0] -= 1
        try:
            one = _analyze_batch(single, client, model, lang, emotions, name_readings, usage=usage, prices=prices, attempts=1)
            if cache is not None:
                cache.store(key, one)
            results.extend(one)
        except Exception:
            if failed_out is not None:
                failed_out.append(line.get("index"))


def _process_analyze_batch(batch, client, model, lang, emotions, name_readings, cache=None, usage=None, failed_out=None, prices=None, budget=None):
    """处理一个批次：缓存命中 / 整批调用；解析类失败才做单句重试，API/网络错误整批标记失败。"""
    norm_items = [
        {"index": line.get("index"), "character": line.get("character"), "text": line.get("text")}
        for line in batch
    ]
    key = make_cache_key("analyze", model, lang, emotions, name_readings, norm_items)
    if cache is not None:
        cached = cache.lookup(key)
        if cached is not None:
            if usage:
                usage.record("analyze", model, cache_hit=True, prices=prices)
            return list(cached)
    out = []
    batch_results = None
    batch_error = None
    try:
        batch_results = _analyze_batch(batch, client, model, lang, emotions, name_readings, usage=usage, prices=prices)
    except Exception as e:
        batch_results = None
        batch_error = e
    if batch_results is not None:
        if cache is not None:
            cache.store(key, batch_results)
        out.extend(batch_results)
        returned_idx = {r.get("index") for r in batch_results}
        missing = [line for line in batch if line.get("index") not in returned_idx]
        if missing:
            _retry_single_lines(missing, client, model, lang, emotions, name_readings, out, failed_out, cache, usage, prices, budget)
    else:
        if _is_soft_parse_error(batch_error):
            _retry_single_lines(batch, client, model, lang, emotions, name_readings, out, failed_out, cache, usage, prices, budget)
        elif failed_out is not None:
            failed_out.extend(l.get("index") for l in batch if l.get("index") is not None)
    return out


ONE_SHOT_MAX_LINES = 500


def _try_analyze_one_shot(lines, client, model, lang, emotions, name_readings, cache=None, usage=None, prices=None):
    """先按客户端习惯整本一次调用；失败只返回已解析部分，不做逐句兜底。"""
    norm_items = [
        {"index": line.get("index"), "character": line.get("character"), "text": line.get("text")}
        for line in lines
    ]
    key = make_cache_key("analyze", model, lang, emotions, name_readings, norm_items)
    if cache is not None:
        cached = cache.lookup(key)
        if cached is not None:
            if usage:
                usage.record("analyze", model, cache_hit=True, prices=prices)
            return list(cached)
    try:
        out = _analyze_batch(lines, client, model, lang, emotions, name_readings, usage=usage, prices=prices)
    except Exception as e:
        if _is_soft_parse_error(e):
            return []
        raise
    if cache is not None:
        cache.store(key, out)
    return out


def _run_analyze_batches(batch_lines, client, model, lang, emotions, name_readings, cache=None, usage=None, failed_out=None, prices=None, batch_size=100, budget=None):
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
                    _process_analyze_batch,
                    b, client, model, lang, emotions, name_readings,
                    cache, usage, failed_out, prices, budget,
                )
                for b in batches
            ]
            outputs = [f.result() for f in futures]
    else:
        for b in batches:
            outputs.append(
                _process_analyze_batch(
                    b, client, model, lang, emotions, name_readings,
                    cache, usage, failed_out, prices, budget,
                )
            )
    out = []
    for o in outputs:
        out.extend(o)
    return out


def analyze_emotions(
    lines,
    api_key,
    base_url="https://api.deepseek.com",
    model="deepseek-v4-flash",
    lang="zh",
    emotions=None,
    name_readings=None,
    cache=None,
    usage=None,
    failed_out=None,
    prices=None,
    batch_size=100,
):
    """调用 DeepSeek API 分析台词情绪（分批 + 缓存 + 失败句单独重试）。

    cache/usage/failed_out 由调用方传入；failed_out 会收集最终仍失败的 index。
    """
    if not emotions:
        emotions = list(DEFAULT_EMOTIONS)
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    if not emotions:
        emotions = list(DEFAULT_EMOTIONS)

    client = create_ai_client(api_key, base_url)
    results = []
    hard_failed = False
    if len(lines) <= ONE_SHOT_MAX_LINES:
        try:
            results.extend(_try_analyze_one_shot(lines, client, model, lang, emotions, name_readings, cache, usage, prices))
        except Exception:
            hard_failed = True
        if not hard_failed:
            covered = {r.get("index") for r in results}
            missing = [line for line in lines if line.get("index") not in covered]
            if missing:
                results.extend(_run_analyze_batches(missing, client, model, lang, emotions, name_readings, cache, usage, failed_out, prices, batch_size))
    else:
        results.extend(_run_analyze_batches(lines, client, model, lang, emotions, name_readings, cache, usage, failed_out, prices, batch_size))
    if hard_failed and failed_out is not None:
        failed_out.extend(l.get("index") for l in lines if l.get("index") is not None)

    fallback = "思考" if "思考" in emotions else emotions[0]
    seen = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        emotion = item.get("emotion")
        if emotion not in emotions:
            emotion = fallback
        seen[idx] = {"index": idx, "emotion": emotion}
    cleaned = sorted(seen.values(), key=lambda x: x["index"])
    return cleaned
