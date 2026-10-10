from __future__ import annotations

import re
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def split_text(text: str, limit: int = 100) -> list[str]:
    """Preserve every character, prefer sentence boundaries, bound long sentences."""
    text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
    sentences = re.findall(r'.+?(?:[。！？!?；;]+[”’」』\"\']*|\n+|$)', text, flags=re.S)
    chunks: list[str] = []
    for sentence in sentences:
        while len(sentence) > limit:
            # Avoid cutting in the middle of a word if punctuation/space is nearby.
            cut = max(sentence.rfind(mark, limit // 2, limit) for mark in "，,、：: ")
            cut = cut + 1 if cut >= 0 else limit
            chunks.append(sentence[:cut])
            sentence = sentence[cut:]
        if not sentence:
            continue
        if chunks and len(chunks[-1]) + len(sentence) <= limit:
            chunks[-1] += sentence
        else:
            chunks.append(sentence)
    return [part for part in chunks if part.strip()]


def convert_reference(source: Path, destination: Path) -> dict:
    result = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-protocol_whitelist", "file,pipe", "-i", str(source), "-vn", "-t", "46", "-ac", "1", "-ar", "48000",
        "-c:a", "pcm_s16le", str(destination),
    ], capture_output=True, timeout=45)
    if result.returncode:
        raise ValueError("无法读取这个音频。请上传 WAV、MP3、M4A 或浏览器录音。")
    samples, rate = sf.read(destination, dtype="float32")
    duration = len(samples) / rate
    if not 3 <= duration <= 45:
        raise ValueError("参考录音需要 3～45 秒，建议录制 8～20 秒的一段完整话语。")
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples ** 2)))
    if rms < 0.0005:
        raise ValueError("录音几乎没有声音，请检查麦克风后重新录制。")
    warnings = []
    if np.mean(np.abs(samples) > 0.995) > 0.003:
        warnings.append("录音有明显过载，建议稍远离麦克风重新录制。")
    if rms < 0.012:
        warnings.append("录音音量偏低，靠近麦克风录制通常更清晰。")
    if duration > 25:
        warnings.append("参考录音较长。建议用 8～20 秒的完整短句进行对照。")
    return {"duration": round(duration, 2), "sample_rate": rate,
            "peak": round(peak, 4), "warnings": warnings}


def export_audio(source: Path, wav: Path, mp3: Path, speed: float):
    result = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(source), "-af", f"atempo={speed},alimiter=limit=0.95:level=false",
        "-c:a", "pcm_s16le", str(wav),
    ], capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("音频导出失败，请检查 FFmpeg。")
    result = subprocess.run([
        "ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
        "-i", str(wav), "-c:a", "libmp3lame", "-b:a", "192k", str(mp3),
    ], capture_output=True, timeout=90)
    if result.returncode:
        raise RuntimeError("MP3 导出失败。")
