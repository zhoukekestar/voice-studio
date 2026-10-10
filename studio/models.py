"""Small, explicit registry. Downloaded weights and recordings never enter git."""
from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("STORY_VOICE_DATA", ROOT / "data")).resolve()
MODEL_HOME = Path(os.environ.get("STORY_VOICE_MODELS", ROOT / "models")).resolve()
CATALOG = {
    "voxcpm2": {
        "name": "VoxCPM2", "variant": "2B · 8-bit", "repo": "mlx-community/VoxCPM2-8bit",
        "revision": "d52725898a0675703f7f9ddc5a4d1a3cdbb99032",
        "size_gb": 3.3, "sample_rate": 48000, "tag": "优先试听",
        "description": "换一种克隆方案，支持音色与录音细节两种模式。",
    },
    "qwen3": {
        "name": "Qwen3-TTS", "variant": "1.7B Base · 8-bit",
        "repo": "mlx-community/Qwen3-TTS-12Hz-1.7B-Base-8bit",
        "revision": "main", "size_gb": 2.2, "sample_rate": 24000,
        "tag": "对照模型", "description": "使用完整参考文字，作为千问声音相似度的对照。",
    },
    "voxcpm15": {
        "name": "VoxCPM 1.5", "variant": "0.6B backbone · fp16",
        "repo": "mlx-community/VoxCPM1.5-fp16", "revision": "main",
        "size_gb": 1.9, "sample_rate": 44100,
        "tag": "实验备选", "description": "体积更小；本次试读有文字偏差，建议逐句检查。",
    },
}


def saved_path(key: str) -> Path | None:
    if key not in CATALOG:
        return None
    manifest = MODEL_HOME / f"{key}.json"
    if manifest.exists():
        try:
            path = Path(json.loads(manifest.read_text())["path"])
            if (path / "config.json").exists() and any(path.glob("*.safetensors")):
                return path
        except (OSError, ValueError, KeyError):
            pass
    if key == "qwen3":
        path = Path(os.environ.get("STORY_QWEN_MODEL_PATH", str(
            Path.home() / "Library/Application Support/QwenVoice/models/Qwen3-TTS-12Hz-1.7B-Base-8bit"
        )))
        if (path / "config.json").exists() and any(path.glob("*.safetensors")):
            return path
    return None


def model_list() -> list[dict]:
    return [{"id": key, **value, "installed": saved_path(key) is not None}
            for key, value in CATALOG.items()]


def install_model(key: str) -> Path:
    from huggingface_hub import snapshot_download
    if existing := saved_path(key):
        return existing
    spec = CATALOG[key]
    MODEL_HOME.mkdir(parents=True, exist_ok=True)
    path = Path(snapshot_download(
        spec["repo"], revision=spec["revision"], cache_dir=MODEL_HOME / "hub",
        ignore_patterns=["*.wav", "*.mp3", "*.md", ".gitattributes"], max_workers=2,
    ))
    manifest = MODEL_HOME / f"{key}.json"
    temporary = manifest.with_suffix(".tmp")
    temporary.write_text(json.dumps({"path": str(path), "revision": path.name,
                                    "repo": spec["repo"]}, indent=2))
    temporary.replace(manifest)
    return path
