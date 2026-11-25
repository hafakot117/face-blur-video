"""End-to-end tests for the remote server.

    pip install -r requirements.txt -r requirements-dev.txt
    pytest -q
"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["FACEBLUR_HOME"] = tempfile.mkdtemp(prefix="faceblur-test-")

import cv2  # noqa: E402
import numpy as np  # noqa: E402

import server  # noqa: E402
from face_tracker import find_ffmpeg  # noqa: E402

H = {"X-FaceBlur": "1"}
PASSWORD = "test-password-123"


@pytest.fixture(scope="module")
def client():
    server.set_password(PASSWORD)
    server.CFG.update(server.load_config())
    server.STORE.start_workers()
    c = server.app.test_client()
    yield c


@pytest.fixture(scope="module")
def face_video(tmp_path_factory):
    """5 s, 1280x720, two copies of a real face far apart, plus an audio track."""
    skimage_data = pytest.importorskip("skimage.data")
    face = cv2.resize(cv2.cvtColor(skimage_data.astronaut(), cv2.COLOR_RGB2BGR), (300, 300))
    d = tmp_path_factory.mktemp("media")
    raw = d / "raw.mp4"
    out = cv2.VideoWriter(str(raw), cv2.VideoWriter_fourcc(*"mp4v"), 25, (1280, 720))
    for i in range(100):
        fr = np.full((720, 1280, 3), 60, np.uint8)
        dx = int(20 * np.sin(i / 10))
        fr[200:500, 80 + dx:380 + dx] = face
        fr[250:550, 880 - dx:1180 - dx] = cv2.flip(face, 1)
        out.write(fr)
    out.release()
    final = d / "שני אנשים.mp4"
    subprocess.run([find_ffmpeg(), "-loglevel", "error", "-y", "-i", str(raw), "-f", "lavfi",
                    "-i", "sine=frequency=440:duration=4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-shortest", str(final)], check=True)
    return final


def login(c):
    r = c.post("/api/login", json={"password": PASSWORD}, headers=H)
    assert r.status_code == 200


def wait_state(c, job_id, states, timeout=120):
    end = time.time() + timeout
    while time.time() < end:
        job = c.get(f"/api/jobs/{job_id}").get_json()["job"]
        if job["state"] in states:
            return job
        time.sleep(0.3)
    raise AssertionError(f"timeout, last state {job['state']} {job.get('error')}")


# ------------------------------------------------------------------ auth
def test_auth_and_csrf(client):
    assert client.get("/api/health").status_code == 200
    assert client.get("/").status_code == 200
    assert client.get("/api/jobs").status_code == 401
    assert client.post("/api/login", json={"password": PASSWORD}).status_code == 400  # no header
    assert client.post("/api/login", json={"password": "wrong"}, headers=H).status_code == 401
    login(client)
    assert client.get("/api/jobs").status_code == 200
    assert client.post("/api/logout", headers=H).status_code == 200
    assert client.get("/api/jobs").status_code == 401


def test_password_change_logs_out(client):
    login(client)
    server.set_password(PASSWORD + "x")
    server.CFG.update(server.load_config())
    assert client.get("/api/jobs").status_code == 401
    server.set_password(PASSWORD)
    server.CFG.update(server.load_config())
    login(client)


# --------------------------------------------------------------- uploads
def test_full_flow_upload(client, face_video):
    login(client)
    data = face_video.read_bytes()
    r = client.post("/api/uploads", json={"name": face_video.name, "size": len(data)}, headers=H)
    assert r.status_code == 200, r.get_json()
    job_id = r.get_json()["job"]["id"]

    half = len(data) // 2
    assert client.put(f"/api/uploads/{job_id}?offset=0", data=data[:half], headers=H).get_json()["size"] == half
    # Resending the first chunk (a retry) is rejected with the real offset.
    r = client.put(f"/api/uploads/{job_id}?offset=0", data=data[:half], headers=H)
    assert r.status_code == 409 and r.get_json()["size"] == half
    # Completing early is refused.
    assert client.post(f"/api/uploads/{job_id}/complete", headers=H).status_code == 409
    client.put(f"/api/uploads/{job_id}?offset={half}", data=data[half:], headers=H)
    assert client.post(f"/api/uploads/{job_id}/complete", headers=H).status_code == 200

    job = wait_state(client, job_id, {"ready", "error"})
    assert job["state"] == "ready", job["error"]
    assert len(job["faces"]) == 2, job["faces"]
    assert job["info"]["width"] == 1280 and job["info"]["height"] == 720
    thumb = client.get(f"/api/jobs/{job_id}/faces/{job['faces'][0]['id']}.jpg")
    assert thumb.status_code == 200 and thumb.data[:2] == b"\xff\xd8"

    # Blur only the left face.
    r = client.post(f"/api/jobs/{job_id}/render", headers=H,
                    json={"faces": [0], "blur_type": "pixelate", "blur_strength": 151})
    assert r.status_code == 200
    job = wait_state(client, job_id, {"done", "error"})
    assert job["state"] == "done", job["error"]
    assert job["output_name"].endswith("_blurred.mp4")
    out_path = server.STORE.outputs_dir / job["output_name"]
    assert out_path.exists()

    # Audio survived, size is right, left face blurred and right face untouched.
    probe = subprocess.run([find_ffmpeg(), "-i", str(out_path)], capture_output=True, text=True).stderr
    assert "Audio:" in probe and "1280x720" in probe
    cap = cv2.VideoCapture(str(out_path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 50)
    ok, frame = cap.read()
    assert ok
    tracker_cls = __import__("face_tracker").FaceTracker
    t = tracker_cls(str(out_path))
    centers = [f["center"][0] for f in t.detect_faces_in_frame(frame, 0)]
    assert any(c > 640 for c in centers), "right face should still be detectable"
    assert not any(c < 640 for c in centers), "left face should be blurred"

    # Streaming with Range (needed by the iPhone video player) and download.
    r = client.get(f"/api/jobs/{job_id}/video", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.data) == 100
    r = client.get(f"/api/jobs/{job_id}/download")
    assert r.status_code == 200 and "attachment" in r.headers["Content-Disposition"]

    # Re-render with other settings replaces the previous output file.
    client.post(f"/api/jobs/{job_id}/render", headers=H, json={"faces": [0, 1], "blur_type": "oval"})
    job = wait_state(client, job_id, {"done", "error"})
    assert job["state"] == "done"
    assert not out_path.exists() or out_path.name == job["output_name"]

    assert client.delete(f"/api/jobs/{job_id}", headers=H).status_code == 200
    assert client.get(f"/api/jobs/{job_id}").status_code == 404


def test_upload_limits(client):
    login(client)
    assert client.post("/api/uploads", json={"name": "a.mp4", "size": 0}, headers=H).status_code == 400
    huge = (server.CFG["max_upload_gb"] + 1) * 1024 ** 3
    assert client.post("/api/uploads", json={"name": "a.mp4", "size": huge}, headers=H).status_code == 413
    r = client.post("/api/uploads", json={"name": "a.mp4", "size": 10}, headers=H)
    job_id = r.get_json()["job"]["id"]
    assert client.put(f"/api/uploads/{job_id}?offset=0", data=b"x" * 11, headers=H).status_code == 413
    client.delete(f"/api/jobs/{job_id}", headers=H)


def test_bad_video_reports_error(client):
    login(client)
    blob = b"this is not a video" * 10
    job_id = client.post("/api/uploads", json={"name": "bad.mov", "size": len(blob)}, headers=H).get_json()["job"]["id"]
    client.put(f"/api/uploads/{job_id}?offset=0", data=blob, headers=H)
    client.post(f"/api/uploads/{job_id}/complete", headers=H)
    job = wait_state(client, job_id, {"error", "ready"})
    assert job["state"] == "error" and job["error"]
    client.delete(f"/api/jobs/{job_id}", headers=H)


# --------------------------------------------------------------- library
def test_library_and_traversal(client, face_video):
    login(client)
    (server.INBOX / "sub").mkdir(exist_ok=True)
    target = server.INBOX / "sub" / "clip.mov"
    target.write_bytes(face_video.read_bytes())
    files = client.get("/api/library").get_json()["files"]
    assert any(f["ref"] == "0/sub/clip.mov" for f in files)

    for ref in ("0/../config.json", "0/../../etc/passwd", "5/x.mp4", "x", "0/sub/../../config.json"):
        assert client.post("/api/library/use", json={"ref": ref}, headers=H).status_code == 404, ref

    r = client.post("/api/library/use", json={"ref": "0/sub/clip.mov"}, headers=H)
    assert r.status_code == 200
    job_id = r.get_json()["job"]["id"]
    job = wait_state(client, job_id, {"ready", "error"})
    assert job["state"] == "ready"
    client.delete(f"/api/jobs/{job_id}", headers=H)
    assert target.exists(), "deleting a job must never delete the Mac's own file"


def test_invalid_ids(client):
    login(client)
    for bad in ("../../etc", "abc", "0" * 31):
        assert client.get(f"/api/jobs/{bad}").status_code == 404
