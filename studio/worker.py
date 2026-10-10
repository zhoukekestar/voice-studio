"""One MLX process per job: one model in memory, immediate cancellation/release."""
from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

from studio.models import CATALOG, install_model, saved_path


def write_state(directory: Path, **fields):
    path = directory / "state.json"
    current = json.loads(path.read_text()) if path.exists() else {}
    current.update(fields)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(current, ensure_ascii=False, indent=2))
    temporary.replace(path)


def main(directory: Path):
    request = json.loads((directory / "request.json").read_text())
    start = time.monotonic()
    key = request["model"]
    try:
        if request["kind"] == "install":
            write_state(directory, status="running", message="正在下载模型，可在下方查看实时下载日志。")
            path = install_model(key)
            write_state(directory, status="completed", message="模型已准备好，可以生成语音。",
                        elapsed=round(time.monotonic() - start, 2), model_path=str(path))
            return

        import mlx.core as mx
        import numpy as np
        import soundfile as sf
        from studio.engines import load_engine
        from studio.audio import export_audio, split_text

        path = saved_path(key)
        if not path:
            raise ValueError("请先下载所选模型。")
        # Leave room for macOS and the browser on a 16 GB unified-memory machine.
        mx.set_cache_limit(256 * 1024 ** 2)
        write_state(directory, status="running", message="正在载入模型…", phase="loading")
        model = load_engine(key, path)
        load_seconds = time.monotonic() - start
        parts = split_text(request["text"])
        reference = str(Path(request["reference_path"]))
        reference_input = reference
        if key == "voxcpm15":
            from mlx_audio.utils import load_audio
            # This older adapter expects a waveform, despite its str annotation.
            reference_input = load_audio(reference, sample_rate=model.audio_vae.sample_rate)
        transcript = request["transcript"].strip()
        waves = []
        segment_stats = []
        sample_rate = CATALOG[key]["sample_rate"]
        generation_start = time.monotonic()
        for index, part in enumerate(parts):
            write_state(directory, message=f"正在生成第 {index + 1} / {len(parts)} 段…",
                        phase="generating", segment=index + 1, total_segments=len(parts))
            mx.random.seed(request["seed"] + index)
            options = {"text": part, "ref_audio": reference_input,
                       "max_tokens": min(1400, max(256, len(part) * 8))}
            if key == "voxcpm2":
                # ref_text is NOT used for continuation in the MLX VoxCPM2 adapter.
                # Explicitly pass prompt_audio + prompt_text as well as ref_audio.
                if request["clone_mode"] == "faithful":
                    options.update(prompt_audio=reference, prompt_text=transcript)
                options.update(inference_timesteps=request["steps"], cfg_value=2.0)
            elif key == "voxcpm15":
                options.update(ref_text=transcript, inference_timesteps=request["steps"], cfg_value=2.0)
            else:
                options.update(ref_text=transcript, language="Chinese", temperature=0.7,
                               top_p=0.8, repetition_penalty=1.5)
            segment_start = time.monotonic()
            generated = []
            for result in model.generate(**options):
                sample_rate = result.sample_rate
                array = np.asarray(result.audio, dtype=np.float32).reshape(-1)
                if not np.all(np.isfinite(array)):
                    raise RuntimeError("模型输出出现无效数值，请重新生成或更换模型。")
                generated.append(array)
            if not generated or sum(map(len, generated)) < sample_rate // 10:
                raise RuntimeError("模型没有生成有效语音，请重试或检查参考录音。")
            audio = np.concatenate(generated)
            sf.write(directory / f"segment-{index + 1:03d}.wav", audio, sample_rate)
            segment_stats.append({"text": part, "seconds": round(len(audio) / sample_rate, 2),
                                  "generation_seconds": round(time.monotonic() - segment_start, 2)})
            if waves:
                waves.append(np.zeros(int(sample_rate * request["pause_ms"] / 1000), dtype=np.float32))
            waves.append(audio)
            mx.clear_cache()
        write_state(directory, phase="exporting", message="正在合并并导出 MP3 / WAV…")
        raw = directory / "raw.wav"
        sf.write(raw, np.concatenate(waves), sample_rate)
        export_audio(raw, directory / "output.wav", directory / "output.mp3", request["speed"])
        raw.unlink(missing_ok=True)
        info = sf.info(directory / "output.wav")
        duration = info.duration
        elapsed = time.monotonic() - start
        generation_seconds = time.monotonic() - generation_start
        write_state(directory, status="completed", phase="done", message="语音已生成，可以试听和下载。",
                    duration=round(duration, 2), sample_rate=info.samplerate,
                    elapsed=round(elapsed, 2), load_seconds=round(load_seconds, 2),
                    generation_seconds=round(generation_seconds, 2),
                    peak_mlx_gb=round(mx.get_peak_memory() / 1024 ** 3, 2),
                    rtf=round(generation_seconds / duration, 2), segments=segment_stats,
                    wav_url=f"/api/jobs/{directory.name}/audio.wav",
                    mp3_url=f"/api/jobs/{directory.name}/audio.mp3")
    except Exception as exc:
        traceback.print_exc()
        write_state(directory, status="failed", message=str(exc)[:700],
                    elapsed=round(time.monotonic() - start, 2))
        sys.exit(1)


if __name__ == "__main__":
    main(Path(sys.argv[1]).resolve())
