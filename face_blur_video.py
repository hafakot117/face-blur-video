#!/usr/bin/env python3
"""
face_blur_video.py — Automatically detect and blur all faces in a video file.

Usage:
    python face_blur_video.py /path/to/video.mp4
    python face_blur_video.py /path/to/video.mp4 --crf 0       # lossless
    python face_blur_video.py /path/to/video.mp4 --blur 151    # stronger blur
    python face_blur_video.py /path/to/video.mp4 --confidence 0.3  # catch more faces

Drag & drop the video file onto the terminal icon, then run the command.
Output is saved next to the input file with a _blurred suffix.

────────────────────────────────────────────────────────────────────────────────
INSTALL INSTRUCTIONS (macOS)
────────────────────────────────────────────────────────────────────────────────

1. Install Homebrew (if not already installed):
       /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

2. Install FFmpeg:
       brew install ffmpeg

3. Install Python dependencies:
       pip install opencv-python mediapipe tqdm numpy

   On Apple Silicon (M1/M2/M3) if you hit issues:
       pip install opencv-python mediapipe tqdm numpy --upgrade

   Optional — if you prefer face_recognition instead of MediaPipe:
       brew install cmake
       pip install face_recognition dlib

4. Run:
       python face_blur_video.py your_video.mp4
────────────────────────────────────────────────────────────────────────────────
"""

import sys
import os
import subprocess
import tempfile
import argparse
import shutil
from pathlib import Path

# ── Dependency check with friendly errors ─────────────────────────────────────

def _check_imports():
    missing = []
    try:
        import cv2          # noqa: F401
    except ImportError:
        missing.append("opencv-python")
    try:
        import mediapipe    # noqa: F401
    except ImportError:
        missing.append("mediapipe")
    try:
        import tqdm         # noqa: F401
    except ImportError:
        missing.append("tqdm")
    try:
        import numpy        # noqa: F401
    except ImportError:
        missing.append("numpy")

    if missing:
        print("Missing Python packages. Install them with:", file=sys.stderr)
        print(f"  pip install {' '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    if not shutil.which("ffmpeg"):
        print("FFmpeg not found. Install it with:", file=sys.stderr)
        print("  brew install ffmpeg", file=sys.stderr)
        sys.exit(1)

_check_imports()

import cv2
import mediapipe as mp
import numpy as np
from tqdm import tqdm


# ── Face blurring ──────────────────────────────────────────────────────────────

def apply_blur_to_faces(
    frame: np.ndarray,
    detections,
    blur_kernel: int,
    padding: float = 0.15,
) -> np.ndarray:
    """
    Gaussian-blur every detected face bounding box in the frame.

    Args:
        frame:       BGR frame from OpenCV.
        detections:  MediaPipe face detection results.
        blur_kernel: Gaussian kernel size (must be odd; higher = more blur).
        padding:     Fractional padding to expand each bounding box.
    """
    h, w = frame.shape[:2]
    k = blur_kernel | 1  # enforce odd number

    for det in detections:
        bbox = det.location_data.relative_bounding_box

        # Relative coords → pixels, with optional padding
        bx = bbox.xmin * w
        by = bbox.ymin * h
        bw = bbox.width * w
        bh = bbox.height * h

        pad_x = bw * padding
        pad_y = bh * padding

        x1 = max(0, int(bx - pad_x))
        y1 = max(0, int(by - pad_y))
        x2 = min(w, int(bx + bw + pad_x))
        y2 = min(h, int(by + bh + pad_y))

        if x2 <= x1 or y2 <= y1:
            continue

        face_roi = frame[y1:y2, x1:x2]
        frame[y1:y2, x1:x2] = cv2.GaussianBlur(face_roi, (k, k), 0)

    return frame


# ── Core pipeline ──────────────────────────────────────────────────────────────

def process_video(
    input_path: str,
    crf: int = 18,
    blur_kernel: int = 99,
    min_confidence: float = 0.5,
) -> None:
    input_file = Path(input_path).resolve()

    if not input_file.exists():
        print(f"Error: file not found — {input_path}", file=sys.stderr)
        sys.exit(1)

    output_file = input_file.parent / f"{input_file.stem}_blurred.mp4"

    # ── Open input video ──
    cap = cv2.VideoCapture(str(input_file))
    if not cap.isOpened():
        print(f"Error: cannot open video — {input_path}", file=sys.stderr)
        sys.exit(1)

    fps          = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width        = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height       = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))

    print()
    print(f"  Input   : {input_file.name}")
    print(f"  Size    : {width}×{height}  |  {fps:.3f} fps  |  ~{total_frames} frames")
    print(f"  Output  : {output_file.name}")
    print(f"  CRF     : {crf}  |  blur kernel: {blur_kernel | 1}  |  confidence: {min_confidence}")
    print()

    # ── Write processed frames to a temp file ──
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(tmp_fd)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(tmp_path, fourcc, fps, (width, height))

    if not writer.isOpened():
        print("Error: could not open VideoWriter.", file=sys.stderr)
        cap.release()
        os.unlink(tmp_path)
        sys.exit(1)

    # ── MediaPipe Face Detection ──
    # model_selection=1 → full-range model, better for video (≤5m detection range)
    mp_face = mp.solutions.face_detection

    faces_found_total = 0

    with mp_face.FaceDetection(
        model_selection=1,
        min_detection_confidence=min_confidence,
    ) as detector:
        with tqdm(
            total=total_frames,
            unit="frame",
            desc="Blurring faces",
            dynamic_ncols=True,
            bar_format="{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
        ) as pbar:
            frame_idx = 0
            while True:
                ret, frame = cap.read()
                if not ret:
                    break

                # MediaPipe requires RGB input
                rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                result = detector.process(rgb)

                if result.detections:
                    faces_found_total += len(result.detections)
                    frame = apply_blur_to_faces(frame, result.detections, blur_kernel)

                writer.write(frame)
                frame_idx += 1
                pbar.update(1)

    cap.release()
    writer.release()

    print(f"\n  Faces blurred across all frames: {faces_found_total}")
    print()

    # ── FFmpeg: merge processed video + original audio ──
    print("  Encoding final video with FFmpeg…")

    ffmpeg_cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", tmp_path,          # processed video (no audio)
        "-i", str(input_file),   # original file (audio source)
        "-map", "0:v:0",         # video track from processed file
        "-map", "1:a?",          # audio track(s) from original (? = skip if none)
        "-c:v", "libx264",
        "-crf", str(crf),
        "-preset", "slow",
        "-pix_fmt", "yuv420p",
        "-c:a", "copy",          # copy audio without re-encoding
        "-movflags", "+faststart",
        str(output_file),
    ]

    try:
        result = subprocess.run(ffmpeg_cmd, check=True, text=True, capture_output=False)
    except subprocess.CalledProcessError as e:
        print(f"\nFFmpeg failed (exit {e.returncode}).", file=sys.stderr)
        os.unlink(tmp_path)
        sys.exit(1)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)

    size_mb = output_file.stat().st_size / (1024 * 1024)
    print()
    print(f"  Done! → {output_file}")
    print(f"  Size  : {size_mb:.1f} MB")
    print()


# ── CLI ────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="face_blur_video",
        description="Automatically detect and blur all faces in a video file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python face_blur_video.py clip.mp4\n"
            "  python face_blur_video.py clip.mp4 --crf 0        # lossless\n"
            "  python face_blur_video.py clip.mp4 --blur 151     # heavier blur\n"
            "  python face_blur_video.py clip.mp4 --confidence 0.3  # catch more faces\n"
        ),
    )
    parser.add_argument(
        "input",
        metavar="VIDEO",
        help="Path to the input video file (drag & drop onto terminal).",
    )
    parser.add_argument(
        "--crf",
        type=int,
        default=18,
        metavar="N",
        help=(
            "FFmpeg CRF quality (0 = lossless, 18 = visually lossless [default], "
            "23 = good quality, 28 = smaller file)."
        ),
    )
    parser.add_argument(
        "--blur",
        type=int,
        default=99,
        metavar="N",
        dest="blur_kernel",
        help="Gaussian blur kernel size — must be odd (default: 99). Higher = stronger blur.",
    )
    parser.add_argument(
        "--confidence",
        type=float,
        default=0.5,
        metavar="F",
        help="Minimum face detection confidence 0.0–1.0 (default: 0.5). Lower catches more faces.",
    )

    args = parser.parse_args()

    if not 0 <= args.crf <= 51:
        parser.error("--crf must be between 0 and 51")
    if args.blur_kernel < 3:
        parser.error("--blur must be at least 3")
    if not 0.0 <= args.confidence <= 1.0:
        parser.error("--confidence must be between 0.0 and 1.0")

    # Strip quotes that shells sometimes add when dragging files
    input_path = args.input.strip("'\"")

    process_video(
        input_path=input_path,
        crf=args.crf,
        blur_kernel=args.blur_kernel,
        min_confidence=args.confidence,
    )


if __name__ == "__main__":
    main()
