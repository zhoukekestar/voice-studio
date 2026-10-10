#!/bin/zsh
set -eu
cd "${0:A:h}"
if [[ -x .venv/bin/python ]]; then
  if ! .venv/bin/python -m studio.service stop; then
    read -r "?按回车退出"
    exit 1
  fi
else
  echo "声音工坊尚未安装。"
fi
