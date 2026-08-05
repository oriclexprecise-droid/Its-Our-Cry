"""解析 MyGO 剧本格式的文本，提取角色-台词列表，并检查角色名错误。"""

import re
from difflib import SequenceMatcher
from typing import Optional

# 匹配 "角色名：台词" 的行
LINE_PATTERN = re.compile(r"^(.+?)[：:]\s*(.+)$")
WRONG_SEPARATORS = [
    ("；", "；"),
    (";", ";"),
    ("，", "，"),
    (",", ","),
    ("、", "、"),
    ("|", "|"),
    ("　", "　"),
]


def describe_skip_reason(raw):
    """给出无法解析行的原因，帮助用户快速定位格式错误。"""
    text = str(raw or "").strip()
    if not text:
        return "空行"
    if text.startswith(("：", ":")):
        return "「：」前缺少角色名"
    if re.search(r"[：:]\s*$", text):
        return "「：」后缺少台词"
    for sep, _name in WRONG_SEPARATORS:
        if sep in text:
            return f"角色与台词之间使用了「{sep}」，应为「：」"
    return "未匹配到“角色：台词”格式（示例：千早爱音：大家好）"


def parse_script(text: str) -> list[dict]:
    """
    解析剧本文本，返回结构化的台词列表。

    每行格式：角色名：台词
    空行被跳过，角色名和台词前后的空白会被去除。

    Returns:
        list of dict: [{"character": "千早爱音", "text": "大家好！", "index": 0, "line_no": 1}, ...]
    """
    lines_data = []
    raw_lines = text.strip().split("\n")

    for line_no, raw_line in enumerate(raw_lines, start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue  # 跳过空行

        match = LINE_PATTERN.match(stripped)
        if match:
            character = match.group(1).strip()
            dialogue = match.group(2).strip()
            lines_data.append({
                "character": character,
                "text": dialogue,
                "line_no": line_no,
            })

    # 添加序号
    for i, item in enumerate(lines_data):
        item["index"] = i

    return lines_data


def _similarity(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a, b).ratio()


def suggest_character(name: str, valid_chars: list[str]) -> Optional[str]:
    """给出最接近的已知角色名；无法可靠判断时返回 None。"""
    if name in valid_chars:
        return name
    best = None
    best_score = 0.0
    for candidate in valid_chars:
        score = _similarity(name, candidate)
        # 子串包含也视为强相关，例如漏写了姓氏的情况
        if name in candidate or candidate in name:
            score = max(score, 0.72)
        if score > best_score:
            best_score = score
            best = candidate
    if best and best_score >= 0.5:
        return best
    return None


def find_character_issues(lines: list[dict], valid_chars: list[str]) -> list[dict]:
    """找出不存在的角色，返回校对建议列表。"""
    issues = []
    for line in lines:
        name = line["character"]
        if name in valid_chars:
            continue
        issues.append({
            "line_no": line.get("line_no", line.get("index", 0) + 1),
            "name": name,
            "suggestion": suggest_character(name, valid_chars),
        })
    return issues