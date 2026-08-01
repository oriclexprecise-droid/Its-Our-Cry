"""解析 MyGO 剧本格式的文本，提取角色-台词列表。"""

import re
from typing import Optional

# 匹配 "角色名：台词" 的行
LINE_PATTERN = re.compile(r"^(.+?)[：:]\s*(.+)$")


def parse_script(text: str) -> list[dict]:
    """
    解析剧本文本，返回结构化的台词列表。

    每行格式：角色名：台词
    空行被跳过，角色名和台词前后的空白会被去除。

    Returns:
        list of dict: [{"character": "千早爱音", "text": "大家好！", "index": 0}, ...]
    """
    lines_data = []
    raw_lines = text.strip().split("\n")

    for line in raw_lines:
        stripped = line.strip()
        if not stripped:
            continue  # 跳过空行

        match = LINE_PATTERN.match(stripped)
        if match:
            character = match.group(1).strip()
            dialogue = match.group(2).strip()
            lines_data.append({
                "character": character,
                "text": dialogue,
            })

    # 添加序号
    for i, item in enumerate(lines_data):
        item["index"] = i

    return lines_data


def validate_characters(lines: list[dict], valid_chars: set[str]) -> list[str]:
    """检查是否有未识别的角色名，返回警告列表。"""
    warnings = []
    for line in lines:
        if line["character"] not in valid_chars:
            warnings.append(
                f"第 {line['index'] + 1} 行: 未识别角色「{line['character']}」"
            )
    return warnings
