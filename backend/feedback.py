"""本地反馈日志：记录用户修正等事件，供导出后优化 AI 使用。"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

VERSION = "1.0.0"
FEEDBACK_DIR_NAME = "feedback"


def _feedback_dir(project_root: Optional[Path] = None) -> Path:
    base = project_root or Path(__file__).resolve().parent.parent
    return base / FEEDBACK_DIR_NAME


def get_anon_id(project_root: Optional[Path] = None) -> str:
    """生成或读取持久化的匿名 ID，不包含任何用户身份信息。"""
    fdir = _feedback_dir(project_root)
    fdir.mkdir(parents=True, exist_ok=True)
    id_file = fdir / "anon_id.txt"
    if id_file.exists():
        return id_file.read_text(encoding="utf-8").strip()
    anon_id = uuid.uuid4().hex[:12]
    id_file.write_text(anon_id, encoding="utf-8")
    return anon_id


def record_event(event: dict, project_root: Optional[Path] = None) -> dict:
    """追加一条事件到 feedback/events.jsonl，并返回带时间戳的完整记录。"""
    fdir = _feedback_dir(project_root)
    fdir.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "version": VERSION,
        "anon_id": get_anon_id(project_root),
        "type": event.get("type", "event"),
        "message": event.get("message", ""),
        "payload": event.get("payload", {}),
    }
    with open(fdir / "events.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return record


def read_events(limit: int = 200, project_root: Optional[Path] = None) -> list[dict]:
    """读取最近的事件，按时间从新到旧返回。"""
    path = _feedback_dir(project_root) / "events.jsonl"
    if not path.exists():
        return []
    events = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return events[-limit:][::-1]