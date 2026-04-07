#!/usr/bin/env python3
"""Face Tracker v2.0"""
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict
import subprocess

VERSION = "2.0"

class FaceTracker:
    def __init__(self, video_path: str):
        self.video_path = video_path
        self.cap = cv2.VideoCapture(video_path)
        self.fps = self.cap.get(cv2.CAP_PROP_FPS)
        self.total_frames = int(self.cap.get(cv2.CAP_PROP_FRAME_COUNT))
        self.width = int(self.cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        self.height = int(self.cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        cascade_path = cv2.data.haarcascades
        self.face_cascade = cv2.CascadeClassifier(cascade_path + 'haarcascade_frontalface_default.xml')
        self.profile_cascade = cv2.CascadeClassifier(cascade_path + 'haarcascade_profileface.xml')
        
    def detect_faces_in_frame(self, frame: np.ndarray, frame_num: int) -> List[Dict]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        frontal = self.face_cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=6, minSize=(40, 40))
        profile_right = self.profile_cascade.detectMultiScale(gray, scaleFactor=1.05, minNeighbors=6, minSize=(40, 40))
        gray_flip = cv2.flip(gray, 1)
        profile_left = self.profile_cascade.detectMultiScale(gray_flip, scaleFactor=1.05, minNeighbors=6, minSize=(40, 40))
        profile_left = [(self.width - x - w, y, w, h) for (x, y, w, h) in profile_left]
        detected = []
        for (x, y, w, h) in list(frontal) + list(profile_right) + profile_left:
            detected.append({'bbox': (x, y, w, h), 'center': (x + w//2, y + h//2), 'frame': frame_num, 'size': w * h})
        return detected
    
    def sample_video_frames(self, sample_interval: int = 10):
        print(f"📹 מנתח וידאו: {self.total_frames} פריימים")
        sampled_frames = {}
        frame_num = 0
        while True:
            ret, frame = self.cap.read()
            if not ret:
                break
            if frame_num % sample_interval == 0:
                faces = self.detect_faces_in_frame(frame, frame_num)
                if faces:
                    sampled_frames[frame_num] = faces
            frame_num += 1
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        print(f"   נדגמו {len(sampled_frames)} פריימים")
        return sampled_frames
    
    def cluster_faces(self, sampled_frames):
        unique_faces = {}
        face_id = 0
        for frame_num, faces in sorted(sampled_frames.items()):
            for face in faces:
                center, bbox, size = face['center'], face['bbox'], face['size']
                matched, best_match, best_dist = False, None, float('inf')
                for uid, data in unique_faces.items():
                    dist = np.sqrt((center[0] - data['avg_center'][0])**2 + (center[1] - data['avg_center'][1])**2)
                    if dist < 250 and dist < best_dist:
                        best_dist, best_match, matched = dist, uid, True
                if matched:
                    data = unique_faces[best_match]
                    data['centers'].append(center)
                    data['bboxes'].append(bbox)
                    data['frames'].append(frame_num)
                    data['sizes'].append(size)
                else:
                    unique_faces[face_id] = {'id': face_id, 'centers': [center], 'bboxes': [bbox], 'frames': [frame_num], 'sizes': [size], 'avg_center': center, 'avg_bbox': bbox, 'best_frame': frame_num, 'best_bbox': bbox}
                    face_id += 1
        for uid, data in unique_faces.items():
            centers, bboxes = np.array(data['centers']), np.array(data['bboxes'])
            data['avg_center'] = tuple(centers.mean(axis=0).astype(int))
            data['avg_bbox'] = tuple(bboxes.mean(axis=0).astype(int))
            max_idx = np.argmax(data['sizes'])
            data['best_frame'] = data['frames'][max_idx]
            data['best_bbox'] = data['bboxes'][max_idx]
        print(f"✅ זוהו {len(unique_faces)} פרצופים")
        return unique_faces
    
    def apply_blur(self, frame, x, y, w, h, blur_type='gaussian', blur_strength=151):
        if blur_type == 'gaussian':
            roi = frame[y:y+h, x:x+w]
            frame[y:y+h, x:x+w] = cv2.GaussianBlur(roi, (blur_strength, blur_strength), 0)
        elif blur_type == 'oval':
            roi = frame[y:y+h, x:x+w].copy()
            mask = np.zeros((h, w), dtype=np.uint8)
            cv2.ellipse(mask, (w//2, h//2), (w//2, h//2), 0, 0, 360, 255, -1)
            blurred = cv2.GaussianBlur(roi, (blur_strength, blur_strength), 0)
            mask_3ch = cv2.cvtColor(mask, cv2.COLOR_GRAY2BGR) / 255.0
            frame[y:y+h, x:x+w] = (roi * (1 - mask_3ch) + blurred * mask_3ch).astype(np.uint8)
        elif blur_type == 'pixelate':
            roi = frame[y:y+h, x:x+w]
            px = max(8, blur_strength // 10)
            temp = cv2.resize(roi, (w // px, h // px), interpolation=cv2.INTER_LINEAR)
            frame[y:y+h, x:x+w] = cv2.resize(temp, (w, h), interpolation=cv2.INTER_NEAREST)
        return frame
    
    def process_video(self, output_path: str, faces_to_blur: List[int], blur_type='gaussian', blur_strength: int = 151):
        print(f"🎬 מעבד v2.0 | סוג: {blur_type} | עוצמה: {blur_strength}")
        temp = output_path.replace('.mp4', '_temp.mp4')
        out = cv2.VideoWriter(temp, cv2.VideoWriter_fourcc(*'mp4v'), self.fps, (self.width, self.height))
        self.cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        frame_num = 0
        while True:
            ret, frame = self.cap.read()
            if not ret:
                break
            for face in self.detect_faces_in_frame(frame, frame_num):
                x, y, w, h = face['bbox']
                for fid in faces_to_blur:
                    if fid in self.unique_faces:
                        c1, c2 = face['center'], self.unique_faces[fid]['avg_center']
                        if np.sqrt((c1[0]-c2[0])**2 + (c1[1]-c2[1])**2) < 250:
                            frame = self.apply_blur(frame, x, y, w, h, blur_type, blur_strength)
                            break
            out.write(frame)
            frame_num += 1
        out.release()
        self.cap.release()
        print("🔊 מוסיף אודיו...")
        subprocess.run(['ffmpeg', '-i', temp, '-i', self.video_path, '-c:v', 'libx264', '-crf', '18', '-preset', 'medium', '-c:a', 'aac', '-b:a', '192k', '-map', '0:v:0', '-map', '1:a:0?', '-shortest', '-y', output_path], capture_output=True)
        Path(temp).unlink()
        print("✅ סיום!")
