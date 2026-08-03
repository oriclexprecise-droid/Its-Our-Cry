"""使用 DeepSeek API 分析每句台词的纯本地逻辑。"""

import json
import re
from openai import OpenAI


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
        "只输出 JSON 对象，键是情绪名，值是该情绪的参数字典，不要输出其他内容。",
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
        "严格要求：",
        "- 每个情绪都必须给出全部 5 个参数，值必须在范围内",
        "- 不同情绪至少要在 temperature 或 speed_factor 上体现出明显差异",
        "- 禁止所有情绪返回同一组参数，禁止原样返回默认值组合",
        "示例：",
        '{"哭泣": {"temperature": 0.6, "top_k": 8, "top_p": 0.8, "speed_factor": 0.9, "seed": -1}}',
    ]
    if lines:
        parts.append("以下为剧本片段（情绪已标注），请结合台词语气调整参数：")
        for line in lines[:60]:
            parts.append("- {character}（{emotion}）：{text}".format(
                character=line.get("character", ""),
                emotion=line.get("emotion", ""),
                text=(line.get("text") or "").replace("\n", " ")[:120],
            ))
    parts.append("情绪列表：" + "、".join(emotions))
    return "\n".join(parts)


def suggest_params(emotions, api_key, base_url="https://api.deepseek.com", model="deepseek-v4-flash", lines=None):
    """调用 DeepSeek API 为情绪列表推荐 SoVITS 合成参数。"""
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    if not emotions:
        return {}
    client = OpenAI(api_key=api_key, base_url=base_url)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": build_param_prompt(emotions, lines)},
            {"role": "user", "content": "请为这些情绪给出参数建议。"},
        ],
        temperature=0.3,
        max_tokens=1500,
    )
    content = (response.choices[0].message.content or "").strip()
    content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
    data = json.loads(content)
    if not isinstance(data, dict):
        raise ValueError("AI 返回的不是 JSON 对象")
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
):
    """调用 DeepSeek API 分析台词情绪。

    emotions 为当前可用的情绪列表；为空时使用系统默认情绪。
    """
    if not emotions:
        emotions = list(DEFAULT_EMOTIONS)
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    if not emotions:
        emotions = list(DEFAULT_EMOTIONS)

    client = OpenAI(api_key=api_key, base_url=base_url)

    user_prompt = build_analysis_prompt(lines)

    lang_hint = ""
    if lang == "ja":
        lang_hint = "\n注意：本剧本为日语，请根据日语的表达习惯来判断情绪。"
    elif lang == "auto":
        lang_hint = "\n注意：剧本可能混合中日文，请根据实际内容来判断。"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": build_system_prompt(emotions) + lang_hint},
            {"role": "user", "content": user_prompt},
        ],
        temperature=0.3,
        max_tokens=4096,
    )

    content = response.choices[0].message.content.strip()

    # 尝试解析 JSON（可能被包裹在代码块中）
    json_match = re.search(r"\[.*\]", content, re.DOTALL)
    if json_match:
        content = json_match.group(0)

    results = json.loads(content)
    if not isinstance(results, list):
        raise ValueError("AI 返回的不是列表格式: " + str(results)[:200])

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