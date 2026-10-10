from __future__ import annotations

import asyncio
import json
import os
import platform
import shutil
import subprocess
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import psutil
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from studio.audio import convert_reference
from studio.models import CATALOG, DATA, MODEL_HOME, ROOT, model_list, saved_path

@asynccontextmanager
async def lifespan(application):
    yield
    # A stopped server must not leave a GPU worker running in the background.
    for key, process in list(active.items()):
        if process.poll() is None:
            process.terminate()
            try:
                await asyncio.to_thread(process.wait, timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                await asyncio.to_thread(process.wait, timeout=5)
            path = DATA / "jobs" / key / "state.json"
            if path.exists():
                state = json.loads(path.read_text())
                if state.get("status") in ("pending", "running"):
                    state.update(status="cancelled", message="服务已停止，任务取消。")
                    path.write_text(json.dumps(state, ensure_ascii=False))


app = FastAPI(title="故事声音工坊", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
for folder in (DATA / "references", DATA / "jobs", MODEL_HOME):
    folder.mkdir(parents=True, exist_ok=True)
MAX_UPLOAD = 25 * 1024 ** 2
active: dict[str, subprocess.Popen] = {}
job_lock = threading.Lock()


@app.middleware("http")
async def local_requests(request: Request, call_next):
    origin = request.headers.get("origin")
    if request.method not in ("GET", "HEAD", "OPTIONS") and origin:
        from urllib.parse import urlsplit
        if origin != f"{request.url.scheme}://{request.headers.get('host')}" or urlsplit(origin).hostname not in ("localhost", "127.0.0.1"):
            return JSONResponse({"detail": "请在本机页面中操作。"}, status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def identifier(value: str) -> str:
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise HTTPException(404, "找不到这条记录。")


def job_directory(value: str) -> Path:
    path = DATA / "jobs" / identifier(value)
    if not (path / "state.json").exists():
        raise HTTPException(404, "找不到这个任务。")
    return path


def read_job(path: Path) -> dict:
    state = json.loads((path / "state.json").read_text())
    process = active.get(path.name)
    if state["status"] in ("pending", "running") and (process is None or process.poll() is not None):
        # The worker may have atomically completed between our read and poll.
        state = json.loads((path / "state.json").read_text())
        if state["status"] in ("pending", "running"):
            state.update(status="failed", message="生成进程已结束，请重新生成。")
            (path / "state.json").write_text(json.dumps(state, ensure_ascii=False))
    if state["status"] in ("pending", "running"):
        state["elapsed"] = round(time.time() - state["created_at"], 1)
    return state


def create_job(request: dict) -> dict:
    with job_lock:
        if any(process.poll() is None for process in active.values()):
            raise HTTPException(409, "正在处理另一个任务，请等待完成或先取消。")
        active.clear()
        key = str(uuid.uuid4())
        path = DATA / "jobs" / key
        path.mkdir()
        state = {"id": key, "kind": request["kind"], "model": request["model"],
                 "title": request.get("title", CATALOG[request["model"]]["name"]),
                 "status": "pending", "message": "任务准备中…", "created_at": time.time()}
        (path / "request.json").write_text(json.dumps(request, ensure_ascii=False))
        (path / "state.json").write_text(json.dumps(state, ensure_ascii=False))
        worker_env = {**os.environ, "PYTHONUNBUFFERED": "1", "HF_HUB_DISABLE_XET": "1",
                      "HF_HUB_DISABLE_TELEMETRY": "1"}
        if request["kind"] == "generate":
            worker_env.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        with (path / "worker.log").open("w") as log:
            active[key] = subprocess.Popen(
                [sys.executable, "-m", "studio.worker", str(path)], cwd=ROOT,
                stdout=log, stderr=subprocess.STDOUT,
                env=worker_env,
            )
        return state


@app.get("/api/status")
def status():
    machine = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"],
                             capture_output=True, text=True).stdout.strip() if sys.platform == "darwin" else platform.machine()
    return {"models": model_list(), "hardware": {"chip": machine,
            "memory_gb": round(psutil.virtual_memory().total / 1024 ** 3),
            "free_disk_gb": round(shutil.disk_usage(ROOT).free / 1024 ** 3, 1)},
            "ffmpeg": shutil.which("ffmpeg") is not None}


@app.post("/api/references")
async def upload_reference(file: UploadFile = File(...)):
    key = str(uuid.uuid4())
    path = DATA / "references" / key
    path.mkdir()
    source = path / "original.audio"
    size = 0
    try:
        with source.open("wb") as target:
            while chunk := await file.read(1024 ** 2):
                size += len(chunk)
                if size > MAX_UPLOAD:
                    raise HTTPException(413, "音频文件不能超过 25 MB。")
                target.write(chunk)
        if not size:
            raise HTTPException(400, "音频文件为空。")
        try:
            info = await asyncio.to_thread(convert_reference, source, path / "reference.wav")
        except (ValueError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(400, str(exc))
        info.update(id=key, name=(file.filename or "我的录音")[:150], audio_url=f"/api/references/{key}/audio")
        (path / "metadata.json").write_text(json.dumps(info, ensure_ascii=False))
        return info
    except Exception:
        shutil.rmtree(path, ignore_errors=True)
        raise
    finally:
        await file.close()
        source.unlink(missing_ok=True)


@app.get("/api/references/{reference_id}/audio")
def reference_audio(reference_id: str):
    path = DATA / "references" / identifier(reference_id) / "reference.wav"
    if not path.exists():
        raise HTTPException(404, "录音不存在。")
    return FileResponse(path, media_type="audio/wav")


@app.get("/api/references/{reference_id}")
def reference_metadata(reference_id: str):
    path = DATA / "references" / identifier(reference_id) / "metadata.json"
    if not path.exists():
        raise HTTPException(404, "录音不存在。")
    return json.loads(path.read_text())


@app.delete("/api/references/{reference_id}")
def delete_reference(reference_id: str):
    key = identifier(reference_id)
    with job_lock:
        for job_id, process in active.items():
            if process.poll() is None:
                request = json.loads((DATA / "jobs" / job_id / "request.json").read_text())
                if request.get("reference_id") == key:
                    raise HTTPException(409, "这段录音正在生成中，请先取消任务。")
        shutil.rmtree(DATA / "references" / key, ignore_errors=True)
    return {"ok": True}


class InstallRequest(BaseModel):
    model: str


@app.post("/api/models/install")
def install(request: InstallRequest):
    if request.model not in CATALOG:
        raise HTTPException(400, "未知模型。")
    if not saved_path(request.model) and shutil.disk_usage(ROOT).free < (CATALOG[request.model]["size_gb"] + 2) * 1024 ** 3:
        raise HTTPException(400, "可用磁盘空间不足，请腾出空间后下载。")
    return create_job({"kind": "install", "model": request.model})


class GenerateRequest(BaseModel):
    model: str
    reference_id: str
    transcript: str = Field(min_length=1, max_length=1500)
    text: str = Field(min_length=1, max_length=5000)
    title: str = Field(default="我的故事", max_length=80)
    clone_mode: Literal["faithful", "timbre"] = "faithful"
    speed: float = Field(default=1.0, ge=0.75, le=1.25)
    pause_ms: int = Field(default=350, ge=0, le=1500)
    seed: int = Field(default=42, ge=0, le=2147483647)
    steps: int = Field(default=10, ge=4, le=30)


@app.post("/api/generate")
def generate(request: GenerateRequest):
    if request.model not in CATALOG or not saved_path(request.model):
        raise HTTPException(400, "请先准备所选模型。")
    reference = DATA / "references" / identifier(request.reference_id) / "reference.wav"
    if not reference.exists():
        raise HTTPException(400, "请先录制或上传你的声音。")
    if not request.transcript.strip() or not request.text.strip():
        raise HTTPException(400, "请填写录音原文和需要朗读的文字。")
    payload = request.model_dump()
    payload.update(kind="generate", reference_path=str(reference))
    return create_job(payload)


@app.get("/api/jobs")
def history():
    paths = sorted((DATA / "jobs").glob("*/state.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [read_job(path.parent) for path in paths[:50]]


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    path = job_directory(job_id)
    state = read_job(path)
    log = path / "worker.log"
    if log.exists() and state["status"] in ("running", "failed"):
        with log.open("rb") as file:
            file.seek(max(0, log.stat().st_size - 2500))
            state["log"] = file.read().decode("utf-8", errors="replace")
    return state


@app.post("/api/jobs/{job_id}/cancel")
def cancel(job_id: str):
    path = job_directory(job_id)
    with job_lock:
        process = active.get(path.name)
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            state = json.loads((path / "state.json").read_text())
            state.update(status="cancelled", message="任务已取消，内存已释放。",
                         elapsed=round(time.time() - state["created_at"], 2))
            (path / "state.json").write_text(json.dumps(state, ensure_ascii=False))
    return read_job(path)


@app.get("/api/jobs/{job_id}/audio.{format}")
def audio(job_id: str, format: str):
    if format not in ("wav", "mp3"):
        raise HTTPException(404, "未知音频格式。")
    directory = job_directory(job_id)
    state = read_job(directory)
    path = directory / f"output.{format}"
    if state["status"] != "completed" or not path.exists():
        raise HTTPException(404, "音频尚未生成。")
    title = state.get("title", "我的故事").replace("/", "_").replace("\\", "_").replace("\n", " ").replace("\r", " ")
    return FileResponse(path, media_type="audio/wav" if format == "wav" else "audio/mpeg",
                        filename=f"{title}.{format}")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    directory = job_directory(job_id)
    with job_lock:
        process = active.get(directory.name)
        if process is not None and process.poll() is None:
            raise HTTPException(409, "请先取消这个任务。")
        shutil.rmtree(directory)
        active.pop(directory.name, None)
    return {"ok": True}


app.mount("/", StaticFiles(directory=ROOT / "web", html=True), name="web")
