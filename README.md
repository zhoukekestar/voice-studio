# 故事声音工坊

在 Apple Silicon Mac 上运行的本地声音克隆应用：录音（或上传音频）→ 填写录音原文 → 输入新的故事文字 → 试听并下载 MP3 / WAV。

## 启动

需要 Apple Silicon、macOS、Python 3.12、[uv](https://docs.astral.sh/uv/) 和 FFmpeg。已在开发机器安装依赖。

双击 `启动声音工坊.command`，或在项目目录运行：

```sh
uv sync --frozen
uv run python -m uvicorn studio.app:app --host 127.0.0.1 --port 8765
```

浏览器打开 <http://127.0.0.1:8765>。Safari / Chrome 支持在 localhost 获取麦克风权限；如果应用内浏览器不能录音，可以上传手机录音。

双击启动器后，服务在后台运行，关闭启动终端或结束聊天不会停止它。双击 `停止声音工坊.command` 可以停止；重启电脑后需重新双击启动器。没有安装开机自启。服务日志在 `data/server.log`。

页面每 10 秒检查一次连接。服务断开时，会显示中文提示并禁用生成；恢复服务后会自动重连。录音上传遇到断线时，音频暂存在当前页面，可点击“重新保存录音”；保存成功前请勿关闭页面。

## 使用

1. 在安静环境录下 8～20 秒完整话语，避免背景音乐。页面提供可直接朗读的示例，也支持 WAV / MP3 / M4A / WebM 等已有音频。文件最大 25 MB，实际时长 3～45 秒。
2. 填写与录音完全一致的原文；上传文件时会清空示例，避免错用文字。不要把需要合成的新故事放在这里。
3. 输入需要朗读的新文字（最多 5000 字符），选择已准备好的模型。首次使用新模型需联网下载，之后离线生成。
4. 初次对比使用速度 1.0、种子 42、相同参考录音和短文本。VoxCPM2 默认使用参考音频及原文的 combined / continuation 模式，也可选择只参考音色。
5. 点击生成，完成后试听、下载 MP3 或 WAV。长文本按句子自动分段，原文不删减，默认段间加 350 毫秒静音，再合成为一份音频。

速度滑块是 FFmpeg 的生成后处理（保持音高），不是模型内部语速指令。扩散步数只作用于 VoxCPM 系列。种子有助于重复对比，但跨版本或硬件不保证逐样本一致。

## 模型选择

| 模型 | 本地运行方式 | 用途 |
|---|---|---|
| VoxCPM2 2B / 8-bit | MLX，约 3.3 GB 权重 | 首选试听的新路线；中文、48 kHz，支持 reference 与 continuation combined 克隆 |
| Qwen3-TTS 1.7B Base / 8-bit | MLX，约 2.2 GB | 对照；传入准确参考原文，优先复用本机 Vocello / QwenVoice 的 Base 权重 |
| VoxCPM 1.5 / fp16 | MLX，约 1.9 GB | 轻量实验备选；本次试读有文字偏差，暂不作为默认模型 |

不能只靠模型大小或项目发布的 CUDA 跑分判断这台 M1 Pro 的效果。应用显示实际载入时间、总用时、音频时长和 RTF（生成/导出用时 ÷ 输出时长，越小越快；不含模型载入时间）。是否比 Vocello 更像用户，需要用用户自己的录音盲听确认。

VoxCPM2 的 MLX 适配中 `ref_text` 不是 continuation 的开关；应用明确传 `prompt_audio`、`prompt_text` 和 `ref_audio`，与官方所述组合克隆方式对应。Qwen 使用 Base 模型而非预设音色的 CustomVoice。

IndexTTS2 / CosyVoice3 也值得后续试听，目前先控制适配复杂度和磁盘用量，不在界面列出尚未接通的引擎。

## 数据与资源

- 只监听 `127.0.0.1`，没有云端推理或外部字体请求；推理进程开启 Hugging Face / Transformers 离线模式，并关闭 Hugging Face 遥测。
- 首次下载从 Hugging Face 获取开源权重；录音、原文和生成文字不发送给模型仓库。
- `data/references/`：参考音频及元数据；页面“重新录制”会删除当前参考。
- `data/jobs/`：任务参数、分段 WAV、完整 MP3 / WAV、运行日志；“我的音频”可删除记录。
- `models/`：新下载的模型缓存与版本记录。VoxCPM2 固定到已核实的模型 revision；其他模型首次下载后在 manifest 中记录 revision。
- `.venv/`、`data/`、`models/` 均不进入 Git。每个生成任务用单独进程，结束或取消时释放模型内存，不同时加载多个模型。
- 重启服务时会把已中断的任务标为失败，已完成音频和模型保留。
- 本机已有 Vocello Base 权重时仅只读复用，不修改原应用的文件。也可用 `STORY_QWEN_MODEL_PATH` 指向兼容的本地模型目录。
- 用 `STORY_VOICE_DATA` / `STORY_VOICE_MODELS` 设置独立数据与模型目录。

## 验证

```sh
uv run pytest
node --check web/app.js
```

模型推理还需执行真实音频 smoke test；单元测试不会下载模型，也不把 mock 输出当作模型验证。

本机已完成 14 项测试，并实际运行三种模型，验证上传参考音频、推理及 MP3 / WAV 导出。2026-10-10 在 Apple M1 Pro / 16 GB 上，使用同一段约 12 秒的 macOS 合成中文声音作为参考，朗读同一句 31 字短文本，速度 1.0、种子 42、VoxCPM 扩散步数 10：

| 模型 | 输出时长 | 总用时（含载入） | MLX 峰值分配 |
|---|---:|---:|---:|
| VoxCPM2 8-bit | 7.02 秒 | 41.69 秒 | 5.92 GB |
| Qwen3-TTS Base 8-bit | 6.86 秒 | 22.15 秒 | 7.80 GB |
| VoxCPM 1.5 fp16 | 5.91 秒 | 18.86 秒 | 5.17 GB |

这些数字是单次流程检查，不是完整跑分；峰值仅指 MLX 分配，不含系统和其他进程。参考声音并非用户录音，因此没有验证“更像用户”。临时用 Whisper base 做转写检查时，千问与 VoxCPM2 大致覆盖目标文本，VoxCPM 1.5 出现较多文字偏差；自动转写也可能出错，最终需人工试听。试读文件已保存在“我的音频”，标题明确标为流程测试。

网页端另已验证 VoxCPM2 的 130 字故事：自动切成两段，合并得到 29.93 秒音频，总用时 132.12 秒，成功通过浏览器下载为 48 kHz 单声道 MP3。长故事适合提前生成，不适合作为实时对话。

Qwen 的载入兼容本机 Vocello 较早的量化权重布局，依据权重中的 scales / biases 设置量化层，不修改原权重。参考录音统一保留为 48 kHz，交给各模型适配器按需要重采样。

复现推理检查（仅使用你有权使用的参考音频）：

```sh
uv run python scripts/smoke_test.py --reference /绝对路径/参考.wav --transcript '录音中实际说的话' --models voxcpm2 qwen3 voxcpm15
```

## 模型与适配来源

- [VoxCPM 官方](https://github.com/OpenBMB/VoxCPM)（代码与权重 Apache 2.0）
- [VoxCPM2 MLX 适配及调用参数](https://github.com/Blaizzy/mlx-audio/tree/main/mlx_audio/tts/models/voxcpm2)
- [Qwen3-TTS 官方](https://github.com/QwenLM/Qwen3-TTS)（Apache 2.0）
- [MLX-Audio](https://github.com/Blaizzy/mlx-audio)（MIT，当前锁定 0.5.8）
- [VoxCPM2 8-bit 权重](https://huggingface.co/mlx-community/VoxCPM2-8bit)

生成自己的声音无需第三方声线授权。绘本文字仍需来自你有权使用的内容。
