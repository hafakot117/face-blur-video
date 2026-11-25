#!/usr/bin/env python3
"""Face Blur remote server - run on the iMac, use it from the phone.

    python server.py                 start the server
    python server.py --set-password  choose the login password

Data lives in ~/FaceBlur (change with FACEBLUR_HOME):
    config.json   password hash, secret key, settings
    Inbox/        put videos here on the Mac to process them from the phone
    Outputs/      finished, blurred videos
    jobs/         one folder per job (uploads, thumbnails) - cleaned up automatically
    logs/         server.log (written by the launchd service)
"""
import argparse
import getpass
import hmac
import json
import os
import secrets
import shutil
import socket
import sys
import threading
import time
from collections import deque
from datetime import timedelta
from pathlib import Path

from flask import Flask, abort, jsonify, request, send_file, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

import jobs as J
from face_tracker import VERSION, find_ffmpeg

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("FACEBLUR_HOME", "~/FaceBlur")).expanduser()
CONFIG_PATH = DATA_DIR / "config.json"
CHUNK_LIMIT = 64 * 1024 * 1024  # max size of one upload request (client sends 8 MB)

DEFAULTS = {
    "host": "127.0.0.1",   # only reachable through Tailscale Serve / this Mac
    "port": 8765,          # not 5000: macOS AirPlay Receiver uses 5000
    "library_dirs": [],    # extra folders to browse; Inbox is always included
    "retention_days": 14,  # delete uploads/thumbnails of old jobs after this
    "max_upload_gb": 20,
}


# ---------------------------------------------------------------- config
def load_config() -> dict:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(json.loads(CONFIG_PATH.read_text("utf-8")))
    changed = False
    if not cfg.get("secret_key"):
        cfg["secret_key"] = secrets.token_hex(32)
        changed = True
    if changed or not CONFIG_PATH.exists():
        save_config(cfg)
    return cfg


def save_config(cfg: dict):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


def set_password(password: str):
    cfg = load_config()
    cfg["password_hash"] = generate_password_hash(password)
    save_config(cfg)


CFG = load_config()
INBOX = DATA_DIR / "Inbox"
INBOX.mkdir(parents=True, exist_ok=True)
(DATA_DIR / "logs").mkdir(parents=True, exist_ok=True)
STORE = J.JobStore(DATA_DIR)
STARTED = time.time()

app = Flask(__name__, static_folder=str(BASE_DIR / "static"), static_url_path="/static")
app.config.update(
    SECRET_KEY=CFG["secret_key"],
    SESSION_COOKIE_NAME="faceblur",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=60),
    MAX_CONTENT_LENGTH=CHUNK_LIMIT,
)


# ------------------------------------------------------------------ auth
_failures: deque = deque()
_fail_lock = threading.Lock()


def _password_tag() -> str:
    """Changes whenever the password changes, which logs out old sessions."""
    return (CFG.get("password_hash") or "")[-16:]


def logged_in() -> bool:
    tag = _password_tag()
    return bool(tag) and hmac.compare_digest(session.get("auth", ""), tag)


@app.before_request
def guard():
    path = request.path
    if not path.startswith("/api/"):
        return None
    # Custom header on every state-changing call: a cross-site page cannot set it.
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get("X-FaceBlur") != "1":
        return jsonify(error="בקשה לא תקינה"), 400
    if path in ("/api/login", "/api/health"):
        return None
    if not logged_in():
        return jsonify(error="נדרשת התחברות"), 401
    return None


@app.post("/api/login")
def login():
    now = time.time()
    with _fail_lock:
        while _failures and _failures[0] < now - 900:
            _failures.popleft()
        if len(_failures) >= 10:
            return jsonify(error="יותר מדי ניסיונות. נסה שוב בעוד כמה דקות"), 429
    pw = (request.get_json(silent=True) or {}).get("password", "")
    h = CFG.get("password_hash")
    if h and pw and check_password_hash(h, pw):
        session.clear()
        session.permanent = True
        session["auth"] = _password_tag()
        return jsonify(ok=True)
    with _fail_lock:
        _failures.append(now)
    time.sleep(1)
    return jsonify(error="סיסמה שגויה"), 401


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/health")
def health():
    return jsonify(ok=True, version=VERSION)


# ---------------------------------------------------------------- helpers
def job_or_404(job_id: str) -> dict:
    job = STORE.get(job_id) if J.valid_id(job_id) else None
    if not job:
        abort(404)
    return job


def public_job(job: dict) -> dict:
    out = {k: job[k] for k in ("id", "name", "source", "state", "progress", "message", "error",
                               "faces", "settings", "info", "created_at", "updated_at", "expected_size")}
    out["output_name"] = Path(job["output_path"]).name if job.get("output_path") else ""
    out["output_size"] = job.get("output_size", 0)
    out["queue_position"] = STORE.queue_position(job["id"])
    if job["state"] == J.UPLOADING:
        p = Path(job["input_path"])
        out["uploaded"] = p.stat().st_size if p.exists() else 0
    return out


def library_roots() -> list:
    roots = [INBOX] + [Path(p).expanduser() for p in CFG.get("library_dirs", [])]
    seen, result = set(), []
    for r in roots:
        r = r.resolve()
        if r not in seen:
            seen.add(r)
            result.append(r)
    return result


def resolve_library_ref(ref: str):
    """'<root index>/<relative path>' -> absolute Path inside that root, or None."""
    try:
        idx, rel = ref.split("/", 1)
        root = library_roots()[int(idx)]
    except (ValueError, IndexError):
        return None
    p = (root / rel).resolve()
    if root != p and root not in p.parents:
        return None
    if not p.is_file() or p.suffix.lower() not in J.VIDEO_EXTS:
        return None
    return p


def short_path(p) -> str:
    home = str(Path.home())
    s = str(p)
    return "~" + s[len(home):] if s.startswith(home + os.sep) else s


def free_space() -> int:
    return shutil.disk_usage(DATA_DIR).free


# -------------------------------------------------------------------- pages
@app.get("/")
def index():
    resp = send_from_directory(app.static_folder, "index.html")
    resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest",
                               mimetype="application/manifest+json")


# --------------------------------------------------------------------- api
@app.get("/api/status")
def status():
    du = shutil.disk_usage(DATA_DIR)
    all_jobs = STORE.list()
    return jsonify(
        version=VERSION,
        host=socket.gethostname().replace(".local", ""),
        disk_free=du.free, disk_total=du.total,
        analyzing=sum(j["state"] in (J.ANALYZING, J.QUEUED_ANALYZE) for j in all_jobs),
        rendering=sum(j["state"] in (J.RENDERING, J.QUEUED_RENDER) for j in all_jobs),
        jobs=len(all_jobs),
        uptime=int(time.time() - STARTED),
        ffmpeg=bool(find_ffmpeg()),
        inbox=short_path(INBOX), outputs=short_path(STORE.outputs_dir),
        load=os.getloadavg()[0] if hasattr(os, "getloadavg") else None,
        cpus=os.cpu_count(),
    )


@app.get("/api/jobs")
def list_jobs():
    return jsonify(jobs=[public_job(j) for j in STORE.list()])


@app.get("/api/jobs/<job_id>")
def get_job(job_id):
    return jsonify(job=public_job(job_or_404(job_id)))


@app.delete("/api/jobs/<job_id>")
def delete_job(job_id):
    job_or_404(job_id)
    STORE.delete(job_id)
    return jsonify(ok=True)


@app.post("/api/jobs/<job_id>/render")
def render(job_id):
    job = job_or_404(job_id)
    if job["state"] not in (J.READY, J.DONE, J.ERROR, J.CANCELLED) or not job["faces"]:
        return jsonify(error="העבודה לא מוכנה לעיבוד"), 409
    data = request.get_json(silent=True) or {}
    known = {f["id"] for f in job["faces"]}
    try:
        faces = sorted({int(f) for f in data.get("faces", [])} & known)
        strength = int(data.get("blur_strength", 151))
    except (TypeError, ValueError):
        return jsonify(error="נתונים לא תקינים"), 400
    if not faces:
        return jsonify(error="בחר לפחות פרצוף אחד"), 400
    blur_type = data.get("blur_type", "gaussian")
    if blur_type not in J.BLUR_TYPES:
        blur_type = "gaussian"
    strength = max(21, min(301, strength)) | 1
    return jsonify(job=public_job(STORE.start_render(job_id, faces, blur_type, strength)))


@app.post("/api/jobs/<job_id>/cancel")
def cancel(job_id):
    job_or_404(job_id)
    return jsonify(job=public_job(STORE.cancel(job_id)))


@app.post("/api/jobs/<job_id>/retry")
def retry(job_id):
    job = job_or_404(job_id)
    if job["state"] not in (J.ERROR, J.CANCELLED):
        return jsonify(error="אין מה לנסות שוב"), 409
    if not Path(job["input_path"]).exists():
        return jsonify(error="קובץ המקור כבר לא קיים"), 410
    return jsonify(job=public_job(STORE.retry(job_id)))


@app.get("/api/jobs/<job_id>/faces/<int:face_id>.jpg")
def face_thumb(job_id, face_id):
    job_or_404(job_id)
    p = STORE.job_dir(job_id) / "faces" / f"{face_id}.jpg"
    if not p.exists():
        abort(404)
    resp = send_file(p, mimetype="image/jpeg")
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


def _output(job_id) -> Path:
    job = job_or_404(job_id)
    p = Path(job.get("output_path") or "")
    if job["state"] != J.DONE or not p.is_file():
        abort(404)
    return p


@app.get("/api/jobs/<job_id>/video")
def video(job_id):
    return send_file(_output(job_id), mimetype="video/mp4", conditional=True)


@app.get("/api/jobs/<job_id>/download")
def download(job_id):
    p = _output(job_id)
    return send_file(p, mimetype="video/mp4", as_attachment=True, download_name=p.name, conditional=True)


# ------------------------------------------------------------------ uploads
@app.post("/api/uploads")
def new_upload():
    data = request.get_json(silent=True) or {}
    name = str(data.get("name") or "video.mp4")
    try:
        size = int(data.get("size") or 0)
    except (TypeError, ValueError):
        size = 0
    if size <= 0:
        return jsonify(error="קובץ ריק"), 400
    if size > CFG["max_upload_gb"] * 1024 ** 3:
        return jsonify(error=f"הקובץ גדול מדי (מקסימום {CFG['max_upload_gb']}GB)"), 413
    if free_space() < size * 3 + 2 * 1024 ** 3:  # upload + temp + output, plus margin
        return jsonify(error="אין מספיק מקום פנוי בדיסק של ה-iMac"), 507
    job = STORE.create(name, "upload", size=size)
    return jsonify(job=public_job(job))


@app.put("/api/uploads/<job_id>")
def upload_chunk(job_id):
    job = job_or_404(job_id)
    if job["state"] != J.UPLOADING:
        return jsonify(error="ההעלאה כבר הסתיימה"), 409
    try:
        offset = int(request.args.get("offset", "-1"))
    except ValueError:
        return jsonify(error="offset"), 400
    try:
        size = STORE.append_chunk(job_id, offset, request.stream, job["expected_size"])
    except ValueError as e:  # wrong offset: tell the client where we are
        return jsonify(error="offset mismatch", size=e.args[0]), 409
    except OverflowError:
        return jsonify(error="הקובץ גדול מהצפוי"), 413
    return jsonify(size=size)


@app.post("/api/uploads/<job_id>/complete")
def upload_complete(job_id):
    job = job_or_404(job_id)
    if job["state"] != J.UPLOADING:
        return jsonify(job=public_job(job))
    try:
        job = STORE.finish_upload(job_id)
    except ValueError as e:
        return jsonify(error="ההעלאה לא הושלמה", size=e.args[0]), 409
    return jsonify(job=public_job(job))


# ------------------------------------------------------------------ library
@app.get("/api/library")
def library():
    files = []
    for idx, root in enumerate(library_roots()):
        if not root.is_dir():
            continue
        try:
            for dirpath, dirnames, filenames in os.walk(root):
                rel_dir = Path(dirpath).relative_to(root)
                dirnames[:] = [d for d in dirnames if not d.startswith(".")] if len(rel_dir.parts) < 3 else []
                for fn in filenames:
                    if fn.startswith(".") or Path(fn).suffix.lower() not in J.VIDEO_EXTS:
                        continue
                    p = Path(dirpath) / fn
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    files.append({
                        "ref": f"{idx}/{(rel_dir / fn).as_posix()}",
                        "name": fn,
                        "folder": root.name if str(rel_dir) == "." else f"{root.name}/{rel_dir.as_posix()}",
                        "size": st.st_size, "mtime": st.st_mtime,
                    })
        except PermissionError:
            continue
    files.sort(key=lambda f: -f["mtime"])
    return jsonify(files=files[:500], roots=[short_path(r) for r in library_roots()])


@app.post("/api/library/use")
def library_use():
    ref = str((request.get_json(silent=True) or {}).get("ref", ""))
    p = resolve_library_ref(ref)
    if not p:
        return jsonify(error="הקובץ לא נמצא"), 404
    job = STORE.create(p.name, "library", input_path=str(p), size=p.stat().st_size)
    return jsonify(job=public_job(job))


# -------------------------------------------------------------------- main
def _cleanup_loop():
    while True:
        try:
            STORE.cleanup(float(CFG.get("retention_days", 14)))
        except Exception as e:
            print("cleanup error:", e)
        time.sleep(6 * 3600)


def keep_awake():
    """On macOS, stop the Mac from sleeping for as long as this process lives."""
    if sys.platform == "darwin" and Path("/usr/bin/caffeinate").exists():
        import subprocess
        subprocess.Popen(["/usr/bin/caffeinate", "-i", "-s", "-w", str(os.getpid())])


def main():
    parser = argparse.ArgumentParser(description="Face Blur remote server")
    parser.add_argument("--set-password", action="store_true", help="choose the login password and exit")
    parser.add_argument("--password-stdin", action="store_true", help="read the new password from stdin")
    parser.add_argument("--host", default=os.environ.get("FACEBLUR_HOST", CFG["host"]))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FACEBLUR_PORT", CFG["port"])))
    args = parser.parse_args()

    if args.set_password or args.password_stdin:
        if args.password_stdin:
            pw = sys.stdin.readline().rstrip("\n")
        else:
            pw = getpass.getpass("סיסמה חדשה: ")
            if pw != getpass.getpass("שוב: "):
                sys.exit("הסיסמאות לא תואמות")
        if len(pw) < 6:
            sys.exit("הסיסמה חייבת להכיל לפחות 6 תווים")
        set_password(pw)
        print("✅ הסיסמה נשמרה")
        return

    if not CFG.get("password_hash"):
        pw = secrets.token_urlsafe(9)
        set_password(pw)
        CFG.update(load_config())
        print(f"⚠️  לא הוגדרה סיסמה - נוצרה סיסמה זמנית: {pw}")
        print("    להחלפה: python server.py --set-password")

    STORE.start_workers()
    threading.Thread(target=_cleanup_loop, daemon=True).start()
    keep_awake()

    print("=" * 50)
    print(f"🎭 Face Blur Remote v{VERSION}")
    print(f"   http://{args.host}:{args.port}")
    print(f"   data: {DATA_DIR}")
    print(f"   ffmpeg: {find_ffmpeg() or 'NOT FOUND'}")
    print("=" * 50, flush=True)

    try:
        from waitress import serve
        serve(app, host=args.host, port=args.port, threads=8,
              channel_timeout=600, max_request_body_size=CHUNK_LIMIT + 1024 * 1024)
    except ImportError:
        app.run(host=args.host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
