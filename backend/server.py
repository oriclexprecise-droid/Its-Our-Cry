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
from pathlib import Path
from typing import Optional

import yaml
from flask import Flask, jsonify, render_template, request, send_file

from .script_parser import parse_script
from .emotion_analyzer import analyze_emotions
from .tts_engine import get_engine
from .audio_merger import merge_wav_files, generate_srt
from .translator import translate_lines
from .deploy_check import scan_environment


DEFAULT_INTERVAL = 0.5


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
        "time_info": [],
        "deploy_install": {"running": False, "done": False, "success": None, "log": []},
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
            "gptsovits_path": config["gptsovits_path"],
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
        for line in lines:
            line.setdefault("interval", DEFAULT_INTERVAL)

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
        if "interval" in data:
            try:
                interval = float(data["interval"])
            except (TypeError, ValueError):
                return jsonify({"error": "间隔时间必须是数字"}), 400
            if not 0 <= interval <= 10:
                return jsonify({"error": "间隔时间需在 0-10 秒之间"}), 400
            state["lines"][index]["interval"] = round(interval, 3)
        return jsonify({"status": "ok", "line": state["lines"][index]})

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
        gaps = [line.get("interval", DEFAULT_INTERVAL) for line in merged_lines]
        time_info = merge_wav_files(
            wav_paths=wav_paths,
            output_path=str(merged_path),
            leading_gaps=gaps,
        )
        state["time_info"] = time_info
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
        indices = [i for i in indices if isinstance(i, int) and 0 <= i < len(state["lines"])]
        if not indices:
            return jsonify({"error": "没有可生成的台词"}), 400

        state["generating"] = True
        state["progress"] = {"current": 0, "total": len(indices)}
        for idx in indices:
            state["generated"].pop(idx, None)
        state["time_info"] = []

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
                        if tts_lang not in ("zh", "ja"):
                            tts_lang = "zh"
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

    @app.route("/api/progress", methods=["GET"])
    def get_progress():
        return jsonify({
            "generating": state["generating"],
            "progress": state["progress"],
            "generated_count": len(state["generated"]),
            "generated_indices": sorted(state["generated"].keys()),
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

    @app.route("/api/export_tracks", methods=["POST"])
    def export_tracks():
        if state["generating"]:
            return jsonify({"error": "生成中，请稍后再导出"}), 409
        if not state["generated"]:
            return jsonify({"error": "没有已生成的音频"}), 400

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

            ordered_idx = sorted(state["generated"].keys())
            wav_paths = []
            merged_lines = []
            gaps = []
            for idx in ordered_idx:
                gen_path = state["generated"][idx]["path"]
                if Path(gen_path).exists():
                    wav_paths.append(gen_path)
                    merged_lines.append(state["lines"][idx])
                    gaps.append(state["lines"][idx].get("interval", DEFAULT_INTERVAL))
            if not wav_paths:
                raise RuntimeError("没有可导出的音频文件")

            time_map = dict(zip(ordered_idx, state["time_info"]))

            import numpy as np
            import soundfile as sf

            first_info = sf.info(wav_paths[0])
            sample_rate = first_info.samplerate
            channels = first_info.channels
            total_duration = state["time_info"][-1]["end"]
            total_samples = max(1, int(round(total_duration * sample_rate)))

            char_indices = {}
            for idx in ordered_idx:
                char = state["lines"][idx]["character"]
                char_indices.setdefault(char, []).append(idx)

            created_files = []
            for char, idx_list in char_indices.items():
                track = np.zeros((total_samples, channels), dtype=np.float32)
                has_audio = False
                for idx in idx_list:
                    info = time_map.get(idx)
                    gen_path = state["generated"][idx]["path"]
                    if info is None or not Path(gen_path).exists():
                        continue
                    data, seg_sr = sf.read(gen_path, dtype="float32", always_2d=True)
                    start_sample = int(round(info["start"] * sample_rate))
                    length = min(len(data), max(0, total_samples - start_sample))
                    if length > 0:
                        seg = data[:length]
                        if seg.shape[1] != channels:
                            if seg.shape[1] == 1:
                                seg = np.repeat(seg, channels, axis=1)
                            elif channels == 1:
                                seg = seg[:, :1]
                        track[start_sample:start_sample + length] = seg
                        has_audio = True

                    shutil.copy2(gen_path, segments_dir / Path(gen_path).name)
                    created_files.append(str(segments_dir / Path(gen_path).name))

                if has_audio:
                    track_path = tracks_dir / f"{char}.wav"
                    sf.write(str(track_path), track, sample_rate)
                    created_files.append(str(track_path))

            export_merged = export_dir / "merged_output.wav"
            time_info = merge_wav_files(
                wav_paths=wav_paths,
                output_path=str(export_merged),
                leading_gaps=gaps,
            )
            export_srt = export_dir / "subtitles.srt"
            generate_srt(
                time_info=time_info,
                lines=merged_lines,
                output_path=str(export_srt),
            )
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
            if not str(resolved).startswith(str(project_resolved)):
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
        try:
            return jsonify(scan_environment(config, project_root, user_path or None))
        except Exception as e:
            traceback.print_exc()
            return jsonify({"error": "环境扫描失败: " + str(e)}), 500

    @app.route("/api/deploy/install", methods=["POST"])
    def deploy_install():
        data = request.get_json(silent=True) or {}
        user_path = str(data.get("gptsovits_path", "")).strip()
        if state["deploy_install"]["running"]:
            return jsonify({"error": "安装正在进行中"}), 409
        try:
            result = scan_environment(config, project_root, user_path or None)
            commands = result["install_plan"]["commands"]
            if not commands:
                return jsonify({"status": "nothing_to_install", "install_plan": result["install_plan"]})
            state["deploy_install"] = {"running": True, "done": False, "success": None, "log": []}

            def worker():
                try:
                    for cmd in commands:
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
                        proc.wait()
                        if proc.returncode != 0:
                            state["deploy_install"]["success"] = False
                            return
                    state["deploy_install"]["success"] = True
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
    return app
