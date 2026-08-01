"""Flask backend for MyGO TTS Workbench."""

import json
import os
import random
import threading
import traceback
from pathlib import Path
from typing import Optional

import yaml
from flask import Flask, jsonify, render_template, request, send_file

from .script_parser import parse_script
from .emotion_analyzer import analyze_emotions
from .tts_engine import get_engine
from .audio_merger import merge_wav_files, generate_srt


def create_app(config_path="config.yaml"):
    app = Flask(
        __name__,
        template_folder="../frontend/templates",
        static_folder="../frontend/static",
    )

    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    # Resolve all relative paths to absolute to survive cwd changes
    project_root = Path(config_path).parent.resolve()
    config["output_dir"] = str(project_root / config["output_dir"])
    config["gptsovits_path"] = str(Path(config["gptsovits_path"]))
    for char_name, char_cfg in config["characters"].items():
        char_cfg["ref_audio_dir"] = str(project_root / char_cfg["ref_audio_dir"])
        char_cfg["model"] = str(Path(config["gptsovits_path"]) / char_cfg["model"])

    state = {
        "lines": [],
        "emotions": [],
        "lang": "zh",
        "generated": {},
        "merged_path": None,
        "srt_path": None,
        "generating": False,
        "progress": {"current": 0, "total": 0},
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
        })

    @app.route("/api/config/api_key", methods=["GET"])
    def get_api_key():
        """Return the saved API key (masked except last 4 chars)."""
        key = config["deepseek"].get("api_key", "")
        if key:
            return jsonify({"api_key": key})
        return jsonify({"api_key": ""})

    @app.route("/api/config", methods=["POST"])
    def save_config():
        data = request.get_json()
        if "deepseek_api_key" in data:
            config["deepseek"]["api_key"] = data["deepseek_api_key"]
        return jsonify({"status": "ok"})

    @app.route("/api/analyze", methods=["POST"])
    def analyze():
        data = request.get_json()
        script_text = data.get("text", "")
        api_key = data.get("api_key", "") or config["deepseek"]["api_key"]
        lang = data.get("lang", "zh")

        if not script_text.strip():
            return jsonify({"error": "script is empty"}), 400
        if not api_key:
            return jsonify({"error": "please configure DeepSeek API Key"}), 400

        lines = parse_script(script_text)
        if not lines:
            return jsonify({"error": "no valid lines found"}), 400

        try:
            emotions = analyze_emotions(
                lines=lines,
                api_key=api_key,
                base_url=config["deepseek"]["base_url"],
                model=config["deepseek"]["model"],
                lang=lang,
            )
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "emotion analysis failed: " + str(e)}), 500

        emotion_map = {}
        for e in emotions:
            emotion_map[e["index"]] = e["emotion"]
        for line in lines:
            line["emotion"] = emotion_map.get(line["index"], "thinking")

        state["lines"] = lines
        state["emotions"] = emotions
        state["lang"] = lang

        return jsonify({"lines": lines})

    @app.route("/api/line/<int:index>", methods=["PUT"])
    def update_line(index):
        data = request.get_json()
        if index < 0 or index >= len(state["lines"]):
            return jsonify({"error": "invalid index"}), 400
        if "emotion" in data:
            if data["emotion"] not in config["emotions"]:
                return jsonify({"error": "invalid emotion"}), 400
            state["lines"][index]["emotion"] = data["emotion"]
        if "text" in data:
            state["lines"][index]["text"] = data["text"]
        return jsonify({"status": "ok", "line": state["lines"][index]})

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
            # fallback: any audio from this character's directories
            parent_dir = ref_dir.parent
            if parent_dir.exists():
                for root, dirs, filenames in os.walk(str(parent_dir)):
                    for fn in filenames:
                        if Path(fn).suffix.lower() in audio_exts:
                            files.append(os.path.join(root, fn))
        if not files:
            return None
        return random.choice(files)

    def finalize_output():
        indices = sorted(state["generated"].keys())
        if not indices:
            return
        wav_paths = []
        merged_lines = []
        for idx in indices:
            gen = state["generated"][idx]
            if Path(gen["path"]).exists():
                wav_paths.append(gen["path"])
                merged_lines.append(state["lines"][idx])
        if not wav_paths:
            return
        output_dir = Path(config["output_dir"])
        merged_path = output_dir / "merged_output.wav"
        srt_path = output_dir / "subtitles.srt"
        time_info = merge_wav_files(
            wav_paths=wav_paths,
            output_path=str(merged_path),
            silence_between=0.3,
        )
        generate_srt(
            time_info=time_info,
            lines=merged_lines,
            output_path=str(srt_path),
        )
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

        state["generating"] = True
        state["progress"] = {"current": 0, "total": len(indices)}
        state["generated"] = {}

        def generate_worker():
            try:
                engine = get_engine(config["gptsovits_path"])
                engine.load()

                char_groups = {}
                for idx in indices:
                    line = state["lines"][idx]
                    char = line["character"]
                    if char not in char_groups:
                        char_groups[char] = []
                    char_groups[char].append(idx)

                for char, idx_list in char_groups.items():
                    if char not in config["characters"]:
                        continue
                    char_config = config["characters"][char]
                    engine.switch_character(char_config["model"])

                    for idx in idx_list:
                        line = state["lines"][idx]
                        emotion = line.get("emotion", "thinking")
                        ref_audio = pick_ref_audio(char, emotion)
                        if ref_audio is None:
                            state["progress"]["current"] += 1
                            continue

                        output_dir = Path(config["output_dir"]) / "segments"
                        output_path = output_dir / f"{idx:04d}_{char}_{emotion}.wav"

                        tts_cfg = config["tts"]
                        tts_lang = state.get("lang", "zh")
                        if tts_lang == "auto":
                            tts_lang = "zh"  # default to Chinese for auto
                        duration = engine.synthesize_to_file(
                            text=line["text"],
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
                        state["generated"][idx] = {
                            "path": str(output_path),
                            "duration": duration,
                        }
                        state["progress"]["current"] += 1

                finalize_output()
            except Exception as e:
                traceback.print_exc()
                state["error"] = str(e)
            finally:
                state["generating"] = False

        threading.Thread(target=generate_worker, daemon=True).start()
        return jsonify({"status": "started", "total": len(indices)})

    @app.route("/api/merge", methods=["POST"])
    def merge():
        """用户手动触发合并音频和生成 SRT。"""
        if not state["generated"]:
            return jsonify({"error": "没有已生成的音频"}), 400
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

        threading.Thread(target=generate_worker, daemon=True).start()
        return jsonify({"status": "started", "total": len(indices)})

    @app.route("/api/progress", methods=["GET"])
    def get_progress():
        return jsonify({
            "generating": state["generating"],
            "progress": state["progress"],
            "generated_count": len(state["generated"]),
            "error": state.get("error"),
            "merged_path": state.get("merged_path"),
            "srt_path": state.get("srt_path"),
        })

    @app.route("/api/download/<path:file_type>", methods=["GET"])
    def download_file(file_type):
        from flask import send_from_directory
        OUT = r"C:\Users\admin\Documents\Codex\2026-07-26\niu-a-hi\output"
        files = {"merged": "merged_output.wav", "srt": "subtitles.srt"}
        filename = files.get(file_type)
        if not filename:
            return jsonify({"error": "unknown"}), 400
        return send_from_directory(OUT, filename, as_attachment=True)

    return app
