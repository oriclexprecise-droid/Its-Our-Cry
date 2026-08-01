"""GPT-SoVITS TTS 推理引擎。

开发模式：直接在当前进程导入 GPT-SoVITS 推理代码。
打包模式：调用 GPT-SoVITS runtime 的 python 运行 tts_worker.py 子进程，
这样打包版 exe 不需要携带 torch / numpy / soundfile。
"""

import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from typing import Optional


class TTSEngine:
    """GPT-SoVITS TTS 推理引擎，支持进程内与子进程两种模式。"""

    def __init__(self, gptsovits_path: str, project_root=None, worker_script=None):
        self.gptsovits_path = str(gptsovits_path or "").strip()
        self.project_root = Path(project_root) if project_root else None
        self.worker_script = str(worker_script) if worker_script else None
        self._lock = threading.Lock()
        self._loaded = False
        self._tts_config = None
        self._tts_pipeline = None
        self._original_cwd = None
        self._current_model = ""
        self._current_gpt_model = ""
        self._in_process = self._can_run_in_process()

    def _can_run_in_process(self):
        """只有当前 python 就是 GPT-SoVITS runtime 时才在进程内加载。"""
        if not self.gptsovits_path:
            return False
        gs = Path(self.gptsovits_path).resolve()
        try:
            Path(sys.executable).resolve().relative_to(gs)
            return True
        except ValueError:
            return False

    def _runtime_python(self) -> str:
        if not self.gptsovits_path:
            raise RuntimeError("未配置 GPT-SoVITS 目录，请先在部署板块安装或填写路径")
        candidate = Path(self.gptsovits_path) / "runtime" / "python.exe"
        if not candidate.exists():
            raise RuntimeError("未找到 GPT-SoVITS 运行时 (runtime/python.exe)，请检查部署目录是否完整")
        return str(candidate)

    def load(self):
        """进程内模式加载模型（仅开发模式使用）。"""
        if self._loaded or not self._in_process:
            return
        with self._lock:
            if self._loaded:
                return
            gs = Path(self.gptsovits_path).resolve()
            sys.path.insert(0, str(gs))
            sys.path.insert(0, str(gs / "GPT_SoVITS"))
            self._original_cwd = os.getcwd()
            os.chdir(str(gs))

            from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

            config_path = str(gs / "GPT_SoVITS" / "configs" / "tts_infer.yaml")
            self._tts_config = TTS_Config(config_path)
            self._tts_pipeline = TTS(self._tts_config)
            self._loaded = True
            print("[TTSEngine] 模型加载完成（进程内）")

    def switch_character(self, model_path: str, gpt_model_path: Optional[str] = None):
        """记录当前角色模型；进程内模式还会直接切换权重。"""
        self._current_model = str(model_path)
        self._current_gpt_model = str(gpt_model_path) if gpt_model_path else ""
        if not self._in_process:
            return
        self.load()

        model_full_path = Path(model_path)
        if not model_full_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {model_full_path}")

        with self._lock:
            self._tts_pipeline.init_vits_weights(str(model_full_path))
            if gpt_model_path:
                gpt_full_path = Path(gpt_model_path)
                if not gpt_full_path.exists():
                    raise FileNotFoundError(f"GPT 模型文件不存在: {gpt_full_path}")
                self._tts_pipeline.init_t2s_weights(str(gpt_full_path))
                print(f"[TTSEngine] 已切换到 GPT 模型: {gpt_model_path}")
            print(f"[TTSEngine] 已切换到模型: {model_path}")

    def synthesize(
        self,
        text: str,
        ref_audio_path: str,
        prompt_text: str = "",
        text_lang: str = "zh",
        prompt_lang: str = "zh",
        text_split_method: str = "cut5",
        batch_size: int = 1,
        speed_factor: float = 1.0,
        fragment_interval: float = 0.3,
        temperature: float = 1.0,
        top_k: int = 15,
        top_p: float = 1.0,
        seed: int = -1,
    ) -> tuple:
        """合成语音，返回 (sample_rate, audio_data)。仅进程内模式使用。"""
        self.load()

        req = {
            "text": text,
            "text_lang": text_lang,
            "ref_audio_path": ref_audio_path,
            "prompt_text": prompt_text,
            "prompt_lang": prompt_lang,
            "text_split_method": text_split_method,
            "batch_size": batch_size,
            "speed_factor": speed_factor,
            "fragment_interval": fragment_interval,
            "temperature": temperature,
            "top_k": top_k,
            "top_p": top_p,
            "seed": seed,
            "streaming_mode": False,
            "media_type": "wav",
        }

        with self._lock:
            generator = self._tts_pipeline.run(req)
            sr, audio_data = next(generator)

        return sr, audio_data

    def synthesize_to_file(
        self,
        text: str,
        ref_audio_path: str,
        output_path: str,
        **kwargs,
    ) -> float:
        """合成语音并保存到文件，返回音频时长（秒）。"""
        if self._in_process:
            return self._synthesize_in_process(
                text=text,
                ref_audio_path=ref_audio_path,
                output_path=output_path,
                **kwargs,
            )
        return self._synthesize_worker(
            text=text,
            ref_audio_path=ref_audio_path,
            output_path=output_path,
            **kwargs,
        )

    def _synthesize_in_process(self, text, ref_audio_path, output_path, **kwargs) -> float:
        import numpy as np  # noqa: F401
        import soundfile as sf

        sr, audio_data = self.synthesize(
            text=text,
            ref_audio_path=ref_audio_path,
            **kwargs,
        )

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(output_path), audio_data, sr)

        duration = len(audio_data) / sr
        return duration

    def _synthesize_worker(self, text, ref_audio_path, output_path, **kwargs) -> float:
        if not self.worker_script or not Path(self.worker_script).exists():
            raise RuntimeError("找不到 TTS 推理脚本: " + str(self.worker_script))
        if not self._current_model:
            raise RuntimeError("尚未选择角色模型")

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        request = {
            "gptsovits_path": self.gptsovits_path,
            "model_path": self._current_model,
            "gpt_model_path": self._current_gpt_model,
            "text": text,
            "ref_audio_path": ref_audio_path,
            "output_path": str(output_path),
            "params": dict(kwargs),
        }
        req_path = output_path.with_suffix(".req.json")
        result_path = output_path.with_suffix(".result.json")
        req_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        if result_path.exists():
            result_path.unlink()

        try:
            cmd = [self._runtime_python(), str(self.worker_script), "--request", str(req_path)]
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            stdout, _ = proc.communicate()
            tail = (stdout or "")[-1500:]

            if not result_path.exists():
                raise RuntimeError("推理子进程异常退出（无结果文件）\n" + tail)

            result = json.loads(result_path.read_text(encoding="utf-8"))
            if result.get("error"):
                detail = str(result.get("traceback", ""))[-800:]
                raise RuntimeError("推理失败: " + str(result["error"]) + "\n" + detail)
            if proc.returncode != 0:
                raise RuntimeError("推理子进程返回码 " + str(proc.returncode) + "\n" + tail)

            duration = float(result["duration"])
            print("[TTSEngine] 子进程推理完成: " + output_path.name + " 时长 " + str(round(duration, 3)) + "s")
            return duration
        finally:
            for tmp in (req_path, result_path):
                try:
                    tmp.unlink()
                except Exception:
                    pass

    def cleanup(self):
        """恢复原始工作目录（进程内模式）。"""
        if self._original_cwd:
            try:
                os.chdir(self._original_cwd)
            except OSError:
                pass


# 全局引擎实例（单例，避免多次加载模型）
_engine: Optional[TTSEngine] = None


def get_engine(gptsovits_path: str, project_root=None, worker_script=None) -> TTSEngine:
    """获取全局 TTS 引擎实例。"""
    global _engine
    if _engine is None:
        _engine = TTSEngine(
            gptsovits_path,
            project_root=project_root,
            worker_script=worker_script,
        )
    return _engine
