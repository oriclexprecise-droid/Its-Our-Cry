"""Flask backend for MyGO TTS Workbench."""

import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import traceback
import urllib.request
import wave
import zipfile
from pathlib import Path
from typing import Optional

import yaml
from flask import Flask, jsonify, render_template, request, send_file

from .script_parser import find_character_issues, parse_script
from .emotion_analyzer import analyze_emotions
from .tts_engine import get_engine
from .audio_merger import merge_wav_files, generate_srt, convert_channels
from .translator import translate_lines
from .deploy_check import scan_environment, get_download_options, GPT_SOVITS_DOWNLOADS, recommend_download
from .feedback import read_events, record_event
from .cleanup import clean_items, scan_cleanable


DEFAULT_INTERVAL = 0.5


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
    dpapi_ok = _dpapi_encrypt("probe") is not None

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
        "emotions": [],
        "lang": "zh",
        "generated": {},
        "merged_path": None,
        "srt_path": None,
        "generating": False,
        "cancel_requested": False,
        "cancelled": False,
        "progress": {"current": 0, "total": 0},
        "failures": {},
        "time_info": [],
        "deploy_install": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "total_commands": 0, "command_index": 0, "current_packages": []},
        "deploy_model_copy": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "total": 0, "current": ""},
        "deploy_clone": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "target_dir": ""},
        "deploy_download": {"running": False, "done": False, "success": None, "cancelled": False, "cancel_requested": False, "log": [], "progress": 0, "target_dir": "", "extracted_path": ""},
        "deploy_ffmpeg": {"running": False, "done": False, "success": None, "log": [], "progress": 0, "target": ""}
    }

    @app.route("/")
    def index():
        return render_template("index.html")

    @app.route("/api/config", methods=["GET"])
    def get_config():
        has_key = bool(config["deepseek"].get("api_key", ""))
        return jsonify({
            "characters": list(config["characters"].keys()),
            "emotions": config["emotions"],
            "has_api_key": has_key,
            "default_interval": DEFAULT_INTERVAL,
            "narration": config.get("narration", {}),
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

        try:
            emotions = analyze_emotions(
                lines=lines,
                api_key=api_key,
                base_url=base_url,
                model=model,
                lang=lang,
            )
        except Exception as e:
            traceback.print_exc()
            record_event(
                {"type": "error", "message": "情绪分析失败：" + str(e)},
                project_root=project_root,
            )
            return jsonify({"error": "emotion analysis failed: " + str(e)}), 500

        emotion_map = {}
        for e in emotions:
            idx = e.get("index")
            if isinstance(idx, int) and not isinstance(idx, bool):
                emotion_map[idx] = e.get("emotion") or "思考"
        for line in lines:
            line["emotion"] = emotion_map.get(line["index"], "思考")

        if lang == "ja":
            try:
                translations = translate_lines(
                    lines=lines,
                    api_key=api_key,
                    base_url=config["deepseek"]["base_url"],
                    model=config["deepseek"]["model"],
                )
            except Exception as e:
                traceback.print_exc()
                return jsonify({"error": "日语翻译失败: " + str(e)}), 500
            translation_map = {}
            for t in translations:
                idx = t.get("index")
                if idx is not None:
                    translation_map[idx] = t.get("translation", "")
            for line in lines:
                line["translated_text"] = (
                    translation_map.get(line["index"], "").strip() or line["text"]
                )

        valid_chars = list(config["characters"].keys()) + ["旁白"]
        proofread = find_character_issues(lines, valid_chars)
        record_event(
            {"type": "analyze", "message": f"情绪分析完成：共 {len(lines)} 条台词", "payload": {"count": len(lines)}},
            project_root=project_root,
        )

        skipped = []
        raw_lines = script_text.strip().split("\n")
        parsed_line_nos = {line["line_no"] for line in lines}
        for skipped_no, raw in enumerate(raw_lines, start=1):
            if raw.strip() and skipped_no not in parsed_line_nos:
                skipped.append({"line_no": skipped_no, "text": raw.strip()[:100]})

        state["lines"] = lines
        state["emotions"] = emotions
        state["lang"] = lang
        state["generated"] = {}
        state["time_info"] = []
        state["merged_path"] = None
        state["srt_path"] = None
        state["failures"] = {}
        state["progress"] = {"current": 0, "total": 0}
        state["srt_only"] = False

        return jsonify({"lines": lines, "proofread": proofread, "skipped": skipped})

    @app.route("/api/line/<int:index>", methods=["PUT"])
    def update_line(index):
        data = request.get_json() or {}
        if index < 0 or index >= len(state["lines"]):
            return jsonify({"error": "invalid index"}), 400
        line = state["lines"][index]
        had_generated = False
        reanalyze_error = None

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
                line["text"] = new_text
                invalidate_segment()
                try:
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
                    )
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
                            base_url=config["deepseek"].get("base_url", "https://api.deepseek.com"),
                            model=config["deepseek"].get("model", "deepseek-v4-flash"),
                        )
                        translated = next((t.get("translation", "") for t in translations if t.get("index") == 0), "").strip()
                        line["translated_text"] = translated or line["text"]
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
                    state["failures"][index] = "文本已修改，情绪重新分析失败：" + str(e)[-200:]
                    record_event(
                        {
                            "type": "line_edit",
                            "message": f"台词修改：第{index + 1}行 文本已保存，情绪重分析失败",
                            "payload": {"index": index, "error": str(e)[-200:]},
                        },
                        project_root=project_root,
                    )
        if "character" in data:
            new_char = data["character"]
            if not isinstance(new_char, str) or not new_char.strip():
                return jsonify({"error": "角色名不能为空"}), 400
            new_char = new_char.strip()
            if new_char != line.get("character"):
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
            line["interval"] = round(interval, 3)
        resp = {"status": "ok", "line": line, "had_generated": had_generated}
        if reanalyze_error:
            resp["reanalyze_error"] = reanalyze_error
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
        for idx in valid:
            state["lines"][idx]["interval"] = interval
        return jsonify({"status": "ok", "updated": len(valid), "indices": valid})

    @app.route("/api/lines/characters", methods=["POST"])
    def update_lines_characters():
        data = request.get_json() or {}
        fixes = data.get("fixes") or {}
        if not isinstance(fixes, dict):
            return jsonify({"error": "无效的修正数据"}), 400
        updated = 0
        for line in state["lines"]:
            old = line.get("character")
            new = fixes.get(old)
            if isinstance(new, str) and new.strip() and new.strip() != old:
                line["character"] = new.strip()
                updated += 1
        return jsonify({"status": "ok", "updated": updated})

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
        audio_exts = {".wav", ".mp3", ".flac", ".ogg", ".m4a"}
        files = []
        for f in ref_dir.iterdir():
            if f.suffix.lower() in audio_exts and f.is_file():
                files.append(str(f))
        if not files:
            return None
        return random.choice(files)

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
                        ref_audio = pick_ref_audio(char, emotion)
                        if ref_audio is None:
                            fail(idx, f"缺少参考音频：{char}「{emotion}」，仅保留字幕，请先在语音库该情绪文件夹放入音频")
                            continue

                        output_dir = Path(config["output_dir"]) / "segments"
                        output_path = output_dir / f"{idx:04d}_{char}_{emotion}.wav"

                        tts_cfg = config["tts"]
                        tts_lang = state.get("lang", "zh")
                        if tts_lang not in ("zh", "ja"):
                            tts_lang = "zh"
                        try:
                            duration = engine.synthesize_to_file(
                                text=line.get("translated_text") or line["text"],
                                ref_audio_path=ref_audio,
                                output_path=str(output_path),
                                text_lang=tts_lang,
                                prompt_lang=tts_lang,
                                text_split_method=tts_cfg.get("text_split_method", "cut5"),
                                batch_size=tts_cfg.get("batch_size", 1),
                                speed_factor=tts_cfg.get("speed_factor", 1.0),
                                fragment_interval=tts_cfg.get("fragment_interval", 0.3),
                                temperature=tts_cfg.get("temperature", 1.0),
                                top_k=tts_cfg.get("top_k", 15),
                                top_p=tts_cfg.get("top_p", 1.0),
                                seed=tts_cfg.get("seed", -1),
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
        })

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

    return app