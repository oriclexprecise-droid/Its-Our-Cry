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