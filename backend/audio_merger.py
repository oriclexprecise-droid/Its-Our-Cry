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
    leading_gaps: Optional[list[float]] = None,
    sample_rate: Optional[int] = None,
) -> list[dict]:
    """
    将多个 wav 文件按顺序合并为一个文件。

    Args:
        wav_paths: 按顺序排列的 wav 文件路径列表
        output_path: 合并后的输出路径
        silence_between: 句子之间的静音间隔（秒）
        leading_gaps: 每句前的自定义间隔（秒），长度需与 wav_paths 一致
        sample_rate: 目标采样率，None 表示使用第一个文件的采样率

    Returns:
        list of dict: 每条音频的时间信息
            [{"path": "...", "start": 0.0, "end": 1.5, "duration": 1.5}, ...]
    """
    if not wav_paths:
        raise ValueError("wav_paths 不能为空")
    if leading_gaps is not None and len(leading_gaps) != len(wav_paths):
        raise ValueError("leading_gaps 长度必须与 wav_paths 一致")

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

    if leading_gaps is None:
        gaps = [0.0] + [silence_between] * (len(audio_segments) - 1)
    else:
        gaps = [max(0.0, float(g)) for g in leading_gaps]

    # 计算时间信息
    current_time = 0.0
    for i, seg in enumerate(audio_segments):
        current_time += gaps[i]
        duration = len(seg["frames"]) / (seg["sr"] * seg["width"] * seg["channels"])
        time_info.append({
            "path": wav_paths[i],
            "start": current_time,
            "end": current_time + duration,
            "duration": duration,
        })
        current_time += duration

    def make_silence(seconds: float) -> bytes:
        frames = int(target_sr * seconds) * target_width * target_channels
        return b"\x00" * frames

    # 合并写入
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(output_path), "w") as out:
        out.setnchannels(target_channels)
        out.setsampwidth(target_width)
        out.setframerate(target_sr)

        for i, seg in enumerate(audio_segments):
            out.writeframes(make_silence(gaps[i]))
            # 重采样（如果需要）
            if seg["sr"] != target_sr or seg["width"] != target_width:
                # 简单情况：采样率和位深度不同时，直接写原始数据
                # 复杂重采样留给 pydub 或 sox，这里做基础支持
                out.writeframes(seg["frames"])
            else:
                out.writeframes(seg["frames"])

    return time_info



def convert_channels(data: bytes, src_channels: int, dst_channels: int, sampwidth: int) -> bytes:
    """在单/双声道之间转换 PCM 字节帧，不改变采样率与位深。"""
    if src_channels == dst_channels:
        return data
    frame_len = src_channels * sampwidth
    n_frames = len(data) // frame_len
    out = bytearray()
    if dst_channels > src_channels:
        for i in range(n_frames):
            frame = data[i * frame_len:(i + 1) * frame_len]
            out.extend(frame)
            out.extend(frame)
    else:
        for i in range(n_frames):
            frame = data[i * frame_len:(i + 1) * frame_len]
            out.extend(frame[:dst_channels * sampwidth])
    return bytes(out)

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
            f.write(f"{line['text']}\n\n")

    return output_path
