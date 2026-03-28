"""
Anchor -- Privacy-First Activity Monitor using MediaPipe
Detects: focused, typing, idle, phone, phone_scrolling, looking_down, away
Uses MediaPipe Tasks API (v0.10.33+) with hand, pose, and face landmarkers.
NO video is recorded -- frames are processed for landmarks then discarded.
"""

import os
import time
import math
import urllib.request
import cv2
import numpy as np
import mediapipe as mp
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

# ============================================================
# MODEL DOWNLOADS
# ============================================================
MODELS = {
    "hand": {
        "url": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
        "path": "models/hand_landmarker.task",
    },
    "pose": {
        "url": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
        "path": "models/pose_landmarker_lite.task",
    },
    "face": {
        "url": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
        "path": "models/face_landmarker.task",
    },
}


def download_models():
    os.makedirs("models", exist_ok=True)
    for name, info in MODELS.items():
        if not os.path.exists(info["path"]):
            print(f"  Downloading {name} model...")
            urllib.request.urlretrieve(info["url"], info["path"])
            print(f"  {name} model ready.")


# ============================================================
# ACTIVITY DETECTOR
# ============================================================
class ActivityDetector:
    def __init__(self):
        self.frame_ts = 0

        # Hand landmarker
        hand_opts = vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=MODELS["hand"]["path"]),
            running_mode=vision.RunningMode.VIDEO,
            num_hands=2,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
        )
        self.hand_landmarker = vision.HandLandmarker.create_from_options(hand_opts)

        # Pose landmarker
        pose_opts = vision.PoseLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=MODELS["pose"]["path"]),
            running_mode=vision.RunningMode.VIDEO,
            min_pose_detection_confidence=0.5,
            min_pose_presence_confidence=0.5,
        )
        self.pose_landmarker = vision.PoseLandmarker.create_from_options(pose_opts)

        # Face landmarker
        face_opts = vision.FaceLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=MODELS["face"]["path"]),
            running_mode=vision.RunningMode.VIDEO,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
        )
        self.face_landmarker = vision.FaceLandmarker.create_from_options(face_opts)

        # Activity tracking
        self.last_activity = "initializing"
        self.activity_start_time = time.time()
        self.idle_start = None

    def detect(self, frame):
        """Run all three landmarkers and classify activity."""
        ts = int(time.time() * 1000)
        if ts <= self.frame_ts:
            ts = self.frame_ts + 1
        self.frame_ts = ts

        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        hand_result = self.hand_landmarker.detect_for_video(mp_image, ts)
        pose_result = self.pose_landmarker.detect_for_video(mp_image, ts)
        face_result = self.face_landmarker.detect_for_video(mp_image, ts)

        activity, confidence, details = self._classify(hand_result, pose_result, face_result)

        if activity != self.last_activity:
            self.last_activity = activity
            self.activity_start_time = time.time()

        return activity, confidence, details, hand_result, pose_result, face_result

    def _classify(self, hand_result, pose_result, face_result):
        details = {}
        has_hands = bool(hand_result.hand_landmarks)
        has_pose = bool(pose_result.pose_landmarks)
        has_face = bool(face_result.face_landmarks)

        details["hands_detected"] = len(hand_result.hand_landmarks) if has_hands else 0
        details["pose_detected"] = has_pose
        details["face_detected"] = has_face

        # No person visible
        if not has_pose and not has_face:
            self.idle_start = self.idle_start or time.time()
            idle_sec = time.time() - self.idle_start
            if idle_sec > 10:
                return "away", 0.9, details
            return "idle", 0.7, details

        self.idle_start = None

        # Check if looking down (phone use indicator)
        if has_face:
            face_lms = face_result.face_landmarks[0]
            nose = face_lms[1]  # nose tip
            chin = face_lms[152]  # chin
            forehead = face_lms[10]  # forehead

            head_tilt = chin.y - forehead.y
            details["head_tilt"] = round(head_tilt, 3)

            if nose.y > 0.65 and head_tilt > 0.15:
                if has_hands and len(hand_result.hand_landmarks) >= 1:
                    # Check if hands are in phone-holding position (lower frame)
                    for hand_lms in hand_result.hand_landmarks:
                        wrist = hand_lms[0]
                        if wrist.y > 0.5:
                            # Check for scrolling motion (thumb extended, fingers curled)
                            thumb_tip = hand_lms[4]
                            index_tip = hand_lms[8]
                            dist = math.sqrt((thumb_tip.x - index_tip.x)**2 + (thumb_tip.y - index_tip.y)**2)
                            if dist > 0.08:
                                return "phone_scrolling", 0.85, details
                            return "phone", 0.8, details
                return "looking_down", 0.7, details

        # Check hand positions for typing
        if has_hands and len(hand_result.hand_landmarks) == 2:
            left_wrist = hand_result.hand_landmarks[0][0]
            right_wrist = hand_result.hand_landmarks[1][0]
            wrist_y_avg = (left_wrist.y + right_wrist.y) / 2
            wrist_x_dist = abs(left_wrist.x - right_wrist.x)

            if wrist_y_avg > 0.6 and wrist_x_dist > 0.15:
                return "typing", 0.75, details

        # Person present & facing screen
        if has_face:
            return "focused", 0.8, details

        # Pose visible but no face (turned away?)
        if has_pose and not has_face:
            return "looking_away", 0.6, details

        return "focused", 0.5, details

    def cleanup(self):
        self.hand_landmarker.close()
        self.pose_landmarker.close()
        self.face_landmarker.close()


# ============================================================
# DRAWING HELPERS
# ============================================================
def draw_hand_landmarks(frame, hand_lms):
    h, w = frame.shape[:2]
    connections = [
        (0,1),(1,2),(2,3),(3,4),
        (0,5),(5,6),(6,7),(7,8),
        (5,9),(9,10),(10,11),(11,12),
        (9,13),(13,14),(14,15),(15,16),
        (13,17),(17,18),(18,19),(19,20),(0,17),
    ]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in hand_lms]
    for a, b in connections:
        if a < len(pts) and b < len(pts):
            cv2.line(frame, pts[a], pts[b], (100, 200, 100), 2)
    for pt in pts:
        cv2.circle(frame, pt, 4, (150, 255, 150), -1)


def draw_pose_landmarks(frame, pose_lms):
    h, w = frame.shape[:2]
    connections = [
        (11,12),(11,13),(13,15),(12,14),(14,16),
        (11,23),(12,24),(23,24),(23,25),(24,26),(25,27),(26,28),
    ]
    pts = [(int(lm.x * w), int(lm.y * h)) for lm in pose_lms]
    for a, b in connections:
        if a < len(pts) and b < len(pts):
            cv2.line(frame, pts[a], pts[b], (100, 150, 255), 2)
    for i, pt in enumerate(pts):
        if 11 <= i <= 28:
            cv2.circle(frame, pt, 4, (150, 200, 255), -1)


def draw_status_overlay(frame, activity, confidence, details, detector):
    h, w = frame.shape[:2]
    colors = {
        "focused": (80, 180, 80), "typing": (80, 180, 80),
        "idle": (80, 160, 220), "phone": (60, 80, 220),
        "phone_scrolling": (60, 80, 220), "looking_down": (60, 140, 220),
        "away": (100, 100, 100), "looking_away": (80, 160, 220),
    }
    color = colors.get(activity, (150, 150, 150))

    # Status bar
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 40), (20, 20, 20), -1)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

    cv2.circle(frame, (20, 20), 8, color, -1)
    label = f"{activity.upper()} ({confidence:.0%})"
    cv2.putText(frame, label, (36, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)

    # Duration
    duration = time.time() - detector.activity_start_time
    dur_text = f"{int(duration)}s"
    cv2.putText(frame, dur_text, (w - 60, 26), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

    # Details
    y = h - 10
    for key, val in details.items():
        text = f"{key}: {val}"
        cv2.putText(frame, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.35, (180, 180, 180), 1)
        y -= 16

    return frame


def blur_background(frame, pose_landmarks):
    """Blur background, keep person sharp. Uses pose landmarks for person mask."""
    if pose_landmarks is None:
        return cv2.GaussianBlur(frame, (31, 31), 15)

    h, w = frame.shape[:2]
    mask = np.zeros((h, w), dtype=np.uint8)

    # Get bounding box from pose landmarks
    xs = [lm.x * w for lm in pose_landmarks]
    ys = [lm.y * h for lm in pose_landmarks]
    cx, cy = int(np.mean(xs)), int(np.mean(ys))
    rx = int((max(xs) - min(xs)) * 0.7)
    ry = int((max(ys) - min(ys)) * 0.6)
    rx = max(rx, 80)
    ry = max(ry, 120)

    cv2.ellipse(mask, (cx, cy), (rx, ry), 0, 0, 360, 255, -1)
    mask = cv2.GaussianBlur(mask, (51, 51), 30)

    blurred = cv2.GaussianBlur(frame, (31, 31), 15)
    mask_3ch = cv2.merge([mask, mask, mask]).astype(np.float32) / 255.0
    result = (frame.astype(np.float32) * mask_3ch + blurred.astype(np.float32) * (1 - mask_3ch)).astype(np.uint8)

    return result
