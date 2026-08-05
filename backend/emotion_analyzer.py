"""使用 DeepSeek API 分析每句台词的纯本地逻辑。"""

import json
import re
from .ai_client import MAX_AI_ATTEMPTS, create_ai_client


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


def build_system_prompt(emotions):
    """根据当前情绪列表动态生成 AI 分析准则。"""
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    lines = [
        "你是一个情绪分析助手，专门分析 MyGO!!!!! 同人剧本中的台词情绪。",
        "给定剧本台词列表（每行包含角色和台词），请你为每句台词标注一种最合适的情绪。",
        "无论剧本多长多短，都必须逐句在能力范围内判断情绪；文本过短或缺少上下文时按字面语气正常分析，不得拒绝处理或跳过。",
        "可选情绪类别（请只从下面选一个）：" + "、".join(emotions),
    ]
    desc_lines = []
    for e in emotions:
        desc = DEFAULT_EMOTION_DESC.get(e)
        if desc:
            desc_lines.append(f"- {e}：{desc}")
    if desc_lines:
        lines.append("情绪说明：")
        lines.extend(desc_lines)
    lines.extend([
        "请严格按以下 JSON 格式返回，不要返回其他内容：",
        "[",
        f'  {{"index": 0, "emotion": "{emotions[0] if emotions else "思考"}"}},',
        "  ...",
        "]",
        "注意：",
        "1. index 从 0 开始，与输入的台词顺序一致",
        "2. 旁白通常选择最中性、最平稳的情绪",
        "3. 如果一句话包含多种情绪，选择最主要的那一种",
    ])
    return "\n".join(lines)


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
    except Exception as e:
        raise ValueError("JSON 解析失败：" + str(e))
    if not isinstance(obj, dict):
        raise ValueError("AI 返回的不是 JSON 对象")
    return obj


def _extract_result_list(content):
    """兼容代码块、数组、对象包裹数组、单条对象四种返回。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("AI 返回内容为空")
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
        raise ValueError("AI 返回的不是列表格式: " + str(e))
    if isinstance(obj, list):
        return obj
    if isinstance(obj, dict):
        for key in ("result", "emotions", "data", "items", "lines"):
            if isinstance(obj.get(key), list):
                return obj[key]
        for v in obj.values():
            if isinstance(v, list):
                return v
        if "emotion" in obj:
            return [obj]
    raise ValueError("AI 返回的不是列表格式")


def suggest_params(emotions, api_key, base_url="https://api.deepseek.com", model="deepseek-v4-flash", lines=None):
    """调用 DeepSeek API 为情绪列表推荐 SoVITS 合成参数。"""
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    if not emotions:
        return {}
    client = create_ai_client(api_key, base_url)
    last_error = None
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": build_param_prompt(emotions, lines if attempt == 0 else None)},
                    {"role": "user", "content": "请为这些情绪给出参数建议。"},
                ],
                "temperature": 0.3,
                "max_tokens": 2500,
            }
            # 第一次优先用 JSON 模式，第二次退回普通模式
            if attempt == 0:
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
            content = (response.choices[0].message.content or "").strip()
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


def build_analysis_prompt(lines):
    """构建发给 AI 的分析 prompt。"""
    script_lines = []
    for line in lines:
        script_lines.append(f"[{line['index']}] {line['character']}：{line['text']}")
    return "\n".join(script_lines)


def analyze_emotions(
    lines,
    api_key,
    base_url="https://api.deepseek.com",
    model="deepseek-v4-flash",
    lang="zh",
    emotions=None,
    name_readings=None,
):
    """调用 DeepSeek API 分析台词情绪。

    emotions 为当前可用的情绪列表；为空时使用系统默认情绪。
    """
    if not emotions:
        emotions = list(DEFAULT_EMOTIONS)
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    if not emotions:
        emotions = list(DEFAULT_EMOTIONS)

    client = create_ai_client(api_key, base_url)

    # 与客户端模式共用同一套提示词，保证角色名单、纠音参考与翻译要求一致
    from .manual_ai import build_client_prompt

    system_prompt = build_client_prompt(
        lines,
        emotions,
        lang=lang,
        mode="analyze",
        name_readings=name_readings,
    )

    results = None
    last_error = None
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            kwargs = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": "请直接输出 JSON 数组，不要输出其他内容。"},
                ],
                "temperature": 0.3,
                "max_tokens": 4096,
            }
            # 第一次优先 JSON 模式，失败时第二次退回普通模式
            if attempt == 0:
                kwargs["response_format"] = {"type": "json_object"}
            response = client.chat.completions.create(**kwargs)
            content = (response.choices[0].message.content or "").strip()
            results = _extract_result_list(content)
            break
        except Exception as e:
            last_error = e
    if results is None:
        raise RuntimeError("情绪分析调用已连续失败 2 次，已停止调用 API: " + str(last_error))

    fallback = "思考" if "思考" in emotions else emotions[0]
    cleaned = []
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
        cleaned.append({"index": idx, "emotion": emotion})

    # 保证顺序并按 index 排序，重复 index 时保留最后一条
    cleaned.sort(key=lambda x: x["index"])
    return cleaned