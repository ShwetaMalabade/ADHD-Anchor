"""
Anchor -- Activity Monitor (Privacy-First)
Uses MediaPipe Tasks API to detect user activity from webcam WITHOUT recording.
Each frame is processed for landmarks and immediately discarded.
Only activity labels are kept: "typing", "idle", "phone", "away", etc.

Run standalone: python3 activity_monitor.py
"""

import cv2
import mediapipe as mp
import numpy as np
import time
import math
import requests
import threading
import urllib.request
import os

# ============================================================
# DOWNLOAD MODELS (one-time)
# ============================================================
MODEL_DIR = os.path.join(os.path.dirname(__file__), "models")
os.makedirs(MODEL_DIR, exist_ok=True)

MODELS = {
    "hand": {
        "path": os.path.join(MODEL_DIR, "hand_landmarker.task"),
        "url": "https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    },
    "pose": {
        "path": os.path.join(MODEL_DIR, "pose_landmarker_lite.task"),
        "url": "https://storage.googleapis.com/mediapipe-models/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
    },
    "face": {
        "path": os.path.join(MODEL_DIR, "face_landmarker.task"),
        "url": "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    },
}


def download_models():
    for name, info in MODELS.items():
        if not os.path.exists(info["path"]):
            print(f"  Downloading {name} model...")
            urllib.request.urlretrieve(info["url"], info["path"])
            print(f"  Done: {info['path']}")


# ============================================================
# MEDIAPIPE TASK-BASED SETUP
# ============================================================
BaseOptions = mp.tasks.BaseOptions
HandLandmarker = mp.tasks.vision.HandLandmarker
HandLandmarkerOptions = mp.tasks.vision.HandLandmarkerOptions
PoseLandmarker = mp.tasks.vision.PoseLandmarker
PoseLandmarkerOptions = mp.tasks.vision.PoseLandmarkerOptions
FaceLandmarker = mp.tasks.vision.FaceLandmarker
FaceLandmarkerOptions = mp.tasks.vision.FaceLandmarkerOptions
VisionRunningMode = mp.tasks.vision.RunningMode


# ============================================================
# LANDMARK DRAWING HELPERS
# ============================================================
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),       # thumb
    (0, 5), (5, 6), (6, 7), (7, 8),       # index
    (0, 9), (9, 10), (10, 11), (11, 12),  # middle
    (0, 13), (13, 14), (14, 15), (15, 16),# ring
    (0, 17), (17, 18), (18, 19), (19, 20),# pinky
    (5, 9), (9, 13), (13, 17),            # palm
]

POSE_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 7),
    (0, 4), (4, 5), (5, 6), (6, 8),
    (11, 12), (11, 13), (13, 15),
    (12, 14), (14, 16), (11, 23),
    (12, 24), (23, 24), (23, 25), (24, 26),
]


def draw_hand_landmarks(frame, hand_landmarks, color=(0, 220, 180)):
    h, w = frame.shape[:2]
    points = []
    for lm in hand_landmarks:
        x, y = int(lm.x * w), int(lm.y * h)
        points.append((x, y))
        cv2.circle(frame, (x, y), 4, (255, 255, 255), -1)
        cv2.circle(frame, (x, y), 3, color, -1)
    for i, j in HAND_CONNECTIONS:
        if i < len(points) and j < len(points):
            cv2.line(frame, points[i], points[j], color, 2)


def draw_pose_landmarks(frame, pose_landmarks, color=(100, 200, 100)):
    h, w = frame.shape[:2]
    points = []
    for lm in pose_landmarks:
        x, y = int(lm.x * w), int(lm.y * h)
        points.append((x, y))
        cv2.circle(frame, (x, y), 3, color, -1)
    for i, j in POSE_CONNECTIONS:
        if i < len(points) and j < len(points):
            cv2.line(frame, points[i], points[j], color, 1)


# ============================================================
# ACTIVITY DETECTOR
# ============================================================
class ActivityDetector:
    def __init__(self):
        # Create landmarkers
        self.hand_landmarker = HandLandmarker.create_from_options(
            HandLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=MODELS["hand"]["path"]),
                running_mode=VisionRunningMode.VIDEO,
                num_hands=2,
                min_hand_detection_confidence=0.5,
                min_tracking_confidence=0.4,
            )
        )
        self.pose_landmarker = PoseLandmarker.create_from_options(
            PoseLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=MODELS["pose"]["path"]),
                running_mode=VisionRunningMode.VIDEO,
                min_pose_detection_confidence=0.5,
                min_tracking_confidence=0.4,
            )
        )
        self.face_landmarker = FaceLandmarker.create_from_options(
            FaceLandmarkerOptions(
                base_options=BaseOptions(model_asset_path=MODELS["face"]["path"]),
                running_mode=VisionRunningMode.VIDEO,
                num_faces=1,
                min_face_detection_confidence=0.5,
                min_tracking_confidence=0.4,
            )
        )

        # State tracking
        self.prev_hand_positions = []
        self.prev_pose_positions = []
        self.movement_history = []
        self.last_activity = "initializing"
        self.last_activity_time = time.time()
        self.idle_start = None
        self.person_absent_start = None
        self.activity_log = []
        self.frame_ts = 0

    def _landmark_to_tuples(self, landmarks):
        return [(lm.x, lm.y, lm.z) for lm in landmarks]

    def _movement_magnitude(self, current, previous):
        if not previous or len(current) != len(previous):
            return 0
        total = 0
        for (cx, cy, cz), (px, py, pz) in zip(current, previous):
            total += math.sqrt((cx - px) ** 2 + (cy - py) ** 2 + (cz - pz) ** 2)
        return total

    def _is_hand_near_face(self, hand_lms, face_lms):
        if not hand_lms or not face_lms:
            return False
        wrist = hand_lms[0]
        middle_tip = hand_lms[12]
        nose = face_lms[1]
        left_ear = face_lms[234] if len(face_lms) > 234 else None
        right_ear = face_lms[454] if len(face_lms) > 454 else None

        for ear in [left_ear, right_ear]:
            if ear:
                dist = math.sqrt((wrist.x - ear.x) ** 2 + (wrist.y - ear.y) ** 2)
                if dist < 0.15:
                    return True

        dist_to_nose = math.sqrt((middle_tip.x - nose.x) ** 2 + (middle_tip.y - nose.y) ** 2)
        return dist_to_nose < 0.12

    def _is_phone_scrolling(self, hand_lms):
        if not hand_lms:
            return False
        thumb_tip = hand_lms[4]
        index_tip = hand_lms[8]
        wrist = hand_lms[0]
        finger_dist = math.sqrt((thumb_tip.x - index_tip.x) ** 2 + (thumb_tip.y - index_tip.y) ** 2)
        return 0.3 < wrist.y < 0.8 and finger_dist < 0.08

    def _get_head_direction(self, face_lms):
        if not face_lms or len(face_lms) < 455:
            return "unknown"
        nose = face_lms[1]
        left_ear = face_lms[234]
        right_ear = face_lms[454]

        left_dist = abs(nose.x - left_ear.x)
        right_dist = abs(nose.x - right_ear.x)

        if left_dist > 0 and right_dist > 0:
            ratio = left_dist / right_dist
            if ratio > 2.0:
                return "looking_right"
            elif ratio < 0.5:
                return "looking_left"

        chin = face_lms[152]
        forehead = face_lms[10]
        face_height = abs(forehead.y - chin.y)
        if nose.y > chin.y - face_height * 0.15:
            return "looking_down"

        return "forward"

    def detect(self, frame):
        """Process a single frame. Frame is NOT stored."""
        self.frame_ts += 33  # ~30fps timestamps
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))

        hand_result = self.hand_landmarker.detect_for_video(mp_image, self.frame_ts)
        pose_result = self.pose_landmarker.detect_for_video(mp_image, self.frame_ts)
        face_result = self.face_landmarker.detect_for_video(mp_image, self.frame_ts)

        now = time.time()
        activity = "unknown"
        confidence = 0.0
        details = {}

        has_hands = bool(hand_result.hand_landmarks)
        has_pose = bool(pose_result.pose_landmarks)
        has_face = bool(face_result.face_landmarks)
        person_detected = has_face or has_pose

        # --- Person absent ---
        if not person_detected:
            if self.person_absent_start is None:
                self.person_absent_start = now
            absent_duration = now - self.person_absent_start
            if absent_duration > 3:
                activity = "away"
                confidence = min(0.99, 0.7 + absent_duration * 0.05)
                details = {"absent_seconds": round(absent_duration)}
            else:
                activity = "checking"
                confidence = 0.5
            self._update_state(activity, confidence, details)
            return activity, confidence, details, hand_result, pose_result, face_result

        self.person_absent_start = None

        # --- Phone near face ---
        if has_hands and has_face:
            face_lms = face_result.face_landmarks[0]
            for hand_lms in hand_result.hand_landmarks:
                if self._is_hand_near_face(hand_lms, face_lms):
                    activity = "phone"
                    confidence = 0.85
                    details = {"gesture": "hand_near_face"}
                    self._update_state(activity, confidence, details)
                    return activity, confidence, details, hand_result, pose_result, face_result

        # --- Phone scrolling ---
        if has_hands:
            for hand_lms in hand_result.hand_landmarks:
                if self._is_phone_scrolling(hand_lms):
                    activity = "phone_scrolling"
                    confidence = 0.7
                    details = {"gesture": "scrolling"}
                    self._update_state(activity, confidence, details)
                    return activity, confidence, details, hand_result, pose_result, face_result

        # --- Head direction ---
        head_dir = "forward"
        if has_face:
            head_dir = self._get_head_direction(face_result.face_landmarks[0])
            if head_dir == "looking_down":
                details["head"] = "looking_down"

        # --- Finger movement ---
        finger_movement = 0
        if has_hands:
            current_positions = []
            for hand_lms in hand_result.hand_landmarks:
                for tip_id in [8, 12, 16, 20]:  # fingertip indices
                    lm = hand_lms[tip_id]
                    current_positions.append((lm.x, lm.y, lm.z))
            finger_movement = self._movement_magnitude(current_positions, self.prev_hand_positions)
            self.prev_hand_positions = current_positions
            self.movement_history.append(finger_movement)
        else:
            self.movement_history.append(0)
            self.prev_hand_positions = []

        self.movement_history = self.movement_history[-10:]
        avg_movement = sum(self.movement_history) / len(self.movement_history) if self.movement_history else 0

        # --- Pose movement ---
        pose_movement = 0
        if has_pose:
            current_pose = self._landmark_to_tuples(pose_result.pose_landmarks[0])
            pose_movement = self._movement_magnitude(current_pose, self.prev_pose_positions)
            self.prev_pose_positions = current_pose
        else:
            self.prev_pose_positions = []

        # --- Classify ---
        if avg_movement > 0.15:
            activity = "typing"
            confidence = min(0.95, 0.6 + avg_movement)
            details["finger_movement"] = round(avg_movement, 3)
        elif head_dir == "looking_down" and avg_movement < 0.03:
            activity = "looking_down"
            confidence = 0.75
            details["possible"] = "phone or zoned out"
        elif pose_movement < 0.01 and avg_movement < 0.02:
            if self.idle_start is None:
                self.idle_start = now
            idle_duration = now - self.idle_start
            if idle_duration > 10:
                activity = "idle"
                confidence = min(0.95, 0.5 + idle_duration * 0.03)
                details = {"idle_seconds": round(idle_duration)}
            else:
                activity = "focused"
                confidence = 0.6
        else:
            self.idle_start = None
            activity = "focused"
            confidence = 0.7
            if head_dir == "forward":
                confidence = 0.85

        if activity != "idle":
            self.idle_start = None

        self._update_state(activity, confidence, details)
        return activity, confidence, details, hand_result, pose_result, face_result

    def _update_state(self, activity, confidence, details):
        now = time.time()
        if activity != self.last_activity:
            duration = round(now - self.last_activity_time)
            self.activity_log.append({
                "activity": self.last_activity,
                "duration_sec": duration,
                "ended_at": time.strftime("%H:%M:%S"),
            })
            self.activity_log = self.activity_log[-50:]
            self.last_activity = activity
            self.last_activity_time = now

    def get_current_duration(self):
        return round(time.time() - self.last_activity_time)

    def cleanup(self):
        self.hand_landmarker.close()
        self.pose_landmarker.close()
        self.face_landmarker.close()


# ============================================================
# COLORS & DRAWING
# ============================================================
ACTIVITY_COLORS = {
    "focused":        (76, 175, 80),
    "typing":         (76, 175, 80),
    "idle":           (0, 152, 255),
    "phone":          (0, 0, 255),
    "phone_scrolling": (0, 0, 255),
    "looking_down":   (0, 165, 255),
    "away":           (0, 0, 200),
    "checking":       (200, 200, 200),
    "unknown":        (200, 200, 200),
    "initializing":   (200, 200, 200),
}

ACTIVITY_LABELS = {
    "focused":        "FOCUSED",
    "typing":         "TYPING (Active)",
    "idle":           "IDLE -- Zoned Out?",
    "phone":          "PHONE DETECTED",
    "phone_scrolling": "SCROLLING PHONE",
    "looking_down":   "LOOKING DOWN",
    "away":           "LEFT DESK",
    "checking":       "Checking...",
    "unknown":        "...",
    "initializing":   "Starting...",
}

ACTIVITY_ICONS = {
    "focused":   ">>",
    "typing":    "<<>>",
    "idle":      "ZZZ",
    "phone":     "[P]",
    "phone_scrolling": "[P]",
    "looking_down": "[v]",
    "away":      "[X]",
}


def draw_status_overlay(frame, activity, confidence, details, detector):
    h, w = frame.shape[:2]
    color = ACTIVITY_COLORS.get(activity, (200, 200, 200))
    label = ACTIVITY_LABELS.get(activity, activity)
    duration = detector.get_current_duration()

    # Top status bar
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (w, 70), (30, 30, 30), -1)
    cv2.addWeighted(overlay, 0.7, frame, 0.3, 0, frame)

    cv2.circle(frame, (30, 35), 12, color, -1)
    cv2.circle(frame, (30, 35), 12, (255, 255, 255), 2)
    cv2.putText(frame, label, (55, 32), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

    duration_text = f"{duration}s" if duration < 60 else f"{duration // 60}m {duration % 60}s"
    cv2.putText(frame, duration_text, (55, 55), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

    bar_x = w - 160
    cv2.rectangle(frame, (bar_x, 20), (bar_x + 130, 35), (60, 60, 60), -1)
    cv2.rectangle(frame, (bar_x, 20), (bar_x + int(130 * confidence), 35), color, -1)
    cv2.putText(frame, f"{confidence:.0%}", (bar_x + 135, 33), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (180, 180, 180), 1)

    # Bottom timeline
    if detector.activity_log:
        overlay2 = frame.copy()
        cv2.rectangle(overlay2, (0, h - 40), (w, h), (30, 30, 30), -1)
        cv2.addWeighted(overlay2, 0.7, frame, 0.3, 0, frame)

        x_pos = 10
        for entry in detector.activity_log[-15:]:
            c = ACTIVITY_COLORS.get(entry["activity"], (100, 100, 100))
            bar_width = min(max(entry["duration_sec"] * 2, 8), 60)
            cv2.rectangle(frame, (x_pos, h - 30), (x_pos + bar_width, h - 10), c, -1)
            x_pos += bar_width + 3

    # Privacy badge
    cv2.putText(frame, "NO RECORDING - Landmarks Only", (w - 280, h - 8),
                cv2.FONT_HERSHEY_SIMPLEX, 0.35, (0, 200, 0), 1)

    return frame


# ============================================================
# MAIN
# ============================================================
def main():
    print("=" * 60)
    print("ANCHOR -- Activity Monitor (Privacy-First)")
    print("=" * 60)
    print("- Webcam frames are processed and IMMEDIATELY discarded")
    print("- Only landmark data is analyzed, NO video is recorded")
    print("- Press 'q' to quit")
    print("=" * 60)

    print("\nDownloading models (first run only)...")
    download_models()
    print("Models ready.\n")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("ERROR: Cannot open webcam.")
        print("Go to System Settings > Privacy & Security > Camera")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    cap.set(cv2.CAP_PROP_FPS, 15)

    detector = ActivityDetector()
    last_printed_activity = ""

    # Cache last detection results so every frame gets a consistent overlay
    cached_activity = "initializing"
    cached_confidence = 0.0
    cached_details = {}
    cached_hand_result = None
    cached_pose_result = None

    print("Camera opened. You should see a preview window.")
    print("Try: sitting still (idle), typing, picking up phone, leaving desk\n")

    try:
        while cap.isOpened():
            ret, frame = cap.read()
            if not ret:
                break

            frame = cv2.flip(frame, 1)

            # Run detection on every frame (MediaPipe is fast enough on M-series)
            activity, confidence, details, hand_result, pose_result, face_result = detector.detect(frame)
            cached_activity = activity
            cached_confidence = confidence
            cached_details = details
            cached_hand_result = hand_result
            cached_pose_result = pose_result

            # Draw landmarks from cached results on every frame
            if cached_hand_result and cached_hand_result.hand_landmarks:
                for hand_lms in cached_hand_result.hand_landmarks:
                    draw_hand_landmarks(frame, hand_lms)

            if cached_pose_result and cached_pose_result.pose_landmarks:
                draw_pose_landmarks(frame, cached_pose_result.pose_landmarks[0])

            # Draw overlay on every frame
            frame = draw_status_overlay(frame, cached_activity, cached_confidence, cached_details, detector)

            # Print on change
            if cached_activity != last_printed_activity:
                icon = ACTIVITY_ICONS.get(cached_activity, "?")
                print(f"  [{time.strftime('%H:%M:%S')}] {icon} {ACTIVITY_LABELS.get(cached_activity, cached_activity)} "
                      f"({cached_confidence:.0%}) | {cached_details}")
                last_printed_activity = cached_activity

            cv2.imshow("Anchor Activity Monitor (Press Q to quit)", frame)

            if cv2.waitKey(1) & 0xFF == ord("q"):
                break

    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        print(f"\n{'=' * 60}")
        print("ACTIVITY SUMMARY")
        print(f"{'=' * 60}")
        for entry in detector.activity_log[-15:]:
            icon = ACTIVITY_ICONS.get(entry["activity"], "?")
            print(f"  {icon} {entry['activity']:20s} | {entry['duration_sec']:4d}s | ended {entry['ended_at']}")

        detector.cleanup()
        cap.release()
        cv2.destroyAllWindows()
        print("\nCamera released. No video was recorded or stored.")


if __name__ == "__main__":
    main()
