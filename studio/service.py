"""Manually started background service; closing the launcher does not stop it."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

import psutil

from studio.models import DATA, ROOT

URL = "http://127.0.0.1:8765"
PID_FILE = DATA / "server.pid"
LOG_FILE = DATA / "server.log"


def ready() -> bool:
    try:
        with urllib.request.urlopen(f"{URL}/api/status", timeout=1) as response:
            data = json.load(response)
        return any(model.get("id") == "voxcpm2" for model in data.get("models", []))
    except (OSError, ValueError, urllib.error.URLError):
        return False


def owned_process():
    try:
        process = psutil.Process(int(PID_FILE.read_text().strip()))
        command = process.cmdline()
        if "uvicorn" in command and "studio.app:app" in command and process.cwd() == str(ROOT):
            return process
    except (OSError, ValueError, psutil.Error):
        pass
    return None


def start():
    if ready():
        print(f"声音工坊已在运行：{URL}")
        return
    if owned_process():
        raise RuntimeError(f"服务仍在启动，请稍后重试。日志：{LOG_FILE}")
    with LOG_FILE.open("ab") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "studio.app:app", "--host", "127.0.0.1",
             "--port", "8765", "--no-access-log"],
            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
            start_new_session=True,
            env={**os.environ, "PATH": "/opt/homebrew/bin:" + os.environ.get("PATH", "")},
        )
    PID_FILE.write_text(str(process.pid))
    for _ in range(40):
        if process.poll() is not None:
            PID_FILE.unlink(missing_ok=True)
            raise RuntimeError(f"服务启动失败，请查看日志：{LOG_FILE}")
        if ready():
            print(f"声音工坊已在后台运行：{URL}\n可以关闭启动终端。")
            return
        time.sleep(.25)
    raise RuntimeError(f"服务尚未就绪，请查看日志：{LOG_FILE}")


def stop():
    process = owned_process()
    if process:
        process.terminate()
        try:
            process.wait(timeout=15)
        except psutil.TimeoutExpired:
            raise RuntimeError("服务尚未停止，请稍后重试。")
        PID_FILE.unlink(missing_ok=True)
        print("声音工坊已停止。")
    elif ready():
        raise RuntimeError("8765 上的服务不是由此启动器管理的，请在原终端停止。")
    else:
        PID_FILE.unlink(missing_ok=True)
        print("声音工坊当前未运行。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "stop", "status"])
    action = parser.parse_args().action
    DATA.mkdir(parents=True, exist_ok=True)
    try:
        with (DATA / "server.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            if action == "start":
                start()
            elif action == "stop":
                stop()
            else:
                print("运行中" if ready() else "未运行")
    except RuntimeError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
