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


DEFAULT_EMOTION_MAP = {
    "smile": "微笑",
    "serious": "认真",
    "thinking": "思考",
    "angry": "生气",
    "surprised": "惊讶",
    "shame": "害羞",
    "kandou": "感动",
    "kime": "决心",
    "idle": "思考",
    "cry": "哭泣",
}

EXPRESSION_RE = re.compile(r"-expression\s*=\s*([^\s;]+)", re.IGNORECASE)
MOTION_RE = re.compile(r"-motion\s*=\s*([^\s;]+)", re.IGNORECASE)
EMOTION_LINE_RE = re.compile(r"^(动作|表情)\s*[:：]\s*(.+)$")

def short_name_for(character, short_names=None):
    """角色 -> 音频目录短名（爱音/素世...），未知角色回退到原名。"""
    table = dict(DEFAULT_SHORT_NAMES)
    if short_names:
        table.update(short_names)
    return table.get(character) or character


def dir_component_error(name):
    """返回角色名作为目录/文件名的安全校验错误；安全时返回 None。"""
    text = str(name or "").strip()
    if not text:
        return "角色名为空"
    if text in (".", ".."):
        return "角色名不能是 . 或 .."
    for ch, label in (
        ("/", "斜杠 /"),
        ("\\", "反斜杠 \\"),
        (":", "冒号 :"),
        ("*", "星号 *"),
        ("?", "问号 ?"),
        ('"', "双引号"),
        ("<", "尖括号 <"),
        (">", "尖括号 >"),
        ("|", "竖线 |"),
    ):
        if ch in text:
            return f"角色名包含{label}"
    if re.search(r"[\x00-\x1f]", text):
        return "角色名包含无法显示的字符"
    if text.endswith(".") or text.endswith(" "):
        return "角色名不能以 . 或空格结尾"
    if len(text) > 64:
        return "角色名过长（最多 64 个字符）"
    base = text.split(".", 1)[0].strip()
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", base, re.IGNORECASE):
        return f"角色名「{text}」是 Windows 保留名"
    return None


def safe_dir_component(name):
    """生成可安全用作目录/文件名的角色名；无法自动修复时返回空字符串。"""
    text = str(name or "").strip()
    if not text:
        return ""
    if re.match(r"^[A-Za-z]:[\\/]", text):
        return ""
    text = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", text)
    text = text.rstrip(" .")
    if not text or text in (".", ".."):
        return ""
    if len(text) > 64:
        return ""
    base = text.split(".", 1)[0].strip()
    if re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$", base, re.IGNORECASE):
        return ""
    return text


def dialogue_name_issues(dialogues):
    """返回角色名不能安全用作目录时的逐行问题列表。"""
    issues = []
    for d in dialogues or []:
        name = str(d.get("character") or "").strip()
        reason = dir_component_error(name)
        if reason:
            issues.append({
                "index": d.get("index"),
                "name": name,
                "reason": reason,
                "suggested": safe_dir_component(name),
            })
    return issues


def _normalize_emotion_map(emotion_map):
    table = {}
    for k, v in (emotion_map or {}).items():
        key = str(k).strip().lower()
        val = str(v).strip()
        if key and val:
            table[key] = val
    return table


def _expression_stem(value):
    part = value.strip()
    if "/" in part:
        part = part.rsplit("/", 1)[-1].strip()
    part = re.sub(r"(_ingameV2.*|_e\d+.*)$", "", part, flags=re.IGNORECASE)
    while re.search(r"[_\d]+$", part):
        part = re.sub(r"[_\d]+$", "", part)
    return part.lower().strip("_")


def _resolve_emotion(value, table, system_emotions):
    raw = (value or "").strip()
    if not raw:
        return ""
    system = [str(e).strip() for e in (system_emotions or []) if str(e).strip()]
    candidates = [raw, raw.lower(), _expression_stem(raw)]
    for c in candidates:
        if c in system:
            return c
    for c in candidates:
        if c in table:
            return table[c]
    return ""


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


def parse_script(text, emotion_map=None, system_emotions=None):
    """解析 anogo 脚本，返回逐行 entries。"""
    entries = []
    dialogue_index = 0
    table = _normalize_emotion_map(emotion_map if emotion_map is not None else DEFAULT_EMOTION_MAP)
    pending_emotion = ""
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
        emotion_line = EMOTION_LINE_RE.match(stripped)
        if emotion_line:
            pending_emotion = _resolve_emotion(emotion_line.group(2), table, system_emotions)
            entries.append({"type": "command", "raw": raw, "cmd": emotion_line.group(1)})
            continue
        if COMMAND_RE.match(stripped):
            expr_m = EXPRESSION_RE.search(stripped)
            mot_m = MOTION_RE.search(stripped)
            if expr_m:
                pending_emotion = _resolve_emotion(expr_m.group(1), table, system_emotions)
            elif mot_m:
                pending_emotion = _resolve_emotion(mot_m.group(1), table, system_emotions)
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
            "emotion": pending_emotion,
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
