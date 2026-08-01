"""使用 DeepSeek API 分析每句台词的情绪。"""

import json
import re
from openai import OpenAI


EMOTION_CATEGORIES = [
    "生气", "告别", "哭泣", "感动", "决心",
    "悲伤", "认真", "害羞", "微笑", "惊讶", "思考",
]

SYSTEM_PROMPT = f"""你是一个情绪分析助手，专门分析 MyGO!!!!! 同人剧本中的台词情绪。

给定剧本台词列表（每行包含角色和台词），请你为每句台词标注一种最合适的情绪。

可选情绪类别（请只从下面选一个）：
{', '.join(EMOTION_CATEGORIES)}

情绪说明：
- 生气：愤怒、不满、指责
- 告别：道别、分离、离开
- 哭泣：流泪、悲痛、崩溃
- 感动：被触动、温暖、欣慰
- 决心：坚定、发誓、目标明确
- 悲伤：忧郁、低落、遗憾
- 认真：严肃、专注、正经
- 害羞：羞涩、不好意思、扭捏
- 微笑：开心、温暖笑容、愉悦
- 惊讶：震惊、意外、没想到
- 思考：思索、犹豫、自言自语式思考

请严格按以下 JSON 格式返回，不要返回其他内容：
[
  {{"index": 0, "emotion": "微笑"}},
  {{"index": 1, "emotion": "认真"}},
  ...
]

注意：
1. index 从 0 开始，与输入的台词顺序一致
2. 旁白的情绪通常是"思考"或"认真"
3. 如果一句话包含多种情绪，选择最主要的那一种
"""


def build_analysis_prompt(lines: list[dict]) -> str:
    """构建发给 AI 的分析 prompt。"""
    script_lines = []
    for line in lines:
        script_lines.append(f"[{line['index']}] {line['character']}：{line['text']}")
    return "\n".join(script_lines)


def analyze_emotions(
    lines: list[dict],
    api_key: str,
    base_url: str = "https://api.deepseek.com",
    model: str = "deepseek-v4-flash",
    lang: str = "zh",
) -> list[dict]:
    """
    调用 DeepSeek API 分析台词情绪。

    Args:
        lines: parse_script 的输出
        api_key: DeepSeek API key
        base_url: API 地址
        model: 模型名称
        lang: 剧本语言 (zh/ja/auto)

    Returns:
        list of dict: [{"index": 0, "emotion": "微笑"}, ...]
    """
    client = OpenAI(api_key=api_key, base_url=base_url)

    user_prompt = build_analysis_prompt(lines)

    # 根据语言调整 system prompt 提示
    lang_hint = ""
    if lang == "ja":
        lang_hint = "\n注意：本剧本为日语，请根据日语的表达习惯来判断情绪。"
    elif lang == "auto":
        lang_hint = "\n注意：剧本可能混合中日文，请根据实际内容来判断。"

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT + lang_hint},
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

    # 保证顺序并按 index 排序
    results.sort(key=lambda x: x.get("index", 0))

    # 验证情绪值
    for r in results:
        if r.get("emotion") not in EMOTION_CATEGORIES:
            r["emotion"] = "认真"  # fallback

    return results
