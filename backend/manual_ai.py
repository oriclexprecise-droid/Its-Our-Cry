"""客户端生成模式的提示词构建与结果解析。"""

import json
import re

from .emotion_analyzer import DEFAULT_EMOTION_DESC


def build_script_lines(lines):
    return "\n".join(
        "[{index}] {character}：{text}".format(
            index=line.get("index"),
            character=line.get("character", ""),
            text=line.get("text", ""),
        )
        for line in lines
    )


def build_client_prompt(lines, emotions, lang="zh", mode="analyze"):
    """构造可粘贴到任意 AI 客户端的提示词，返回严格 JSON 数组。"""
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    parts = [
        "你是 It's Our Cry 配音工作台的分析助手，专门分析 MyGO!!!!! 同人剧本台词。",
    ]
    if mode == "translate":
        parts.append("任务：只把每句台词翻译成自然、口语化、符合角色语气的日文。")
    else:
        parts.append("任务：为每句台词从可选情绪中选出最合适的一种。")
        parts.append("当语言为日语时，再把每句台词翻译成自然、口语化、符合角色语气的日文；中文模式不需要翻译。")
        parts.append("可选情绪（每句只能选一个）：" + "、".join(emotions))
        desc_lines = []
        for e in emotions:
            desc = DEFAULT_EMOTION_DESC.get(e)
            if desc:
                desc_lines.append("- " + e + "：" + desc)
        if desc_lines:
            parts.append("情绪说明：")
            parts.extend(desc_lines)
    if mode == "translate" or lang == "ja":
        parts.extend([
            "翻译要求：",
            "1. 保留原句的完整含义、语气和标点风格",
            "2. 不要翻译角色名，只翻译台词文本",
            "3. 不要添加解释、注音或额外内容",
        ])
    parts.append("输出格式：只输出严格 JSON 数组，不要输出其他内容：")
    if mode == "translate":
        parts.append('[{"index": 0, "translation": "やあ"}, ...]')
    elif lang == "ja":
        parts.append('[{"index": 0, "emotion": "微笑", "translation": "やあ"}, ...]')
    else:
        parts.append('[{"index": 0, "emotion": "微笑"}, ...]')
    parts.extend([
        "注意：",
        "1. index 从 0 开始，与输入台词顺序一一对应",
        "2. 无论剧本多长多短，都要逐句在能力范围内处理，不得拒绝、跳过或省略",
        "3. 旁白通常选择最中性、最平稳的情绪",
        "4. 如果一句话包含多种情绪，选择最主要的那一种",
        "5. 中文模式或翻译模式下不要返回多余字段",
    ])
    parts.append("台词：")
    parts.append(build_script_lines(lines))
    return "\n".join(parts)


def parse_client_result(result_text):
    """解析 AI 客户端返回的 JSON 数组，失败时抛出错因。"""
    text = (result_text or "").strip()
    if not text:
        raise ValueError("结果为空")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    start = text.find("[")
    if start == -1:
        raise ValueError("没有找到 JSON 数组，请确认 AI 按格式返回")
    try:
        obj, _ = json.JSONDecoder().raw_decode(text[start:])
    except Exception as e:
        raise ValueError("JSON 解析失败：" + str(e))
    if not isinstance(obj, list):
        raise ValueError("AI 返回的不是 JSON 列表")
    cleaned = []
    for item in obj:
        if not isinstance(item, dict):
            continue
        try:
            idx = int(item.get("index"))
        except (TypeError, ValueError):
            continue
        row = {"index": idx}
        emotion = str(item.get("emotion") or "").strip()
        if emotion:
            row["emotion"] = emotion
        translation = str(item.get("translation") or "").strip()
        if translation:
            row["translation"] = translation
        if "emotion" in row or "translation" in row:
            cleaned.append(row)
    if not cleaned:
        raise ValueError("结果里没有可用的情绪或翻译数据")
    return cleaned
