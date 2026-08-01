"""GPT-SoVITS TTS 推理引擎 —— 直接调用 Python 推理代码。"""

import os
import sys
import time
import threading
from io import BytesIO
from pathlib import Path
from typing import Optional

import numpy as np
import soundfile as sf


class TTSEngine:
    """GPT-SoVITS TTS 推理引擎，直接导入并调用项目内的推理模块。"""

    def __init__(self, gptsovits_path: str):
        """
        Args:
            gptsovits_path: GPT-SoVITS 项目的根目录绝对路径
        """
        self.gptsovits_path = Path(gptsovits_path).resolve()
        if not self.gptsovits_path.exists():
            raise FileNotFoundError(f"GPT-SoVITS 路径不存在: {self.gptsovits_path}")

        # 将 GPT-SoVITS 目录加入 sys.path
        sys.path.insert(0, str(self.gptsovits_path))
        sys.path.insert(0, str(self.gptsovits_path / "GPT_SoVITS"))

        # 切换到 GPT-SoVITS 目录（因为项目内部使用相对路径）
        self._original_cwd = os.getcwd()
        os.chdir(str(self.gptsovits_path))

        self._tts_config = None
        self._tts_pipeline = None
        self._lock = threading.Lock()
        self._loaded = False

    def load(self):
        """加载 TTS 模型（首次调用时自动完成）。"""
        if self._loaded:
            return

        with self._lock:
            if self._loaded:
                return

            from GPT_SoVITS.TTS_infer_pack.TTS import TTS, TTS_Config

            config_path = str(
                self.gptsovits_path / "GPT_SoVITS" / "configs" / "tts_infer.yaml"
            )
            self._tts_config = TTS_Config(config_path)
            self._tts_pipeline = TTS(self._tts_config)
            self._loaded = True
            print("[TTSEngine] 模型加载完成")

    def switch_character(self, model_path: str):
        """切换当前角色的 SoVITS 模型权重。

        Args:
            model_path: 模型权重文件路径，相对于 gptsovits_path 或绝对路径
        """
        self.load()

        model_full_path = self.gptsovits_path / model_path
        if not model_full_path.exists():
            raise FileNotFoundError(f"模型文件不存在: {model_full_path}")

        with self._lock:
            self._tts_pipeline.init_vits_weights(str(model_full_path))
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
    ) -> tuple[int, np.ndarray]:
        """
        合成语音。

        Returns:
            (sample_rate, audio_data): 采样率和音频 numpy 数组
        """
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
        """
        合成语音并保存到文件。

        Returns:
            float: 音频时长（秒）
        """
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

    def cleanup(self):
        """恢复原始工作目录。"""
        try:
            os.chdir(self._original_cwd)
        except OSError:
            pass


# 全局引擎实例（单例，避免多次加载模型）
_engine: Optional[TTSEngine] = None


def get_engine(gptsovits_path: str) -> TTSEngine:
    """获取全局 TTS 引擎实例。"""
    global _engine
    if _engine is None:
        _engine = TTSEngine(gptsovits_path)
    return _engine
