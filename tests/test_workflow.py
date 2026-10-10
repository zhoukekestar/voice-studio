"""Validate file boundaries, text integrity and API failures without fake speech."""
import io
import json
import subprocess
import uuid
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from studio.audio import split_text
from studio import app as server


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "DATA", tmp_path)
    (tmp_path / "references").mkdir()
    (tmp_path / "jobs").mkdir()
    with TestClient(server.app) as client:
        yield client


def wav_bytes(seconds=5, silence=False):
    rate = 24000
    t = np.arange(int(rate * seconds)) / rate
    samples = np.zeros(len(t)) if silence else 0.08 * np.sin(2 * np.pi * 240 * t)
    target = io.BytesIO()
    sf.write(target, samples, rate, format="WAV")
    return target.getvalue()


@pytest.mark.parametrize("text", [
    "小熊有一盏灯。\n\n它说：‘晚安！’月亮回答：‘晚安。’",
    "没有标点的长句子" * 100,
    "Hello world! “A story.”\n小朋友，你好。" * 20,
    "它问：“你好吗？！”她说：“很好！”然后大家一起回家。" * 30,
])
def test_long_story_loses_no_characters(text):
    chunks = split_text(text)
    assert "".join(chunks) == text.strip()
    assert all(0 < len(chunk) <= 100 for chunk in chunks)


def test_upload_normalizes_and_validates_actual_audio(client):
    response = client.post("/api/references", files={"file": ("录音.wav", wav_bytes(), "audio/wav")})
    assert response.status_code == 200
    reference = response.json()
    assert reference["duration"] == 5
    audio = client.get(reference["audio_url"])
    info = sf.info(io.BytesIO(audio.content))
    assert info.samplerate == 48000 and info.channels == 1
    assert client.get(f'/api/references/{reference["id"]}').json()["id"] == reference["id"]
    assert not list((server.DATA / "references").glob("*/original.audio"))


@pytest.mark.parametrize("content", [b"not an audio file", wav_bytes(seconds=1), wav_bytes(silence=True), wav_bytes(seconds=46)], ids=["corrupt", "too-short", "silent", "too-long"])
def test_bad_reference_is_rejected_and_removed(client, content):
    response = client.post("/api/references", files={"file": ("bad.wav", content)})
    assert response.status_code == 400
    assert list((server.DATA / "references").iterdir()) == []


def test_generation_requires_real_reference_and_installed_model(client, monkeypatch):
    payload = {"model": "unknown", "reference_id": str(uuid.uuid4()), "transcript": "你好", "text": "晚安"}
    assert client.post("/api/generate", json=payload).status_code == 400
    monkeypatch.setattr(server, "saved_path", lambda key: Path("/available"))
    payload["model"] = "qwen3"
    assert client.post("/api/generate", json=payload).status_code == 400
    assert list((server.DATA / "jobs").iterdir()) == []


def test_cross_origin_cannot_upload_private_audio(client):
    response = client.post("/api/references", headers={"Origin": "https://example.com"}, files={"file": ("sample.wav", wav_bytes())})
    assert response.status_code == 403


def test_arbitrary_paths_are_not_audio_endpoints(client):
    assert client.get("/api/jobs/not-a-uuid/audio.wav").status_code == 404
    assert client.get(f"/api/jobs/{uuid.uuid4()}/audio.txt").status_code == 404
    assert client.get("/api/references/not-a-uuid/audio").status_code == 404


def test_restart_marks_interrupted_job_failed(client):
    key = str(uuid.uuid4())
    path = server.DATA / "jobs" / key
    path.mkdir()
    (path / "state.json").write_text(json.dumps({"id": key, "status": "running", "created_at": 0}))
    response = client.get(f"/api/jobs/{key}")
    assert response.json()["status"] == "failed"


def test_cancellation_releases_worker_and_allows_next_job(client):
    key = str(uuid.uuid4())
    path = server.DATA / "jobs" / key
    path.mkdir()
    (path / "state.json").write_text(json.dumps({"id": key, "status": "running", "created_at": 0}))
    process = subprocess.Popen(["python3", "-c", "import time; time.sleep(30)"])
    server.active[key] = process
    try:
        response = client.post(f"/api/jobs/{key}/cancel")
        assert response.json()["status"] == "cancelled"
        assert process.poll() is not None
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait()
        server.active.pop(key, None)
