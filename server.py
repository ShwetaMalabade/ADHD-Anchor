"""
Anchor Backend -- FastAPI Server
The backbone that connects everything:
- Window watcher reads your screen
- Classifier judges each window
- Anchor Agent reasons about what to do
- ElevenLabs speaks the nudge
- WebSocket pushes events to React frontend
- REST endpoints for session start/end

Run: uvicorn main:app --reload --port 8000
"""

import os
import json
import time
import asyncio
import subprocess
import threading
import cv2
import numpy as np
from datetime import datetime
from collections import Counter
from typing import Optional
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play
from pynput import mouse, keyboard as kb

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from dotenv import load_dotenv
load_dotenv()

from activity_monitor import ActivityDetector, download_models, draw_hand_landmarks, draw_pose_landmarks, draw_status_overlay

# ============================================================
# WEBCAM ACTIVITY MONITOR (MediaPipe, runs in background)
# ============================================================
print("[CAMERA] Downloading MediaPipe models...")
download_models()
print("[CAMERA] Models ready.")

camera_lock = threading.Lock()
camera_cap = None
activity_detector = None
latest_activity = {"activity": "initializing", "confidence": 0.0, "details": {}}
latest_jpeg_frame = None
camera_running = False


def start_camera():
    global camera_cap, activity_detector, camera_running
    with camera_lock:
        if camera_running:
            return True
        camera_cap = cv2.VideoCapture(0)
        if not camera_cap.isOpened():
            print("[CAMERA] ERROR: Cannot open webcam")
            return False
        camera_cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        camera_cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
        camera_cap.set(cv2.CAP_PROP_FPS, 15)
        activity_detector = ActivityDetector()
        camera_running = True
        print("[CAMERA] Webcam started")
    return True


def stop_camera():
    global camera_cap, activity_detector, camera_running
    with camera_lock:
        camera_running = False
        if camera_cap:
            camera_cap.release()
            camera_cap = None
        if activity_detector:
            activity_detector.cleanup()
            activity_detector = None
        print("[CAMERA] Webcam released")


def camera_loop():
    global latest_activity, latest_jpeg_frame, camera_running
    last_sent_activity = ""
    while camera_running:
        with camera_lock:
            if not camera_cap or not camera_cap.isOpened():
                break
            ret, frame = camera_cap.read()
        if not ret:
            time.sleep(0.1)
            continue
        frame = cv2.flip(frame, 1)
        activity, confidence, details, hand_result, pose_result, face_result = activity_detector.detect(frame)
        latest_activity = {"activity": activity, "confidence": confidence, "details": details}
        if hand_result.hand_landmarks:
            for hand_lms in hand_result.hand_landmarks:
                draw_hand_landmarks(frame, hand_lms)
        if pose_result.pose_landmarks:
            draw_pose_landmarks(frame, pose_result.pose_landmarks[0])
        frame = draw_status_overlay(frame, activity, confidence, details, activity_detector)
        _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
        latest_jpeg_frame = jpeg.tobytes()
        if activity != last_sent_activity and activity not in ("initializing", "checking", "unknown"):
            print(f"  [CAMERA] Activity: {activity} ({confidence:.0%})")
            last_sent_activity = activity
        time.sleep(0.05)


def generate_mjpeg():
    while camera_running:
        if latest_jpeg_frame:
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + latest_jpeg_frame + b'\r\n')
        time.sleep(0.05)


# ============================================================
# ACTIVITY TRACKING (keyboard + mouse via pynput)
# ============================================================
last_activity_time = time.time()

def on_activity(*args):
    global last_activity_time
    last_activity_time = time.time()

# Start listeners in background threads (run forever, non-blocking)
mouse.Listener(on_move=on_activity, on_click=on_activity, on_scroll=on_activity).start()
kb.Listener(on_press=on_activity).start()
print("[PYNPUT] Keyboard and mouse listeners started.")

# ============================================================
# INITIALIZE
# ============================================================
app = FastAPI(title="Anchor Backend")

# Allow React frontend (localhost:3000) to talk to this server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini client
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Connected WebSocket clients (React frontends)
connected_clients: list[WebSocket] = []

# ============================================================
# DATA MODELS (what React sends and receives)
# ============================================================
class SessionStartRequest(BaseModel):
    task: str
    duration: int = 60
    dnd: bool = False
    expected_notifications: str = ""
    session_type: str = "solid"

class UserResponse(BaseModel):
    action: str

# ============================================================
# SESSION STATE
# ============================================================
session_active = False
session_state = {}
observation_history = []
classification_cache = {}
monitoring_task = None


# ============================================================
# WINDOW WATCHER (macOS)
# ============================================================
def get_active_window_title() -> str:
    """Read the currently active window on macOS"""
    script = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
        set frontAppName to name of first window of (first application process whose frontmost is true)
        return frontApp & " - " & frontAppName
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            fallback = '''
            tell application "System Events"
                return name of first application process whose frontmost is true
            end tell
            '''
            result2 = subprocess.run(
                ["osascript", "-e", fallback],
                capture_output=True, text=True, timeout=5
            )
            return result2.stdout.strip() if result2.returncode == 0 else "Unknown"
    except:
        return "Unknown"


# ============================================================
# CLASSIFIER
# ============================================================
def create_task_context(task_description: str) -> dict:
    """Build semantic understanding of the task using Gemini"""
    prompt = f"""A user is about to start a focus session. Their task is: "{task_description}"

Analyze this task and return a JSON object with:
- "task": the task description cleaned up
- "domain": what field/topics this task involves (comma separated)
- "likely_tools": list of apps and tools they might legitimately use
- "likely_sites": list of websites they might legitimately visit
- "activity_type": one of "reading", "writing", "coding", "browsing", "mixed"
- "always_ok": apps that are always fine regardless of task (music players, calculator, etc.). NEVER include messaging apps like WhatsApp, Slack, iMessage, Telegram in always_ok.

Return ONLY valid JSON. No markdown, no backticks."""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        result_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(result_text)
    except:
        return {
            "task": task_description, "domain": "general",
            "likely_tools": [], "likely_sites": [],
            "activity_type": "mixed", "always_ok": ["Spotify", "Apple Music"]
        }


def classify_window(task_context: dict, window_title: str, expected_notifications: str = "") -> dict:
    """Classify if a window is relevant to the task"""
    if window_title in classification_cache:
        cached = classification_cache[window_title].copy()
        cached["from_cache"] = True
        return cached

    for app_name in task_context.get("always_ok", []):
        if app_name.lower() in window_title.lower():
            result = {"verdict": "relevant", "confidence": 0.99, "reason": f"{app_name} is always allowed"}
            classification_cache[window_title] = result
            return result

    notifications_context = ""
    if expected_notifications:
        notifications_context = f"\nUSER IS EXPECTING NOTIFICATIONS FROM: {expected_notifications} (be lenient with this specific app)"

    prompt = f"""You are a focus assistant. Decide if this window is RELEVANT to the task or DRIFT.

USER'S TASK: {task_context.get('task', '')}
DOMAIN: {task_context.get('domain', '')}
LIKELY TOOLS: {', '.join(task_context.get('likely_tools', []))}
CURRENT WINDOW: {window_title}
{notifications_context}

CRITICAL RULES:
1. The APP NAME alone does NOT determine relevance. The CONTENT shown in the window title determines relevance.
   - "Claude - personal skills assessment" when task is filling an application = RELEVANT
   - "Claude - Novel AI agent idea for yhack" when task is filling an application = DRIFT
   - "YouTube - MIT lecture on TPUs" when task is reading TPU paper = RELEVANT
   - "YouTube - best headphones 2026" when task is reading TPU paper = DRIFT

2. Social messaging apps (WhatsApp, iMessage, Telegram, Discord, Facebook Messenger) = "unsure" by default.

3. Social media (Twitter, Instagram, Reddit, TikTok) = "drift" unless content in title clearly relates to task.

4. Shopping sites (Amazon, eBay) = "drift" always.

5. AI tools (ChatGPT, Claude, Gemini) = READ THE CONVERSATION TITLE. If topic matches task = "relevant". If unrelated = "drift". If no title visible = "unsure".

6. Terminal/command line = "relevant" ONLY if user's task involves coding. Otherwise "drift".

Return ONLY JSON:
{{"verdict": "relevant" or "drift" or "unsure", "confidence": 0.0 to 1.0, "reason": "brief explanation"}}"""

    try:
        start = time.time()
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        elapsed = time.time() - start
        result_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        result = json.loads(result_text)
        result["latency_ms"] = round(elapsed * 1000)
        result["from_cache"] = False
        classification_cache[window_title] = result
        return result
    except:
        return {"verdict": "unsure", "confidence": 0.5, "reason": "classification error", "from_cache": False}


# ============================================================
# ANCHOR AGENT
# ============================================================
def add_observation(summary: str, event_type: str = "event"):
    """Add an observation to session history"""
    if not session_state.get("start_time"):
        return
    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    observation_history.append({
        "time": time.strftime("%H:%M:%S"),
        "elapsed_min": elapsed,
        "type": event_type,
        "summary": summary
    })


def get_history_text(last_n: int = 25) -> str:
    """Get formatted history for the agent"""
    recent = observation_history[-last_n:]
    return "\n".join(f"[{o['elapsed_min']}m] {o['summary']}" for o in recent)


def anchor_agent_decide(new_event_summary: str) -> dict:
    """The agent reads full history and decides what to do"""

    add_observation(new_event_summary)

    # Progressive cooldown -- gets longer each time you ignore nudges
    recent_nudges = [o for o in observation_history
                     if o["type"] == "nudge"
                     and o["elapsed_min"] >= round((time.time() - session_state["start_time"]) / 60, 1) - 5]
    nudge_count_recent = len(recent_nudges)

    if nudge_count_recent >= 4:
        cooldown = 300  # 5 min -- stop pushing, wait for them
    elif nudge_count_recent >= 3:
        cooldown = 120  # 2 min -- back off significantly
    elif nudge_count_recent >= 2:
        cooldown = 60   # 1 min -- give more space
    else:
        cooldown = 30   # 30 sec -- standard

    if session_state.get("last_nudge_time"):
        since_nudge = time.time() - session_state["last_nudge_time"]
        if since_nudge < cooldown:
            return {"action": "stay_silent", "message": "", "options": [],
                    "reason": f"Cooldown: {cooldown}s ({nudge_count_recent} recent nudges)"}

    # Don't intervene during breaks
    if session_state.get("break_active"):
        return {"action": "stay_silent", "message": "", "options": [], "reason": "On break"}

    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    history = get_history_text()

    prompt = f"""You are Anchor, an AI body double with deep knowledge of ADHD neuroscience.
You understand executive function depletion (Barkley's fuel tank model), time blindness,
task initiation paralysis, hyperfocus risks, and the difference between good breaks and bad breaks.
You are warm, supportive, and never accusatory -- like a knowledgeable friend who understands
how ADHD brains work. You speak in 1-2 sentences MAX. You NEVER repeat the same phrasing twice
in a session -- vary your tone, wording, and approach every time.

SESSION INFO:
- Task: {session_state['task']}
- Domain: {session_state['task_context'].get('domain', 'general')}
- Duration: {session_state['duration_minutes']} min
- Elapsed: {elapsed} min
- Current time: {time.strftime("%I:%M %p")}
- Total drifts: {session_state['drift_count']}
- Ever been on-task: {session_state['ever_on_task']}
- DND: {session_state['dnd_enabled']}
- Expecting notifications from: {session_state['expected_notifications'] or 'nobody'}
- Recent nudges in last 5 min: {nudge_count_recent}

FULL SESSION HISTORY:
{history}

LATEST EVENT:
{new_event_summary}

Think through like an ADHD specialist:
1. What is the user's current state? (focused, drifting, stuck, tired, overwhelmed, task initiation paralysis, hyperfocusing)
2. WHY might they be in this state? (bored, avoiding something hard, tired brain, notification pulled them, genuinely stuck on the task)
3. What does ADHD research say is the best intervention for this state?
4. Should you speak, ask a question, suggest a break, or stay silent?

DRIFT RULES:
- If user is FOCUSED and on a task-relevant app, STAY SILENT. Never interrupt good focus.
- FIRST drift of the ENTIRE session: stay silent, give them a chance to self-correct.
- SECOND drift: speak with a gentle nudge.
- THIRD or more drift: you MUST intervene, even if they self-corrected previous times.
- If drift_count >= 2, you MUST speak or ask. Do NOT stay silent on repeated drifts.

WHEN TO SUGGEST A BREAK (use action "suggest_break"):
- User has drifted 3+ times in the last 5 minutes -- their executive function tank is empty. Pushing them back to work won't help. They need to refuel.
- User has been focused 25-40 minutes without a break -- proactively suggest a break BEFORE they crash. Don't wait for 40 minutes. ADHD brains deplete faster than neurotypical ones.
- User was focused for a good stretch and then suddenly starts drifting repeatedly -- they hit a wall. Their brain is telling them it's out of fuel.
- User keeps coming back to the task but drifting again within 2-3 minutes -- the drift-return-drift cycle means they need a real reset, not willpower.
- If recent_nudges >= 3, ALWAYS suggest a break instead of another "get back to work" nudge. Nagging doesn't work with ADHD -- it makes them rebel more.

BREAK GUIDANCE (include in your message when suggesting a break):
- Recommend GOOD break activities: stand up, stretch, walk around, get water, step outside for fresh air, do some jumping jacks.
- Specifically warn against phone/social media: "Try to stay off your phone -- your brain needs actual rest, not more screen time."
- Keep breaks short: suggest 3-5 minutes, not 15-30. Shorter frequent breaks beat fewer long ones.
- Research shows 5 minutes of physical movement restores more focus than 30 minutes of scrolling.

TASK CHUNKING (when user seems stuck or overwhelmed):
- When the user keeps drifting repeatedly or can't start, do NOT just say "get back to work."
- Instead, suggest ONE tiny specific action: "Just fill in the first field" or "Just write one sentence" or "Just open the file and read the first paragraph."
- Make the next step so small it feels effortless. The goal is to lower activation energy.
- After a break ends, always ask "What's the ONE small thing you'll do first?" to help with re-initiation.

NOTIFICATION HANDLING:
- If the latest event says "NOTIFICATION PULL", be extra gentle. The user did NOT choose to open this app. Say something like "Looks like [app] pulled you away. Take a moment if needed, I'll be here."
- If the drift app matches their expected notification source, be even more lenient.

SILENT DRIFT:
- If the latest event mentions "NO keyboard or mouse activity", the correct window is open but the user is not engaging. Ask gently: "Your screen has been quiet for a while. Still with me, or need a reset?"

TASK-SPECIFIC RESPONSES (CRITICAL -- never give generic advice):
- All your responses MUST be specific to the user's ACTUAL TASK: "{session_state['task']}". Never say generic things like "get back to work" or "return to your task."
- Diagnose WHY they are drifting based on the session history pattern:
  * DEPLETED: Was focused 20+ min then suddenly drifting → suggest break, their fuel tank is empty
  * STUCK: Was working then hit a wall, started drifting → task chunking, ask what part is hard, suggest skipping to an easier section
  * OVERWHELMED: Never started, bouncing between random apps → give them the smallest possible first step specific to their task
  * BORED: Drifting to entertaining apps (Reddit, YouTube, games) → acknowledge the tedium of their specific task, give a mini-goal ("5 more fields then a break")
  * AVOIDING: Keeps returning but drifting again within 1-2 min → name it gently, suggest skipping the hard part and coming back
  * NOTIFICATION PULL: Fast window switch, messaging apps → suggest DND
- For task chunking: suggest the SMALLEST possible next step specific to THEIR task. Examples:
  * Filling forms: "Just fill in your name and email. Start with the easy fields."
  * Reading paper: "Just read the abstract. 5 sentences. That's your only job right now."
  * Coding: "Just write the function signature. Don't worry about the logic yet."
  * Grading: "Just open the next submission. Don't grade it yet, just read it."
  * Writing essay: "Just write one bad sentence. You can fix it later."
- For boredom: acknowledge what specifically is tedious about THEIR task. "Forms are repetitive" or "Dense papers are hard to stay with" or "Grading the same rubric gets numbing."
- For being stuck: suggest a task-specific strategy. Reading → "skip to the conclusion." Writing → "just write one bad sentence." Coding → "run what you have so far." Forms → "skip the hard question, do the easy ones first."
- Use your knowledge of the task domain to give relevant suggestions.

ACCURACY RULES:
- CRITICAL: ONLY reference apps and windows that appear in the session history above. NEVER mention apps the user has not visited. Use the ACTUAL app name from the LATEST EVENT.
- If ever_on_task is False, do NOT say "you left your task." They never opened it. Instead say "you're on [actual app] -- ready to open your [task]?"
- If the latest event mentions sustained drift, acknowledge the TIME they've been there.
- NEVER repeat the same phrasing you used earlier in the session. Check the history for your previous messages and use different words, different tone, different approach each time.

Return ONLY JSON (no markdown, no backticks):
{{
    "action": "speak" or "ask" or "suggest_break" or "stay_silent",
    "message": "what to say (empty if stay_silent)",
    "options": ["button1", "button2"] or [],
    "reason": "your internal reasoning (user won't see this)"
}}"""

    try:
        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        result_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        decision = json.loads(result_text)

        decision["action"] = decision.get("action", "stay_silent")
        decision["message"] = decision.get("message", "")
        decision["options"] = decision.get("options", [])
        decision["reason"] = decision.get("reason", "")

        if decision["action"] != "stay_silent":
            session_state["last_nudge_time"] = time.time()
            add_observation(f"Anchor said: \"{decision['message']}\"", "nudge")

        return decision
    except Exception as e:
        return {"action": "stay_silent", "message": "", "options": [], "reason": f"Agent error: {e}"}


# ============================================================
# VOICE (ElevenLabs)
# ============================================================
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"  # Roger

async def speak(message: str):
    try:
        print(f'\nSpeaking: "{message}"')
        audio = elevenlabs_client.text_to_speech.convert(
            text=message,
            voice_id=VOICE_ID,
            model_id="eleven_multilingual_v2",
            output_format="mp3_44100_128",
        )
        await asyncio.to_thread(play, audio)
        print("Done.")
    except Exception as e:
        print("[VOICE ERROR]", repr(e))


# ============================================================
# WEBSOCKET -- send events to React frontend
# ============================================================
async def broadcast(event: dict):
    disconnected = []
    for ws in connected_clients:
        try:
            await ws.send_json(event)
        except:
            disconnected.append(ws)
    for ws in disconnected:
        connected_clients.remove(ws)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """React frontend connects here for real-time updates"""
    await websocket.accept()
    connected_clients.append(websocket)
    print(f"Frontend connected. Total clients: {len(connected_clients)}")

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")

            if action == "pull_me_back":
                add_observation("User clicked: Pull me back", "user_response")
                await broadcast({"type": "status", "value": "focused"})

            elif action == "taking_break":
                session_state["break_active"] = True
                session_state["break_start"] = time.time()
                add_observation("User started a 5-minute break", "break_started")
                await broadcast({"type": "break_started", "duration": 300})

            elif action == "im_ready":
                add_observation("User clicked: I'm ready (task initiation)", "user_response")
                await broadcast({"type": "status", "value": "focused"})

            elif action == "got_it":
                add_observation("User clicked: Got it (notification acknowledged)", "user_response")
                await broadcast({"type": "status", "value": "focused"})

    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        print(f"Frontend disconnected. Total clients: {len(connected_clients)}")


# ============================================================
# MONITORING LOOP (runs in background during session)
# ============================================================
async def monitoring_loop():
    """The main loop that watches the screen and triggers the pipeline"""
    global session_active

    last_title = ""
    last_relevant_window = ""
    relevant_window_start = None
    last_window_change_time = time.time()  # Track when the last window switch happened

    print("\n[MONITOR] Starting monitoring loop...")

    while session_active:
        title = get_active_window_title()
        elapsed = round((time.time() - session_state["start_time"]) / 60, 1)

        # Check if break should end
        if session_state.get("break_active") and session_state.get("break_start"):
            break_duration = time.time() - session_state["break_start"]
            if break_duration >= 300:  # 5 minutes
                session_state["break_active"] = False
                add_observation("Break ended", "break_ended")
                await broadcast({"type": "break_ended"})
                await speak("Break's over. Ready to get back?")
                await broadcast({
                    "type": "nudge",
                    "nudge_type": "speak",
                    "message": "Break's over. Ready to get back?",
                    "options": ["Let's go"]
                })

        # Skip monitoring during breaks
        if session_state.get("break_active"):
            await asyncio.sleep(5)
            continue

        # Window changed
        if title != last_title:

            # Detect notification pull vs self-initiated switch
            time_since_last_switch = time.time() - last_window_change_time
            time_since_last_activity = time.time() - last_activity_time
            # If window changed within 2 sec of last activity on previous window,
            # AND user didn't type/click in the new window yet = notification pull
            is_notification_pull = time_since_last_switch < 3 and time_since_last_activity < 3
            last_window_change_time = time.time()

            # Reset sustained drift tracking (user moved to a different window)
            session_state["sustained_drift_start"] = None
            session_state["sustained_drift_nudged"] = False
            # Reset idle tracking (window changed = activity)
            session_state["idle_nudged"] = False

            # Classify the new window
            result = classify_window(
                session_state["task_context"],
                title,
                session_state.get("expected_notifications", "")
            )
            verdict = result["verdict"]
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")
            cached = result.get("from_cache", False)

            # Update session state
            if verdict == "drift":
                session_state["drift_count"] += 1
            elif verdict == "relevant":
                session_state["ever_on_task"] = True

            # Track time on relevant windows
            if verdict == "relevant":
                if title != last_relevant_window:
                    relevant_window_start = time.time()
                    last_relevant_window = title
            else:
                relevant_window_start = None
                last_relevant_window = ""

            # Send classification to frontend
            await broadcast({
                "type": "classification",
                "window": title,
                "verdict": verdict,
                "confidence": confidence,
                "reason": reason,
                "cached": cached,
                "drift_count": session_state["drift_count"],
                "elapsed_min": elapsed
            })

            # RELEVANT: save to history, don't call agent
            if verdict == "relevant":
                add_observation(f"Window: {title} --> relevant ({confidence:.0%})")
                await broadcast({"type": "status", "value": "focused"})
                print(f"  [MONITOR] [{elapsed}m] RELEVANT: {title[:60]}")

            # DRIFT: save to history, call agent
            elif verdict == "drift":
                notification_note = " This was likely a NOTIFICATION PULL (app popped up on its own, user didn't deliberately navigate here). Be extra gentle." if is_notification_pull else " User deliberately navigated here."
                add_observation(f"Window: {title} --> drift ({confidence:.0%}). Reason: {reason}.{notification_note}")
                event_summary = f"Window changed to: {title} --> drift ({confidence:.0%}). Reason: {reason}. Total drifts: {session_state['drift_count']}.{notification_note}"

                print(f"  [MONITOR] [{elapsed}m] DRIFT: {title[:60]}")
                print(f"  [AGENT] Thinking...")

                decision = anchor_agent_decide(event_summary)

                if decision["action"] != "stay_silent":
                    print(f"  [AGENT] {decision['action'].upper()}: {decision['message']}")
                    await speak(decision["message"])
                    await broadcast({
                        "type": "nudge",
                        "nudge_type": decision["action"],
                        "message": decision["message"],
                        "options": decision.get("options", []),
                        "drift_count": session_state["drift_count"]
                    })
                    await broadcast({"type": "status", "value": "drifted"})
                else:
                    print(f"  [AGENT] Staying silent: {decision['reason']}")

            # UNSURE: save to history, call agent to decide whether to ask
            elif verdict == "unsure":
                notification_note = " This was likely a NOTIFICATION PULL (app popped up on its own)." if is_notification_pull else ""
                add_observation(f"Window: {title} --> unsure ({confidence:.0%}). Reason: {reason}.{notification_note}")
                event_summary = f"Window changed to: {title} --> unsure ({confidence:.0%}). Reason: {reason}.{notification_note}"

                print(f"  [MONITOR] [{elapsed}m] UNSURE: {title[:60]}")

                decision = anchor_agent_decide(event_summary)

                if decision["action"] != "stay_silent":
                    print(f"  [AGENT] {decision['action'].upper()}: {decision['message']}")
                    await speak(decision["message"])
                    await broadcast({
                        "type": "nudge",
                        "nudge_type": decision["action"],
                        "message": decision["message"],
                        "options": decision.get("options", ["Yes, it's for my task", "I drifted"]),
                        "drift_count": session_state["drift_count"]
                    })

            last_title = title

        else:
            # Same window -- check timeouts

            # Long stay on relevant app (15+ min)
            if relevant_window_start and last_relevant_window:
                time_on_window = (time.time() - relevant_window_start) / 60
                long_stay_apps = ["claude", "chatgpt", "youtube", "openai"]
                is_long_stay = any(app in last_relevant_window.lower() for app in long_stay_apps)

                if is_long_stay and time_on_window >= 15:
                    event_summary = f"User has been on '{last_relevant_window}' for {time_on_window:.1f} minutes. Might have drifted within this app."
                    print(f"  [MONITOR] Long stay: {time_on_window:.1f}min on {last_relevant_window[:40]}")

                    decision = anchor_agent_decide(event_summary)
                    if decision["action"] != "stay_silent":
                        await speak(decision["message"])
                        await broadcast({
                            "type": "nudge",
                            "nudge_type": decision["action"],
                            "message": decision["message"],
                            "options": decision.get("options", [])
                        })
                    relevant_window_start = time.time()  # reset

            # Sustained drift -- stuck on a drift app too long
            if last_title and last_title in classification_cache:
                last_verdict = classification_cache[last_title].get("verdict")
                if last_verdict == "drift" and relevant_window_start is None:
                    # Start tracking if not already
                    if not session_state.get("sustained_drift_start"):
                        session_state["sustained_drift_start"] = time.time()

                    sustained_minutes = (time.time() - session_state["sustained_drift_start"]) / 60

                    if sustained_minutes >= 1 and not session_state.get("sustained_drift_nudged"):
                        event_summary = f"User has been on drift app '{last_title}' for {sustained_minutes:.1f} minutes without leaving. They are stuck on this app even though it's not task-related."
                        print(f"  [MONITOR] Sustained drift: {sustained_minutes:.1f}min on {last_title[:40]}")

                        decision = anchor_agent_decide(event_summary)
                        if decision["action"] != "stay_silent":
                            print(f"  [AGENT] SUSTAINED DRIFT: {decision['message']}")
                            await speak(decision["message"])
                            await broadcast({
                                "type": "nudge",
                                "nudge_type": decision["action"],
                                "message": decision["message"],
                                "options": decision.get("options", [])
                            })
                        session_state["sustained_drift_nudged"] = True

            # Silent drift -- correct window open but no keyboard/mouse activity
            # Threshold depends on task type
            activity_type = session_state.get("task_context", {}).get("activity_type", "mixed")
            idle_thresholds = {
                "writing": 1,    # forms, essays, quizzes -- constant typing expected
                "coding": 3,     # sometimes you think before typing
                "browsing": 1,   # should be clicking/scrolling
                "reading": 7,    # legitimately staring at screen
                "mixed": 1,      # default
            }
            idle_threshold = idle_thresholds.get(activity_type, 3)

            idle_minutes = (time.time() - last_activity_time) / 60
            if idle_minutes >= idle_threshold and session_state.get("ever_on_task") and not session_state.get("idle_nudged"):
                event_summary = f"User has the correct window open but has had NO keyboard or mouse activity for {idle_minutes:.1f} minutes (threshold for {activity_type} task: {idle_threshold} min). They may have picked up their phone, zoned out, or left their desk."
                print(f"  [MONITOR] Silent drift: {idle_minutes:.1f}min idle (threshold: {idle_threshold}min for {activity_type})")

                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    print(f"  [AGENT] SILENT DRIFT: {decision['message']}")
                    await speak(decision["message"])
                    await broadcast({
                        "type": "nudge",
                        "nudge_type": "silent_drift",
                        "message": decision["message"],
                        "options": ["I'm here", "Taking a break"]
                    })
                session_state["idle_nudged"] = True

            # Task initiation (1 min for testing, 3 min production)
            if not session_state.get("ever_on_task") and not session_state.get("task_initiation_nudged") and elapsed >= 1:
                event_summary = f"User has been in session for {elapsed} minutes but NEVER opened a task-relevant app. Task initiation paralysis."
                print(f"  [MONITOR] Task initiation timeout: {elapsed}min, never on task")

                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    await speak(decision["message"])
                    await broadcast({
                        "type": "nudge",
                        "nudge_type": "task_initiation",
                        "message": decision["message"],
                        "options": ["I'm ready"]
                    })
                session_state["task_initiation_nudged"] = True

            # Hyperfocus (40+ min no break)
            last_break = session_state.get("last_break_time", session_state.get("start_time", time.time()))
            minutes_since_break = (time.time() - last_break) / 60
            if minutes_since_break >= 40 and session_state.get("ever_on_task"):
                event_summary = f"User has been focused for {minutes_since_break:.0f} minutes without a break. Possible hyperfocus."
                print(f"  [MONITOR] Hyperfocus: {minutes_since_break:.0f}min without break")

                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    await speak(decision["message"])
                    await broadcast({
                        "type": "nudge",
                        "nudge_type": "suggest_break",
                        "message": decision["message"],
                        "options": ["Take a break", "Keep going"]
                    })
                session_state["last_break_time"] = time.time()

        await asyncio.sleep(5)

    print("[MONITOR] Monitoring loop stopped.")


# ============================================================
# SESSION SUMMARY
# ============================================================
def build_session_summary() -> dict:
    """Build the end-of-session summary from observation history"""
    total_time = round((time.time() - session_state["start_time"]) / 60, 1)

    drift_observations = [o for o in observation_history
                          if o["type"] == "event" and "drift" in o.get("summary", "").lower()]
    nudge_observations = [o for o in observation_history if o["type"] == "nudge"]

    drift_apps = []
    for d in drift_observations:
        summary = d.get("summary", "")
        if "Window:" in summary:
            window_part = summary.split("Window:")[1].split("-->")[0].strip()
            app = window_part.split("-")[-1].strip()
            drift_apps.append(app)

    top_drift = Counter(drift_apps).most_common(1)
    top_trigger = top_drift[0][0] if top_drift else "None"
    top_trigger_count = top_drift[0][1] if top_drift else 0

    longest_streak = 0
    streak_start = None
    for obs in observation_history:
        if obs["type"] == "event":
            if "relevant" in obs.get("summary", "").lower():
                if streak_start is None:
                    streak_start = obs["elapsed_min"]
            elif "drift" in obs.get("summary", "").lower():
                if streak_start is not None:
                    streak = obs["elapsed_min"] - streak_start
                    longest_streak = max(longest_streak, streak)
                    streak_start = None
    if streak_start is not None:
        streak = total_time - streak_start
        longest_streak = max(longest_streak, streak)

    timeline = []
    current_type = "focused"
    segment_start = 0
    for obs in observation_history:
        if obs["type"] == "event":
            if "drift" in obs.get("summary", "").lower() and current_type != "drift":
                timeline.append({"start": segment_start, "end": obs["elapsed_min"], "type": current_type})
                segment_start = obs["elapsed_min"]
                current_type = "drift"
            elif "relevant" in obs.get("summary", "").lower() and current_type != "focused":
                timeline.append({"start": segment_start, "end": obs["elapsed_min"], "type": current_type})
                segment_start = obs["elapsed_min"]
                current_type = "focused"
        elif obs["type"] == "break_started":
            timeline.append({"start": segment_start, "end": obs["elapsed_min"], "type": current_type})
            segment_start = obs["elapsed_min"]
            current_type = "break"
        elif obs["type"] == "break_ended":
            timeline.append({"start": segment_start, "end": obs["elapsed_min"], "type": current_type})
            segment_start = obs["elapsed_min"]
            current_type = "focused"
    timeline.append({"start": segment_start, "end": total_time, "type": current_type})

    return {
        "total_time_min": total_time,
        "focused_time_min": round(sum(s["end"] - s["start"] for s in timeline if s["type"] == "focused"), 1),
        "drift_count": session_state.get("drift_count", 0),
        "nudge_count": len(nudge_observations),
        "longest_streak_min": round(longest_streak, 1),
        "top_drift_trigger": top_trigger,
        "top_drift_trigger_count": top_trigger_count,
        "timeline": timeline,
        "observation_history": observation_history,
        "task": session_state.get("task", ""),
        "session_date": datetime.now().isoformat(),
        "day_of_week": datetime.now().strftime("%A"),
        "time_of_day": datetime.now().strftime("%H:%M")
    }


# ============================================================
# CAMERA ENDPOINTS
# ============================================================
@app.get("/video_feed")
async def video_feed():
    return StreamingResponse(generate_mjpeg(), media_type="multipart/x-mixed-replace; boundary=frame")

@app.get("/activity")
async def get_activity():
    return latest_activity

@app.get("/debug_frame")
async def debug_frame():
    """Returns current frame as PNG for debugging (open in browser or curl > frame.png)"""
    if latest_jpeg_frame is None:
        return {"error": "No frame available yet"}
    # Decode JPEG → re-encode as PNG for easier viewing
    arr = np.frombuffer(latest_jpeg_frame, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    _, png = cv2.imencode('.png', img)
    return StreamingResponse(iter([png.tobytes()]), media_type="image/png")

@app.post("/camera/start")
async def start_camera_endpoint():
    success = start_camera()
    if success:
        threading.Thread(target=camera_loop, daemon=True).start()
        return {"status": "camera_started"}
    return {"error": "Could not open webcam"}

@app.post("/camera/stop")
async def stop_camera_endpoint():
    stop_camera()
    return {"status": "camera_stopped"}


# ============================================================
# REST ENDPOINTS
# ============================================================
@app.get("/")
async def root():
    return {"status": "Anchor backend running", "session_active": session_active}


@app.post("/session/start")
async def start_session(request: SessionStartRequest):
    """Start a new focus session"""
    global session_active, session_state, observation_history, classification_cache, monitoring_task

    if session_active:
        return {"error": "Session already active. End it first."}

    print(f"\n[SESSION] Starting: '{request.task}' for {request.duration} min")
    task_context = create_task_context(request.task)
    print(f"[SESSION] Context built: {task_context.get('domain', 'unknown')}")

    session_state = {
        "task": request.task,
        "task_context": task_context,
        "start_time": time.time(),
        "duration_minutes": request.duration,
        "drift_count": 0,
        "last_nudge_time": None,
        "break_active": False,
        "break_start": None,
        "last_break_time": time.time(),
        "ever_on_task": False,
        "expected_notifications": request.expected_notifications,
        "dnd_enabled": request.dnd,
        "task_initiation_nudged": False,
        "sustained_drift_start": None,
        "sustained_drift_nudged": False,
        "idle_nudged": False,
    }

    observation_history = []
    classification_cache = {}

    add_observation(
        f"Session started: '{request.task}', Duration: {request.duration}min, "
        f"DND: {request.dnd}, Expecting: {request.expected_notifications or 'nothing'}",
        "session_started"
    )

    session_active = True
    monitoring_task = asyncio.create_task(monitoring_loop())

    await broadcast({
        "type": "session_started",
        "task": request.task,
        "duration": request.duration,
        "task_context": task_context
    })

    return {
        "status": "session_started",
        "task": request.task,
        "task_context": task_context,
        "duration": request.duration
    }


@app.post("/session/end")
async def end_session():
    """End the current session and return summary"""
    global session_active, monitoring_task

    if not session_active:
        return {"error": "No active session"}

    session_active = False
    if monitoring_task:
        monitoring_task.cancel()
        try:
            await monitoring_task
        except asyncio.CancelledError:
            pass

    summary = build_session_summary()

    print(f"\n[SESSION] Ended. Focus: {summary['focused_time_min']}min, Drifts: {summary['drift_count']}")

    await broadcast({
        "type": "session_ended",
        "summary": summary
    })

    return {"status": "session_ended", "summary": summary}


@app.get("/session/status")
async def get_session_status():
    """Get current session status"""
    if not session_active:
        return {"active": False}

    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    return {
        "active": True,
        "task": session_state.get("task", ""),
        "elapsed_min": elapsed,
        "duration_min": session_state.get("duration_minutes", 0),
        "drift_count": session_state.get("drift_count", 0),
        "ever_on_task": session_state.get("ever_on_task", False),
        "break_active": session_state.get("break_active", False)
    }


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    import uvicorn

    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: export GEMINI_API_KEY='your-key-here'")
        exit(1)

    print("=" * 60)
    print("ANCHOR BACKEND")
    print("=" * 60)
    print("Endpoints:")
    print("  POST /session/start  -- start a focus session")
    print("  POST /session/end    -- end session, get summary")
    print("  GET  /session/status -- check current status")
    print("  WS   /ws             -- real-time updates to frontend")
    print("  GET  /video_feed     -- MJPEG webcam stream")
    print("  GET  /activity       -- current activity status")
    print("  POST /camera/start   -- start webcam monitoring")
    print("  POST /camera/stop    -- stop webcam monitoring")
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000)