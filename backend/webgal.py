"""WebGaL / anogo script parsing and audio-return rendering."""

import re


DEFAULT_SHORT_NAMES = {
    "千早爱音": "爱音",
    "长崎素世": "素世",
    "高松灯": "灯",
    "椎名立希": "立希",
    "要乐奈": "乐奈",
}

AUDIO_TOKEN_RE = re.compile(
    r"\s+-\s*([^\s;]+?\.(?:wav|mp3|flac|ogg|m4a|aac|wma|webm))(?=\s|$)",
    re.IGNORECASE,
)
ID_TOKEN_RE = re.compile(r"-id\b")
FIGURE_RE = re.compile(r"-figureId\s*=\s*([^\s;]+)")
COMMAND_RE = re.compile(
    r"^(changeBg|changeFigure|setTransform|changeBackground|playBGM|playSE|playVoice|end|label)\s*:",
    re.IGNORECASE,
)


def short_name_for(character, short_names=None):
    """角色 -> 音频目录短名（爱音/素世...），未知角色回退到原名。"""
    table = dict(DEFAULT_SHORT_NAMES)
    if short_names:
        table.update(short_names)
    return table.get(character) or character


def _split_dialogue(line):
    body = line.rstrip()
    if body.endswith(";"):
        body = body[:-1].rstrip()
    idx = body.find(":")
    if idx <= 0:
        return None
    character = body[:idx].strip()
    rest = body[idx + 1:].strip()
    if not character or not rest:
        return None
    return character, rest


def parse_script(text):
    """解析 anogo 脚本，返回逐行 entries。"""
    entries = []
    dialogue_index = 0
    for raw_line in (text or "").splitlines():
        raw = raw_line.rstrip()
        stripped = raw.strip()
        if not stripped:
            entries.append({"type": "blank", "raw": raw})
            continue
        if stripped.startswith(":"):
            entries.append({
                "type": "narrate",
                "raw": raw,
                "character": "",
                "text": stripped[1:].strip().rstrip(";").strip(),
            })
            continue
        if COMMAND_RE.match(stripped):
            entries.append({"type": "command", "raw": raw, "cmd": stripped.split(":", 1)[0]})
            continue
        parts = _split_dialogue(stripped)
        if parts is None:
            entries.append({"type": "other", "raw": raw})
            continue
        character, rest = parts
        existing_audio = ""
        audio_m = AUDIO_TOKEN_RE.search(rest)
        if audio_m:
            existing_audio = audio_m.group(1)
            rest = (rest[:audio_m.start()] + " " + rest[audio_m.end():]).strip()
        has_id = bool(ID_TOKEN_RE.search(rest))
        figure_m = FIGURE_RE.search(rest)
        figure_id = figure_m.group(1) if figure_m else ""
        text = ID_TOKEN_RE.sub("", rest)
        text = FIGURE_RE.sub("", text)
        text = text.strip().rstrip("-").strip()
        is_psy = bool(
            re.fullmatch(r"（.+）", text)
            or re.fullmatch(r"\(.+\)", text)
        )
        entries.append({
            "type": "dialogue",
            "raw": raw,
            "index": dialogue_index,
            "character": character,
            "text": text,
            "figure_id": figure_id,
            "has_id": has_id,
            "existing_audio": existing_audio,
            "is_psy": is_psy,
            "emotion": "",
            "voice_psy": False,
            "psy_character": "",
        })
        dialogue_index += 1
    return entries


def render_script(entries, audio_map):
    """按 audio_map 把音频引用插回对话行，其余行原样保留。"""
    out = []
    for e in entries:
        if e["type"] != "dialogue":
            out.append(e["raw"])
            continue
        audio = audio_map.get(e["index"])
        if not audio or e.get("existing_audio"):
            out.append(e["raw"])
            continue
        marker = "-" + audio
        if e["has_id"]:
            pos = e["raw"].find("-id")
            new_raw = e["raw"][:pos].rstrip() + " " + marker + " " + e["raw"][pos:].lstrip()
        elif e["raw"].rstrip().endswith(";"):
            new_raw = e["raw"].rstrip()[:-1].rstrip() + " " + marker + ";"
        else:
            new_raw = e["raw"].rstrip() + " " + marker
        out.append(new_raw)
    return "\n".join(out)


def dialogue_summary(entry):
    """前端表格需要的最小字段。"""
    return {
        "index": entry["index"],
        "character": entry["character"],
        "text": entry["text"],
        "figure_id": entry["figure_id"],
        "is_psy": entry["is_psy"],
        "has_id": entry["has_id"],
        "existing_audio": entry["existing_audio"],
        "emotion": entry.get("emotion", ""),
    }
