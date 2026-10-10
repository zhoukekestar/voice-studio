"""Real HTTP → MLX → WAV/MP3 integration, using supplied test audio.

This checks function and speed, not whether a person's clone sounds like them.
"""
import argparse
import io
import json
import time
from pathlib import Path

import httpx
import soundfile as sf


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--models", nargs="+", default=["voxcpm2", "qwen3", "voxcpm15"])
    args = parser.parse_args()
    evidence = []
    with httpx.Client(base_url="http://127.0.0.1:8765", timeout=60) as client:
        with args.reference.open("rb") as file:
            response = client.post("/api/references", files={"file": (args.reference.name, file)})
        response.raise_for_status()
        reference = response.json()
        print("Reference:", reference["duration"], "seconds", flush=True)
        for model in args.models:
            response = client.post("/api/generate", json={
                "model": model, "reference_id": reference["id"],
                "transcript": args.transcript,
                "text": "小熊提着一盏小灯，轻轻地说：晚安，小朋友。愿你做一个甜甜的梦。",
                "title": f"流程测试 · {model}", "seed": 42,
            })
            response.raise_for_status()
            job_id = response.json()["id"]
            print("Started", model, job_id, flush=True)
            deadline = time.monotonic() + 900
            previous = None
            while time.monotonic() < deadline:
                response = client.get(f"/api/jobs/{job_id}")
                response.raise_for_status()
                job = response.json()
                if job["message"] != previous:
                    print(model, job["status"], job["message"], flush=True)
                    previous = job["message"]
                if job["status"] in ("failed", "cancelled"):
                    print(job.get("log", ""), flush=True)
                    evidence.append(job)
                    break
                if job["status"] == "completed":
                    wav = client.get(job["wav_url"])
                    mp3 = client.get(job["mp3_url"])
                    assert wav.status_code == mp3.status_code == 200
                    assert len(wav.content) > 4000 and len(mp3.content) > 1000
                    info = sf.info(io.BytesIO(wav.content))
                    assert info.duration > 0.5
                    print(json.dumps(job, ensure_ascii=False), flush=True)
                    evidence.append(job)
                    break
                time.sleep(2)
            else:
                client.post(f"/api/jobs/{job_id}/cancel")
                raise TimeoutError(f"{model} did not finish")
            Path("data/smoke-results.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2))
    assert all(job["status"] == "completed" for job in evidence), "Some real inference tests failed"


if __name__ == "__main__":
    main()
