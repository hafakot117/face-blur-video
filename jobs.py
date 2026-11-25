"""Persistent job store and background workers.

Every video is a "job" with its own folder under <data>/jobs/<id>/ holding
job.json, the uploaded file, the analysis and face thumbnails. The finished
video goes to <data>/Outputs/ so it is also easy to find on the Mac itself.

Two worker threads run in the background: one analyses (finds faces), one
renders (blurs). Work continues when the phone is closed, and jobs that were
interrupted by a restart are queued again on start-up.
"""
import json
import os
import queue
import re
import shutil
import threading
import time
import traceback
import uuid
from pathlib import Path
from typing import Dict, List, Optional

from face_tracker import Cancelled, FaceTracker

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".mts", ".m2ts", ".3gp", ".mpg", ".mpeg", ".wmv"}
BLUR_TYPES = {"gaussian", "oval", "pixelate"}

# States
UPLOADING = "uploading"
QUEUED_ANALYZE = "queued_analyze"
ANALYZING = "analyzing"
READY = "ready"
QUEUED_RENDER = "queued_render"
RENDERING = "rendering"
DONE = "done"
ERROR = "error"
CANCELLED = "cancelled"
ACTIVE_STATES = {QUEUED_ANALYZE, ANALYZING, QUEUED_RENDER, RENDERING}

_ID_RE = re.compile(r"^[0-9a-f]{32}$")


def valid_id(job_id: str) -> bool:
    return bool(_ID_RE.match(job_id or ""))


def safe_stem(name: str) -> str:
    stem = Path(name or "video").stem
    stem = re.sub(r"[^\w\-. א-ת]", "_", stem, flags=re.UNICODE).strip(" ._") or "video"
    return stem[:80]


class JobStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.jobs_dir = self.data_dir / "jobs"
        self.outputs_dir = self.data_dir / "Outputs"
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.outputs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._jobs: Dict[str, Dict] = {}
        self._cancel: set = set()
        self._queues = {"analyze": queue.Queue(), "render": queue.Queue()}
        self._load()

    # --------------------------------------------------------------- storage
    def job_dir(self, job_id: str) -> Path:
        return self.jobs_dir / job_id

    def _load(self):
        for d in sorted(self.jobs_dir.iterdir()):
            f = d / "job.json"
            if not (d.is_dir() and valid_id(d.name) and f.exists()):
                continue
            try:
                self._jobs[d.name] = json.loads(f.read_text("utf-8"))
            except Exception:
                continue

    def _save(self, job: Dict):
        job["updated_at"] = time.time()
        path = self.job_dir(job["id"]) / "job.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(job, ensure_ascii=False, indent=1), "utf-8")
        os.replace(tmp, path)

    def get(self, job_id: str) -> Optional[Dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self) -> List[Dict]:
        with self._lock:
            return sorted((dict(j) for j in self._jobs.values()), key=lambda j: j["created_at"], reverse=True)

    def update(self, job_id: str, **fields) -> Dict:
        with self._lock:
            job = self._jobs[job_id]
            job.update(fields)
            self._save(job)
            return dict(job)

    def create(self, name: str, source: str, input_path: Optional[str] = None, size: int = 0) -> Dict:
        job_id = uuid.uuid4().hex
        d = self.job_dir(job_id)
        d.mkdir(parents=True)
        ext = Path(name).suffix.lower()
        if ext not in VIDEO_EXTS:
            ext = ".mp4"
        now = time.time()
        job = {
            "id": job_id, "name": Path(name).name[:200] or "video", "source": source,
            "input_path": input_path or str(d / ("input" + ext)),
            "expected_size": int(size or 0),
            "state": UPLOADING if source == "upload" else QUEUED_ANALYZE,
            "progress": 0.0, "message": "", "error": "",
            "faces": [], "settings": {}, "output_path": "", "output_size": 0,
            "info": {}, "created_at": now, "updated_at": now,
        }
        with self._lock:
            self._jobs[job_id] = job
            self._save(job)
        if job["state"] == QUEUED_ANALYZE:
            self._queues["analyze"].put(job_id)
        return dict(job)

    def delete(self, job_id: str):
        with self._lock:
            job = self._jobs.pop(job_id, None)
            self._cancel.add(job_id)
        if job:
            shutil.rmtree(self.job_dir(job_id), ignore_errors=True)

    # -------------------------------------------------------------- uploads
    def append_chunk(self, job_id: str, offset: int, stream, max_size: int) -> int:
        """Append a chunk at `offset`. Returns the file size afterwards.
        Raises ValueError(size) when the offset does not match (client resyncs)."""
        job = self.get(job_id)
        path = Path(job["input_path"])
        current = path.stat().st_size if path.exists() else 0
        if offset != current:
            raise ValueError(current)
        written = current
        with open(path, "ab") as f:
            while True:
                buf = stream.read(1024 * 1024)
                if not buf:
                    break
                written += len(buf)
                if written > max_size:
                    raise OverflowError()
                f.write(buf)
        return written

    def finish_upload(self, job_id: str) -> Dict:
        job = self.get(job_id)
        path = Path(job["input_path"])
        size = path.stat().st_size if path.exists() else 0
        if job["expected_size"] and size != job["expected_size"]:
            raise ValueError(size)
        job = self.update(job_id, state=QUEUED_ANALYZE, progress=0.0, message="ממתין לניתוח", error="")
        self._queues["analyze"].put(job_id)
        return job

    # ------------------------------------------------------------- actions
    def start_render(self, job_id: str, faces: List[int], blur_type: str, blur_strength: int) -> Dict:
        job = self.update(job_id, state=QUEUED_RENDER, progress=0.0, message="ממתין בתור", error="",
                          settings={"faces": faces, "blur_type": blur_type, "blur_strength": blur_strength})
        self._cancel.discard(job_id)
        self._queues["render"].put(job_id)
        return job

    def cancel(self, job_id: str) -> Dict:
        self._cancel.add(job_id)
        job = self.get(job_id)
        if job["state"] in (QUEUED_ANALYZE, ANALYZING):
            return self.update(job_id, state=CANCELLED, message="בוטל", progress=0.0)
        if job["state"] in (QUEUED_RENDER, RENDERING):
            # Back to face selection; analysis is still valid.
            return self.update(job_id, state=READY, message="העיבוד בוטל", progress=0.0)
        return job

    def retry(self, job_id: str) -> Dict:
        job = self.get(job_id)
        self._cancel.discard(job_id)
        if job.get("settings") and job.get("faces") and (self.job_dir(job_id) / "analysis.json").exists():
            s = job["settings"]
            return self.start_render(job_id, s["faces"], s["blur_type"], s["blur_strength"])
        job = self.update(job_id, state=QUEUED_ANALYZE, progress=0.0, message="ממתין לניתוח", error="")
        self._queues["analyze"].put(job_id)
        return job

    def queue_position(self, job_id: str) -> int:
        """1-based position among jobs waiting for the same worker (0 = not waiting)."""
        job = self.get(job_id)
        if not job or job["state"] not in (QUEUED_ANALYZE, QUEUED_RENDER):
            return 0
        same = [j for j in self.list() if j["state"] == job["state"]]
        same.sort(key=lambda j: j["updated_at"])
        return [j["id"] for j in same].index(job_id) + 1

    # -------------------------------------------------------------- workers
    def start_workers(self):
        # Resume whatever a restart interrupted.
        for job in self.list():
            if job["state"] in (QUEUED_ANALYZE, ANALYZING):
                self.update(job["id"], state=QUEUED_ANALYZE, progress=0.0, message="ממתין לניתוח")
                self._queues["analyze"].put(job["id"])
            elif job["state"] in (QUEUED_RENDER, RENDERING):
                self.update(job["id"], state=QUEUED_RENDER, progress=0.0, message="ממתין בתור")
                self._queues["render"].put(job["id"])
        for kind in ("analyze", "render"):
            threading.Thread(target=self._worker, args=(kind,), name=f"worker-{kind}", daemon=True).start()

    def _worker(self, kind: str):
        q = self._queues[kind]
        while True:
            job_id = q.get()
            try:
                job = self.get(job_id)
                if not job:
                    continue
                if kind == "analyze" and job["state"] == QUEUED_ANALYZE:
                    self._analyze(job_id)
                elif kind == "render" and job["state"] == QUEUED_RENDER:
                    self._render(job_id)
            except Cancelled:
                pass
            except Exception as e:  # keep the worker alive whatever happens
                traceback.print_exc()
                if self.get(job_id):
                    self.update(job_id, state=ERROR, error=str(e) or e.__class__.__name__, message="")
            finally:
                q.task_done()

    def _callbacks(self, job_id: str):
        last = [0.0]

        def progress(frac: float, msg: str):
            now = time.time()
            if now - last[0] >= 1.0:  # don't rewrite job.json more than once a second
                last[0] = now
                if self.get(job_id):
                    self.update(job_id, progress=round(float(frac), 4), message=msg)

        def cancel() -> bool:
            return job_id in self._cancel

        return progress, cancel

    def _analyze(self, job_id: str):
        job = self.update(job_id, state=ANALYZING, progress=0.0, message="פותח וידאו", error="")
        progress, cancel = self._callbacks(job_id)
        tracker = FaceTracker(job["input_path"])
        info = {"width": tracker.width, "height": tracker.height,
                "fps": round(tracker.fps, 3), "duration": round(tracker.duration, 2)}
        sampled = tracker.sample_video_frames(sample_interval=10, progress=progress, cancel=cancel)
        unique = tracker.cluster_faces(sampled)

        d = self.job_dir(job_id)
        faces_dir = d / "faces"
        faces_dir.mkdir(exist_ok=True)
        faces = []
        for uid, data in unique.items():
            thumb = tracker.face_thumbnail(data)
            if thumb:
                (faces_dir / f"{uid}.jpg").write_bytes(thumb)
            faces.append({"id": int(uid), "appearances": len(data["frames"]), "thumb": bool(thumb)})
        tracker.cap.release()
        faces.sort(key=lambda f: -f["appearances"])

        analysis = {str(uid): {"avg_center": list(map(int, v["avg_center"]))} for uid, v in unique.items()}
        (d / "analysis.json").write_text(json.dumps(analysis), "utf-8")
        if cancel():
            raise Cancelled()
        self.update(job_id, state=READY, progress=1.0, message="", faces=faces, info=info)

    def _render(self, job_id: str):
        job = self.update(job_id, state=RENDERING, progress=0.0, message="מתחיל", error="")
        progress, cancel = self._callbacks(job_id)
        s = job["settings"]
        analysis = json.loads((self.job_dir(job_id) / "analysis.json").read_text("utf-8"))

        tracker = FaceTracker(job["input_path"])
        tracker.unique_faces = {int(k): {"avg_center": tuple(v["avg_center"])} for k, v in analysis.items()}

        # Remove the previous result of this job, if any, then pick a free name.
        old = job.get("output_path")
        if old and Path(old).exists() and Path(old).parent == self.outputs_dir:
            Path(old).unlink(missing_ok=True)
        stem = safe_stem(job["name"]) + "_blurred"
        out = self.outputs_dir / f"{stem}.mp4"
        n = 2
        while out.exists():
            out = self.outputs_dir / f"{stem}_{n}.mp4"
            n += 1
        # Reserve the name so a parallel job can't take it.
        out.touch()
        try:
            tracker.process_video(str(out), s["faces"], blur_type=s["blur_type"],
                                  blur_strength=s["blur_strength"], progress=progress, cancel=cancel)
        except BaseException:
            out.unlink(missing_ok=True)
            raise
        self.update(job_id, state=DONE, progress=1.0, message="", output_path=str(out),
                    output_size=out.stat().st_size)

    # ------------------------------------------------------------- cleanup
    def cleanup(self, retention_days: float):
        """Delete job folders (uploads, thumbnails) older than retention_days.
        Finished videos in Outputs/ are kept."""
        cutoff = time.time() - retention_days * 86400
        for job in self.list():
            if job["state"] in ACTIVE_STATES:
                continue
            stale_upload = job["state"] == UPLOADING and job["updated_at"] < time.time() - 86400
            if job["updated_at"] < cutoff or stale_upload:
                self.delete(job["id"])
