"""客户端生成模式的提示词构建与结果解析。"""

import json
import math
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


def _collect_characters(lines, characters=None):
    chars = []
    for c in characters or []:
        s = str(c).strip()
        if s and s not in chars:
            chars.append(s)
    if not chars:
        for line in lines:
            c = str(line.get("character") or "").strip()
            if c and c not in chars:
                chars.append(c)
    if "旁白" not in chars:
        chars.append("旁白")
    return chars


def _collect_name_readings(chars, lines, name_readings=None):
    dialogue = "".join(str(l.get("text") or "") for l in lines)
    readings = []
    for r in name_readings or []:
        zh = str(r.get("zh") or "").strip()
        ja = str(r.get("ja") or "").strip()
        if not zh or not ja:
            continue
        if any(zh == c or zh in c for c in chars) or zh in dialogue:
            readings.append((zh, ja))
    return readings


def _prompt_parts(lines, emotions, lang="zh", mode="analyze", characters=None, name_readings=None):
    """构建提示词公共部分（不含台词列表），供整本与分段共用。"""
    emotions = [str(e).strip() for e in emotions if str(e).strip()]
    chars = _collect_characters(lines, characters)
    readings = _collect_name_readings(chars, lines, name_readings)
    parts = [
        "你是 It's Our Cry 配音工作台的分析助手，专门分析 MyGO!!!!! 同人剧本台词。",
    ]
    if mode == "translate":
        parts.append("任务：只把每句台词翻译成自然、口语化、符合角色语气的日文，不要分析情绪，不要返回 emotion 字段。")
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
    if chars:
        parts.append("角色名单（这些是专有名词，不得意译、不得拆改，也不能按普通词翻译）：")
        parts.append("、".join(chars))
    if mode == "translate" or lang == "ja":
        if readings:
            parts.append("特殊词读音参考（译文里出现这些词时，必须替换成右侧日文写法，不得保留原词或改用其他音译）：")
            parts.append("；".join(f"{zh} → {ja}" for zh, ja in readings))
        parts.extend([
            "翻译要求：",
            "1. 保留原句的完整含义、语气和标点风格",
            "2. 严格按上面的角色名单识别角色名，一律保留，不得意译、不得拆改",
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
    return parts


def build_client_prompt(lines, emotions, lang="zh", mode="analyze", characters=None, name_readings=None):
    """构造可粘贴到任意 AI 客户端的整本提示词，返回严格 JSON 数组。"""
    parts = _prompt_parts(lines, emotions, lang=lang, mode=mode, characters=characters, name_readings=name_readings)
    parts.append("台词：")
    parts.append(build_script_lines(lines))
    return "\n".join(parts)


def build_client_prompt_segments(lines, emotions, lang="zh", mode="analyze", characters=None, name_readings=None, segment_size=60):
    """把长剧本按行切段（在台词边界截断），每段一个提示词；最后一段让 AI 汇总完整 JSON。"""
    lines = list(lines or [])
    if not lines:
        return []
    total = max(1, math.ceil(len(lines) / max(1, int(segment_size))))
    segments = []
    for i in range(0, len(lines), max(1, int(segment_size))):
        chunk = lines[i:i + max(1, int(segment_size))]
        seq = i // max(1, int(segment_size)) + 1
        parts = _prompt_parts(chunk, emotions, lang=lang, mode=mode, characters=characters, name_readings=name_readings)
        parts.insert(0, "【第 " + str(seq) + " 段 / 共 " + str(total) + " 段】这是长剧本的分段提示词，请在同一对话里按顺序处理，index 保持原始编号不变。")
        parts.append("台词：")
        parts.append(build_script_lines(chunk))
        if seq < total:
            parts.extend(["", "本段处理完请先保留结果，不要输出最终 JSON，等我发送下一段。"])
        else:
            parts.extend([
                "",
                "【最后一段】请先处理本段，然后把前面所有段与本次结果合并成一份完整的 JSON 数组返回。",
                "要求：包含全部台词，index 保持原始编号不变，按 index 升序排列，不得遗漏、不得重复，只输出这份 JSON。",
            ])
        segments.append({"seq": seq, "total": total, "prompt": "\n".join(parts)})
    return segments


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
