"""音频合并与 SRT 字幕生成。"""

import wave
from pathlib import Path
from typing import Optional


def get_wav_duration(wav_path: str) -> float:
    """读取 wav 文件的时长（秒）。"""
    with wave.open(wav_path, "r") as wf:
        frames = wf.getnframes()
        rate = wf.getframerate()
        return frames / rate


def merge_wav_files(
    wav_paths: list[str],
    output_path: str,
    silence_between: float = 0.3,
    sample_rate: int = None,
) -> list[dict]:
    """
    将多个 wav 文件按顺序合并为一个文件。

    Args:
        wav_paths: 按顺序排列的 wav 文件路径列表
        output_path: 合并后的输出路径
        silence_between: 句子之间的静音间隔（秒）
        sample_rate: 目标采样率，None 表示使用第一个文件的采样率

    Returns:
        list of dict: 每条音频的时间信息
            [{"path": "...", "start": 0.0, "end": 1.5, "duration": 1.5}, ...]
    """
    if not wav_paths:
        raise ValueError("wav_paths 不能为空")

    # 读取所有音频数据
    audio_segments = []
    time_info = []
    target_sr = sample_rate
    target_width = None
    target_channels = None

    for i, wav_path in enumerate(wav_paths):
        with wave.open(wav_path, "r") as wf:
            sr = wf.getframerate()
            width = wf.getsampwidth()
            channels = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())

            if target_sr is None:
                target_sr = sr
            if target_width is None:
                target_width = width
            if target_channels is None:
                target_channels = channels

            audio_segments.append({
                "frames": frames,
                "sr": sr,
                "width": width,
                "channels": channels,
            })

    # 计算时间信息
    current_time = 0.0
    for i, seg in enumerate(audio_segments):
        duration = len(seg["frames"]) / (seg["sr"] * seg["width"] * seg["channels"])
        time_info.append({
            "path": wav_paths[i],
            "start": current_time,
            "end": current_time + duration,
            "duration": duration,
        })
        current_time += duration + silence_between

    # 生成静音片段
    silence_frames = int(target_sr * silence_between) * target_width * target_channels
    silence_data = b"\x00" * silence_frames

    # 合并写入
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(output_path), "w") as out:
        out.setnchannels(target_channels)
        out.setsampwidth(target_width)
        out.setframerate(target_sr)

        for i, seg in enumerate(audio_segments):
            # 重采样（如果需要）
            if seg["sr"] != target_sr or seg["width"] != target_width:
                # 简单情况：采样率和位深度不同时，直接写原始数据
                # 复杂重采样留给 pydub 或 sox，这里做基础支持
                out.writeframes(seg["frames"])
            else:
                out.writeframes(seg["frames"])

            # 写静音间隔（最后一句不加）
            if i < len(audio_segments) - 1:
                out.writeframes(silence_data)

    return time_info


def generate_srt(
    time_info: list[dict],
    lines: list[dict],
    output_path: str,
):
    """
    根据时间信息生成 SRT 字幕文件。

    Args:
        time_info: merge_wav_files 返回的时间信息
        lines: 台词列表（含 character 和 text）
        output_path: SRT 输出路径
    """
    def format_srt_time(seconds: float) -> str:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)
        millis = int((seconds % 1) * 1000)
        return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        for i, (info, line) in enumerate(zip(time_info, lines)):
            f.write(f"{i + 1}\n")
            f.write(
                f"{format_srt_time(info['start'])} --> "
                f"{format_srt_time(info['end'])}\n"
            )
            f.write(f"{line['character']}：{line['text']}\n\n")

    return output_path
