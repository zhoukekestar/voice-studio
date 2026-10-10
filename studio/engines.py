"""Compatibility at the boundary; no changes to the user's original weights."""
from pathlib import Path


def load_engine(key: str, path: Path):
    from mlx_audio.tts.utils import load_model
    if key != "qwen3":
        return load_model(path)

    import json
    import mlx.core as mx
    from mlx_audio.tts.models.qwen3_tts import Model, ModelConfig
    from mlx_audio.utils import apply_quantization, load_weights

    config = json.loads((path / "config.json").read_text())
    config["model_path"] = str(path)
    model = Model(ModelConfig.from_dict(config))
    weights = model.sanitize(load_weights(path))

    # Some MLX Swift / older MLX conversions quantize text_embedding too.
    # Upstream's new quantization predicate skips this embedding, leaving packed
    # uint32 weights interpreted as dense floats. Respect the actual scales in
    # this checkpoint, while still validating every loaded parameter strictly.
    def checkpoint_predicate(name, module):
        if f"{name}.scales" in weights:
            return True
        return model.model_quant_predicate(name, module)

    apply_quantization(model, config, weights, checkpoint_predicate)
    model.load_weights(list(weights.items()), strict=True)
    mx.eval(model.parameters())
    model.eval()
    model = Model.post_load_hook(model, path)
    if model.speech_tokenizer is None:
        raise RuntimeError("千问的语音编解码器未载入，请重新安装兼容的 Base 模型。")
    return model

