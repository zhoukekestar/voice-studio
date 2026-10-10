#!/bin/zsh
set -eu
cd "${0:A:h}"
export PATH="/opt/homebrew/bin:$HOME/.local/bin:$PATH"
if ! command -v uv >/dev/null 2>&1; then
  echo "请先安装 uv：https://docs.astral.sh/uv/getting-started/installation/"
  read -r "?按回车退出"
  exit 1
fi
if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "请先安装 FFmpeg：brew install ffmpeg"
  read -r "?按回车退出"
  exit 1
fi
uv sync --frozen --no-dev
if ! .venv/bin/python -m studio.service start; then
  read -r "?按回车退出"
  exit 1
fi
open http://127.0.0.1:8765
echo "关闭本窗口不会停止服务。需要停止时，双击‘停止声音工坊.command’。"
