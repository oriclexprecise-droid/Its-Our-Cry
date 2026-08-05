"""AI 运行上下文：本地缓存、用量统计、失败句收集。"""

import ast
import hashlib
import json
import math
import os
import re
import threading
import time

PROMPT_SCHEMA_VERSION = "v1-20260805"
USAGE_HISTORY_LIMIT = 200
CACHE_ENTRY_LIMIT = 3000

DEFAULT_INPUT_PRICE = 2.0
DEFAULT_OUTPUT_PRICE = 8.0


def _read_json(path, default):
    try:
        if path:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return default


def _write_json(path, data):
    if not path:
        return
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        pass


def estimate_tokens(text):
    """粗略估算 token：CJK 一字约 1 token，其他按 4 字符 1 token。"""
    text = str(text or "")
    total = 0.0
    for ch in text:
        if ord(ch) > 0x2E80:
            total += 1.0
        else:
            total += 0.25
    return max(0, int(math.ceil(total)))


def estimate_tokens_from_usage(usage, prompt_chars, completion_chars):
    """优先使用 API 返回的 usage，缺失时用字符数估算。"""
    if usage is not None and getattr(usage, "prompt_tokens", None) is not None:
        return int(usage.prompt_tokens or 0), int(usage.completion_tokens or 0)
    return estimate_tokens(prompt_chars), estimate_tokens(completion_chars)


class AiUsage:
    """持久化 AI 用量：本次会话 + 累计 + 最近记录。"""

    def __init__(self, path):
        self.path = path
        data = _read_json(path, {})
        self.session = data.get("session") or self._blank_session()
        self.total = data.get("total") or self._blank_total()
        self.history = data.get("history") or []

    @staticmethod
    def _blank_session():
        return {
            "api_calls": 0, "cache_hits": 0, "failed_lines": 0,
            "input_chars": 0, "output_chars": 0,
            "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }

    @staticmethod
    def _blank_total():
        return {
            "api_calls": 0, "cache_hits": 0, "failed_lines": 0,
            "input_chars": 0, "output_chars": 0,
            "input_tokens": 0, "output_tokens": 0, "cost": 0.0,
        }

    def record(self, kind, model, input_chars=0, output_chars=0, input_tokens=0, output_tokens=0, cache_hit=False, failed_lines=0, prices=None):
        prices = prices or {}
        in_price = float(prices.get("input_price_per_1m", DEFAULT_INPUT_PRICE) or DEFAULT_INPUT_PRICE)
        out_price = float(prices.get("output_price_per_1m", DEFAULT_OUTPUT_PRICE) or DEFAULT_OUTPUT_PRICE)
        cost = (input_tokens / 1e6 * in_price) + (output_tokens / 1e6 * out_price)
        for bucket in (self.session, self.total):
            bucket["api_calls"] = bucket.get("api_calls", 0) + (0 if cache_hit else 1)
            bucket["cache_hits"] = bucket.get("cache_hits", 0) + (1 if cache_hit else 0)
            bucket["failed_lines"] = bucket.get("failed_lines", 0) + failed_lines
            bucket["input_chars"] = bucket.get("input_chars", 0) + input_chars
            bucket["output_chars"] = bucket.get("output_chars", 0) + output_chars
            bucket["input_tokens"] = bucket.get("input_tokens", 0) + input_tokens
            bucket["output_tokens"] = bucket.get("output_tokens", 0) + output_tokens
            bucket["cost"] = round(bucket.get("cost", 0.0) + cost, 6)
        self.history.append({
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "model": model,
            "cache_hit": bool(cache_hit),
            "input_chars": input_chars,
            "output_chars": output_chars,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost": round(cost, 6),
            "failed_lines": failed_lines,
        })
        if len(self.history) > USAGE_HISTORY_LIMIT:
            self.history = self.history[-USAGE_HISTORY_LIMIT:]
        self.save()

    def reset_session(self):
        self.session = self._blank_session()
        self.history = []
        self.save()

    def stats(self):
        return {
            "session": self.session,
            "total": self.total,
            "recent": self.history[-20:],
        }

    def save(self):
        _write_json(self.path, {"session": self.session, "total": self.total, "history": self.history})


class AiCache:
    """本地 AI 结果缓存，按输入与提示词版本做精确命中。"""

    def __init__(self, path):
        self.path = path
        self._lock = threading.RLock()
        self.data = _read_json(path, {"entries": {}})
        if not isinstance(self.data.get("entries"), dict):
            self.data = {"entries": {}}

    def lookup(self, key):
        if getattr(self, "bypass", False):
            return None
        with self._lock:
            entry = self.data["entries"].get(key)
            if isinstance(entry, dict) and "result" in entry:
                return entry["result"]
            return None

    def store(self, key, result):
        with self._lock:
            self.data["entries"][key] = {"result": result, "created_at": time.time()}
            if len(self.data["entries"]) > CACHE_ENTRY_LIMIT:
                items = sorted(self.data["entries"].items(), key=lambda kv: kv[1].get("created_at", 0))
                for k, _ in items[: max(1, int(CACHE_ENTRY_LIMIT * 0.1))]:
                    self.data["entries"].pop(k, None)
            self.save()

    def clear(self):
        with self._lock:
            self.data = {"entries": {}}
            self.save()

    def size(self):
        return len(self.data.get("entries", {}))

    def save(self):
        _write_json(self.path, self.data)


def _try_parse_json(text):
    try:
        obj, _ = json.JSONDecoder().raw_decode(text)
        return obj
    except Exception:
        pass
    try:
        return ast.literal_eval(text)
    except Exception:
        return None


def extract_json_array(content):
    """健壮提取 AI 返回的 JSON 数组，容忍代码块、包裹对象、全角引号与截断。"""
    text = (content or "").strip()
    if not text:
        raise ValueError("AI returned empty content")
    text = re.sub(r"^```[A-Za-z0-9_]*\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()
    text = text.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    start = text.find("[")
    if start != -1:
        tail = text[start:]
        obj = _try_parse_json(tail)
        if isinstance(obj, list):
            return obj
        # 截断恢复：从末尾逐项补全右括号，尽量保留已完整返回的条目
        for pos in range(len(tail) - 1, 0, -1):
            if tail[pos] in "}]":
                for candidate in (tail[:pos + 1], tail[:pos + 1] + "]"):
                    obj = _try_parse_json(candidate)
                    if isinstance(obj, list):
                        return obj
    obj = _try_parse_json(text)
    if isinstance(obj, dict):
        for key in ("result", "emotions", "translations", "data", "items", "lines", "output"):
            if isinstance(obj.get(key), list):
                return obj[key]
        for v in obj.values():
            if isinstance(v, list):
                return v
        if "emotion" in obj or "translation" in obj:
            return [obj]
    raise ValueError("AI returned no JSON array")


def make_cache_key(kind, model, lang, emotions, name_readings, items):
    """生成缓存键：提示词版本 + 任务 + 模型 + 输入，任何变化都会重新请求。"""
    payload = {
        "schema": PROMPT_SCHEMA_VERSION,
        "kind": kind,
        "model": model,
        "lang": lang,
        "emotions": [str(e) for e in (emotions or [])],
        "name_readings": [dict(r) for r in (name_readings or [])],
        "items": items,
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
