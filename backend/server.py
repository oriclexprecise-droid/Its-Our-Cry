"""Flask backend for MyGO TTS Workbench."""

import io
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import uuid
import wave
import zipfile
import copy
from pathlib import Path
from typing import Optional

import yaml
from flask import Flask, after_this_request, jsonify, render_template, request, send_file

from .script_parser import find_character_issues, parse_script
from .emotion_analyzer import analyze_emotions, suggest_params
from .tts_engine import get_engine
from .audio_merger import merge_wav_files, generate_srt, convert_channels
from .translator import translate_lines
from .manual_ai import build_client_prompt, parse_client_result
from .deploy_check import scan_environment, get_download_options, GPT_SOVITS_DOWNLOADS, recommend_download
from .feedback import read_events, record_event
from .cleanup import clean_items, scan_cleanable
from .webgal import DEFAULT_EMOTION_MAP, dialogue_summary, parse_script as parse_webgal_script, render_script, short_name_for


DEFAULT_INTERVAL = 0.5

RECENT_LIMIT_MIN = 10
RECENT_LIMIT_MAX = 500
DEFAULT_RECENT_LIMIT = 50
VERSION_LIMIT_MIN = 5
VERSION_LIMIT_MAX = 200
DEFAULT_VERSION_LIMIT = 50
AUTO_SAVE_INTERVAL_MIN = 1
AUTO_SAVE_INTERVAL_MAX = 120
DEFAULT_AUTO_SAVE_INTERVAL = 5
APP_VERSION = "2.1.1"


def _recent_version_meta(record, limit=DEFAULT_VERSION_LIMIT):
    return [
        {"id": v.get("id"), "saved_at": v.get("saved_at"), "source": v.get("source", "auto")}
        for v in (record.get("versions") or [])[:limit]
    ]


def _snapshot_signature(snap):
    try:
        return json.dumps({
            "script": snap.get("script", ""),
            "lines": snap.get("lines", []),
            "lang": snap.get("lang", "zh"),
            "generated": snap.get("generated", {}),
            "failures": snap.get("failures", {}),
            "merged_path": snap.get("merged_path"),
            "srt_path": snap.get("srt_path"),
            "time_info": snap.get("time_info", []),
            "config": snap.get("config", {}),
            "webgal": snap.get("webgal", {}),
        }, ensure_ascii=False, sort_keys=True)
    except Exception:
        return None


def _same_recent_version(version, current):
    old_sig = _snapshot_signature(version)
    new_sig = _snapshot_signature(current)
    return old_sig is not None and old_sig == new_sig

def _pick_save_dialog(default_name, filetypes, initial_dir=None):
    """Open a native Windows save dialog; returns chosen path or empty string."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        kwargs = {
            "title": "选择导出保存位置",
            "initialfile": str(default_name or "its_our_cry_export.json"),
            "defaultextension": str(filetypes.get("ext", "")),
            "filetypes": [(str(filetypes.get("desc", "文件")), str(filetypes.get("pattern", "*.*")))],
            "parent": root,
        }
        if initial_dir:
            try:
                Path(initial_dir).mkdir(parents=True, exist_ok=True)
                kwargs["initialdir"] = str(initial_dir)
            except Exception:
                pass
        path = filedialog.asksaveasfilename(**kwargs)
        root.destroy()
        return str(path or "").strip()
    except Exception:
        return ""


def _pick_folder_dialog(initial_dir=None):
    """Open a native Windows folder picker; returns chosen path or empty string."""
    try:
        import tkinter as tk
        from tkinter import filedialog
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        kwargs = {"title": "选择音频导出位置", "parent": root}
        if initial_dir:
            try:
                Path(initial_dir).mkdir(parents=True, exist_ok=True)
                kwargs["initialdir"] = str(initial_dir)
            except Exception:
                pass
        path = filedialog.askdirectory(**kwargs)
        root.destroy()
        return str(path or "").strip()
    except Exception:
        return ""

DEFAULT_PRONUNCIATION = [
    {"zh": "长崎素世", "ja": "長崎そよ"},
    {"zh": "soyo", "ja": "そよ"},
    {"zh": "soyorin", "ja": "そよりん"},
    {"zh": "soyo酱", "ja": "そよちゃん"},
    {"zh": "高松灯", "ja": "たかまつともり"},
    {"zh": "灯", "ja": "ともり"},
    {"zh": "tomorin", "ja": "ともりん"},
    {"zh": "小灯", "ja": "ともりちゃん"},
    {"zh": "千早爱音", "ja": "ちはやあのん"},
    {"zh": "爱音", "ja": "あのん"},
    {"zh": "小爱音", "ja": "あのんちゃん"},
    {"zh": "椎名立希", "ja": "しいなたき"},
    {"zh": "立希", "ja": "たき"},
    {"zh": "小立希", "ja": "たきちゃん"},
    {"zh": "rikki~", "ja": "りっきー"},
    {"zh": "要乐奈", "ja": "かなめらーな"},
    {"zh": "乐奈", "ja": "らーな"},
    {"zh": "小乐奈", "ja": "らーなちゃん"},
]


def _strip_psy_wrap(text):
    """去掉心理活动常见的（…）括号后再查纠音，保证“（爱音）”也能命中。"""
    t = str(text or "").strip()
    if len(t) >= 2 and ((t[0] == "(" and t[-1] == ")") or (t[0] == "（" and t[-1] == "）")):
        return t[1:-1].strip()
    return t


def _exact_pronunciation(text, pronunciation):
    """仅整句完全命中词条时返回日文，用于覆盖 AI 翻译。"""
    key = _strip_psy_wrap(text)
    for entry in pronunciation or []:
        if str(entry.get("zh", "")).strip() == key:
            return str(entry.get("ja", "")).strip()
    return None

def correct_pronunciation(text, pronunciation):
    """整句完全等于词条中文时返回日文；否则按“长词条优先”做包含替换。"""
    if not text:
        return None
    table = {}
    for entry in pronunciation or []:
        zh = str(entry.get("zh", "")).strip()
        ja = str(entry.get("ja", "")).strip()
        if zh and ja and zh not in table:
            table[zh] = ja
    if not table:
        return None
    key = _strip_psy_wrap(text)
    if key in table:
        return table[key]
    out = key
    for zh in sorted(table, key=len, reverse=True):
        ja = table[zh]
        if zh in out and ja not in out:
            out = out.replace(zh, ja)
    if out == key:
        return None
    return out


def _clean_emotion_params(raw):
    """Clamp a raw emotion param dict to valid SoVITS ranges."""
    def _clamp_f(v, lo, hi, default):
        try:
            val = float(v)
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, val))

    def _clamp_i(v, lo, hi, default):
        try:
            val = int(float(v))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, val))

    cleaned = {}
    for name, p in raw.items():
        if not isinstance(p, dict):
            continue
        cleaned[str(name).strip()] = {
            "temperature": _clamp_f(p.get("temperature"), 0.1, 1.5, 1.0),
            "top_k": _clamp_i(p.get("top_k"), 1, 50, 15),
            "top_p": _clamp_f(p.get("top_p"), 0.1, 1.0, 1.0),
            "speed_factor": _clamp_f(p.get("speed_factor"), 0.5, 1.5, 1.0),
            "seed": _clamp_i(p.get("seed"), -1, 2147483647, -1),
        }
    return cleaned


def _deploy_target_error(target_path, project_root):
    """Return an error message when a deploy target could erase the app itself."""
    try:
        target = Path(target_path).resolve()
        root = Path(project_root).resolve()
        if target == root:
            return "不能选择程序所在目录"
        try:
            root.relative_to(target)
            return "不能选择程序目录的上级目录"
        except ValueError:
            pass
        try:
            target.relative_to(root)
            return "不能选择程序目录内部的目录"
        except ValueError:
            pass
    except Exception:
        pass
    return None



DEFAULT_EMOTIONS = [
    "生气", "告别", "哭泣", "感动", "决心",
    "悲伤", "认真", "害羞", "微笑", "惊讶", "思考",
]

DEFAULT_MODEL_ALIASES = {
    "MyGO_千早爱音_v2pp": "千早爱音",
    "MyGO_要乐奈_v2pp": "要乐奈",
    "MyGO_高松灯_v2pp": "高松灯",
    "MyGO_椎名立希_v2pp": "椎名立希",
    "MyGO_长崎素世_v2pp": "长崎素世",
    "Mujica_Mortis_v2pp": "Mortis",
    "Mujica_三角初華_v2pp": "三角初华",
    "Mujica_八幡海鈴_v2pp": "八幡海铃",
    "Mujica_祐天寺若麥_乖猫_v2pp": "祐天寺若麦乖猫",
    "Mujica_祐天寺若麥_哈气_v2pp": "祐天寺若麦哈气",
    "Mujica_若葉睦_v2pp": "若叶睦",
    "Mujica_豊川祥子_白_v2pp": "丰川祥子白",
    "Mujica_豊川祥子_黒_v2pp": "丰川祥子黑",
}


def _model_aliases_path(project_root):
    return project_root / "model_aliases.json"


def _load_model_aliases(project_root):
    path = _model_aliases_path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _user_models_path(project_root):
    return project_root / "user_models.json"


def _load_user_models(project_root):
    path = _user_models_path(project_root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_user_models(project_root, models):
    path = _user_models_path(project_root)
    path.write_text(json.dumps(models, ensure_ascii=False, indent=2), encoding="utf-8")


def _scan_model_files(project_root, gs_path):
    gpt_map, sovits_map = {}, {}
    roots = []
    if gs_path:
        roots.append(Path(gs_path).resolve())
    roots.append(project_root)
    for root in roots:
        for sub, ext in (("GPT_weights_v2ProPlus", ".ckpt"), ("SoVITS_weights_v2ProPlus", ".pth")):
            folder = root / sub
            if folder.is_dir():
                for f in sorted(folder.iterdir()):
                    if f.is_file() and f.suffix.lower() == ext:
                        target = gpt_map if ext == ".ckpt" else sovits_map
                        if f.name not in target:
                            target[f.name] = str(f)
    gpt_by_stem = {Path(name).stem: path for name, path in gpt_map.items()}
    sovits_by_stem = {Path(name).stem: path for name, path in sovits_map.items()}
    stems = sorted(set(gpt_by_stem) & set(sovits_by_stem), key=str.lower)
    pairs = [{"name": stem, "gpt": gpt_by_stem[stem], "sovits": sovits_by_stem[stem]} for stem in stems]
    gpt_only = [gpt_by_stem[s] for s in sorted(set(gpt_by_stem) - set(sovits_by_stem), key=str.lower)]
    sovits_only = [sovits_by_stem[s] for s in sorted(set(sovits_by_stem) - set(gpt_by_stem), key=str.lower)]
    return {"gpt": list(gpt_map.values()), "sovits": list(sovits_map.values()), "pairs": pairs, "gpt_only": gpt_only, "sovits_only": sovits_only}


def _resolve_model_path(model_base, rel):
    p = Path(str(rel).strip())
    return p if p.is_absolute() else (model_base / p)


def _model_store_value(model_base, abs_path):
    abs_path = Path(abs_path)
    try:
        return abs_path.relative_to(model_base).as_posix()
    except ValueError:
        return str(abs_path)


def _build_models_dict(project_root, gs_path):
    saved_aliases = _load_model_aliases(project_root)
    scan = _scan_model_files(project_root, gs_path)
    models = {}
    for pair in scan.get("pairs", []):
        key = pair["name"]
        default_alias = DEFAULT_MODEL_ALIASES.get(key, key)
        models[key] = {
            "gpt": pair["gpt"],
            "sovits": pair["sovits"],
            "ref_audio_dir": "reference_audio/" + default_alias,
            "aliases": [default_alias],
        }
    for key, saved in saved_aliases.items():
        if key not in models:
            continue
        if saved.get("ref_audio_dir"):
            models[key]["ref_audio_dir"] = str(saved["ref_audio_dir"]).replace("\\", "/")
        for alias in saved.get("aliases", []):
            if isinstance(alias, str) and alias.strip() and alias not in models[key]["aliases"]:
                models[key]["aliases"].append(alias.strip())

    legacy_models = _load_user_models(project_root)
    for name, cfg in legacy_models.items():
        for m in models.values():
            if (Path(str(cfg.get("gpt_model") or "")).name == Path(m["gpt"]).name and
                    Path(str(cfg.get("model") or "")).name == Path(m["sovits"]).name):
                if name not in m["aliases"]:
                    m["aliases"].append(name)
                break
    return models


def _dpapi_encrypt(text):
    """Encrypt a secret with Windows DPAPI, scoped to the current user."""
    if not text:
        return ""
    try:
        import base64
        import win32crypt
        blob = win32crypt.CryptProtectData(text.encode("utf-8"), "ItsOurCry", None, None, None, 0)
        return "dpapi:" + base64.b64encode(bytes(blob)).decode("ascii")
    except Exception:
        return None


def _dpapi_decrypt(text):
    if not text or not text.startswith("dpapi:"):
        return text
    try:
        import base64
        import win32crypt
        blob = base64.b64decode(text[len("dpapi:"):])
        _, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
        return bytes(data).decode("utf-8")
    except Exception:
        return ""

def _persist_user_settings(project_root, config):
    """Write user settings; API key is encrypted with DPAPI before saving."""
    try:
        settings_path = project_root / "user_settings.json"
        settings = {}
        if settings_path.exists():
            try:
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                settings = {}
        raw_key = config["deepseek"].get("api_key", "")
        if raw_key:
            encrypted = _dpapi_encrypt(raw_key)
            if encrypted:
                settings["deepseek_api_key"] = encrypted
        else:
            settings["deepseek_api_key"] = ""
        settings["gptsovits_path"] = config.get("gptsovits_path", "")
        deepseek = config.get("deepseek", {})
        for field, key in (("base_url", "deepseek_base_url"), ("model", "deepseek_model"), ("name", "deepseek_name")):
            settings[key] = deepseek.get(field, "")
        settings["emotions"] = list(config.get("emotions", []))
        settings["pronunciation"] = [dict(x) for x in (config.get("pronunciation") or [])]
        settings["webgal_emotion_map"] = dict(config.get("webgal_emotion_map") or {})
        settings["webgal_retranslate_on_analyze"] = bool(config.get("webgal_retranslate_on_analyze", True))
        settings["emotion_params"] = config.get("emotion_params", {})
        settings["use_emotion_params"] = bool(config.get("use_emotion_params", True))
        settings["emotion_param_presets"] = config.get("emotion_param_presets", {})
        settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def _find_extractor(project_root):
    """Return (exe, kind, label) for an available 7-Zip/WinRAR executable."""
    root = Path(project_root)
    for rel in ("tools/7z/7z.exe", "packaging/tools/7z/7z.exe"):
        candidate = root / rel
        if candidate.exists():
            return (str(candidate), "7z", "内置 7-Zip")
    for name in ("7z", "7zr"):
        found = shutil.which(name)
        if found:
            return (found, "7z", "系统 7-Zip")
    for base in (r"C:\Program Files\7-Zip", r"C:\Program Files (x86)\7-Zip"):
        for name in ("7z.exe", "7zr.exe"):
            candidate = Path(base) / name
            if candidate.exists():
                return (str(candidate), "7z", "系统 7-Zip")
    for name in ("WinRAR", "UnRAR"):
        found = shutil.which(name)
        if found:
            return (found, "winrar", "系统 WinRAR")
    for base in (r"C:\Program Files\WinRAR", r"C:\Program Files (x86)\WinRAR"):
        for name in ("WinRAR.exe", "UnRAR.exe"):
            candidate = Path(base) / name
            if candidate.exists():
                return (str(candidate), "winrar", "系统 WinRAR")
    tar_exe = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "tar.exe"
    if tar_exe.exists():
        return (str(tar_exe), "tar", "系统内置 tar")
    return None


def _archive_total_uncompressed(tmp_file):
    """Estimate uncompressed size in bytes; 0 means unknown."""
    try:
        import py7zr
        total = 0
        with py7zr.SevenZipFile(str(tmp_file), "r") as archive:
            for info in archive.list():
                if not getattr(info, "is_directory", False):
                    total += int(getattr(info, "uncompressed", 0) or 0)
        return total
    except Exception:
        return 0


def _extract_archive(exe, kind, tmp_file, target_path, state, log):
    """Extract a 7z archive with 7-Zip or WinRAR, streaming progress."""
    if kind == "winrar":
        cmd = [exe, "x", "-o+", "-y"]
        if os.path.basename(exe).lower().startswith("winrar"):
            cmd.append("-ibck")
        cmd += [str(tmp_file), str(target_path) + os.sep]
    elif kind == "tar":
        cmd = [exe, "-xf", str(tmp_file), "-C", str(target_path)]
    else:
        cmd = [exe, "x", str(tmp_file), "-o" + str(target_path), "-y", "-bsp1", "-bso1", "-bse1"]
    log.append("正在解压: " + tmp_file.name + " -> " + str(target_path))
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    tail_lines = []
    last_progress = -1
    for raw in iter(proc.stdout.readline, ""):
        line = raw.strip()
        match = re.search(r"(\d{1,3})\s*%", line)
        if match:
            pct = min(100, max(0, int(match.group(1))))
            progress = min(94, 80 + round(pct * 14 / 100))
            if progress != last_progress:
                last_progress = progress
                state["progress"] = progress
        if line:
            tail_lines.append(line)
            if len(tail_lines) > 40:
                del tail_lines[:len(tail_lines) - 40]
        if state.get("cancel_requested"):
            try:
                proc.kill()
            except Exception:
                pass
            raise RuntimeError("用户取消解压")
    returncode = proc.wait()
    if returncode != 0:
        tail = " | ".join(tail_lines[-8:])[-1200:]
        raise RuntimeError("解压工具退出码 %s: %s" % (returncode, tail))


def create_app(config_path="config.yaml"):
    project_root = Path(config_path).parent.resolve()

    app = Flask(
        __name__,
        template_folder=str(project_root / "frontend" / "templates"),
        static_folder=str(project_root / "frontend" / "static"),
    )

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    config.setdefault("narration", {
        "base_duration": 2.0,
        "per_char": 0.32,
        "min_duration": 1.5,
        "max_duration": 8.0,
        "fixed_duration": 0.0,
    })

    config.setdefault("emotion_params", {})
    config.setdefault("use_emotion_params", True)
    config.setdefault("emotion_param_presets", {})
    config.setdefault("webgal_emotion_map", dict(DEFAULT_EMOTION_MAP))
    config.setdefault("webgal_retranslate_on_analyze", True)
    # 用户本地设置（API Key 等）覆盖，不写回仓库里的 config.yaml
    user_settings = {}
    user_settings_path = project_root / "user_settings.json"
    if user_settings_path.exists():
        try:
            user_settings = json.loads(user_settings_path.read_text(encoding="utf-8"))
        except Exception:
            user_settings = {}
    stored_key = user_settings.get("deepseek_api_key", "")
    if stored_key:
        config["deepseek"]["api_key"] = _dpapi_decrypt(stored_key)
        if not str(stored_key).startswith("dpapi:"):
            _persist_user_settings(project_root, config)
    for field, key in (("base_url", "deepseek_base_url"), ("model", "deepseek_model"), ("name", "deepseek_name")):
        if user_settings.get(key):
            config["deepseek"][field] = user_settings[key]
    if user_settings.get("gptsovits_path"):
        config["gptsovits_path"] = user_settings["gptsovits_path"]
    if user_settings.get("narration"):
        config["narration"] = {**config.get("narration", {}), **user_settings["narration"]}
    if "emotions" in user_settings and isinstance(user_settings["emotions"], list):
        custom_emotions = [str(e).strip() for e in user_settings["emotions"] if str(e).strip()]
        if custom_emotions:
            config["emotions"] = custom_emotions
    if isinstance(user_settings.get("emotion_params"), dict):
        config["emotion_params"] = {
            str(k).strip(): dict(v) for k, v in user_settings["emotion_params"].items() if isinstance(v, dict)
        }
    if "use_emotion_params" in user_settings:
        config["use_emotion_params"] = bool(user_settings["use_emotion_params"])
    if isinstance(user_settings.get("emotion_param_presets"), dict):
        config["emotion_param_presets"] = {
            str(k).strip(): dict(v) for k, v in user_settings["emotion_param_presets"].items() if isinstance(v, dict)
        }
    if "pronunciation" in user_settings and isinstance(user_settings["pronunciation"], list):
        config["pronunciation"] = [
            {"zh": str(p.get("zh", "")).strip(), "ja": str(p.get("ja", "")).strip()}
            for p in user_settings["pronunciation"]
            if isinstance(p, dict) and str(p.get("zh", "")).strip() and str(p.get("ja", "")).strip()
        ]
    else:
        config["pronunciation"] = [dict(x) for x in DEFAULT_PRONUNCIATION]
    if isinstance(user_settings.get("webgal_emotion_map"), dict):
        cleaned_map = {}
        for k, v in user_settings["webgal_emotion_map"].items():
            key = str(k).strip().lower()
            val = str(v).strip()
            if key and val:
                cleaned_map[key] = val
        if cleaned_map:
            config["webgal_emotion_map"] = cleaned_map
    if "webgal_retranslate_on_analyze" in user_settings:
        config["webgal_retranslate_on_analyze"] = bool(user_settings["webgal_retranslate_on_analyze"])
    dpapi_ok = _dpapi_encrypt("probe") is not None

    # 近期记录：本地 JSON 文件，不提交 git、不联网
    work_dir = project_root / "work"
    work_dir.mkdir(parents=True, exist_ok=True)
    recent_path = work_dir / "recent_results.json"
    recent_lock = threading.RLock()
    raw_auto = user_settings.get("recent_auto_save", True)
    if isinstance(raw_auto, bool):
        recent_auto_save = raw_auto
    else:
        recent_auto_save = str(raw_auto).lower() not in ("", "0", "false", "no")
    raw_version = user_settings.get("recent_version_auto_save", True)
    if isinstance(raw_version, bool):
        version_auto_save = raw_version
    else:
        version_auto_save = str(raw_version).lower() not in ("", "0", "false", "no")
    try:
        auto_interval = int(user_settings.get("auto_save_interval") or DEFAULT_AUTO_SAVE_INTERVAL)
    except (TypeError, ValueError):
        auto_interval = DEFAULT_AUTO_SAVE_INTERVAL
    try:
        version_limit = int(user_settings.get("recent_version_limit") or DEFAULT_VERSION_LIMIT)
    except (TypeError, ValueError):
        version_limit = DEFAULT_VERSION_LIMIT
    recent_settings = {
        "limit": max(RECENT_LIMIT_MIN, min(RECENT_LIMIT_MAX, int(user_settings.get("recent_limit") or DEFAULT_RECENT_LIMIT))),
        "auto_save": recent_auto_save,
        "version_auto_save": version_auto_save,
        "auto_save_interval": max(AUTO_SAVE_INTERVAL_MIN, min(AUTO_SAVE_INTERVAL_MAX, auto_interval)),
        "version_limit": max(VERSION_LIMIT_MIN, min(VERSION_LIMIT_MAX, version_limit)),
    }

    # Resolve all relative paths to absolute to survive cwd changes
    gs_path = str(config.get("gptsovits_path") or "").strip()
    config["gptsovits_path"] = gs_path
    config["output_dir"] = str(project_root / config["output_dir"])
    # 打包版不携带 GPT-SoVITS 时，角色权重随程序放在项目目录内
    model_base = Path(gs_path) if gs_path else project_root
    # 角色模型：权重对固定，激活词只是模型上的标签（可增删）
    models = _build_models_dict(project_root, gs_path)

    def persist_model_aliases():
        data = {}
        for key, m in models.items():
            data[key] = {
                "ref_audio_dir": m.get("ref_audio_dir") or "",
                "aliases": list(m["aliases"]),
            }
        try:
            _model_aliases_path(project_root).write_text(
                json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception:
            pass

    def rebuild_characters():
        config["characters"] = {}
        for key, m in models.items():
            for alias in m["aliases"]:
                ref_rel = m.get("ref_audio_dir") or ("reference_audio/" + alias)
                config["characters"][alias] = {
                    "model": m["sovits"],
                    "gpt_model": m["gpt"],
                    "model_rel": "SoVITS_weights_v2ProPlus/" + Path(m["sovits"]).name,
                    "gpt_model_rel": "GPT_weights_v2ProPlus/" + Path(m["gpt"]).name,
                    "ref_audio_dir": str(project_root / ref_rel),
                }

    rebuild_characters()

    state = {
        "lines": [],
        "script": "",
        "script_seq": 0,
        "emotions": [],
        "lang": "zh",
        "analysis_seq": 0,
        "analysis_cancel_seq": -1,
        "generated": {},
        "merged_path": None,
        "srt_path": None,
        "generating": False,
        "cancel_requested": False,
        "cancelled": False,
        "progress": {"current": 0, "total": 0},
        "failures": {},
        "time_info": [],
        "history_undo": [],
        "history_redo": [],
        "history_limit": 50,
        "current_record_id": None,
        "project_type": "srt",
        "ai_mode": "api",
        "webgal": {
            "source": "",
            "entries": [],
            "dialogues": [],
            "emotions": {},
            "translations": {},
            "generated": {},
            "failures": {},
            "progress": {"current": 0, "total": 0},
            "generating": False,
            "cancel_requested": False,
            "lastExport": "",
            "lang": "zh",
        },
        "deploy_install": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "total_commands": 0, "command_index": 0, "current_packages": []},
        "deploy_model_copy": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "total": 0, "current": ""},
        "deploy_clone": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "target_dir": ""},
        "deploy_download": {"running": False, "done": False, "success": None, "cancelled": False, "cancel_requested": False, "log": [], "progress": 0, "target_dir": "", "extracted_path": ""},
        "deploy_ffmpeg": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "target": ""}
    }

    def take_snapshot():
        return {
            "lines": copy.deepcopy(state.get("lines", [])),
            "script": state.get("script", ""),
            "generated": copy.deepcopy(state.get("generated", {})),
            "failures": copy.deepcopy(state.get("failures", {})),
            "merged_path": state.get("merged_path"),
            "srt_path": state.get("srt_path"),
            "time_info": copy.deepcopy(state.get("time_info", [])),
            "current_record_id": state.get("current_record_id"),
            "lang": state.get("lang", "zh"),
            "ai_mode": state.get("ai_mode", "api"),
        }

    def push_history(label):
        undo = state.setdefault("history_undo", [])
        undo.append({"label": label, "snapshot": take_snapshot()})
        limit = state.get("history_limit", 50)
        if len(undo) > limit:
            del undo[:len(undo) - limit]
        state["history_redo"] = []

    def restore_snapshot(snap):
        state["lines"] = copy.deepcopy(snap.get("lines", []))
        state["script"] = snap.get("script", "")
        state["generated"] = copy.deepcopy(snap.get("generated", {}))
        state["failures"] = copy.deepcopy(snap.get("failures", {}))
        state["merged_path"] = snap.get("merged_path")
        state["srt_path"] = snap.get("srt_path")
        state["time_info"] = copy.deepcopy(snap.get("time_info", []))
        state["lang"] = snap.get("lang", "zh")
        state["ai_mode"] = snap.get("ai_mode", "api")
        state["current_record_id"] = snap.get("current_record_id")
        state["progress"] = {"current": 0, "total": 0}
        state["srt_only"] = False

    def workbench_state():
        return {
            "lines": copy.deepcopy(state.get("lines", [])),
            "script": state.get("script", ""),
            "generated": copy.deepcopy(state.get("generated", {})),
            "failures": copy.deepcopy(state.get("failures", {})),
            "merged_path": state.get("merged_path"),
            "srt_path": state.get("srt_path"),
            "time_info": copy.deepcopy(state.get("time_info", [])),
            "lang": state.get("lang", "zh"),
            "ai_mode": state.get("ai_mode", "api"),
            "project_type": state.get("project_type", "srt"),
            "config": record_config_snapshot(),
            "webgal": copy.deepcopy(state.get("webgal") or {}),
        }

    def history_payload():
        undo = state.get("history_undo", [])
        redo = state.get("history_redo", [])
        return {
            "undo_count": len(undo),
            "redo_count": len(redo),
            "undo_label": undo[-1]["label"] if undo else None,
            "redo_label": redo[-1]["label"] if redo else None,
        }

    def load_recent_records():
        if not recent_path.exists():
            return []
        try:
            with recent_lock:
                data = json.loads(recent_path.read_text(encoding="utf-8"))
            records = data if isinstance(data, list) else []
            records = [r for r in records if isinstance(r, dict) and r.get("id")]
            migrated = False
            for record in records:
                versions = record.get("versions") or []
                if versions and all(isinstance(v, dict) and "generated" in v for v in versions):
                    continue
                migrated = True
                old_versions = [v for v in versions if isinstance(v, dict)]
                new_versions = []
                for v in old_versions:
                    cfg = v.get("config") if isinstance(v.get("config"), dict) else {}
                    new_versions.append({
                        "id": v.get("id") or uuid.uuid4().hex,
                        "saved_at": v.get("saved_at") or record.get("saved_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                        "source": "auto",
                        "script": v.get("script", record.get("script", "")),
                        "lines": copy.deepcopy(v.get("lines", record.get("lines", []))),
                        "lang": cfg.get("lang") or v.get("lang") or record.get("lang", "zh"),
                        "generated": {},
                        "failures": {},
                        "merged_path": None,
                        "srt_path": None,
                        "time_info": [],
                        "config": cfg or record.get("config") or {},
                    })
                base = {
                    "id": str(record.get("id", "")) + "_base",
                    "saved_at": record.get("saved_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
                    "source": record.get("source", "auto"),
                    "script": record.get("script", ""),
                    "lines": copy.deepcopy(record.get("lines", [])),
                    "lang": record.get("lang", "zh"),
                    "generated": {},
                    "failures": {},
                    "merged_path": record.get("merged_path"),
                    "srt_path": record.get("srt_path"),
                    "time_info": copy.deepcopy(record.get("time_info", [])),
                    "config": record.get("config") or {},
                }
                for idx, g in (record.get("generated") or {}).items():
                    base["generated"][str(idx)] = {"path": g.get("path"), "duration": g.get("duration")}
                for idx, msg in (record.get("failures") or {}).items():
                    base["failures"][str(idx)] = str(msg)
                if not any(v.get("saved_at") == base["saved_at"] and v.get("script") == base["script"] for v in new_versions):
                    new_versions.insert(0, base)
                record["versions"] = new_versions
                record.setdefault("created_at", record.get("saved_at"))
                record["updated_at"] = record.get("updated_at") or record.get("saved_at")
            if migrated:
                persist_recent_records(records)
            return records
        except Exception:
            return []

    def persist_recent_records(records):
        try:
            with recent_lock:
                backup_path = recent_path.with_suffix(".bak.json")
                if recent_path.exists():
                    shutil.copy2(str(recent_path), str(backup_path))
                tmp_path = recent_path.with_suffix(".tmp")
                tmp_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
                os.replace(str(tmp_path), str(recent_path))
        except Exception:
            pass

    def enforce_recent_limit(records):
        limit = int(recent_settings.get("limit") or DEFAULT_RECENT_LIMIT)
        if len(records) > limit:
            del records[limit:]

    def persist_recent_settings():
        try:
            settings = {}
            if user_settings_path.exists():
                settings = json.loads(user_settings_path.read_text(encoding="utf-8"))
            settings["recent_limit"] = recent_settings["limit"]
            settings["recent_auto_save"] = recent_settings["auto_save"]
            settings["recent_version_auto_save"] = recent_settings["version_auto_save"]
            settings["auto_save_interval"] = recent_settings["auto_save_interval"]
            settings["recent_version_limit"] = recent_settings["version_limit"]
            user_settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def record_config_snapshot():
        return {
            "lang": state.get("lang", "zh"),
            "narration": config.get("narration", {}),
        }

    def snapshot_current_workbench():
        generated = {}
        for idx, gen in (state.get("generated") or {}).items():
            generated[str(idx)] = {"path": gen.get("path"), "duration": gen.get("duration")}
        failures = {}
        for idx, msg in (state.get("failures") or {}).items():
            failures[str(idx)] = str(msg)
        wg = state.get("webgal") or {}
        webgal_snapshot = {
            "source": wg.get("source", ""),
            "entries": copy.deepcopy(wg.get("entries", [])),
            "lang": wg.get("lang", state.get("lang", "zh")),
            "dialogues": copy.deepcopy(wg.get("dialogues", [])),
            "emotions": dict(wg.get("emotions") or {}),
            "translations": dict(wg.get("translations") or {}),
            "generated": dict(wg.get("generated") or {}),
            "failures": dict(wg.get("failures") or {}),
            "psyVoice": bool(wg.get("psyVoice")),
            "psyCharacter": str(wg.get("psyCharacter") or ""),
            "lastExport": str(wg.get("lastExport") or ""),
        }
        return {
            "script": state.get("script", ""),
            "lines": copy.deepcopy(state.get("lines", [])),
            "lang": state.get("lang", "zh"),
            "generated": generated,
            "failures": failures,
            "merged_path": state.get("merged_path"),
            "srt_path": state.get("srt_path"),
            "time_info": copy.deepcopy(state.get("time_info", [])),
            "config": record_config_snapshot(),
            "project_type": state.get("project_type", "srt"),
            "webgal": webgal_snapshot,
        }

    def save_current_version(source="auto", force=False):
        with recent_lock:
            records = load_recent_records()
            record_id = state.get("current_record_id")
            record = None
            if record_id:
                for r in records:
                    if r.get("id") == record_id:
                        record = r
                        break
            now = time.strftime("%Y-%m-%d %H:%M:%S")
            snapshot = snapshot_current_workbench()
            if record is None:
                first_name = ""
                for ln in str(snapshot.get("script") or "").splitlines():
                    if ln.strip():
                        first_name = ln.strip()[:60]
                        break
                record = {
                    "id": uuid.uuid4().hex,
                    "name": first_name or "未命名项目",
                    "project_type": state.get("project_type", "srt"),
                    "created_at": now,
                    "updated_at": now,
                    "versions": [],
                    "exports": [],
                }
                state["current_record_id"] = record["id"]
                records.insert(0, record)
            record["project_type"] = state.get("project_type") or record.get("project_type") or "srt"
            versions = record.setdefault("versions", [])
            if not force and versions and _same_recent_version(versions[0], snapshot):
                return record, None, False
            version = {"id": uuid.uuid4().hex, "saved_at": now, "source": source}
            version.update(copy.deepcopy(snapshot))
            versions.insert(0, version)
            version_limit = int(recent_settings.get("version_limit") or DEFAULT_VERSION_LIMIT)
            del versions[version_limit:]
            record["updated_at"] = now
            records = [r for r in records if r.get("id") != record["id"]]
            records.insert(0, record)
            enforce_recent_limit(records)
            persist_recent_records(records)
            return record, version, True

    def attach_export_to_record(export_info):
        with recent_lock:
            records = load_recent_records()
            record_id = state.get("current_record_id")
            record = None
            if record_id:
                for r in records:
                    if r.get("id") == record_id:
                        record = r
                        break
            if record is None:
                save_current_version("auto", force=True)
                records = load_recent_records()
                record_id = state.get("current_record_id")
                for r in records:
                    if r.get("id") == record_id:
                        record = r
                        break
            if record is None:
                return
            record.setdefault("exports", []).append(export_info)
            record["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
            records = [r for r in records if r.get("id") != record_id]
            records.insert(0, record)
            enforce_recent_limit(records)
            persist_recent_records(records)

    def recent_summary(record):
        versions = record.get("versions") or []
        latest = versions[0] if versions else record
        lines = latest.get("lines") or []
        exports = record.get("exports") or []
        first = ""
        if lines:
            l0 = lines[0]
            first = (str(l0.get("character") or "") + "：" + str(l0.get("text") or ""))[:80]
        elif str(latest.get("script") or "").strip():
            first = str(latest.get("script") or "").strip().split("\n")[0][:80]
        return {
            "id": record.get("id"),
            "name": str(record.get("name") or ""),
            "project_type": record.get("project_type") or "srt",
            "created_at": record.get("created_at") or record.get("saved_at"),
            "saved_at": latest.get("saved_at") or record.get("saved_at"),
            "updated_at": record.get("updated_at") or record.get("saved_at"),
            "source": latest.get("source") or record.get("source", "auto"),
            "line_count": len(lines),
            "voice_count": len(latest.get("generated") or {}),
            "fail_count": len(latest.get("failures") or {}),
            "export_count": len(exports),
            "last_folder": exports[-1].get("folder") if exports else None,
            "first_line": first,
            "script": str(latest.get("script") or "")[:120],
            "version_count": len(versions),
            "versions": _recent_version_meta(record, int(recent_settings.get("version_limit") or DEFAULT_VERSION_LIMIT)),
            "lang": latest.get("lang", "zh"),
        }

    def restore_workbench_from_version(record, version):
        state["script"] = version.get("script", "")
        state["lines"] = copy.deepcopy(version.get("lines", []))
        state["generated"] = {}
        for k, v in (version.get("generated") or {}).items():
            try:
                state["generated"][int(k)] = {"path": v.get("path"), "duration": v.get("duration")}
            except (TypeError, ValueError):
                continue
        state["failures"] = {}
        for k, v in (version.get("failures") or {}).items():
            state["failures"][int(k)] = str(v)
        cfg = version.get("config") or {}
        lang = version.get("lang", "zh")
        if isinstance(cfg, dict) and cfg.get("lang"):
            lang = cfg["lang"]
        state["lang"] = lang
        if isinstance(cfg, dict) and isinstance(cfg.get("narration"), dict):
            config["narration"] = {**config.get("narration", {}), **cfg["narration"]}
        state["merged_path"] = version.get("merged_path")
        state["srt_path"] = version.get("srt_path")
        state["time_info"] = copy.deepcopy(version.get("time_info", []))
        state["progress"] = {"current": 0, "total": 0}
        state["srt_only"] = False
        state["current_record_id"] = record.get("id")
        state["project_type"] = record.get("project_type") or "srt"
        state["ai_mode"] = version.get("ai_mode") or record.get("ai_mode") or "api"
        wg = version.get("webgal")
        if isinstance(wg, dict):
            state["webgal"] = copy.deepcopy(wg)
        else:
            state["webgal"] = {
                "source": "",
                "entries": [],
                "dialogues": [],
                "emotions": {},
                "translations": {},
                "generated": {},
                "failures": {},
                "progress": {"current": 0, "total": 0},
                "generating": False,
                "cancel_requested": False,
                "lastExport": "",
                "lang": version.get("lang", "zh"),
                "psyVoice": False,
                "psyCharacter": "",
                "analyzing": False,
            }

    def restore_workbench_from_record(record):
        versions = record.get("versions") or []
        version = versions[0] if versions else record
        restore_workbench_from_version(record, version)

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/config", methods=["GET"])
    def get_config():
        has_key = bool(config["deepseek"].get("api_key", ""))
        return jsonify({
            "characters": list(config["characters"].keys()),
            "emotions": config["emotions"],
            "webgal_emotion_map": config.get("webgal_emotion_map", {}),
            "webgal_emotion_defaults": dict(DEFAULT_EMOTION_MAP),
            "webgal_retranslate_on_analyze": bool(config.get("webgal_retranslate_on_analyze", True)),
            "has_api_key": has_key,
            "default_interval": DEFAULT_INTERVAL,
            "narration": config.get("narration", {}),
            "recent": dict(recent_settings),
            "gptsovits_path": config["gptsovits_path"],
            "dpapi_ok": dpapi_ok,
            "deepseek": {
                "name": config["deepseek"].get("name", "DeepSeek"),
                "base_url": config["deepseek"].get("base_url", "https://api.deepseek.com"),
                "model": config["deepseek"].get("model", "deepseek-v4-flash"),
            },
        })

    @app.route("/api/config/api_key", methods=["GET"])
    def get_api_key():
        """Return only a masked preview of the saved API key."""
        key = config["deepseek"].get("api_key", "")
        if key:
            masked = (key[:6] + "****" + key[-4:]) if len(key) > 10 else "****"
            return jsonify({"api_key": "", "api_key_preview": masked, "has_api_key": True})
        return jsonify({"api_key": "", "api_key_preview": "", "has_api_key": False})

    @app.route("/api/config", methods=["POST"])
    def save_config():
        data = request.get_json()
        if "deepseek_api_key" in data:
            config["deepseek"]["api_key"] = data["deepseek_api_key"]
        for field, key in (("base_url", "deepseek_base_url"), ("model", "deepseek_model"), ("name", "deepseek_name")):
            if key in data:
                config["deepseek"][field] = data[key]
        if "gptsovits_path" in data:
            config["gptsovits_path"] = str(data["gptsovits_path"] or "").strip()
        _persist_user_settings(project_root, config)
        return jsonify({"status": "ok"})

    @app.route("/api/emotions", methods=["GET"])
    def get_emotions():
        return jsonify({"emotions": config["emotions"], "defaults": DEFAULT_EMOTIONS})

    @app.route("/api/emotions", methods=["POST"])
    def update_emotions():
        data = request.get_json() or {}
        action = str(data.get("action") or "").strip()
        name = str(data.get("name") or "").strip()
        if action == "add":
            if not name:
                return jsonify({"error": "情绪名不能为空"}), 400
            if name == "旁白":
                return jsonify({"error": "不能添加“旁白”"}), 400
            if "/" in name or "\\" in name or name in (".", ".."):
                return jsonify({"error": "情绪名不能包含路径分隔符"}), 400
            if name in config["emotions"]:
                return jsonify({"error": "情绪已存在"}), 400
            config["emotions"].append(name)
            for mkey in models:
                base = _ref_audio_base(mkey)
                if base is not None:
                    try:
                        (base / name).mkdir(parents=True, exist_ok=True)
                    except Exception:
                        pass
        elif action == "delete":
            if name not in config["emotions"]:
                return jsonify({"error": "情绪不存在"}), 404
            if len(config["emotions"]) <= 1:
                return jsonify({"error": "至少保留一个情绪"}), 400
            config["emotions"].remove(name)
        else:
            return jsonify({"error": "未知操作"}), 400
        _persist_user_settings(project_root, config)
        return jsonify({"status": "ok", "emotions": config["emotions"]})

    @app.route("/api/emotions/reset", methods=["POST"])
    def reset_emotions():
        config["emotions"] = list(DEFAULT_EMOTIONS)
        _persist_user_settings(project_root, config)
        return jsonify({"status": "ok", "emotions": config["emotions"]})

    @app.route("/api/emotion_params", methods=["GET"])
    def get_emotion_params():
        return jsonify({
            "params": config.get("emotion_params", {}),
            "enabled": bool(config.get("use_emotion_params", True)),
        })

    @app.route("/api/emotion_params", methods=["POST"])
    def save_emotion_params():
        data = request.get_json(silent=True) or {}
        if "enabled" in data:
            config["use_emotion_params"] = bool(data.get("enabled"))
        raw = data.get("params")
        if raw is not None and not isinstance(raw, dict):
            return jsonify({"error": "参数格式错误"}), 400
        if isinstance(raw, dict):
            config["emotion_params"] = _clean_emotion_params(raw)
        _persist_user_settings(project_root, config)
        return jsonify({
            "status": "ok",
            "params": config.get("emotion_params", {}),
            "enabled": bool(config.get("use_emotion_params", True)),
        })

    @app.route("/api/emotion_params/suggest", methods=["POST"])
    def suggest_emotion_params():
        data = request.get_json(silent=True) or {}
        api_key = data.get("api_key", "") or config["deepseek"]["api_key"]
        base_url = data.get("base_url") or config["deepseek"].get("base_url", "https://api.deepseek.com")
        model = data.get("model") or config["deepseek"].get("model", "deepseek-v4-flash")
        if not api_key:
            return jsonify({"error": "请先配置 DeepSeek API Key"}), 400
        used = [str(e).strip() for e in config.get("emotions") or [] if str(e).strip()]
        if not used:
            used = list(DEFAULT_EMOTIONS)
        lines = data.get("lines")
        if not isinstance(lines, list) or not lines:
            lines = state.get("lines") or []
        try:
            suggestions = suggest_params(
                emotions=used,
                api_key=api_key,
                base_url=base_url,
                model=model,
                lines=lines,
            )
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "参数建议失败: " + str(e)}), 500
        return jsonify({"params": suggestions, "emotions": used})

    @app.route("/api/emotion_params/presets", methods=["GET"])
    def get_emotion_presets():
        presets = config.get("emotion_param_presets", {})
        items = [
            {
                "name": str(name),
                "params": dict(data.get("params") or {}),
                "enabled": bool(data.get("enabled", True)),
                "updated_at": str(data.get("updated_at") or ""),
            }
            for name, data in presets.items()
            if isinstance(data, dict)
        ]
        return jsonify({"presets": items})

    @app.route("/api/emotion_params/presets", methods=["POST"])
    def manage_emotion_presets():
        data = request.get_json(silent=True) or {}
        action = str(data.get("action") or "").strip()
        presets = config.setdefault("emotion_param_presets", {})
        if action == "save":
            name = str(data.get("name") or "").strip()
            if not name:
                return jsonify({"error": "请输入预设名称"}), 400
            raw = data.get("params")
            if not isinstance(raw, dict):
                return jsonify({"error": "参数格式错误"}), 400
            presets[name] = {
                "params": _clean_emotion_params(raw),
                "enabled": bool(data.get("enabled", config.get("use_emotion_params", True))),
                "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            }
        elif action == "load":
            name = str(data.get("name") or "").strip()
            preset = presets.get(name)
            if not isinstance(preset, dict):
                return jsonify({"error": "预设不存在"}), 404
            config["emotion_params"] = _clean_emotion_params(preset.get("params") or {})
            config["use_emotion_params"] = bool(preset.get("enabled", True))
        elif action == "delete":
            name = str(data.get("name") or "").strip()
            if name not in presets:
                return jsonify({"error": "预设不存在"}), 404
            del presets[name]
        else:
            return jsonify({"error": "未知操作"}), 400
        _persist_user_settings(project_root, config)
        items = [
            {
                "name": str(name),
                "params": dict(preset.get("params") or {}),
                "enabled": bool(preset.get("enabled", True)),
                "updated_at": str(preset.get("updated_at") or ""),
            }
            for name, preset in presets.items()
            if isinstance(preset, dict)
        ]
        return jsonify({
            "status": "ok",
            "presets": items,
            "params": config.get("emotion_params", {}),
            "enabled": bool(config.get("use_emotion_params", True)),
        })

    @app.route("/api/models", methods=["GET"])
    def list_models():
        items = []
        for key, m in models.items():
            items.append({
                "key": key,
                "name": DEFAULT_MODEL_ALIASES.get(key, key),
                "gpt_file": Path(m["gpt"]).name,
                "sovits_file": Path(m["sovits"]).name,
                "ref_audio_dir": Path(m.get("ref_audio_dir") or "").name,
                "aliases": list(m["aliases"]),
            })
        return jsonify({"models": items})

    @app.route("/api/models/available", methods=["GET"])
    def available_models():
        ref_base = project_root / "reference_audio"
        ref_dirs = sorted(d.name for d in ref_base.iterdir() if d.is_dir()) if ref_base.is_dir() else []
        data = _scan_model_files(project_root, gs_path)
        data["ref_dirs"] = ref_dirs
        return jsonify(data)

    @app.route("/api/models/<path:key>/aliases", methods=["POST"])
    def add_model_alias(key):
        data = request.get_json() or {}
        alias = str(data.get("alias") or "").strip()
        if not alias or alias == "旁白":
            return jsonify({"error": "激活词不能为空且不能是“旁白”"}), 400
        if "/" in alias or "\\" in alias or alias in (".", ".."):
            return jsonify({"error": "激活词不能包含路径分隔符"}), 400
        if key not in models:
            return jsonify({"error": "模型不存在"}), 404
        for mkey, m in models.items():
            if alias in m["aliases"]:
                return jsonify({"error": f"激活词“{alias}”已属于模型“{DEFAULT_MODEL_ALIASES.get(mkey, mkey)}”"}), 400
        models[key]["aliases"].append(alias)
        persist_model_aliases()
        rebuild_characters()
        return jsonify({"status": "ok", "aliases": models[key]["aliases"]})

    @app.route("/api/models/<path:key>/aliases/<path:alias>", methods=["DELETE"])
    def delete_model_alias(key, alias):
        if key not in models:
            return jsonify({"error": "模型不存在"}), 404
        if alias not in models[key]["aliases"]:
            return jsonify({"error": "激活词不存在"}), 404
        if len(models[key]["aliases"]) <= 1:
            return jsonify({"error": "每个模型至少保留一个激活词"}), 400
        models[key]["aliases"].remove(alias)
        persist_model_aliases()
        rebuild_characters()
        return jsonify({"status": "ok", "aliases": models[key]["aliases"]})

    @app.route("/api/pronunciation", methods=["GET"])
    def get_pronunciation():
        return jsonify({"entries": config.get("pronunciation", []), "defaults": DEFAULT_PRONUNCIATION})

    @app.route("/api/pronunciation", methods=["PUT"])
    def save_pronunciation():
        data = request.get_json() or {}
        entries = data.get("entries")
        if not isinstance(entries, list):
            return jsonify({"error": "无效的纠音词典数据"}), 400
        cleaned = []
        for p in entries:
            if not isinstance(p, dict):
                continue
            zh = str(p.get("zh", "")).strip()
            ja = str(p.get("ja", "")).strip()
            if zh and ja:
                cleaned.append({"zh": zh, "ja": ja})
        config["pronunciation"] = cleaned
        try:
            settings = {}
            settings_path = project_root / "user_settings.json"
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            settings["pronunciation"] = cleaned
            settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return jsonify({"status": "ok", "entries": cleaned})

    @app.route("/api/settings/narration", methods=["PUT"])
    def save_narration_settings():
        data = request.get_json() or {}

        def _num(value, default):
            try:
                num = float(value)
            except (TypeError, ValueError):
                return default
            return num if num >= 0 else default

        narration = {
            "base_duration": _num(data.get("base_duration"), 2.0),
            "per_char": _num(data.get("per_char"), 0.32),
            "min_duration": _num(data.get("min_duration"), 1.5),
            "max_duration": _num(data.get("max_duration"), 8.0),
            "fixed_duration": _num(data.get("fixed_duration"), 0.0),
        }
        if narration["min_duration"] > narration["max_duration"]:
            narration["min_duration"], narration["max_duration"] = narration["max_duration"], narration["min_duration"]
        config["narration"] = narration
        try:
            settings = {}
            settings_path = project_root / "user_settings.json"
            if settings_path.exists():
                settings = json.loads(settings_path.read_text(encoding="utf-8"))
            settings["narration"] = narration
            settings_path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
        return jsonify({"status": "ok", "narration": narration})

    @app.route("/api/analyze", methods=["POST"])
    def analyze():
        data = request.get_json()
        script_text = data.get("text", "")
        api_key = data.get("api_key", "") or config["deepseek"]["api_key"]
        lang = data.get("lang", "zh")
        base_url = data.get("base_url") or config["deepseek"].get("base_url", "https://api.deepseek.com")
        model = data.get("model") or config["deepseek"].get("model", "deepseek-v4-flash")

        if not script_text.strip():
            return jsonify({"error": "script is empty"}), 400
        if not api_key:
            return jsonify({"error": "please configure DeepSeek API Key"}), 400

        lines = parse_script(script_text)
        if not lines:
            return jsonify({"error": "no valid lines found"}), 400
        for line in lines:
            line.setdefault("interval", DEFAULT_INTERVAL)

        # 智能补齐：剧本未变且情绪/翻译已齐全时，不再重复调用 AI
        existing = state.get("lines") or []
        same_script = state.get("script") == script_text and len(existing) == len(lines)
        if same_script:
            for new_line, old_line in zip(lines, existing):
                if new_line.get("text") != old_line.get("text") or new_line.get("character") != old_line.get("character"):
                    same_script = False
                    break
        emotions_ready = same_script and all((line.get("emotion") or "") for line in existing)
        translations_ready = lang != "ja" or all((line.get("translated_text") or "") for line in existing)
        reused = bool(emotions_ready and translations_ready)
        translated_only = bool(same_script and emotions_ready and not translations_ready)
        emotions_reused = bool(emotions_ready)

        seq = state.get("analysis_seq", 0) + 1
        state["analysis_seq"] = seq

        if reused or translated_only:
            lines = copy.deepcopy(existing)
            emotions = list(state.get("emotions") or [])
        else:
            try:
                emotions = analyze_emotions(
                    lines=lines,
                    api_key=api_key,
                    base_url=base_url,
                    model=model,
                    lang=lang,
                    emotions=config["emotions"],
                )
            except Exception as e:
                traceback.print_exc()
                record_event(
                    {"type": "error", "message": "情绪分析失败：" + str(e)},
                    project_root=project_root,
                )
                return jsonify({"error": "emotion analysis failed: " + str(e)}), 500

            if state.get("analysis_cancel_seq", -1) >= seq:
                return jsonify({"status": "cancelled"}), 200

            emotion_map = {}
            for e in emotions:
                idx = e.get("index")
                if isinstance(idx, int) and not isinstance(idx, bool):
                    emotion_map[idx] = e.get("emotion") or "思考"
            for line in lines:
                line["emotion"] = emotion_map.get(line["index"], "思考")

        if lang == "ja" and not translations_ready:
            missing = [line for line in lines if not (line.get("translated_text") or "").strip()]
            if missing:
                try:
                    translations = translate_lines(
                        lines=missing,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                    )
                except Exception as e:
                    traceback.print_exc()
                    return jsonify({"error": "日语翻译失败: " + str(e)}), 500
                if state.get("analysis_cancel_seq", -1) >= seq:
                    return jsonify({"status": "cancelled"}), 200
                translation_map = {}
                for t in translations:
                    idx = t.get("index")
                    if idx is not None:
                        translation_map[idx] = t.get("translation", "")
                for line in missing:
                    corrected = _exact_pronunciation(line["text"], config.get("pronunciation", []))
                    line["translated_text"] = corrected or (
                        translation_map.get(line["index"], "").strip() or line["text"]
                    )

        if state.get("analysis_cancel_seq", -1) >= seq:
            return jsonify({"status": "cancelled"}), 200

        valid_chars = list(config["characters"].keys()) + ["旁白"]
        proofread = find_character_issues(lines, valid_chars)
        if translated_only:
            event_message = "情绪已是最新，已补齐日语翻译"
        elif reused:
            event_message = "情绪分析完成（已是最新）"
        else:
            event_message = f"情绪分析完成：共 {len(lines)} 条台词"
        record_event(
            {"type": "analyze", "message": event_message, "payload": {"count": len(lines), "reused": reused, "translated_only": translated_only}},
            project_root=project_root,
        )

        skipped = []
        raw_lines = script_text.strip().split("\n")
        parsed_line_nos = {line["line_no"] for line in lines}
        for skipped_no, raw in enumerate(raw_lines, start=1):
            if raw.strip() and skipped_no not in parsed_line_nos:
                skipped.append({"line_no": skipped_no, "text": raw.strip()[:100]})

        if not reused:
            push_history("日语翻译" if translated_only else "重新分析剧本")
            state["generated"] = {}
            state["time_info"] = []
            state["merged_path"] = None
            state["srt_path"] = None
            state["failures"] = {}
            state["progress"] = {"current": 0, "total": 0}
            state["srt_only"] = False
            state["current_record_id"] = None
        state["script"] = script_text
        state["lines"] = lines
        state["emotions"] = emotions
        state["lang"] = lang

        return jsonify({"lines": lines, "proofread": proofread, "skipped": skipped, "reused": reused, "emotions_reused": emotions_reused})

    @app.route("/api/analyze/prompt", methods=["POST"])
    def analyze_prompt():
        data = request.get_json(silent=True) or {}
        script_text = str(data.get("text") or "")
        lang = str(data.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        lines = parse_script(script_text)
        if not lines:
            return jsonify({"error": "no valid lines found"}), 400
        prompt = build_client_prompt(lines, config["emotions"], lang=lang, mode="analyze")
        return jsonify({"prompt": prompt})

    @app.route("/api/analyze/import", methods=["POST"])
    def analyze_import():
        data = request.get_json(silent=True) or {}
        script_text = str(data.get("text") or "")
        lang = str(data.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        result_text = str(data.get("result") or "")
        if not script_text.strip():
            return jsonify({"error": "script is empty"}), 400
        if not result_text.strip():
            return jsonify({"error": "请先粘贴 AI 客户端返回的 JSON 结果"}), 400
        lines = parse_script(script_text)
        if not lines:
            return jsonify({"error": "no valid lines found"}), 400
        for line in lines:
            line.setdefault("interval", DEFAULT_INTERVAL)
        try:
            items = parse_client_result(result_text)
        except Exception as e:
            return jsonify({"error": "结果解析失败：" + str(e)}), 400
        emotion_map = {}
        translation_map = {}
        for item in items:
            idx = item.get("index")
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            if item.get("emotion"):
                emotion_map[idx] = item["emotion"]
            if item.get("translation"):
                translation_map[idx] = item["translation"]
        existing_emotions = {l.get("index"): l.get("emotion") for l in (state.get("lines") or [])}
        for line in lines:
            line["emotion"] = emotion_map.get(line["index"]) or existing_emotions.get(line["index"]) or "思考"
            if lang == "ja":
                corrected = _exact_pronunciation(line["text"], config.get("pronunciation", []))
                line["translated_text"] = corrected or translation_map.get(line["index"], "").strip() or line["text"]
            else:
                line.setdefault("translated_text", "")
        valid_chars = list(config["characters"].keys()) + ["旁白"]
        proofread = find_character_issues(lines, valid_chars)
        skipped = []
        raw_lines = script_text.strip().split("\n")
        parsed_line_nos = {line["line_no"] for line in lines}
        for skipped_no, raw in enumerate(raw_lines, start=1):
            if raw.strip() and skipped_no not in parsed_line_nos:
                skipped.append({"line_no": skipped_no, "text": raw.strip()[:100]})
        push_history("粘贴 AI 分析结果")
        state["generated"] = {}
        state["time_info"] = []
        state["merged_path"] = None
        state["srt_path"] = None
        state["failures"] = {}
        state["progress"] = {"current": 0, "total": 0}
        state["srt_only"] = False
        state["current_record_id"] = None
        state["script"] = script_text
        state["lines"] = lines
        state["emotions"] = [{"index": l["index"], "emotion": l["emotion"]} for l in lines]
        state["lang"] = lang
        record_event(
            {"type": "analyze", "message": "客户端 AI 结果已应用：" + str(len(lines)) + " 条台词", "payload": {"count": len(lines), "manual": True}},
            project_root=project_root,
        )
        return jsonify({
            "lines": lines,
            "proofread": proofread,
            "skipped": skipped,
            "reused": False,
            "emotions_reused": False,
            "manual": True,
        })
    @app.route("/api/translate", methods=["POST"])
    def translate_only():
        data = request.get_json(silent=True) or {}
        script_text = data.get("text", "")
        api_key = data.get("api_key", "") or config["deepseek"]["api_key"]
        base_url = data.get("base_url") or config["deepseek"].get("base_url", "https://api.deepseek.com")
        model = data.get("model") or config["deepseek"].get("model", "deepseek-v4-flash")

        if not api_key:
            return jsonify({"error": "please configure DeepSeek API Key"}), 400

        existing = state.get("lines") or []
        if script_text.strip():
            lines = parse_script(script_text)
            if not lines:
                return jsonify({"error": "no valid lines found"}), 400
            for line in lines:
                line.setdefault("interval", DEFAULT_INTERVAL)
            old_by_index = {old.get("index"): old for old in existing}
            for line in lines:
                old = old_by_index.get(line.get("index"))
                if old:
                    if old.get("emotion"):
                        line["emotion"] = old["emotion"]
                    if old.get("interval") is not None:
                        line["interval"] = old["interval"]
            same = state.get("script") == script_text and len(existing) == len(lines)
            if same:
                for new_line, old_line in zip(lines, existing):
                    if new_line.get("text") != old_line.get("text") or new_line.get("character") != old_line.get("character"):
                        same = False
                        break
            if same and all((old.get("translated_text") or "") for old in existing):
                state["script"] = script_text
                state["lines"] = existing
                state["lang"] = "ja"
                return jsonify({"lines": existing, "count": len(existing), "reused": True})
        else:
            lines = copy.deepcopy(existing)
            if not lines:
                return jsonify({"error": "请先粘贴剧本或先分析情绪"}), 400

        seq = state.get("analysis_seq", 0) + 1
        state["analysis_seq"] = seq
        try:
            translations = translate_lines(
                lines=lines,
                api_key=api_key,
                base_url=base_url,
                model=model,
            )
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "日语翻译失败: " + str(e)}), 500

        if state.get("analysis_cancel_seq", -1) >= seq:
            return jsonify({"status": "cancelled"}), 200

        translation_map = {}
        for t in translations:
            idx = t.get("index")
            if idx is not None:
                translation_map[idx] = t.get("translation", "")
        for line in lines:
            corrected = _exact_pronunciation(line["text"], config.get("pronunciation", []))
            line["translated_text"] = corrected or (
                translation_map.get(line["index"], "").strip() or line["text"]
            )

        push_history("日语翻译")
        state["script"] = script_text.strip() or state.get("script", "")
        state["lines"] = lines
        state["lang"] = "ja"
        state["generated"] = {}
        state["time_info"] = []
        state["merged_path"] = None
        state["srt_path"] = None
        state["failures"] = {}
        state["progress"] = {"current": 0, "total": 0}
        state["srt_only"] = False
        state["current_record_id"] = None
        record_event(
            {"type": "translate", "message": f"日语翻译完成：共 {len(lines)} 条台词", "payload": {"count": len(lines)}},
            project_root=project_root,
        )
        return jsonify({"lines": lines, "count": len(lines), "reused": False})

    @app.route("/api/analyze/cancel", methods=["POST"])
    def cancel_analysis():
        state["analysis_cancel_seq"] = state.get("analysis_seq", 0)
        return jsonify({"status": "ok"})

    @app.route("/api/webgal/emotion_map", methods=["GET"])
    def get_webgal_emotion_map():
        return jsonify({
            "map": config.get("webgal_emotion_map", {}),
            "defaults": dict(DEFAULT_EMOTION_MAP),
        })

    @app.route("/api/webgal/emotion_map", methods=["POST"])
    def save_webgal_emotion_map():
        data = request.get_json(silent=True) or {}
        raw = data.get("map")
        if not isinstance(raw, dict):
            return jsonify({"error": "映射格式不正确"}), 400
        cleaned = {}
        for k, v in raw.items():
            key = str(k).strip().lower()
            val = str(v).strip()
            if key and val:
                cleaned[key] = val
        config["webgal_emotion_map"] = cleaned
        _persist_user_settings(project_root, config)
        return jsonify({"status": "ok", "map": cleaned})

    @app.route("/api/webgal/emotion_map/reset", methods=["POST"])
    def reset_webgal_emotion_map():
        config["webgal_emotion_map"] = dict(DEFAULT_EMOTION_MAP)
        _persist_user_settings(project_root, config)
        return jsonify({"status": "ok", "map": config["webgal_emotion_map"]})

    @app.route("/api/webgal/settings", methods=["POST"])
    def save_webgal_settings():
        data = request.get_json(silent=True) or {}
        if "retranslate_on_analyze" in data:
            config["webgal_retranslate_on_analyze"] = bool(data["retranslate_on_analyze"])
            _persist_user_settings(project_root, config)
        return jsonify({"status": "ok", "retranslate_on_analyze": bool(config.get("webgal_retranslate_on_analyze", True))})

    @app.route("/api/webgal/parse", methods=["POST"])
    def webgal_parse():
        data = request.get_json(silent=True) or {}
        text = str(data.get("text") or "")
        if not text.strip():
            return jsonify({"error": "请先粘贴 anogo 脚本"}), 400
        lang = str(data.get("lang") or state.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        entries = parse_webgal_script(text, emotion_map=config.get("webgal_emotion_map"), system_emotions=config.get("emotions", []))
        dialogues = [e for e in entries if e["type"] == "dialogue"]
        if not dialogues:
            return jsonify({"error": "没有解析到对话行，请确认粘贴的是 anogo 脚本"}), 400
        state["script"] = text
        state["lang"] = lang
        state["webgal"] = {
            "source": text,
            "entries": entries,
            "dialogues": dialogues,
            "emotions": {str(d["index"]): d["emotion"] for d in dialogues if d.get("emotion")},
            "translations": {},
            "generated": {},
            "failures": {},
            "progress": {"current": 0, "total": 0},
            "generating": False,
            "cancel_requested": False,
            "lastExport": "",
            "lang": lang,
        }
        return jsonify({
            "status": "ok",
            "count": len(dialogues),
            "lang": lang,
            "dialogues": [dialogue_summary(d) for d in dialogues],
        })

    @app.route("/api/webgal/sync", methods=["POST"])
    def webgal_sync():
        data = request.get_json(silent=True) or {}
        wg = state.get("webgal") or {}
        keys = ("source", "lang", "entries", "dialogues", "emotions", "translations", "generated", "failures", "psyVoice", "psyCharacter", "lastExport")
        for key in keys:
            if key in data:
                wg[key] = data[key]
        state["webgal"] = wg
        state["script"] = str(wg.get("source") or state.get("script", ""))
        return jsonify({"status": "ok"})

    @app.route("/api/webgal/translate", methods=["POST"])
    def webgal_translate():
        wg = state.get("webgal") or {}
        dialogues = wg.get("dialogues") or []
        if not dialogues:
            return jsonify({"error": "请先解析脚本"}), 400
        data = request.get_json(silent=True) or {}
        lang = str(data.get("lang") or wg.get("lang") or state.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        wg["lang"] = lang
        state["lang"] = lang
        if lang != "ja":
            wg["translations"] = {}
            return jsonify({"status": "ok", "translations": {}})
        api_key = config["deepseek"]["api_key"]
        if not api_key:
            return jsonify({"error": "please configure DeepSeek API Key"}), 400
        base_url = data.get("base_url") or config["deepseek"].get("base_url", "https://api.deepseek.com")
        model = data.get("model") or config["deepseek"].get("model", "deepseek-v4-flash")
        lines = [
            {"index": d["index"], "character": d["character"], "text": d["text"]}
            for d in dialogues
        ]
        try:
            translations = translate_lines(
                lines=lines,
                api_key=api_key,
                base_url=base_url,
                model=model,
            )
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "日语翻译失败: " + str(e)}), 500
        wg["translations"] = {}
        for t in translations:
            if t.get("index") is None:
                continue
            idx = int(t.get("index"))
            d = next((x for x in dialogues if x["index"] == idx), None)
            corrected = _exact_pronunciation(d["text"] if d else "", config.get("pronunciation", []))
            wg["translations"][str(idx)] = corrected or t.get("translation", "")
        return jsonify({"status": "ok", "translations": wg["translations"]})

    @app.route("/api/webgal/analyze", methods=["POST"])
    def webgal_analyze():
        wg = state.get("webgal") or {}
        dialogues = wg.get("dialogues") or []
        if not dialogues:
            return jsonify({"error": "请先解析脚本"}), 400
        api_key = config["deepseek"]["api_key"]
        if not api_key:
            return jsonify({"error": "please configure DeepSeek API Key"}), 400
        data = request.get_json(silent=True) or {}
        lang = str(data.get("lang") or wg.get("lang") or state.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        wg["lang"] = lang
        state["lang"] = lang
        seq = state.get("analysis_seq", 0) + 1
        state["analysis_seq"] = seq
        base_url = data.get("base_url") or config["deepseek"].get("base_url", "https://api.deepseek.com")
        model = data.get("model") or config["deepseek"].get("model", "deepseek-v4-flash")
        lines = [
            {"index": d["index"], "character": d["character"], "text": d["text"]}
            for d in dialogues
        ]
        try:
            emotions = analyze_emotions(
                lines=lines,
                api_key=api_key,
                base_url=base_url,
                model=model,
                lang=lang,
                emotions=config["emotions"],
            )
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "emotion analysis failed: " + str(e)}), 500
        if state.get("analysis_cancel_seq", -1) >= seq:
            return jsonify({"status": "cancelled"}), 200
        emotion_map = {}
        for e in emotions:
            idx = e.get("index")
            if isinstance(idx, int) and not isinstance(idx, bool):
                emotion_map[idx] = e.get("emotion") or "思考"
        wg["emotions"] = {str(idx): emotion_map.get(idx, "思考") for idx in [d["index"] for d in dialogues]}
        if lang == "ja":
            existing = wg.get("translations") or {}
            if config.get("webgal_retranslate_on_analyze", True):
                missing = lines
            else:
                missing = [l for l in lines if not (existing.get(str(l["index"])) or "").strip()]
            if missing:
                try:
                    translations = translate_lines(
                        lines=missing,
                        api_key=api_key,
                        base_url=base_url,
                        model=model,
                    )
                except Exception as e:
                    traceback.print_exc()
                    return jsonify({"error": "日语翻译失败: " + str(e)}), 500
                for t in translations:
                    if t.get("index") is None:
                        continue
                    idx = int(t.get("index"))
                    d = next((x for x in dialogues if x["index"] == idx), None)
                    corrected = _exact_pronunciation(d["text"] if d else "", config.get("pronunciation", []))
                    existing[str(idx)] = corrected or t.get("translation", "")
            wg["translations"] = existing
        else:
            wg["translations"] = {}
        if state.get("analysis_cancel_seq", -1) >= seq:
            return jsonify({"status": "cancelled"}), 200
        return jsonify({"status": "ok", "emotions": wg["emotions"], "translations": wg.get("translations", {})})

    @app.route("/api/webgal/analyze/prompt", methods=["POST"])
    def webgal_analyze_prompt():
        wg = state.get("webgal") or {}
        dialogues = wg.get("dialogues") or []
        if not dialogues:
            return jsonify({"error": "请先解析脚本"}), 400
        data = request.get_json(silent=True) or {}
        lang = str(data.get("lang") or wg.get("lang") or state.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        mode = str(data.get("mode") or "analyze")
        if mode not in ("analyze", "translate"):
            mode = "analyze"
        lines = [{"index": d["index"], "character": d["character"], "text": d["text"]} for d in dialogues]
        prompt = build_client_prompt(lines, config["emotions"], lang=lang, mode=mode)
        return jsonify({"prompt": prompt})

    @app.route("/api/webgal/analyze/import", methods=["POST"])
    def webgal_analyze_import():
        wg = state.get("webgal") or {}
        dialogues = wg.get("dialogues") or []
        if not dialogues:
            return jsonify({"error": "请先解析脚本"}), 400
        data = request.get_json(silent=True) or {}
        lang = str(data.get("lang") or wg.get("lang") or state.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        result_text = str(data.get("result") or "")
        if not result_text.strip():
            return jsonify({"error": "请先粘贴 AI 客户端返回的 JSON 结果"}), 400
        lines = [{"index": d["index"], "character": d["character"], "text": d["text"]} for d in dialogues]
        try:
            items = parse_client_result(result_text)
        except Exception as e:
            return jsonify({"error": "结果解析失败：" + str(e)}), 400
        emotion_map = {}
        translation_map = {}
        for item in items:
            idx = item.get("index")
            if not isinstance(idx, int) or isinstance(idx, bool):
                continue
            if item.get("emotion"):
                emotion_map[idx] = item["emotion"]
            if item.get("translation"):
                translation_map[idx] = item["translation"]
        current_emotions = wg.get("emotions") or {}
        for d in dialogues:
            idx = d["index"]
            current_emotions[str(idx)] = emotion_map.get(idx) or current_emotions.get(str(idx)) or "思考"
        wg["emotions"] = current_emotions
        if lang == "ja":
            current_translations = wg.get("translations") or {}
            for d in dialogues:
                idx = d["index"]
                corrected = _exact_pronunciation(d["text"], config.get("pronunciation", []))
                current_translations[str(idx)] = corrected or translation_map.get(idx, "").strip() or current_translations.get(str(idx), "") or d["text"]
            wg["translations"] = current_translations
        else:
            wg["translations"] = {}
        wg["lang"] = lang
        state["lang"] = lang
        record_event(
            {"type": "analyze", "message": "WebGaL 客户端 AI 结果已应用：" + str(len(dialogues)) + " 条对话", "payload": {"count": len(dialogues), "manual": True}},
            project_root=project_root,
        )
        return jsonify({"status": "ok", "emotions": wg["emotions"], "translations": wg.get("translations", {})})
    @app.route("/api/webgal/generate", methods=["POST"])
    def webgal_generate():
        wg = state.get("webgal") or {}
        dialogues = wg.get("dialogues") or []
        if not dialogues:
            return jsonify({"error": "请先解析脚本"}), 400
        if wg.get("generating"):
            return jsonify({"error": "generation in progress"}), 409
        data = request.get_json(silent=True) or {}
        emotions = data.get("emotions") or {}
        psy_voice = bool(data.get("psy_voice"))
        psy_character = str(data.get("psy_character") or "").strip()
        lang = str(data.get("lang") or wg.get("lang") or state.get("lang") or "zh")
        if lang not in ("zh", "ja"):
            lang = "zh"
        wg["lang"] = lang
        state["lang"] = lang
        requested = data.get("indices")
        if not requested:
            requested = [d["index"] for d in dialogues]
        requested = [i for i in requested if isinstance(i, int) and any(d["index"] == i for d in dialogues)]
        if not requested:
            return jsonify({"error": "没有可生成的台词"}), 400

        for d in dialogues:
            d["emotion"] = str(emotions.get(str(d["index"])) or d.get("emotion") or "思考")
            d["voice_psy"] = psy_voice
            d["psy_character"] = psy_character

        wg["generating"] = True
        wg["cancel_requested"] = False
        wg["psyVoice"] = psy_voice
        wg["psyCharacter"] = psy_character
        wg["failures"] = {}
        wg["progress"] = {"current": 0, "total": len(requested)}
        for idx in requested:
            wg["generated"].pop(str(idx), None)

        def fail(idx, message):
            wg["generated"].pop(str(idx), None)
            wg["failures"][str(idx)] = message
            wg["progress"]["current"] += 1

        def generate_webgal_worker():
            try:
                worker_script = project_root / "backend" / "tts_worker.py"
                engine = get_engine(
                    config["gptsovits_path"],
                    project_root=project_root,
                    worker_script=str(worker_script) if worker_script.exists() else None,
                )
                engine.load()
                out_dir = Path(config["output_dir"]) / "webgal"
                out_dir.mkdir(parents=True, exist_ok=True)
                for idx in requested:
                    if wg["cancel_requested"]:
                        break
                    d = next(x for x in dialogues if x["index"] == idx)
                    char = d["character"]
                    if d["is_psy"] and psy_voice and psy_character:
                        char = psy_character
                    if d["is_psy"] and not psy_voice:
                        fail(idx, "心理活动未开启配音")
                        continue
                    if char not in config["characters"]:
                        fail(idx, f"路人/未配置角色「{d['character']}」，不生成音频")
                        continue
                    emotion = d["emotion"]
                    if emotion not in config["emotions"]:
                        fail(idx, f"情绪「{emotion}」不在当前情绪列表，请重新分析或手动选择")
                        continue
                    ref = pick_ref_audio(char, emotion)
                    if ref is None:
                        fail(idx, f"缺少参考音频：{char}「{emotion}」")
                        continue
                    try:
                        engine.switch_character(
                            config["characters"][char]["model"],
                            config["characters"][char].get("gpt_model"),
                        )
                    except Exception as e:
                        fail(idx, f"角色模型加载失败：{str(e)[-200:]}")
                        continue
                    tts_text = d["text"]
                    if d["is_psy"]:
                        tts_text = tts_text.strip("（）()").strip()
                    if lang == "ja":
                        corrected = _exact_pronunciation(d["text"], config.get("pronunciation", []))
                        if corrected:
                            tts_text = corrected
                        else:
                            tts_text = (wg.get("translations") or {}).get(str(idx), "") or ""
                            if not tts_text:
                                tts_text = correct_pronunciation(d["text"], config.get("pronunciation", [])) or d["text"]
                    ref_prompt = ref.get("prompt_text") or ""
                    output_path = out_dir / f"{idx:04d}_{char}_{emotion}.wav"
                    emo_params = {}
                    if config.get("use_emotion_params", True):
                        emo_params = config.get("emotion_params", {}).get(emotion) or {}
                    tts_cfg = config["tts"]

                    def _param(key, default):
                        v = emo_params.get(key)
                        return default if v is None or v == "" else v

                    try:
                        duration = engine.synthesize_to_file(
                            text=tts_text,
                            ref_audio_path=ref["path"],
                            prompt_text=ref_prompt,
                            output_path=str(output_path),
                            text_lang=lang,
                            prompt_lang=_detect_prompt_lang(ref_prompt, lang),
                            text_split_method=tts_cfg.get("text_split_method", "cut5"),
                            batch_size=tts_cfg.get("batch_size", 1),
                            speed_factor=_param("speed_factor", tts_cfg.get("speed_factor", 1.0)),
                            fragment_interval=tts_cfg.get("fragment_interval", 0.3),
                            temperature=_param("temperature", tts_cfg.get("temperature", 1.0)),
                            top_k=_param("top_k", tts_cfg.get("top_k", 15)),
                            top_p=_param("top_p", tts_cfg.get("top_p", 1.0)),
                            seed=_param("seed", tts_cfg.get("seed", -1)),
                        )
                    except Exception as e:
                        fail(idx, f"{char} 生成失败：{str(e)[-300:]}")
                        continue
                    wg["generated"][str(idx)] = {
                        "path": str(output_path),
                        "duration": duration,
                        "character": char,
                        "emotion": emotion,
                    }
                    wg["progress"]["current"] += 1
            except Exception as e:
                traceback.print_exc()
                state["error"] = str(e)
            finally:
                wg["generating"] = False

        threading.Thread(target=generate_webgal_worker, daemon=True).start()
        return jsonify({"status": "started", "total": len(requested)})

    @app.route("/api/webgal/cancel", methods=["POST"])
    def webgal_cancel():
        wg = state.get("webgal") or {}
        if not wg.get("generating"):
            return jsonify({"status": "ok", "already_stopped": True})
        wg["cancel_requested"] = True
        return jsonify({"status": "cancelling"})

    @app.route("/api/webgal/progress", methods=["GET"])
    def webgal_progress():
        wg = state.get("webgal") or {}
        return jsonify({
            "generating": bool(wg.get("generating")),
            "progress": wg.get("progress", {"current": 0, "total": 0}),
            "failures": wg.get("failures", {}),
            "generated": {
                k: {"duration": v.get("duration")}
                for k, v in (wg.get("generated") or {}).items()
            },
        })

    @app.route("/api/webgal/audio/<int:index>", methods=["GET"])
    def webgal_audio(index):
        wg = state.get("webgal") or {}
        gen = (wg.get("generated") or {}).get(str(index))
        if not gen:
            return jsonify({"error": "该台词还没有音频"}), 404
        path = gen["path"]
        if not Path(path).exists():
            return jsonify({"error": "音频文件不存在"}), 404
        return send_file(path, mimetype="audio/wav")

    @app.route("/api/webgal/pick-export-dir", methods=["POST"])
    def webgal_pick_export_dir():
        try:
            picked = _pick_folder_dialog()
        except Exception as e:
            return jsonify({"error": "选择导出位置失败: " + str(e)}), 500
        if not picked:
            return jsonify({"status": "cancelled", "path": ""})
        return jsonify({"status": "ok", "path": picked})

    @app.route("/api/webgal/export", methods=["POST"])
    def webgal_export():
        wg = state.get("webgal") or {}
        dialogues = wg.get("dialogues") or []
        if not dialogues:
            return jsonify({"error": "请先解析脚本"}), 400
        if wg.get("generating"):
            return jsonify({"error": "生成中，请稍后再导出"}), 409
        data = request.get_json(silent=True) or {}
        folder_name = str(data.get("folder_name") or "").strip()
        if not folder_name:
            return jsonify({"error": "请输入导出文件夹名称"}), 400
        output_dir = str(data.get("output_dir") or "").strip()
        if output_dir and not Path(output_dir).is_dir():
            return jsonify({"error": "指定的导出位置不存在，请重新选择"}), 400
        if re.search(r'[\\/:*?"<>|\r\n]', folder_name) or folder_name in (".", ".."):
            return jsonify({"error": "文件夹名称包含非法字符"}), 400
        if len(folder_name) > 64:
            return jsonify({"error": "文件夹名称过长"}), 400
        export_root = project_root / "exports"
        export_root.mkdir(parents=True, exist_ok=True)
        export_dir = export_root / folder_name
        if export_dir.exists():
            return jsonify({
                "error": f"文件夹「{folder_name}」已存在，请换一个名称或手动删除旧文件夹",
                "code": "folder_exists",
            }), 409
        export_dir.mkdir(parents=True)
        try:
            audio_map = {}
            created = []
            for d in dialogues:
                gen = (wg.get("generated") or {}).get(str(d["index"]))
                if not gen or not Path(gen["path"]).exists():
                    continue
                short_dir = short_name_for(gen.get("character") or d["character"])
                target_dir = export_dir / short_dir
                target_dir.mkdir(parents=True, exist_ok=True)
                target = target_dir / Path(gen["path"]).name
                shutil.copy2(gen["path"], str(target))
                audio_map[d["index"]] = (short_dir + "/" + target.name).replace("\\", "/")
                created.append(str(target))
                if output_dir:
                    game_dir = Path(output_dir) / short_dir
                    game_dir.mkdir(parents=True, exist_ok=True)
                    game_target = game_dir / Path(gen["path"]).name
                    shutil.copy2(gen["path"], str(game_target))
                    created.append(str(game_target))
            output_script = render_script(wg.get("entries") or [], audio_map)
            script_path = export_dir / "script.txt"
            script_path.write_text(output_script, encoding="utf-8")
            (export_dir / "original_script.txt").write_text(wg.get("source") or "", encoding="utf-8")
            unvoiced = []
            for d in dialogues:
                if d["index"] in audio_map:
                    continue
                msg = (wg.get("failures") or {}).get(str(d["index"]))
                unvoiced.append(f"#{d['index'] + 1} {d['character']}：{d['text']} —— {msg or '未生成音频'}")
            (export_dir / "unvoiced.txt").write_text("\n".join(unvoiced), encoding="utf-8")
            created.append(str(script_path))
            created.append(str(export_dir / "original_script.txt"))
            created.append(str(export_dir / "unvoiced.txt"))
            try:
                attach_export_to_record({
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "folder": str(export_dir),
                    "webgal": True,
                })
            except Exception:
                pass
            wg["lastExport"] = str(export_dir)
            return jsonify({
                "status": "ok",
                "folder": str(export_dir),
                "files": created,
                "voiced": len(audio_map),
                "unvoiced": len(unvoiced),
                "audio_copy_dir": str(Path(output_dir)) if output_dir else "",
            })
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "导出失败: " + str(e)}), 500

    @app.route("/api/line/<int:index>", methods=["PUT"])
    def update_line(index):
        data = request.get_json() or {}
        if index < 0 or index >= len(state["lines"]):
            return jsonify({"error": "invalid index"}), 400
        line = state["lines"][index]
        had_generated = False
        reanalyze_error = None
        reanalyze_cancelled = False
        history_pushed = False

        def record_history(label):
            nonlocal history_pushed
            if not history_pushed:
                push_history(label)
                history_pushed = True

        def invalidate_segment():
            nonlocal had_generated
            if index in state["generated"]:
                had_generated = True
                gen = state["generated"].pop(index, None)
                if gen:
                    try:
                        old_path = Path(gen["path"])
                        if old_path.exists():
                            old_path.unlink()
                    except Exception:
                        pass
            state["failures"].pop(index, None)

        if "emotion" in data:
            if data["emotion"] not in config["emotions"]:
                return jsonify({"error": "invalid emotion"}), 400
            old_emotion = line.get("emotion")
            new_emotion = data["emotion"]
            if old_emotion and old_emotion != new_emotion:
                record_history("修改情绪")
                record_event(
                    {
                        "type": "emotion_correction",
                        "message": f"情绪修正：第{index + 1}行 {line['character']} {old_emotion} → {new_emotion}",
                        "payload": {"index": index, "character": line["character"], "old": old_emotion, "new": new_emotion},
                    },
                    project_root=project_root,
                )
                invalidate_segment()
            line["emotion"] = new_emotion
        if "text" in data:
            new_text = data["text"]
            if not isinstance(new_text, str) or not new_text.strip():
                return jsonify({"error": "台词不能为空"}), 400
            new_text = new_text.strip()
            if new_text != line.get("text"):
                record_history("修改台词")
                old_text = line.get("text")
                old_emotion = line.get("emotion")
                line["text"] = new_text
                if data.get("reanalyze", True):
                    try:
                        seq = state.get("analysis_seq", 0) + 1
                        state["analysis_seq"] = seq
                        api_key = data.get("api_key", "") or config["deepseek"]["api_key"]
                        base_url = data.get("base_url") or config["deepseek"].get("base_url", "https://api.deepseek.com")
                        model = data.get("model") or config["deepseek"].get("model", "deepseek-v4-flash")
                        single = {**line, "index": 0}
                        emotions = analyze_emotions(
                            lines=[single],
                            api_key=api_key,
                            base_url=base_url,
                            model=model,
                            lang=state.get("lang", "zh"),
                            emotions=config["emotions"],
                        )
                        if state.get("analysis_cancel_seq", -1) >= seq:
                            invalidate_segment()
                            line["emotion"] = old_emotion
                            reanalyze_cancelled = True
                            record_event(
                                {
                                    "type": "line_edit",
                                    "message": f"台词修改：第{index + 1}行 已停止重新分析，文本已保存",
                                    "payload": {"index": index},
                                },
                                project_root=project_root,
                            )
                        else:
                            emotion = "思考"
                            for e in emotions:
                                if e.get("index") == 0:
                                    emotion = e.get("emotion") or "思考"
                                    break
                            line["emotion"] = emotion
                            if state.get("lang") == "ja":
                                translations = translate_lines(
                                    lines=[single],
                                    api_key=api_key,
                                    base_url=base_url,
                                    model=model,
                                )
                                translated = next((t.get("translation", "") for t in translations if t.get("index") == 0), "").strip()
                                corrected = _exact_pronunciation(line["text"], config.get("pronunciation", []))
                                line["translated_text"] = corrected or translated or line["text"]
                            invalidate_segment()
                            record_event(
                                {
                                    "type": "line_edit",
                                    "message": f"台词修改：第{index + 1}行 {line['character']} 已重新分析情绪",
                                    "payload": {"index": index, "text": line["text"], "emotion": line["emotion"]},
                                },
                                project_root=project_root,
                            )
                    except Exception as e:
                        traceback.print_exc()
                        reanalyze_error = str(e)
                        invalidate_segment()
                        state["failures"][index] = "文本已修改，情绪重新分析失败：" + str(e)[-200:]
                        record_event(
                            {
                                "type": "line_edit",
                                "message": f"台词修改：第{index + 1}行 文本已保存，情绪重分析失败",
                                "payload": {"index": index, "error": str(e)[-200:]},
                            },
                            project_root=project_root,
                        )
                else:
                    invalidate_segment()
                    record_event(
                        {
                            "type": "line_edit",
                            "message": f"台词修改：第{index + 1}行 文本已保存（未重新分析）",
                            "payload": {"index": index},
                        },
                        project_root=project_root,
                    )
        if "character" in data:
            new_char = data["character"]
            if not isinstance(new_char, str) or not new_char.strip():
                return jsonify({"error": "角色名不能为空"}), 400
            new_char = new_char.strip()
            if new_char != line.get("character"):
                record_history("修改角色")
                old_char = line.get("character")
                line["character"] = new_char
                invalidate_segment()
                record_event(
                    {
                        "type": "character_edit",
                        "message": f"角色名修改：第{index + 1}行 {old_char} → {new_char}",
                        "payload": {"index": index, "old": old_char, "new": new_char},
                    },
                    project_root=project_root,
                )
        if "interval" in data:
            try:
                interval = float(data["interval"])
            except (TypeError, ValueError):
                return jsonify({"error": "间隔时间必须是数字"}), 400
            if not 0 <= interval <= 10:
                return jsonify({"error": "间隔时间需在 0-10 秒之间"}), 400
            interval = round(interval, 3)
            if line.get("interval") != interval:
                record_history("修改间隔")
                line["interval"] = interval
        resp = {"status": "ok", "line": line, "had_generated": had_generated}
        if reanalyze_error:
            resp["reanalyze_error"] = reanalyze_error
        if reanalyze_cancelled:
            resp["reanalyze_cancelled"] = True
        return jsonify(resp)

    @app.route("/api/lines/interval", methods=["POST"])
    def update_lines_interval():
        data = request.get_json() or {}
        indices = data.get("indices", [])
        try:
            interval = float(data.get("interval"))
        except (TypeError, ValueError):
            return jsonify({"error": "间隔时间必须是数字"}), 400
        if not 0 <= interval <= 10:
            return jsonify({"error": "间隔时间需在 0-10 秒之间"}), 400
        if not isinstance(indices, list) or not indices:
            return jsonify({"error": "请选择至少一条台词"}), 400
        valid = [
            idx for idx in indices
            if isinstance(idx, int) and not isinstance(idx, bool)
            and 0 <= idx < len(state["lines"])
        ]
        if not valid:
            return jsonify({"error": "没有有效的台词索引"}), 400
        interval = round(interval, 3)
        changed = [idx for idx in valid if state["lines"][idx].get("interval") != interval]
        if changed:
            push_history("批量修改间隔")
            for idx in changed:
                state["lines"][idx]["interval"] = interval
        return jsonify({"status": "ok", "updated": len(changed), "indices": changed})

    @app.route("/api/lines/characters", methods=["POST"])
    def update_lines_characters():
        data = request.get_json() or {}
        fixes = data.get("fixes") or {}
        if not isinstance(fixes, dict):
            return jsonify({"error": "无效的修正数据"}), 400
        pending = []
        for line in state["lines"]:
            old = line.get("character")
            new = fixes.get(old)
            if isinstance(new, str) and new.strip() and new.strip() != old:
                pending.append((line, new.strip()))
        if pending:
            push_history("批量修改角色")
            for line, new in pending:
                line["character"] = new
        return jsonify({"status": "ok", "updated": len(pending)})

    @app.route("/api/logs", methods=["GET"])
    def get_logs():
        events = read_events(limit=200, project_root=project_root)
        return jsonify({"events": events})

    @app.route("/api/logs", methods=["POST"])
    def add_log():
        data = request.get_json() or {}
        record_event(
            {
                "type": data.get("type", "event"),
                "message": data.get("message", ""),
                "payload": data.get("payload", {}),
            },
            project_root=project_root,
        )
        return jsonify({"status": "ok"})

    @app.route("/api/logs/export", methods=["GET"])
    def export_logs():
        events_path = Path(project_root) / "feedback" / "events.jsonl"
        if not events_path.exists():
            return jsonify({"error": "暂无日志"}), 404
        return send_file(
            str(events_path),
            as_attachment=True,
            download_name="feedback-events.jsonl",
            mimetype="application/json",
        )
    @app.route("/api/logs/reset", methods=["POST"])
    def reset_logs():
        events_path = Path(project_root) / "feedback" / "events.jsonl"
        if events_path.exists():
            events_path.write_text("", encoding="utf-8")
        return jsonify({"status": "ok"})
    @app.route("/api/ref_audio/<character>/<emotion>", methods=["GET"])
    def list_ref_audio(character, emotion):
        if character not in config["characters"]:
            return jsonify({"error": "unknown character"}), 400
        ref_dir = Path(config["characters"][character]["ref_audio_dir"]) / emotion
        if not ref_dir.exists():
            return jsonify({"files": []})
        audio_exts = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
        files = []
        for f in ref_dir.iterdir():
            if f.suffix.lower() in audio_exts:
                files.append(f.name)
        return jsonify({"files": sorted(files)})

    def pick_ref_audio(character, emotion):
        ref_dir = Path(config["characters"][character]["ref_audio_dir"]) / emotion
        if not ref_dir.exists():
            ref_dir.mkdir(parents=True, exist_ok=True)
        audio_exts = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}
        files = []
        for f in ref_dir.iterdir():
            if f.suffix.lower() in audio_exts and f.is_file():
                files.append(f)
        if not files:
            return None
        chosen = random.choice(files)
        return {"path": str(chosen), "prompt_text": _read_ref_prompt(chosen)}

    AUDIO_EXTS = {".wav", ".mp3", ".flac", ".ogg", ".m4a", ".aac"}

    def _ref_audio_base(key):
        m = models.get(key)
        if not m:
            return None
        base = Path(str(m.get("ref_audio_dir") or ("reference_audio/" + key)))
        if not base.is_absolute():
            base = project_root / base
        return base

    def _ref_audio_target(key, emotion, name):
        base = _ref_audio_base(key)
        if base is None or emotion not in config["emotions"]:
            return None
        safe_name = Path(str(name)).name.strip()
        safe_name = re.sub(r"[\x00-\x1f<>:\"/\\|?*]", "_", safe_name)
        if not safe_name or Path(safe_name).suffix.lower() not in AUDIO_EXTS:
            return None
        return base / emotion / safe_name

    def _ref_prompt_path(target):
        return Path(str(target) + ".txt")

    def _read_ref_prompt(target):
        p = _ref_prompt_path(target)
        if not p.exists():
            return ""
        try:
            return p.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return ""

    def _write_ref_prompt(target, text):
        p = _ref_prompt_path(target)
        text = (text or "").strip()
        try:
            if text:
                p.write_text(text, encoding="utf-8")
            elif p.exists():
                p.unlink()
        except Exception:
            pass

    def _remove_ref_prompt(target):
        p = _ref_prompt_path(target)
        if p.exists():
            try:
                p.unlink()
            except Exception:
                pass

    def _detect_prompt_lang(text, fallback="zh"):
        if not text:
            return fallback
        kana = sum(1 for ch in text if "\u3040" <= ch <= "\u30ff" or "\u31f0" <= ch <= "\u31ff")
        han = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        if kana > 0:
            return "ja"
        if han > 0:
            return "zh"
        return fallback

    @app.route("/api/reference/audio", methods=["GET"])
    def list_reference_audio():
        items = []
        for key, m in models.items():
            name = DEFAULT_MODEL_ALIASES.get(key, key)
            base = _ref_audio_base(key)
            if base is None:
                continue
            emotions = []
            for emo in config["emotions"]:
                d = base / emo
                files = []
                if d.is_dir():
                    for f in d.iterdir():
                        if f.is_file() and f.suffix.lower() in AUDIO_EXTS:
                            try:
                                size = f.stat().st_size
                            except Exception:
                                size = 0
                            files.append({"name": f.name, "size": size, "prompt_text": _read_ref_prompt(f)})
                files.sort(key=lambda x: str(x["name"]).lower())
                emotions.append({"emotion": emo, "files": files})
            items.append({
                "key": key,
                "name": name,
                "ref_dir": base.name,
                "ref_path": str(base),
                "emotions": emotions,
            })
        return jsonify({"root": str(project_root / "reference_audio"), "items": items})

    @app.route("/api/reference/audio/upload", methods=["POST"])
    def upload_reference_audio():
        key = str(request.form.get("key") or "").strip()
        emotion = str(request.form.get("emotion") or "").strip()
        f = request.files.get("file")
        if key not in models:
            return jsonify({"error": "角色不存在"}), 400
        if emotion not in config["emotions"]:
            return jsonify({"error": "情绪不存在"}), 400
        if not f or not f.filename:
            return jsonify({"error": "未选择文件"}), 400
        target = _ref_audio_target(key, emotion, f.filename)
        if target is None:
            return jsonify({"error": "仅支持音频文件"}), 400
        target.parent.mkdir(parents=True, exist_ok=True)
        stem = target.stem
        suffix = target.suffix
        counter = 1
        while target.exists():
            target = target.with_name(stem + "_" + str(counter) + suffix)
            counter += 1
        prompt_text = str(request.form.get("prompt_text") or "").strip()
        try:
            f.save(str(target))
        except Exception as e:
            return jsonify({"error": "保存失败: " + str(e)}), 500
        _write_ref_prompt(target, prompt_text)
        record_event({
            "type": "ref_audio_upload",
            "message": "上传参考音频：" + key + "「" + emotion + "」",
            "payload": {"character": key, "emotion": emotion, "file": target.name},
        }, project_root)
        return jsonify({"status": "ok", "name": target.name, "size": target.stat().st_size, "prompt_text": prompt_text})

    @app.route("/api/reference/audio/prompt", methods=["POST"])
    def update_reference_audio_prompt():
        data = request.get_json() or {}
        key = str(data.get("key") or "").strip()
        emotion = str(data.get("emotion") or "").strip()
        name = str(data.get("name") or "").strip()
        prompt_text = str(data.get("prompt_text") or "").strip()
        target = _ref_audio_target(key, emotion, name)
        if target is None or not target.exists() or not target.is_file():
            return jsonify({"error": "文件不存在"}), 404
        _write_ref_prompt(target, prompt_text)
        record_event({
            "type": "ref_audio_prompt",
            "message": "更新参考音频字幕：" + key + "「" + emotion + "」" + name,
            "payload": {"character": key, "emotion": emotion, "file": name},
        }, project_root)
        return jsonify({"status": "ok", "prompt_text": prompt_text})

    @app.route("/api/reference/audio/file", methods=["GET"])
    def play_reference_audio():
        key = request.args.get("key", "")
        emotion = request.args.get("emotion", "")
        name = request.args.get("name", "")
        target = _ref_audio_target(key, emotion, name)
        if target is None or not target.exists() or not target.is_file():
            return jsonify({"error": "文件不存在"}), 404
        return send_file(str(target))

    @app.route("/api/reference/audio", methods=["DELETE"])
    def delete_reference_audio():
        data = request.get_json() or {}
        key = str(data.get("key") or "").strip()
        emotion = str(data.get("emotion") or "").strip()
        name = str(data.get("name") or "").strip()
        target = _ref_audio_target(key, emotion, name)
        if target is None or not target.exists() or not target.is_file():
            return jsonify({"error": "文件不存在"}), 404
        base = _ref_audio_base(key).resolve()
        target_res = target.resolve()
        try:
            target_res.relative_to(base)
        except ValueError:
            return jsonify({"error": "非法路径"}), 400
        try:
            target.unlink()
            _remove_ref_prompt(target)
        except Exception as e:
            return jsonify({"error": "删除失败: " + str(e)}), 500
        record_event({
            "type": "ref_audio_delete",
            "message": "删除参考音频：" + key + "「" + emotion + "」" + name,
            "payload": {"character": key, "emotion": emotion, "file": name},
        }, project_root)
        return jsonify({"status": "ok"})

    def _share_param_payload(kind):
        if kind == "pronunciation":
            return {
                "schema": 1,
                "kind": "pronunciation",
                "app_version": APP_VERSION,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": {"entries": [dict(x) for x in (config.get("pronunciation") or [])]},
            }
        if kind == "emotion_params":
            return {
                "schema": 1,
                "kind": "emotion_params",
                "app_version": APP_VERSION,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": {
                    "params": config.get("emotion_params", {}),
                    "enabled": bool(config.get("use_emotion_params", True)),
                    "presets": config.get("emotion_param_presets", {}),
                    "emotions": list(config["emotions"]),
                },
            }
        if kind == "webgal_map":
            return {
                "schema": 1,
                "kind": "webgal_map",
                "app_version": APP_VERSION,
                "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "data": {
                    "map": dict(config.get("webgal_emotion_map", {})),
                    "emotions": list(config["emotions"]),
                },
            }
        return None

    @app.route("/api/share/export", methods=["GET", "POST"])
    def share_export():
        now = time.strftime("%Y%m%d_%H%M%S")
        save_path = ""
        if request.method == "POST":
            data = request.get_json(silent=True) or {}
            kind = str(data.get("type") or "").strip()
            save_path = str(data.get("save_path") or "").strip()
        else:
            kind = str(request.args.get("type") or "").strip()
        if kind != "audio" and _share_param_payload(kind) is None:
            return jsonify({"error": "未知导出类型"}), 400
        default_name = _share_export_filename(kind, now)
        if save_path:
            return _share_write_export(kind, save_path, now)
        if request.method == "POST":
            picked = _pick_save_dialog(default_name, _share_filetypes(kind), project_root / "exports")
            if not picked:
                return jsonify({"status": "cancelled", "message": "已取消导出"})
            return _share_write_export(kind, picked, now)
        if kind == "audio":
            return _share_send_audio(now)
        payload = _share_param_payload(kind)
        raw = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
        return send_file(
            io.BytesIO(raw),
            as_attachment=True,
            download_name=default_name,
            mimetype="application/json",
        )

    def _share_export_filename(kind, now):
        names = {
            "pronunciation": "its_our_cry_纠音词典_" + now + ".json",
            "emotion_params": "its_our_cry_情绪参数模板_" + now + ".json",
            "webgal_map": "its_our_cry_脚本情绪映射_" + now + ".json",
            "audio": "its_our_cry_参考音频库_" + now + ".zip",
        }
        return names.get(kind, "its_our_cry_export_" + now + ".json")

    def _share_filetypes(kind):
        if kind == "audio":
            return {"desc": "ZIP 压缩包", "pattern": "*.zip", "ext": ".zip"}
        return {"desc": "JSON 预设", "pattern": "*.json", "ext": ".json"}

    def _share_write_export(kind, save_path, now):
        try:
            target = Path(save_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            if kind == "audio":
                tmp = _share_build_audio_zip(now)
                try:
                    shutil.copyfile(str(tmp), str(target))
                finally:
                    try:
                        tmp.unlink(missing_ok=True)
                    except Exception:
                        pass
            else:
                payload = _share_param_payload(kind)
                target.write_bytes(json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"))
            record_event({
                "type": "share_export",
                "message": "导出分享文件：" + target.name,
                "payload": {"kind": kind, "path": str(target)},
            }, project_root)
            return jsonify({"status": "ok", "path": str(target), "dir": str(target.parent), "message": "导出完成：" + str(target)})
        except Exception as e:
            return jsonify({"error": "导出失败: " + str(e)}), 500

    def _share_build_audio_zip(now):
        tmp = Path(tempfile.gettempdir()) / ("its_our_cry_audio_" + uuid.uuid4().hex + ".zip")
        manifest_files = []
        characters = []
        try:
            with zipfile.ZipFile(str(tmp), "w", zipfile.ZIP_DEFLATED) as zf:
                for key, m in models.items():
                    base = _ref_audio_base(key)
                    if base is None or not base.is_dir():
                        continue
                    rel_dir = base.name
                    characters.append({
                        "key": key,
                        "name": DEFAULT_MODEL_ALIASES.get(key, key),
                        "dir": rel_dir,
                    })
                    for emo_dir in sorted(p for p in base.iterdir() if p.is_dir()):
                        emotion = emo_dir.name
                        if emotion not in config["emotions"]:
                            continue
                        for f in sorted(emo_dir.iterdir()):
                            if not f.is_file():
                                continue
                            if f.suffix.lower() in AUDIO_EXTS:
                                arc = "reference_audio/" + rel_dir + "/" + emotion + "/" + f.name
                                zf.write(str(f), arc)
                                manifest_files.append({
                                    "zip_path": arc,
                                    "key": key,
                                    "character": DEFAULT_MODEL_ALIASES.get(key, key),
                                    "emotion": emotion,
                                    "name": f.name,
                                    "prompt_text": _read_ref_prompt(f),
                                })
                                prompt_path = _ref_prompt_path(f)
                                if prompt_path.exists():
                                    zf.write(str(prompt_path), arc + ".txt")
                zf.writestr("manifest.json", json.dumps({
                    "schema": 1,
                    "kind": "audio",
                    "app_version": APP_VERSION,
                    "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "emotions": list(config["emotions"]),
                    "characters": characters,
                    "files": manifest_files,
                }, ensure_ascii=False, indent=2))
        except Exception:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            raise
        return tmp

    def _share_send_audio(now):
        try:
            tmp = _share_build_audio_zip(now)
        except Exception as e:
            return jsonify({"error": "导出失败: " + str(e)}), 500

        @after_this_request
        def _cleanup_zip(resp):
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            return resp

        return send_file(
            str(tmp),
            as_attachment=True,
            download_name="its_our_cry_参考音频库_" + now + ".zip",
            mimetype="application/zip",
        )

    @app.route("/api/share/import", methods=["POST"])
    def share_import():
        f = request.files.get("file")
        if not f or not f.filename:
            return jsonify({"error": "未选择文件"}), 400
        raw = f.read()
        if not raw:
            return jsonify({"error": "文件为空"}), 400
        if raw[:3] == b"\xef\xbb\xbf":
            raw = raw[3:]
        filename = str(f.filename).lower()
        if filename.endswith(".json") or raw.lstrip()[:1] == b"{":
            try:
                payload = json.loads(raw.decode("utf-8"))
            except Exception:
                return jsonify({"error": "JSON 文件解析失败"}), 400
            return _share_import_params(payload)
        if filename.endswith(".zip"):
            return _share_import_audio(raw)
        return jsonify({"error": "仅支持 .json 或 .zip 文件"}), 400

    def _share_import_params(payload):
        if not isinstance(payload, dict) or payload.get("schema") != 1:
            return jsonify({"error": "不支持的预设文件格式"}), 400
        kind = str(payload.get("kind") or "").strip()
        data = payload.get("data") or {}
        if kind == "pronunciation":
            entries = data.get("entries")
            if not isinstance(entries, list):
                return jsonify({"error": "纠音词典格式不正确"}), 400
            cleaned = []
            for p in entries:
                if not isinstance(p, dict):
                    continue
                zh = str(p.get("zh", "")).strip()
                ja = str(p.get("ja", "")).strip()
                if zh and ja:
                    cleaned.append({"zh": zh, "ja": ja})
            config["pronunciation"] = cleaned
            _persist_user_settings(project_root, config)
            record_event({"type": "share_import", "message": "导入纠音词典 " + str(len(cleaned)) + " 条", "payload": {"kind": kind}}, project_root)
            return jsonify({"status": "ok", "kind": kind, "message": "已导入纠音词典 " + str(len(cleaned)) + " 条"})
        if kind == "emotion_params":
            raw_params = data.get("params")
            if not isinstance(raw_params, dict):
                return jsonify({"error": "情绪参数格式不正确"}), 400
            config["emotion_params"] = _clean_emotion_params(raw_params)
            config["use_emotion_params"] = bool(data.get("enabled", True))
            if isinstance(data.get("presets"), dict):
                config["emotion_param_presets"] = {
                    str(k).strip(): dict(v) for k, v in data["presets"].items() if isinstance(v, dict)
                }
            added = _share_merge_emotions(data.get("emotions"))
            _persist_user_settings(project_root, config)
            record_event({"type": "share_import", "message": "导入情绪参数模板", "payload": {"kind": kind}}, project_root)
            msg = "已导入情绪参数模板（含已保存预设）"
            if added:
                msg += "，补全新情绪 " + str(len(added)) + " 个"
            return jsonify({"status": "ok", "kind": kind, "message": msg, "added_emotions": added})
        if kind == "webgal_map":
            raw_map = data.get("map")
            if not isinstance(raw_map, dict):
                return jsonify({"error": "情绪映射格式不正确"}), 400
            cleaned = {}
            for k, v in raw_map.items():
                key = str(k).strip().lower()
                val = str(v).strip()
                if key and val:
                    cleaned[key] = val
            config["webgal_emotion_map"] = cleaned
            added = _share_merge_emotions(data.get("emotions"))
            _persist_user_settings(project_root, config)
            record_event({"type": "share_import", "message": "导入脚本情绪映射 " + str(len(cleaned)) + " 条", "payload": {"kind": kind}}, project_root)
            msg = "已导入脚本情绪映射 " + str(len(cleaned)) + " 条"
            if added:
                msg += "，补全新情绪 " + str(len(added)) + " 个"
            return jsonify({"status": "ok", "kind": kind, "message": msg, "added_emotions": added})
        return jsonify({"error": "未知预设类型"}), 400

    def _share_merge_emotions(raw_list):
        added = []
        if not isinstance(raw_list, list):
            return added
        for e in raw_list:
            name = str(e).strip()
            if name and name not in config["emotions"]:
                config["emotions"].append(name)
                added.append(name)
        return added

    def _share_import_audio(raw):
        try:
            zf = zipfile.ZipFile(io.BytesIO(raw))
        except Exception:
            return jsonify({"error": "ZIP 文件无法打开，可能已损坏"}), 400
        try:
            names = zf.namelist()
        except Exception:
            return jsonify({"error": "ZIP 文件读取失败"}), 400
        manifest = None
        if "manifest.json" in names:
            try:
                manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            except Exception:
                manifest = None
        if not isinstance(manifest, dict) or manifest.get("kind") != "audio":
            return jsonify({"error": "不是有效的参考音频包"}), 400
        ref_base = project_root / "reference_audio"
        ref_base.mkdir(parents=True, exist_ok=True)
        ref_resolved = str(ref_base.resolve())
        imported = 0
        replaced = 0
        added_emotions = []
        try:
            for info in zf.infolist():
                arc = str(info.filename).replace(chr(92), "/")
                if info.is_dir() or not arc.startswith("reference_audio/"):
                    continue
                parts = arc.split("/")
                if len(parts) != 4:
                    continue
                _, char_dir, emotion, filename = parts
                if not char_dir or char_dir in (".", "..") or "/" in char_dir:
                    continue
                if not emotion or emotion in (".", "..") or "/" in emotion:
                    continue
                filename = Path(filename).name
                if not filename:
                    continue
                is_prompt = filename.endswith(".txt")
                if is_prompt:
                    if filename == ".txt" or Path(filename[:-4]).suffix.lower() not in AUDIO_EXTS:
                        continue
                else:
                    if Path(filename).suffix.lower() not in AUDIO_EXTS:
                        continue
                target = ref_base / char_dir / emotion / filename
                try:
                    if not str(target.resolve()).startswith(ref_resolved):
                        continue
                except Exception:
                    continue
                if emotion not in config["emotions"]:
                    config["emotions"].append(emotion)
                    added_emotions.append(emotion)
                try:
                    content = zf.read(info)
                except Exception:
                    continue
                existed = target.exists()
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                except Exception as e:
                    return jsonify({"error": "写入失败: " + str(e)}), 500
                if not is_prompt:
                    imported += 1
                    if existed:
                        replaced += 1
        finally:
            zf.close()
        if imported or added_emotions:
            _persist_user_settings(project_root, config)
        message = "已导入参考音频 " + str(imported) + " 个"
        if added_emotions:
            message += "，新增情绪 " + str(len(added_emotions)) + " 个"
        if replaced:
            message += "，覆盖同名文件 " + str(replaced) + " 个"
        record_event({
            "type": "share_import",
            "message": message,
            "payload": {"kind": "audio", "imported": imported, "replaced": replaced, "added": added_emotions},
        }, project_root)
        return jsonify({"status": "ok", "kind": "audio", "message": message, "imported": imported, "replaced": replaced, "added_emotions": added_emotions})

    def narration_seconds(text):
        nr = config.get("narration", {})
        fixed = float(nr.get("fixed_duration", 0.0) or 0.0)
        if fixed > 0:
            return fixed
        base = float(nr.get("base_duration", 2.0))
        per = float(nr.get("per_char", 0.32))
        lo = float(nr.get("min_duration", 1.5))
        hi = float(nr.get("max_duration", 8.0))
        return max(lo, min(hi, base + len(text or "") * per))

    def build_full_timeline(merged_path, srt_path, segments_dir):
        if not state["lines"]:
            return []
        segments_dir = Path(segments_dir)
        segments_dir.mkdir(parents=True, exist_ok=True)
        all_indices = list(range(len(state["lines"])))
        wav_paths = []
        merged_lines = []
        gaps = []
        for idx in all_indices:
            line = state["lines"][idx]
            gen = state["generated"].get(idx)
            if gen and Path(gen["path"]).exists():
                wav_paths.append(gen["path"])
            else:
                wav_paths.append(str(segments_dir / f"silence_{idx:04d}.wav"))
            merged_lines.append(line)
            gaps.append(line.get("interval", DEFAULT_INTERVAL))

        # 没有语音的台词用静音占位，保证 SRT 时间轴完整
        sample_rate, channels, sampwidth = 24000, 1, 2
        for wav_path in wav_paths:
            if Path(wav_path).exists():
                try:
                    with wave.open(wav_path, "r") as wf:
                        sample_rate = wf.getframerate()
                        channels = wf.getnchannels()
                        sampwidth = wf.getsampwidth()
                    break
                except Exception:
                    pass
        for idx, wav_path in enumerate(wav_paths):
            if state["generated"].get(all_indices[idx]) and Path(wav_path).exists():
                continue
            text = merged_lines[idx].get("translated_text") or merged_lines[idx]["text"]
            char_name = merged_lines[idx].get("character")
            if char_name == "旁白" or (state.get("srt_only") and char_name not in config["characters"]):
                seconds = narration_seconds(text)
            else:
                seconds = max(1.2, min(6.0, len(text or "") * 0.32 + 0.6))
            with wave.open(wav_path, "w") as out:
                out.setnchannels(channels)
                out.setsampwidth(sampwidth)
                out.setframerate(sample_rate)
                out.writeframes(b"\x00" * int(sample_rate * seconds) * channels * sampwidth)

        time_info = merge_wav_files(
            wav_paths=wav_paths,
            output_path=str(merged_path),
            leading_gaps=gaps,
        )
        generate_srt(
            time_info=time_info,
            lines=merged_lines,
            output_path=str(srt_path),
        )
        return time_info

    def finalize_output():
        if not state["lines"]:
            return
        output_dir = Path(config["output_dir"])
        segments_dir = output_dir / "segments"
        merged_path = output_dir / "merged_output.wav"
        srt_path = output_dir / "subtitles.srt"
        state["time_info"] = build_full_timeline(str(merged_path), str(srt_path), segments_dir)
        state["merged_path"] = str(merged_path)
        state["srt_path"] = str(srt_path)

    @app.route("/api/generate", methods=["POST"])
    def generate():
        if state["generating"]:
            return jsonify({"error": "generation in progress"}), 409
        if not state["lines"]:
            return jsonify({"error": "please analyze first"}), 400

        data = request.get_json() or {}
        indices = data.get("indices", list(range(len(state["lines"]))))
        indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(state["lines"])]
        if not indices:
            return jsonify({"error": "没有可生成的台词"}), 400
        srt_only = bool(data.get("srt_only"))

        push_history("生成字幕" if srt_only else "生成语音")
        state["generating"] = True
        state["cancel_requested"] = False
        state["cancelled"] = False
        state["srt_only"] = srt_only
        state["progress"] = {"current": 0, "total": len(indices)}
        state["failures"] = {}
        state["error"] = None
        for idx in indices:
            state["generated"].pop(idx, None)
        state["time_info"] = []

        def generate_worker():
            try:
                worker_script = project_root / "backend" / "tts_worker.py"
                engine = get_engine(
                    config["gptsovits_path"],
                    project_root=project_root,
                    worker_script=str(worker_script) if worker_script.exists() else None,
                )
                engine.load()

                char_groups = {}
                for idx in indices:
                    line = state["lines"][idx]
                    char = line["character"]
                    if char not in char_groups:
                        char_groups[char] = []
                    char_groups[char].append(idx)

                def fail(idx, message):
                    state["failures"][idx] = message
                    state["progress"]["current"] += 1
                    record_event(
                        {"type": "generate_failed", "message": f"第{idx + 1}条生成失败：{message}", "payload": {"index": idx, "message": message}},
                        project_root=project_root,
                    )

                for char, idx_list in char_groups.items():
                    if state["cancel_requested"]:
                        break
                    if char not in config["characters"]:
                        if char == "旁白" or srt_only:
                            for idx in idx_list:
                                state["progress"]["current"] += 1
                            continue
                        for idx in idx_list:
                            fail(idx, f"角色「{char}」没有配音模型，仅保留字幕，请检查角色名是否写错")
                        continue
                    char_config = config["characters"][char]
                    try:
                        engine.switch_character(char_config["model"], char_config.get("gpt_model"))
                    except Exception as e:
                        for idx in idx_list:
                            fail(idx, f"角色模型加载失败（仅保留字幕）：{str(e)[-200:]}")
                        continue

                    for idx in idx_list:
                        if state["cancel_requested"]:
                            break
                        line = state["lines"][idx]
                        emotion = line.get("emotion", "thinking")
                        ref = pick_ref_audio(char, emotion)
                        if ref is None:
                            fail(idx, f"缺少参考音频：{char}「{emotion}」，仅保留字幕，请先在语音库该情绪文件夹放入音频")
                            continue
                        ref_audio = ref["path"]
                        ref_prompt = ref.get("prompt_text") or ""

                        output_dir = Path(config["output_dir"]) / "segments"
                        output_path = output_dir / f"{idx:04d}_{char}_{emotion}.wav"

                        tts_cfg = config["tts"]
                        emo_params = {}
                        if config.get("use_emotion_params", True):
                            emo_params = config.get("emotion_params", {}).get(emotion) or {}

                        def _param(key, default):
                            v = emo_params.get(key)
                            if v is None or v == "":
                                return default
                            return v

                        tts_lang = state.get("lang", "zh")
                        if tts_lang not in ("zh", "ja"):
                            tts_lang = "zh"
                        try:
                            tts_text = line.get("translated_text") or line["text"]
                            if tts_lang == "ja":
                                corrected = _exact_pronunciation(line["text"], config.get("pronunciation", []))
                                if corrected:
                                    tts_text = corrected
                            duration = engine.synthesize_to_file(
                                text=tts_text,
                                ref_audio_path=ref_audio,
                                prompt_text=ref_prompt,
                                output_path=str(output_path),
                                text_lang=tts_lang,
                                prompt_lang=_detect_prompt_lang(ref_prompt, tts_lang),
                                text_split_method=tts_cfg.get("text_split_method", "cut5"),
                                batch_size=tts_cfg.get("batch_size", 1),
                                speed_factor=_param("speed_factor", tts_cfg.get("speed_factor", 1.0)),
                                fragment_interval=tts_cfg.get("fragment_interval", 0.3),
                                temperature=_param("temperature", tts_cfg.get("temperature", 1.0)),
                                top_k=_param("top_k", tts_cfg.get("top_k", 15)),
                                top_p=_param("top_p", tts_cfg.get("top_p", 1.0)),
                                seed=_param("seed", tts_cfg.get("seed", -1)),
                            )
                        except Exception as e:
                            fail(idx, f"{char} 生成失败（仅保留字幕）：{str(e)[-300:]}")
                            continue
                        state["generated"][idx] = {
                            "path": str(output_path),
                            "duration": duration,
                        }
                        state["progress"]["current"] += 1

                if state["cancel_requested"]:
                    state["cancelled"] = True
                    for idx in indices:
                        gen = state["generated"].pop(idx, None)
                        if gen:
                            try:
                                path = Path(gen["path"])
                                if path.exists():
                                    path.unlink()
                            except Exception:
                                pass
                else:
                    finalize_output()
            except Exception as e:
                traceback.print_exc()
                state["error"] = str(e)
            finally:
                state["generating"] = False
                if not state.get("cancelled") and recent_settings.get("auto_save", True):
                    try:
                        save_current_version("auto", force=True)
                    except Exception:
                        pass

        threading.Thread(target=generate_worker, daemon=True).start()
        return jsonify({"status": "started", "total": len(indices)})

    @app.route("/api/generate/cancel", methods=["POST"])
    def cancel_generate():
        if not state["generating"]:
            return jsonify({"status": "ok", "already_stopped": True})
        state["cancel_requested"] = True
        return jsonify({"status": "cancelling"})

    @app.route("/api/segment/<int:index>", methods=["GET"])
    def get_segment(index):
        gen = state["generated"].get(index)
        if not gen:
            return jsonify({"error": "该台词还没有音频"}), 404
        path = gen["path"]
        if not Path(path).exists():
            return jsonify({"error": "音频文件不存在"}), 404
        return send_file(path, mimetype="audio/wav")

    @app.route("/api/merge", methods=["POST"])
    def merge():
        """用户手动触发合并音频和生成 SRT。"""
        if not state["lines"]:
            return jsonify({"error": "请先分析剧本"}), 400
        if not state["generated"] and not state.get("merged_path"):
            return jsonify({"error": "还没有已生成的内容，请先生成语音或字幕"}), 400
        try:
            finalize_output()
            return jsonify({
                "status": "ok",
                "merged_path": state.get("merged_path"),
                "srt_path": state.get("srt_path"),
            })
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": str(e)}), 500

    @app.route("/api/progress", methods=["GET"])
    def get_progress():
        return jsonify({
            "generating": state["generating"],
            "progress": state["progress"],
            "generated_count": len(state["generated"]),
            "generated_indices": sorted(state["generated"].keys()),
            "failures": state.get("failures", {}),
            "error": state.get("error"),
            "merged_path": state.get("merged_path"),
            "srt_path": state.get("srt_path"),
            "srt_only": bool(state.get("srt_only")),
            "cancel_requested": bool(state.get("cancel_requested")),
            "cancelled": bool(state.get("cancelled")),
            "history": history_payload(),
        })

    @app.route("/api/state", methods=["GET"])
    def get_state():
        payload = workbench_state()
        payload["generating"] = bool(state.get("generating"))
        return jsonify(payload)

    @app.route("/api/history", methods=["GET"])
    def get_history():
        return jsonify(history_payload())

    @app.route("/api/undo", methods=["POST"])
    def undo_action():
        if state.get("generating"):
            return jsonify({"error": "generation in progress"}), 409
        undo = state.get("history_undo", [])
        if not undo:
            return jsonify({"error": "没有可撤销的操作"}), 400
        entry = undo.pop()
        state.setdefault("history_redo", []).append({"label": entry["label"], "snapshot": take_snapshot()})
        restore_snapshot(entry["snapshot"])
        record_event(
            {"type": "undo", "message": "撤销：" + entry["label"], "payload": {"label": entry["label"]}},
            project_root=project_root,
        )
        return jsonify({"status": "ok", "label": entry["label"], "state": workbench_state(), "history": history_payload()})

    @app.route("/api/redo", methods=["POST"])
    def redo_action():
        if state.get("generating"):
            return jsonify({"error": "generation in progress"}), 409
        redo = state.get("history_redo", [])
        if not redo:
            return jsonify({"error": "没有可重做的操作"}), 400
        entry = redo.pop()
        state.setdefault("history_undo", []).append({"label": entry["label"], "snapshot": take_snapshot()})
        restore_snapshot(entry["snapshot"])
        record_event(
            {"type": "redo", "message": "重做：" + entry["label"], "payload": {"label": entry["label"]}},
            project_root=project_root,
        )
        return jsonify({"status": "ok", "label": entry["label"], "state": workbench_state(), "history": history_payload()})

    @app.route("/api/recent", methods=["GET"])
    def get_recent_records():
        records = load_recent_records()
        return jsonify({
            "records": [recent_summary(r) for r in records],
            "settings": dict(recent_settings),
        })

    @app.route("/api/recent/current", methods=["GET"])
    def get_current_recent():
        records = load_recent_records()
        record_id = state.get("current_record_id")
        record = None
        if record_id:
            for r in records:
                if r.get("id") == record_id:
                    record = r
                    break
        return jsonify({"record": recent_summary(record) if record else None})

    @app.route("/api/recent/new", methods=["POST"])
    def new_recent_project():
        if state.get("generating"):
            return jsonify({"error": "生成中，请稍后再新建项目"}), 409
        state["current_record_id"] = None
        state["project_type"] = "srt"
        state["lang"] = "zh"
        state["script"] = ""
        state["lines"] = []
        state["emotions"] = []
        state["generated"] = {}
        state["failures"] = {}
        state["merged_path"] = None
        state["srt_path"] = None
        state["time_info"] = []
        state["history_undo"] = []
        state["history_redo"] = []
        state["progress"] = {"current": 0, "total": 0}
        state["srt_only"] = False
        state["analysis_cancel_seq"] = state.get("analysis_seq", 0)
        return jsonify({"status": "ok"})

    @app.route("/api/recent/create", methods=["POST"])
    def create_recent_project():
        if state.get("generating"):
            return jsonify({"error": "生成中，请稍后再新建项目"}), 409
        data = request.get_json(silent=True) or {}
        name = str(data.get("name") or "").strip()[:60] or "未命名项目"
        project_type = str(data.get("project_type") or "srt").strip().lower()
        if project_type not in ("srt", "webgal"):
            project_type = "srt"
        ai_mode = str(data.get("ai_mode") or "api").strip().lower()
        if ai_mode not in ("api", "manual"):
            ai_mode = "api"
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        record = {
            "id": uuid.uuid4().hex,
            "name": name,
            "project_type": project_type,
            "ai_mode": ai_mode,
            "saved_at": now,
            "created_at": now,
            "updated_at": now,
            "versions": [],
            "exports": [],
        }
        with recent_lock:
            records = load_recent_records()
            name_key = name.strip().lower()
            type_label = "WebGaL" if project_type == "webgal" else "SRT"
            if any(
                (r.get("project_type") or "srt") == project_type
                and str(r.get("name") or "").strip().lower() == name_key
                for r in records
            ):
                return jsonify({"error": "已存在同名的%s项目「%s」，请换一个名称" % (type_label, name)}), 409
            records.insert(0, record)
            enforce_recent_limit(records)
            persist_recent_records(records)
        state["current_record_id"] = record["id"]
        state["project_type"] = project_type
        state["ai_mode"] = ai_mode
        state["lang"] = "zh"
        state["script"] = ""
        state["lines"] = []
        state["emotions"] = []
        state["generated"] = {}
        state["failures"] = {}
        state["merged_path"] = None
        state["srt_path"] = None
        state["time_info"] = []
        state["history_undo"] = []
        state["history_redo"] = []
        state["progress"] = {"current": 0, "total": 0}
        state["srt_only"] = False
        state["analysis_cancel_seq"] = state.get("analysis_seq", 0)
        return jsonify({"status": "ok", "record": recent_summary(record)})

    @app.route("/api/script", methods=["POST"])
    def save_script_draft():
        data = request.get_json(silent=True) or {}
        seq = data.get("seq")
        if isinstance(seq, int) and seq <= state.get("script_seq", 0):
            return jsonify({"status": "ok", "ignored": True})
        state["script"] = str(data.get("text") or "")
        if isinstance(seq, int):
            state["script_seq"] = seq
        return jsonify({"status": "ok"})

    @app.route("/api/recent/save", methods=["POST"])
    def save_recent_record():
        if state.get("generating"):
            return jsonify({"error": "生成中，请稍后再保存草稿"}), 409
        if not state["lines"] and not str(state.get("script", "")).strip():
            return jsonify({"error": "还没有可保存的剧本"}), 400
        data = request.get_json(silent=True) or {}
        source = str(data.get("source") or "manual")
        if source not in ("manual", "auto"):
            source = "manual"
        record, version, created = save_current_version(source, force=(source == "manual"))
        return jsonify({
            "status": "ok",
            "created": created,
            "record": recent_summary(record),
            "version": {"id": version["id"], "saved_at": version["saved_at"], "source": version["source"]} if version else None,
        })

    @app.route("/api/recent/versions", methods=["POST"])
    def add_current_version():
        if state.get("generating"):
            return jsonify({"error": "生成中，请稍后再保存自动版本"}), 409
        if not state["lines"] and not str(state.get("script", "")).strip():
            return jsonify({"error": "还没有可保存的内容"}), 400
        record, version, created = save_current_version("auto", force=False)
        meta = _recent_version_meta(record, int(recent_settings.get("version_limit") or DEFAULT_VERSION_LIMIT))
        if not created:
            return jsonify({"status": "ok", "created": False, "record_id": record.get("id"), "versions": meta})
        return jsonify({"status": "ok", "created": True, "record_id": record.get("id"), "versions": meta})

    @app.route("/api/recent/<record_id>/versions/<version_id>/load", methods=["POST"])
    def load_recent_version(record_id, version_id):
        if state.get("generating"):
            return jsonify({"error": "生成中，请稍后再载入版本"}), 409
        record = next((r for r in load_recent_records() if r.get("id") == record_id), None)
        if record is None:
            return jsonify({"error": "记录不存在"}), 404
        version = next((v for v in (record.get("versions") or []) if v.get("id") == version_id), None)
        if version is None:
            return jsonify({"error": "版本不存在"}), 404
        state["history_undo"] = []
        state["history_redo"] = []
        restore_workbench_from_version(record, version)
        record_event(
            {"type": "recent_load", "message": "载入版本：" + str(version.get("saved_at")), "payload": {"id": record_id, "version": version_id, "line_count": len(state["lines"])}},
            project_root=project_root,
        )
        return jsonify({
            "status": "ok",
            "state": workbench_state(),
            "history": history_payload(),
            "version": {"id": version.get("id"), "saved_at": version.get("saved_at"), "source": version.get("source", "auto")},
        })

    @app.route("/api/recent/<record_id>/versions/<version_id>", methods=["DELETE"])
    def delete_recent_version(record_id, version_id):
        records = load_recent_records()
        record = next((r for r in records if r.get("id") == record_id), None)
        if record is None:
            return jsonify({"error": "记录不存在"}), 404
        versions = record.get("versions") or []
        if len(versions) <= 1:
            return jsonify({"error": "每个草稿至少保留一个版本，删除整个草稿请点删除"}), 400
        before = len(versions)
        record["versions"] = [v for v in versions if v.get("id") != version_id]
        if len(record["versions"]) == before:
            return jsonify({"error": "版本不存在"}), 404
        record["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        records = [r for r in records if r.get("id") != record_id]
        records.insert(0, record)
        enforce_recent_limit(records)
        persist_recent_records(records)
        return jsonify({"status": "ok", "versions": _recent_version_meta(record, int(recent_settings.get("version_limit") or DEFAULT_VERSION_LIMIT))})

    @app.route("/api/recent/<record_id>", methods=["GET"])
    def get_recent_record(record_id):
        for r in load_recent_records():
            if r.get("id") == record_id:
                return jsonify({"record": r})
        return jsonify({"error": "记录不存在"}), 404

    @app.route("/api/recent/<record_id>/load", methods=["POST"])
    def load_recent_record(record_id):
        if state.get("generating"):
            return jsonify({"error": "生成中，请稍后再载入记录"}), 409
        record = None
        for r in load_recent_records():
            if r.get("id") == record_id:
                record = r
                break
        if record is None:
            return jsonify({"error": "记录不存在"}), 404
        state["history_undo"] = []
        state["history_redo"] = []
        restore_workbench_from_record(record)
        record_event(
            {"type": "recent_load", "message": "载入近期记录：" + str(record.get("saved_at")), "payload": {"id": record_id, "line_count": len(state["lines"])}},
            project_root=project_root,
        )
        return jsonify({"status": "ok", "state": workbench_state(), "history": history_payload(), "record": recent_summary(record)})

    @app.route("/api/recent/<record_id>", methods=["DELETE"])
    def delete_recent_record(record_id):
        records = [r for r in load_recent_records() if r.get("id") != record_id]
        persist_recent_records(records)
        if state.get("current_record_id") == record_id:
            state["current_record_id"] = None
        return jsonify({"status": "ok"})

    @app.route("/api/recent/clear", methods=["POST"])
    def clear_recent_records():
        persist_recent_records([])
        state["current_record_id"] = None
        return jsonify({"status": "ok"})

    @app.route("/api/recent/settings", methods=["POST"])
    def save_recent_settings():
        data = request.get_json() or {}
        if "limit" in data:
            try:
                limit = int(data["limit"])
            except (TypeError, ValueError):
                return jsonify({"error": "记录上限必须是数字"}), 400
            recent_settings["limit"] = max(RECENT_LIMIT_MIN, min(RECENT_LIMIT_MAX, limit))
        if "auto_save" in data:
            recent_settings["auto_save"] = bool(data["auto_save"])
        if "version_auto_save" in data:
            recent_settings["version_auto_save"] = bool(data["version_auto_save"])
        if "auto_save_interval" in data:
            try:
                interval = int(data["auto_save_interval"])
            except (TypeError, ValueError):
                return jsonify({"error": "自动保存间隔必须是数字"}), 400
            recent_settings["auto_save_interval"] = max(AUTO_SAVE_INTERVAL_MIN, min(AUTO_SAVE_INTERVAL_MAX, interval))
        if "version_limit" in data:
            try:
                version_limit = int(data["version_limit"])
            except (TypeError, ValueError):
                return jsonify({"error": "版本上限必须是数字"}), 400
            recent_settings["version_limit"] = max(VERSION_LIMIT_MIN, min(VERSION_LIMIT_MAX, version_limit))
        persist_recent_settings()
        records = load_recent_records()
        enforce_recent_limit(records)
        vlimit = int(recent_settings.get("version_limit") or DEFAULT_VERSION_LIMIT)
        for record in records:
            if len(record.get("versions") or []) > vlimit:
                record["versions"] = (record.get("versions") or [])[:vlimit]
        persist_recent_records(records)
        return jsonify({"status": "ok", "settings": dict(recent_settings)})

    @app.route("/api/download/<path:file_type>", methods=["GET"])
    def download_file(file_type):
        from flask import send_from_directory
        OUT = str(Path(config["output_dir"]).resolve())
        files = {"merged": "merged_output.wav", "srt": "subtitles.srt"}
        filename = files.get(file_type)
        if not filename:
            return jsonify({"error": "unknown"}), 400
        return send_from_directory(OUT, filename, as_attachment=True)

    @app.route("/api/export_tracks", methods=["POST"])
    def export_tracks():
        if state["generating"]:
            return jsonify({"error": "生成中，请稍后再导出"}), 409
        if not state["lines"]:
            return jsonify({"error": "请先分析剧本"}), 400
        if not state["generated"] and not state.get("merged_path"):
            return jsonify({"error": "还没有已生成的内容，请先生成语音或字幕"}), 400

        data = request.get_json() or {}
        folder_name = str(data.get("folder_name", "")).strip()
        if not folder_name:
            return jsonify({"error": "请输入导出文件夹名称"}), 400
        if re.search(r'[\\/:*?"<>|\r\n]', folder_name) or folder_name in (".", ".."):
            return jsonify({"error": "文件夹名称包含非法字符"}), 400
        if len(folder_name) > 64:
            return jsonify({"error": "文件夹名称过长"}), 400

        export_root = project_root / "exports"
        export_root.mkdir(parents=True, exist_ok=True)
        export_dir = export_root / folder_name
        if export_dir.exists():
            return jsonify({
                "error": f"文件夹「{folder_name}」已存在，请换一个名称或手动删除旧文件夹",
                "code": "folder_exists",
            }), 409
        export_dir.mkdir(parents=True)

        try:
            finalize_output()
            if not state.get("time_info"):
                raise RuntimeError("缺少时间轴信息，请先重新生成")

            tracks_dir = export_dir / "tracks"
            tracks_dir.mkdir(parents=True, exist_ok=True)
            segments_dir = export_dir / "segments"
            segments_dir.mkdir(parents=True, exist_ok=True)

            ordered_idx = sorted(i for i in state["generated"].keys() if 0 <= i < len(state["lines"]))
            wav_paths = []
            merged_lines = []
            gaps = []
            for idx in ordered_idx:
                gen_path = state["generated"][idx]["path"]
                if Path(gen_path).exists():
                    wav_paths.append(gen_path)
                    merged_lines.append(state["lines"][idx])
                    gaps.append(state["lines"][idx].get("interval", DEFAULT_INTERVAL))
            # 纯字幕导出时 wav_paths 为空，只导出合并音频（静音占位）与 SRT

            time_map = {idx: state["time_info"][idx] for idx in ordered_idx if idx < len(state["time_info"])}

            import wave

            if wav_paths:
                with wave.open(wav_paths[0], "r") as first_wav:
                    sample_rate = first_wav.getframerate()
                    channels = first_wav.getnchannels()
                    sampwidth = first_wav.getsampwidth()
            else:
                sample_rate, channels, sampwidth = 24000, 1, 2
            frame_bytes = channels * sampwidth
            total_duration = state["time_info"][-1]["end"]
            total_samples = max(1, int(round(total_duration * sample_rate)))

            char_indices = {}
            for idx in ordered_idx:
                char = state["lines"][idx]["character"]
                char_indices.setdefault(char, []).append(idx)

            created_files = []
            for char, idx_list in char_indices.items():
                track = bytearray(total_samples * frame_bytes)
                has_audio = False
                for idx in idx_list:
                    info = time_map.get(idx)
                    gen_path = state["generated"][idx]["path"]
                    if info is None or not Path(gen_path).exists():
                        continue
                    with wave.open(gen_path, "r") as seg_wav:
                        seg_sr = seg_wav.getframerate()
                        seg_width = seg_wav.getsampwidth()
                        seg_channels = seg_wav.getnchannels()
                        seg_data = seg_wav.readframes(seg_wav.getnframes())
                    if seg_sr != sample_rate:
                        raise RuntimeError(f"采样率不一致：{Path(gen_path).name} 为 {seg_sr}，首个音频为 {sample_rate}")
                    if seg_width != sampwidth:
                        raise RuntimeError(f"位深不一致：{Path(gen_path).name} 为 {seg_width * 8}bit，首个音频为 {sampwidth * 8}bit")
                    seg_data = convert_channels(seg_data, seg_channels, channels, sampwidth)
                    start_byte = int(round(info["start"] * sample_rate)) * frame_bytes
                    length_bytes = min(len(seg_data), max(0, total_samples * frame_bytes - start_byte))
                    if length_bytes > 0:
                        track[start_byte:start_byte + length_bytes] = seg_data[:length_bytes]
                        has_audio = True

                    shutil.copy2(gen_path, segments_dir / Path(gen_path).name)
                    created_files.append(str(segments_dir / Path(gen_path).name))

                if has_audio:
                    track_path = tracks_dir / f"{char}.wav"
                    with wave.open(str(track_path), "w") as out_wav:
                        out_wav.setnchannels(channels)
                        out_wav.setsampwidth(sampwidth)
                        out_wav.setframerate(sample_rate)
                        out_wav.writeframes(bytes(track))
                    created_files.append(str(track_path))

            export_merged = export_dir / "merged_output.wav"
            export_srt = export_dir / "subtitles.srt"
            build_full_timeline(str(export_merged), str(export_srt), segments_dir)
            created_files.append(str(export_merged))
            created_files.append(str(export_srt))

            if not created_files:
                raise RuntimeError("没有可导出的音频文件")
            try:
                attach_export_to_record({
                    "time": time.strftime("%Y-%m-%d %H:%M:%S"),
                    "folder": str(export_dir),
                    "merged_path": str(export_merged),
                    "srt_path": str(export_srt),
                })
            except Exception:
                pass
            return jsonify({"status": "ok", "folder": str(export_dir), "files": created_files})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "导出失败: " + str(e)}), 500

    @app.route("/api/open_folder", methods=["POST"])
    def open_folder():
        data = request.get_json() or {}
        folder = str(data.get("path", "")).strip()
        if not folder:
            return jsonify({"error": "缺少文件夹路径"}), 400
        try:
            resolved = Path(folder).resolve()
            project_resolved = project_root.resolve()
            try:
                common = os.path.commonpath([str(resolved), str(project_resolved)])
            except ValueError:
                return jsonify({"error": "只能打开项目内的文件夹"}), 400
            def _norm(p):
                return str(p).lower() if os.name == "nt" else str(p)
            if _norm(common) != _norm(str(project_resolved)):
                return jsonify({"error": "只能打开项目内的文件夹"}), 400
            if not resolved.exists() or not resolved.is_dir():
                return jsonify({"error": "文件夹不存在"}), 400
            os.startfile(str(resolved))
            return jsonify({"status": "ok"})
        except Exception as e:
            return jsonify({"error": "打开失败: " + str(e)}), 500

    @app.route("/api/deploy/scan", methods=["POST"])
    def deploy_scan():
        data = request.get_json(silent=True) or {}
        user_path = str(data.get("gptsovits_path", "")).strip()
        if user_path:
            guard = _deploy_target_error(user_path, project_root)
            if guard:
                return jsonify({"error": guard}), 400
            config["gptsovits_path"] = user_path
            _persist_user_settings(project_root, config)
        try:
            return jsonify(scan_environment(config, project_root, user_path or None))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "环境扫描失败: " + str(e)}), 500

    @app.route("/api/deploy/install", methods=["POST"])
    def deploy_install():
        data = request.get_json(silent=True) or {}
        user_path = str(data.get("gptsovits_path", "")).strip()
        if user_path:
            guard = _deploy_target_error(user_path, project_root)
            if guard:
                return jsonify({"error": guard}), 400
            config["gptsovits_path"] = user_path
            _persist_user_settings(project_root, config)
        if state["deploy_install"]["running"]:
            return jsonify({"error": "安装正在进行中"}), 409
        try:
            result = scan_environment(config, project_root, user_path or None)
            commands = result["install_plan"]["commands"]
            if not commands:
                return jsonify({"status": "nothing_to_install", "install_plan": result["install_plan"]})
            state["deploy_install"] = {
                "running": True, "done": False, "success": None, "log": [],
                "progress": 0, "total_commands": len(commands), "command_index": 0, "current_packages": [],
            }

            def worker():
                try:
                    total_cmds = len(commands)
                    for cmd_index, cmd in enumerate(commands, start=1):
                        pkgs = []
                        in_install = False
                        for arg in cmd:
                            if arg == "install":
                                in_install = True
                                continue
                            if in_install and arg.startswith("-"):
                                in_install = False
                            elif in_install:
                                pkgs.append(arg)
                        state["deploy_install"]["command_index"] = cmd_index
                        state["deploy_install"]["current_packages"] = pkgs
                        state["deploy_install"]["progress"] = round((cmd_index - 1) * 100 / total_cmds)
                        proc = subprocess.Popen(
                            cmd,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT,
                            text=True,
                            encoding="utf-8",
                            errors="replace",
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        for line in proc.stdout:
                            line = line.rstrip()
                            log = state["deploy_install"]["log"]
                            log.append(line)
                            if len(log) > 300:
                                del log[:len(log) - 300]
                            m = re.search(r"(\d+)%", line)
                            if m:
                                line_pct = min(100, int(m.group(1)))
                                base = (cmd_index - 1) * 100 / total_cmds
                                state["deploy_install"]["progress"] = min(99, round(base + line_pct / total_cmds))
                        proc.wait()
                        if proc.returncode != 0:
                            state["deploy_install"]["success"] = False
                            state["deploy_install"]["progress"] = round((cmd_index - 1) * 100 / total_cmds)
                            return
                        state["deploy_install"]["progress"] = round(cmd_index * 100 / total_cmds)
                    state["deploy_install"]["success"] = True
                    state["deploy_install"]["progress"] = 100
                except Exception as e:
                    log = state["deploy_install"]["log"]
                    log.append("安装失败: " + str(e))
                    state["deploy_install"]["success"] = False
                finally:
                    state["deploy_install"]["running"] = False
                    state["deploy_install"]["done"] = True

            threading.Thread(target=worker, daemon=True).start()
            return jsonify({"status": "started", "commands": commands})
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "启动安装失败: " + str(e)}), 500

    @app.route("/api/deploy/install_status", methods=["GET"])
    def deploy_install_status():
        return jsonify(state["deploy_install"])
    @app.route("/api/deploy/clone", methods=["POST"])
    def deploy_clone():
        data = request.get_json(silent=True) or {}
        repo = str(data.get("repo", "")).strip() or "https://github.com/RVC-Boss/GPT-SoVITS.git"
        target = str(data.get("target_dir", "")).strip()
        if not target:
            return jsonify({"error": "请填写克隆目录"}), 400
        if state["deploy_clone"]["running"]:
            return jsonify({"error": "克隆正在进行中"}), 409
        git = shutil.which("git")
        if not git:
            return jsonify({"error": "未检测到 Git，请先安装 Git for Windows"}), 400
        target_path = Path(target).resolve()
        guard = _deploy_target_error(target_path, project_root)
        if guard:
            return jsonify({"error": guard}), 400
        if target_path.exists() and any(target_path.iterdir()):
            return jsonify({"error": "目标目录已存在且不为空"}), 400
        try:
            target_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"error": "目录无效: " + str(e)}), 400

        state["deploy_clone"] = {
            "running": True, "done": False, "success": None, "log": [],
            "progress": 0, "target_dir": str(target_path),
        }

        def worker():
            cmd = [git, "clone", "--depth", "1", repo, str(target_path)]
            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                for line in proc.stdout:
                    line = line.rstrip()
                    log = state["deploy_clone"]["log"]
                    log.append(line)
                    if len(log) > 300:
                        del log[:len(log) - 300]
                    m = re.search(r"(\d+)%", line)
                    if m:
                        state["deploy_clone"]["progress"] = min(99, int(m.group(1)))
                proc.wait()
                if proc.returncode != 0:
                    state["deploy_clone"]["success"] = False
                    return
                state["deploy_clone"]["progress"] = 100
                state["deploy_clone"]["success"] = True
                state["deploy_clone"]["log"].append("克隆完成: " + str(target_path))
            except Exception as e:
                log = state["deploy_clone"]["log"]
                log.append("克隆失败: " + str(e))
                state["deploy_clone"]["success"] = False
            finally:
                state["deploy_clone"]["running"] = False
                state["deploy_clone"]["done"] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"status": "started", "target_dir": str(target_path)})

    @app.route("/api/deploy/clone_status", methods=["GET"])
    def deploy_clone_status():
        return jsonify(state["deploy_clone"])
    @app.route("/api/deploy/copy_models", methods=["POST"])
    def deploy_copy_models():
        data = request.get_json(silent=True) or {}
        gs_path = str(data.get("gptsovits_path", "")).strip()
        if not gs_path:
            return jsonify({"error": "请填写 GPT-SoVITS 目录"}), 400
        if state["deploy_model_copy"]["running"]:
            return jsonify({"error": "模型复制正在进行中"}), 409
        target_root = Path(gs_path).resolve()
        guard = _deploy_target_error(target_root, project_root)
        if guard:
            return jsonify({"error": guard}), 400
        source_root = project_root
        missing = []
        seen = set()
        for char, char_cfg in config.get("characters", {}).items():
            entries = [("SoVITS", char_cfg.get("model_rel") or ""), ("GPT", char_cfg.get("gpt_model_rel") or "")]
            for kind, rel in entries:
                rel = str(rel).replace("\\", "/")
                if not rel or (kind, rel) in seen:
                    continue
                seen.add((kind, rel))
                source = source_root / rel
                target = target_root / rel
                if source.exists() and not target.exists():
                    missing.append({"character": char, "kind": kind, "rel": rel, "source": source, "target": target})
        if not missing:
            return jsonify({"status": "nothing_to_copy"})

        state["deploy_model_copy"] = {
            "running": True, "done": False, "success": None, "log": [],
            "progress": 0, "total": len(missing), "current": "",
        }

        def worker():
            try:
                log = state["deploy_model_copy"]["log"]
                for idx, item in enumerate(missing, start=1):
                    state["deploy_model_copy"]["current"] = item["character"] + " [" + item["kind"] + "]"
                    log.append("正在复制 " + item["character"] + " [" + item["kind"] + "] ...")
                    item["target"].parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(str(item["source"]), str(item["target"]))
                    log.append("已复制 " + item["character"] + " [" + item["kind"] + "]: " + item["rel"])
                    state["deploy_model_copy"]["progress"] = round(idx * 100 / len(missing))
                state["deploy_model_copy"]["success"] = True
                state["deploy_model_copy"]["progress"] = 100
                log.append("角色模型补齐完成")
            except Exception as e:
                log = state["deploy_model_copy"]["log"]
                log.append("复制失败: " + str(e))
                state["deploy_model_copy"]["success"] = False
            finally:
                state["deploy_model_copy"]["running"] = False
                state["deploy_model_copy"]["done"] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"status": "started", "count": len(missing)})

    @app.route("/api/deploy/copy_models_status", methods=["GET"])
    def deploy_copy_models_status():
        return jsonify(state["deploy_model_copy"])

    @app.route("/api/deploy/clean_scan", methods=["POST"])
    def deploy_clean_scan():
        data = request.get_json(silent=True) or {}
        user_path = str(data.get("gptsovits_path", "")).strip()
        if user_path:
            guard = _deploy_target_error(user_path, project_root)
            if guard:
                return jsonify({"error": guard}), 400
            config["gptsovits_path"] = user_path
            _persist_user_settings(project_root, config)
        try:
            return jsonify(scan_cleanable(project_root, config.get("gptsovits_path") or ""))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "清理扫描失败: " + str(e)}), 500

    @app.route("/api/deploy/clean", methods=["POST"])
    def deploy_clean():
        data = request.get_json(silent=True) or {}
        user_path = str(data.get("gptsovits_path", "")).strip()
        items = data.get("items") or []
        if not isinstance(items, list) or not items:
            return jsonify({"error": "请选择要清理的项目"}), 400
        if user_path:
            guard = _deploy_target_error(user_path, project_root)
            if guard:
                return jsonify({"error": guard}), 400
            config["gptsovits_path"] = user_path
            _persist_user_settings(project_root, config)
        confirm_missing = bool(data.get("confirm_missing"))
        try:
            result = clean_items(project_root, config.get("gptsovits_path") or "", items, confirm_missing=confirm_missing)
            if "model_weights" in items:
                rebuilt = _build_models_dict(project_root, config.get("gptsovits_path") or "")
                models.clear()
                models.update(rebuilt)
                rebuild_characters()
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "清理失败: " + str(e)}), 500
        return jsonify(result)

    @app.route("/api/deploy/download_options", methods=["GET"])
    def deploy_download_options():
        return jsonify(get_download_options())

    @app.route("/api/deploy/download", methods=["POST"])
    def deploy_download():
        data = request.get_json(silent=True) or {}
        url = str(data.get("url", "")).strip()
        target = str(data.get("target_dir", "")).strip()
        if not url or not target:
            return jsonify({"error": "请填写下载地址和解压目录"}), 400
        if state["deploy_download"]["running"]:
            return jsonify({"error": "下载正在进行中"}), 409
        try:
            target_path = Path(target).resolve()
            guard = _deploy_target_error(target_path, project_root)
            if guard:
                return jsonify({"error": guard}), 400
            if target_path.exists() and any(target_path.iterdir()):
                return jsonify({"error": "目标目录已存在且不为空"}), 400
            target_path.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return jsonify({"error": "目录无效: " + str(e)}), 400

        state["deploy_download"] = {
            "running": True, "done": False, "success": None, "cancelled": False,
            "cancel_requested": False, "log": [], "progress": 0,
            "target_dir": str(target_path), "extracted_path": "",
        }

        def worker():
            tmp_file = None
            try:
                log = state["deploy_download"]["log"]
                initial_names = {p.name for p in target_path.iterdir()} if target_path.exists() else set()
                tmp_file = target_path / "gptsovits_download.7z"
                log.append("开始下载: " + url)
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=60) as resp, open(tmp_file, "wb") as out:
                    total = int(resp.headers.get("Content-Length") or 0)
                    downloaded = 0
                    while True:
                        if state["deploy_download"]["cancel_requested"]:
                            raise RuntimeError("用户取消下载")
                        chunk = resp.read(1024 * 256)
                        if not chunk:
                            break
                        out.write(chunk)
                        downloaded += len(chunk)
                        if total > 0:
                            state["deploy_download"]["progress"] = min(70, round(downloaded * 60 / total))
                        if len(log) % 10 == 0:
                            log.append("已下载 " + str(downloaded // (1024 * 1024)) + " MB")
                        if len(log) > 300:
                            del log[:len(log) - 300]
                log.append("下载完成，正在准备解压...")
                state["deploy_download"]["progress"] = 75

                log.append("正在查找解压工具...")
                extractor = _find_extractor(project_root)
                if extractor is None:
                    raise RuntimeError(
                        "未找到 7-Zip 或 WinRAR。请安装 7-Zip 后重试，"
                        "或手动将下载好的 .7z 解压到目标目录。"
                    )
                exe, kind, label = extractor
                log.append("使用解压工具: " + label)
                total_size = _archive_total_uncompressed(tmp_file)
                if total_size > 0:
                    free_space = shutil.disk_usage(target_path).free
                    if total_size > free_space:
                        raise RuntimeError(
                            "磁盘空间不足：需要约 %.1f GB，当前可用 %.1f GB"
                            % (total_size / (1024 ** 3), free_space / (1024 ** 3))
                        )
                _extract_archive(exe, kind, tmp_file, target_path, state["deploy_download"], log)
                state["deploy_download"]["progress"] = 95

                entries = [p for p in target_path.iterdir()]
                dirs = [p for p in entries if p.is_dir()]
                files = [p for p in entries if p.is_file()]
                extracted = target_path
                if len(dirs) == 1 and not files:
                    extracted = dirs[0]
                state["deploy_download"]["extracted_path"] = str(extracted)
                config["gptsovits_path"] = str(extracted)
                _persist_user_settings(project_root, config)
                state["deploy_download"]["progress"] = 100
                state["deploy_download"]["success"] = True
                log.append("下载并解压完成: " + str(extracted))
            except Exception as e:
                log = state["deploy_download"]["log"]
                cancelled = state["deploy_download"]["cancel_requested"]
                state["deploy_download"]["cancelled"] = bool(cancelled)
                log.append("已取消下载" if cancelled else ("下载失败: " + str(e)))
                state["deploy_download"]["success"] = False
                try:
                    if _deploy_target_error(target_path, project_root) is not None:
                        log.append("安全校验失败，已停止清理，请检查目标目录")
                    else:
                        for item in target_path.iterdir():
                            if item.name in initial_names or item.name == "gptsovits_download.7z":
                                continue
                            if item.is_dir():
                                shutil.rmtree(item, ignore_errors=True)
                            else:
                                item.unlink(missing_ok=True)
                except Exception:
                    pass
            finally:
                if tmp_file and tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except Exception:
                        pass
                state["deploy_download"]["running"] = False
                state["deploy_download"]["done"] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"status": "started", "target_dir": str(target_path)})

    @app.route("/api/deploy/download_cancel", methods=["POST"])
    def deploy_download_cancel():
        state["deploy_download"]["cancel_requested"] = True
        return jsonify({"status": "ok"})

    @app.route("/api/deploy/download_status", methods=["GET"])
    def deploy_download_status():
        return jsonify(state["deploy_download"])
    @app.route("/api/deploy/install_ffmpeg", methods=["POST"])
    def deploy_install_ffmpeg():
        data = request.get_json(silent=True) or {}
        gs_path = str(data.get("gptsovits_path", "")).strip()
        if not gs_path:
            return jsonify({"error": "请填写 GPT-SoVITS 目录"}), 400
        if state["deploy_ffmpeg"]["running"]:
            return jsonify({"error": "ffmpeg 下载正在进行中"}), 409
        target_root = Path(gs_path).resolve()
        guard = _deploy_target_error(target_root, project_root)
        if guard:
            return jsonify({"error": guard}), 400
        if not target_root.exists():
            return jsonify({"error": "GPT-SoVITS 目录不存在: " + str(target_root)}), 400
        target = target_root / "runtime" / "ffmpeg.exe"
        if target.exists():
            return jsonify({"status": "nothing_to_do"})

        state["deploy_ffmpeg"] = {
            "running": True, "done": False, "success": None, "log": [],
            "progress": 0, "target": str(target),
        }

        urls = [
            "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
            "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl.zip",
        ]

        def worker():
            tmp_file = None
            try:
                log = state["deploy_ffmpeg"]["log"]
                target.parent.mkdir(parents=True, exist_ok=True)
                tmp_file = target.parent / "ffmpeg_download.zip"
                last_err = None
                for url in urls:
                    log.append("正在下载 ffmpeg: " + url)
                    try:
                        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                        with urllib.request.urlopen(req, timeout=120) as resp, open(tmp_file, "wb") as out:
                            total = int(resp.headers.get("Content-Length") or 0)
                            downloaded = 0
                            while True:
                                chunk = resp.read(1024 * 256)
                                if not chunk:
                                    break
                                out.write(chunk)
                                downloaded += len(chunk)
                                if total > 0:
                                    state["deploy_ffmpeg"]["progress"] = min(80, round(downloaded * 70 / total))
                        break
                    except Exception as e:
                        last_err = e
                        log.append("下载源不可用，尝试下一个: " + str(e))
                else:
                    raise RuntimeError("所有下载源均失败: " + str(last_err))
                log.append("下载完成，正在解压...")
                state["deploy_ffmpeg"]["progress"] = 85
                extract_dir = target.parent / "ffmpeg_extract"
                if extract_dir.exists():
                    shutil.rmtree(extract_dir)
                with zipfile.ZipFile(str(tmp_file), "r") as zf:
                    zf.extractall(str(extract_dir))
                found = None
                for root, _, files in os.walk(extract_dir):
                    for name in files:
                        if name.lower() == "ffmpeg.exe":
                            found = Path(root) / name
                            break
                    if found:
                        break
                if not found:
                    raise RuntimeError("压缩包内未找到 ffmpeg.exe")
                shutil.copy2(str(found), str(target))
                shutil.rmtree(extract_dir)
                state["deploy_ffmpeg"]["progress"] = 100
                state["deploy_ffmpeg"]["success"] = True
                log.append("ffmpeg 已安装: " + str(target))
            except Exception as e:
                state["deploy_ffmpeg"]["log"].append("ffmpeg 下载失败: " + str(e))
                state["deploy_ffmpeg"]["success"] = False
            finally:
                if tmp_file and tmp_file.exists():
                    try:
                        tmp_file.unlink()
                    except Exception:
                        pass
                state["deploy_ffmpeg"]["running"] = False
                state["deploy_ffmpeg"]["done"] = True

        threading.Thread(target=worker, daemon=True).start()
        return jsonify({"status": "started"})

    @app.route("/api/deploy/install_ffmpeg_status", methods=["GET"])
    def deploy_install_ffmpeg_status():
        return jsonify(state["deploy_ffmpeg"])

    @app.route("/picture/<path:filename>")
    def picture_file(filename):
        pic_dir = (project_root / "picture").resolve()
        target = (pic_dir / filename).resolve()
        if str(target).lower().startswith(str(pic_dir).lower()) and target.is_file():
            return send_file(str(target))
        return "not found", 404

    @app.route("/api/backgrounds", methods=["GET"])
    def backgrounds():
        pic_dir = project_root / "picture"
        exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
        try:
            files = sorted(p.name for p in pic_dir.iterdir() if p.is_file() and p.suffix.lower() in exts)
        except OSError:
            files = []
        return jsonify({"backgrounds": files})

    @app.after_request
    def _no_frontend_cache(resp):
        if request.path == "/" or request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
        return resp

    return app
