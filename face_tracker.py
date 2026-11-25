#!/usr/bin/env python3
"""Face tracker: find the distinct faces in a video and blur the selected ones.

Used by both the remote server (server.py) and the original local app
(blur_app.py). All new arguments are optional, so the old app keeps working.
"""
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Dict, List, Optional

import cv2
import numpy as np

VERSION = "3.0"

# Faces are matched across frames by distance between centres (in pixels).
MATCH_DISTANCE = 250
# Detection runs on frames no wider than this; wider frames (4K) are scaled
# down for detection only, which is several times faster.
DETECT_MAX_WIDTH = 1920

ProgressCb = Optional[Callable[[float, str], None]]
CancelCb = Optional[Callable[[], bool]]


class Cancelled(Exception):
    """Raised when the caller asked to stop."""


def find_ffmpeg() -> Optional[str]:
    """ffmpeg from PATH (e.g. Homebrew), else the copy bundled with imageio-ffmpeg."""
    for candidate in (shutil.which("ffmpeg"), "/opt/homebrew/bin/ffmpeg", "/usr/local/bin/ffmpeg"):
        if candidate and Path(candidate).exists():
            return candidate
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


class FaceTracker:
    def __init__(self, video_path: str):
        self.video_path = str(video_path)
        self.cap = cv2.VideoCapture(self.video_path)
        if not self.cap.isOpened():
            raise ValueError("לא ניתן לפתוח את קובץ הווידאו")
        fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.fps = fps if fps and 1 <= fps <= 240 else 25.0
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)

        # Take the size from a decoded frame, not from the container: phone
        # videos carry a rotation flag, OpenCV rotates the pixels, and the
        # reported width/height can be the un-rotated ones. A size mismatch
        # makes VideoWriter silently drop every frame.
        ret, frame = self.cap.read()
        if not ret:
            raise ValueError("לא ניתן לקרוא פריימים מהווידאו")
        self.height, self.width = frame.shape[:2]
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

        self.unique_faces: Dict[int, Dict] = {}
        cascade_path = cv2.data.haarcascades
        self.face_cascade = cv2.CascadeClassifier(cascade_path + "haarcascade_frontalface_default.xml")
        self.profile_cascade = cv2.CascadeClassifier(cascade_path + "haarcascade_profileface.xml")

    @property
    def duration(self) -> float:
        return self.total_frames / self.fps if self.fps else 0.0

    # ------------------------------------------------------------------ detect
    def detect_faces_in_frame(self, frame: np.ndarray, frame_num: int) -> List[Dict]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        scale = 1.0
        if gray.shape[1] > DETECT_MAX_WIDTH:
            scale = DETECT_MAX_WIDTH / gray.shape[1]
            gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        det_w = gray.shape[1]

        params = dict(scaleFactor=1.05, minNeighbors=6, minSize=(40, 40))
        frontal = self.face_cascade.detectMultiScale(gray, **params)
        profile_right = self.profile_cascade.detectMultiScale(gray, **params)
        profile_left = self.profile_cascade.detectMultiScale(cv2.flip(gray, 1), **params)
        profile_left = [(det_w - x - w, y, w, h) for (x, y, w, h) in profile_left]

        detected = []
        for (x, y, w, h) in list(frontal) + list(profile_right) + profile_left:
            if scale != 1.0:
                x, y, w, h = (int(round(v / scale)) for v in (x, y, w, h))
            x, y = max(0, int(x)), max(0, int(y))
            w, h = min(int(w), self.width - x), min(int(h), self.height - y)
            if w <= 1 or h <= 1:
                continue
            detected.append({
                "bbox": (x, y, w, h),
                "center": (x + w // 2, y + h // 2),
                "frame": frame_num,
                "size": w * h,
            })
        return detected

    # ----------------------------------------------------------------- analyse
    def sample_video_frames(self, sample_interval: int = 10,
                            progress: ProgressCb = None, cancel: CancelCb = None):
        print(f"📹 מנתח וידאו: {self.total_frames} פריימים")
        sampled_frames = {}
        frame_num = 0
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        while True:
            ret, frame = self.cap.read()
            if not ret:
                break
            if frame_num % sample_interval == 0:
                faces = self.detect_faces_in_frame(frame, frame_num)
                if faces:
                    sampled_frames[frame_num] = faces
            frame_num += 1
            if frame_num % 30 == 0:
                if cancel and cancel():
                    raise Cancelled()
                if progress and self.total_frames:
                    progress(min(frame_num / self.total_frames, 0.99), "מחפש פרצופים")
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        print(f"   נדגמו {len(sampled_frames)} פריימים עם פרצופים")
        return sampled_frames

    def cluster_faces(self, sampled_frames):
        unique_faces = {}
        face_id = 0
        for frame_num, faces in sorted(sampled_frames.items()):
            for face in faces:
                center, bbox, size = face["center"], face["bbox"], face["size"]
                best_match, best_dist = None, float("inf")
                for uid, data in unique_faces.items():
                    avg = data["avg_center"]
                    dist = np.hypot(center[0] - avg[0], center[1] - avg[1])
                    if dist < MATCH_DISTANCE and dist < best_dist:
                        best_dist, best_match = dist, uid
                if best_match is not None:
                    data = unique_faces[best_match]
                    data["centers"].append(center)
                    data["bboxes"].append(bbox)
                    data["frames"].append(frame_num)
                    data["sizes"].append(size)
                else:
                    unique_faces[face_id] = {
                        "id": face_id, "centers": [center], "bboxes": [bbox],
                        "frames": [frame_num], "sizes": [size],
                        "avg_center": center, "avg_bbox": bbox,
                        "best_frame": frame_num, "best_bbox": bbox,
                    }
                    face_id += 1

        for data in unique_faces.values():
            centers, bboxes = np.array(data["centers"]), np.array(data["bboxes"])
            data["avg_center"] = tuple(int(v) for v in centers.mean(axis=0))
            data["avg_bbox"] = tuple(int(v) for v in bboxes.mean(axis=0))
            best = int(np.argmax(data["sizes"]))
            data["best_frame"] = data["frames"][best]
            data["best_bbox"] = data["bboxes"][best]

        print(f"✅ זוהו {len(unique_faces)} פרצופים ייחודיים")
        return unique_faces

    def face_thumbnail(self, face: Dict, max_side: int = 300) -> Optional[bytes]:
        """JPEG bytes of the clearest view of one face, with some margin."""
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, face["best_frame"])
        ret, frame = self.cap.read()
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        if not ret:
            return None
        x, y, w, h = face["best_bbox"]
        pad = int(h * 0.4)
        crop = frame[max(0, y - pad):min(frame.shape[0], y + h + pad),
                     max(0, x - pad):min(frame.shape[1], x + w + pad)]
        if crop.size == 0:
            return None
        if max(crop.shape[:2]) > max_side:
            s = max_side / max(crop.shape[:2])
            crop = cv2.resize(crop, (max(1, int(crop.shape[1] * s)), max(1, int(crop.shape[0] * s))))
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 90])
        return buf.tobytes() if ok else None

    # -------------------------------------------------------------------- blur
    def apply_blur(self, frame, x, y, w, h, blur_type="gaussian", blur_strength=151):
        k = int(blur_strength)
        k = k if k % 2 == 1 else k + 1  # Gaussian kernel must be odd
        if blur_type == "oval":
            roi = frame[y:y + h, x:x + w].copy()
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.ellipse(mask, (w // 2, h // 2), (w // 2, h // 2), 0, 0, 360, 255, -1)
            blurred = cv2.GaussianBlur(roi, (k, k), 0)
            mask_3ch = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) / 255.0
            frame[y:y + h, x:x + w] = (roi * (1 - mask_3ch) + blurred * mask_3ch).astype(np.uint8)
        elif blur_type == "pixelate":
            roi = frame[y:y + h, x:x + w]
            px = max(8, k // 10)
            small = cv2.resize(roi, (max(1, w // px), max(1, h // px)), interpolation=cv2.INTER_LINEAR)
            frame[y:y + h, x:x + w] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
        else:  # gaussian
            roi = frame[y:y + h, x:x + w]
            frame[y:y + h, x:x + w] = cv2.GaussianBlur(roi, (k, k), 0)
        return frame

    def process_video(self, output_path: str, faces_to_blur: List[int],
                      blur_type="gaussian", blur_strength: int = 151,
                      progress: ProgressCb = None, cancel: CancelCb = None):
        print(f"🎬 מעבד | פרצופים: {faces_to_blur} | סוג: {blur_type} | עוצמה: {blur_strength}")
        output_path = str(output_path)
        temp = str(Path(output_path).with_name(Path(output_path).stem + "_temp.mp4"))
        targets = [self.unique_faces[f]["avg_center"] for f in faces_to_blur if f in self.unique_faces]

        out = cv2.VideoWriter(temp, cv2.VideoWriter_fourcc(*"mp4v"), self.fps, (self.width, self.height))
        if not out.isOpened():
            raise RuntimeError("לא ניתן ליצור קובץ פלט")
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frame_num = 0
        try:
            while True:
                ret, frame = self.cap.read()
                if not ret:
                    break
                if frame.shape[:2] != (self.height, self.width):
                    frame = cv2.resize(frame, (self.width, self.height))
                if targets:
                    for face in self.detect_faces_in_frame(frame, frame_num):
                        cx, cy = face["center"]
                        if any(np.hypot(cx - tx, cy - ty) < MATCH_DISTANCE for tx, ty in targets):
                            x, y, w, h = face["bbox"]
                            frame = self.apply_blur(frame, x, y, w, h, blur_type, blur_strength)
                out.write(frame)
                frame_num += 1
                if frame_num % 15 == 0:
                    if cancel and cancel():
                        raise Cancelled()
                    if progress and self.total_frames:
                        progress(min(frame_num / self.total_frames, 1.0) * 0.95, "מטשטש פרצופים")
                if frame_num % 100 == 0:
                    print(f"   עובד... {frame_num}/{self.total_frames} פריימים")
        except BaseException:
            out.release()
            Path(temp).unlink(missing_ok=True)
            raise
        out.release()
        self.cap.release()
        if frame_num == 0:
            Path(temp).unlink(missing_ok=True)
            raise RuntimeError("לא נקראו פריימים מהווידאו")

        if progress:
            progress(0.96, "מקודד ומוסיף אודיו")
        print("🔊 מוסיף אודיו...")
        ffmpeg = find_ffmpeg()
        if not ffmpeg:
            # No ffmpeg at all: keep the silent mp4v file rather than fail.
            Path(temp).replace(output_path)
            print("⚠️ ffmpeg לא נמצא - הפלט ללא אודיו")
            return
        result = subprocess.run([
            ffmpeg, "-hide_banner", "-loglevel", "error",
            "-i", temp, "-i", self.video_path,
            "-map", "0:v:0", "-map", "1:a:0?",
            "-c:v", "libx264", "-crf", "18", "-preset", "medium", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
            "-shortest", "-y", output_path,
        ], capture_output=True, text=True)
        Path(temp).unlink(missing_ok=True)
        if result.returncode != 0 or not Path(output_path).exists():
            raise RuntimeError("ffmpeg נכשל: " + (result.stderr or "").strip()[-400:])
        print("✅ סיום!")
