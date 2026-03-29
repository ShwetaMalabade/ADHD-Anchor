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
import random
import cv2
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

# LangChain imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_classic.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

# Tavily (optional -- works without it, better with it)
try:
    from langchain_community.tools.tavily_search import TavilySearchResults
    TAVILY_AVAILABLE = os.getenv("TAVILY_API_KEY") is not None
except ImportError:
    TAVILY_AVAILABLE = False

from activity_monitor import ActivityDetector, download_models, draw_hand_landmarks, draw_pose_landmarks, draw_status_overlay, blur_background
from anchor_hmm import AnchorHMM

# Initialize HMM
anchor_hmm = AnchorHMM()
print(f"[HMM] Initialized. {len(anchor_hmm.sequences)} past sessions loaded.")

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
        try:
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

            # Focus glow around face -- green when focused, red when drifting
            if face_result and face_result.face_landmarks:
                try:
                    h, w = frame.shape[:2]
                    face_lms = face_result.face_landmarks[0]
                    # Get face bounding box from landmarks
                    xs = [int(lm.x * w) for lm in face_lms]
                    ys = [int(lm.y * h) for lm in face_lms]
                    x1, x2 = max(0, min(xs) - 30), min(w, max(xs) + 30)
                    y1, y2 = max(0, min(ys) - 30), min(h, max(ys) + 30)
                    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                    rx, ry = (x2 - x1) // 2 + 15, (y2 - y1) // 2 + 15

                    # Color based on session focus status
                    is_drifting = session_state.get("drift_count", 0) > 0 and not session_state.get("ever_on_task", False)
                    is_on_break = session_state.get("break_active", False)
                    if is_on_break:
                        glow_color = (255, 165, 0)  # Orange for break
                    elif session_active and activity in ("focused", "typing"):
                        glow_color = (0, 220, 80)  # Green for focused
                    elif session_active:
                        glow_color = (0, 0, 230)  # Red for drifting/idle
                    else:
                        glow_color = (200, 200, 200)  # Gray when no session

                    # Draw soft glow (multiple transparent ellipses)
                    overlay = frame.copy()
                    for i in range(3):
                        cv2.ellipse(overlay, (cx, cy), (rx + i*8, ry + i*8), 0, 0, 360, glow_color, 2)
                    cv2.addWeighted(overlay, 0.5, frame, 0.5, 0, frame)
                except Exception:
                    pass  # Don't crash on glow errors

            _, jpeg = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 75])
            latest_jpeg_frame = jpeg.tobytes()
            if activity != last_sent_activity and activity not in ("initializing", "checking", "unknown"):
                print(f"  [CAMERA] Activity: {activity} ({confidence:.0%})")
                last_sent_activity = activity
            time.sleep(0.05)
        except Exception as e:
            print(f"  [CAMERA] Frame error (continuing): {e}")
            time.sleep(0.1)


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
    """Read the currently active window (macOS + Windows)"""
    import platform
    if platform.system() == "Darwin":
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
    else:
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            return title if title else "Unknown"
        except Exception:
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

    # Our own app windows are never the user's task -- classify as unsure silently
    title_lower = window_title.lower()
    if "anchor" in title_lower and "stay focused" in title_lower:
        return {"verdict": "unsure", "confidence": 0.5, "reason": "Anchor webapp", "is_anchor": True}

    # Empty or useless titles -- browser with no real content
    skip_words = {"google chrome", "mozilla firefox", "safari", "microsoft edge", "electron", "new tab", ""}
    meaningful_parts = [p.strip() for p in window_title.split(" - ") if p.strip().lower() not in skip_words and len(p.strip()) > 1]
    if not meaningful_parts:
        return {"verdict": "unsure", "confidence": 0.3, "reason": "Empty/loading page", "is_anchor": True}

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

2. Social messaging apps (WhatsApp, iMessage, Telegram, Discord, Facebook Messenger, Slack) = ALWAYS "unsure". Never classify as drift.

3. Social media (Twitter, Instagram, Reddit, TikTok) = "drift" unless content in title clearly relates to task.

4. Shopping sites (Amazon, eBay) = "drift" always.

4b. Streaming/entertainment sites (Netflix, Hulu, Disney+, Twitch, Spotify web player) = "drift" unless the task specifically involves watching something on that platform.

5. AI tools (ChatGPT, Claude, Gemini) = READ THE CONVERSATION TITLE. If topic matches task = "relevant". If unrelated = "drift". If no title visible = "unsure".

6. Terminal/command line/VS Code/Electron = "relevant" ONLY if user's task involves coding. Otherwise "drift". "Electron - ADHD-Anchor" or any IDE window is NOT relevant for reading/writing/browsing tasks.

7. Educational/academic content (lectures, tutorials, courses) on ANY platform (YouTube, Coursera, Khan Academy) = "relevant" if the content topic is related to the task's academic domain. A research lecture is relevant to reading a research paper.

8. "New Tab", blank pages, or pages with no clear content = "unsure".

9. Numeric IDs like "2501.00148", "2002.06673" are arXiv research paper IDs. If the task involves reading/research, these are ALWAYS "relevant".

10. MOST IMPORTANT: Read the FULL window title carefully. Ask yourself: "Is what this person is looking at RIGHT NOW directly helping them complete their task?" If the answer is no, it's "drift". Don't be generous -- if the website content has nothing to do with the task, classify as "drift" even if the app itself could theoretically be useful. Netflix for a reading task = drift. YouTube cooking video for a coding task = drift. A login page for a site unrelated to the task = drift.

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


async def anchor_agent_decide_async(new_event_summary: str) -> dict:
    """Async wrapper so monitoring loop doesn't block."""
    return await asyncio.to_thread(anchor_agent_decide, new_event_summary)


def _get_hmm_prediction() -> str:
    """Get HMM prediction for agent context."""
    try:
        recent_obs = []
        for obs in observation_history[-20:]:
            s = obs.get("summary", "").lower()
            if "relevant" in s: recent_obs.append("relevant")
            elif "drift" in s: recent_obs.append("drift")
            elif "break" in s: recent_obs.append("break")
            elif "idle" in s: recent_obs.append("idle")
            elif "unsure" in s: recent_obs.append("unsure")
        prediction = anchor_hmm.predict_next_state(recent_obs)
        if prediction:
            return f"Current: {prediction['current_state']}, Next likely: {prediction['most_likely_next']} ({prediction['confidence']:.0%})"
        return "Not enough data yet"
    except Exception:
        return "Not available"


def anchor_agent_decide(new_event_summary: str) -> dict:
    """LangChain agent with 6 tools. Falls back to direct Gemini if LangChain fails."""

    add_observation(new_event_summary)

    # Progressive cooldown -- gets longer each time you ignore nudges
    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    recent_nudges = [o for o in observation_history
                     if o["type"] == "nudge"
                     and o["elapsed_min"] >= elapsed - 5]
    nudge_count_recent = len(recent_nudges)

    # Cooldowns: short enough for demo, long enough to let voice finish
    # After 3 drifts, break is forced (handled in monitoring loop), so cooldown resets
    if nudge_count_recent >= 3:
        cooldown = 10  # Break suggestion is coming, just prevent overlap
    elif nudge_count_recent >= 2:
        cooldown = 10
    else:
        cooldown = 8  # ~time for one voice message to finish

    if session_state.get("last_nudge_time"):
        since_nudge = time.time() - session_state["last_nudge_time"]
        if since_nudge < cooldown:
            return {"action": "stay_silent", "message": "", "options": [],
                    "reason": f"Cooldown: {cooldown}s ({nudge_count_recent} recent nudges)"}

    if session_state.get("break_active"):
        return {"action": "stay_silent", "message": "", "options": [], "reason": "On break"}

    history = get_history_text()

    agent_input = f"""SESSION INFO:
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
- User profile from past sessions: {json.dumps(user_profile) if user_profile else 'First session (no history)'}
- HMM prediction: {_get_hmm_prediction()}

FULL SESSION HISTORY:
{history}

LATEST EVENT:
{new_event_summary}

Based on the above, diagnose why the user is in this state and choose the right tool(s).
If the user is focused on a task-relevant app, use NO tools (stay silent).
Even on the 1st drift, you MUST call speak_to_user to gently point out the drift.
OTHERWISE YOU MUST CALL A TOOL. Specifically:
- drift_count >= 2: MUST call speak_to_user or ask_user.
- ever_on_task is False and elapsed >= 0.7: MUST call chunk_task or speak_to_user to help them start.
- Sustained drift (1+ min on drift app): MUST call speak_to_user.
- User spoke via voice: MUST call speak_to_user to respond.
- recent_nudges >= 3: MUST call suggest_break -- nagging makes ADHD worse.
DO NOT just output text. You MUST call a tool or the user hears nothing.
All messages must reference the user's actual task: "{session_state['task']}" and actual apps from history.
If ever_on_task is False, say "you're on [actual app] -- ready to open your [task]?" or use countdown "3, 2, 1 let's go!"
If ever_on_task is True, the user HAS already worked on their task before. Do NOT use task initiation language like "let's get started", "3, 2, 1", "ready to open your task?", or "let's begin". Instead, acknowledge they were working and gently redirect: "You were doing great on [task]. Ready to jump back in?" or "You stepped away from [task]. Need a break or want to get back?"
NEVER repeat phrasing from previous nudges in the history."""

    # Try LangChain agent first (run in thread to avoid blocking monitoring loop)
    try:
        print(f"  [AGENT] LangChain agent thinking...")
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as pool:
            future = pool.submit(agent_executor.invoke, {"input": agent_input})
            result = future.result(timeout=20)  # 20 second max
        output = result.get("output", "")

        # Check if any tool was called by looking at intermediate steps
        steps = result.get("intermediate_steps", [])
        tool_used = False
        message = ""
        action = "stay_silent"

        for step in steps:
            tool_action, tool_result = step
            tool_name = tool_action.tool
            tool_used = True
            print(f"  [AGENT] Tool used: {tool_name}")

            if tool_name == "speak_to_user":
                action = "speak"
                message = tool_action.tool_input.get("message", "")
            elif tool_name == "ask_user":
                action = "ask"
                message = tool_action.tool_input.get("question", "")
            elif tool_name == "suggest_break":
                action = "suggest_break"
                activity = tool_action.tool_input.get("activity", "stretch and walk around")
                duration = tool_action.tool_input.get("duration_minutes", 5)
                message = f"Time for a {duration}-minute break. {activity}"
            elif tool_name == "chunk_task":
                action = "speak"
                message = tool_action.tool_input.get("tiny_next_step", "")
            elif tool_name == "suggest_dnd":
                action = "speak"
                message = tool_action.tool_input.get("reason", "")

        if tool_used and message:
            session_state["last_nudge_time"] = time.time()
            add_observation(f"Anchor said: \"{message}\"", "nudge")
            return {"action": action, "message": message, "options": [], "reason": f"LangChain agent (tools: {[s[0].tool for s in steps]})", "already_spoken": True}

        if not tool_used:
            print(f"  [AGENT] No tools used (staying silent)")
            return {"action": "stay_silent", "message": "", "options": [], "reason": f"Agent chose silence: {output[:100]}"}

        return {"action": "stay_silent", "message": "", "options": [], "reason": "No message from tools"}

    except Exception as e:
        print(f"  [AGENT] LangChain failed: {repr(e)[:100]}, falling back to direct Gemini")

    # Fallback: direct Gemini call
    try:
        fallback_prompt = f"""You are Anchor, an ADHD-specialist AI body double. Warm, supportive, never accusatory. 1-2 sentences MAX.

{agent_input}

RULES:
- 1st drift: MUST speak gently. 2nd drift: speak more directly. 3rd+: escalate or suggest break.
- 3+ nudges recently: suggest_break instead of another nudge.
- Messages specific to user's task. Never generic.
- ONLY reference apps from the session history.

Return ONLY JSON:
{{"action": "speak" or "ask" or "suggest_break" or "stay_silent", "message": "what to say", "options": [], "reason": "internal reasoning"}}"""

        response = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=fallback_prompt)
        result_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        decision = json.loads(result_text)
        decision.setdefault("action", "stay_silent")
        decision.setdefault("message", "")
        decision.setdefault("options", [])
        decision["reason"] = decision.get("reason", "") + " (fallback)"

        if decision["action"] != "stay_silent" and decision["message"]:
            session_state["last_nudge_time"] = time.time()
            add_observation(f"Anchor said: \"{decision['message']}\"", "nudge")

        return decision
    except Exception as e2:
        return {"action": "stay_silent", "message": "", "options": [], "reason": f"Both agents failed: {e2}"}


# ============================================================
# VOICE (ElevenLabs)
# ============================================================
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"  # Roger (free tier)

current_voice_process = None
last_voice_time = 0  # Timestamp of last voice start
VOICE_MIN_GAP = 2  # Minimum seconds between any two voice messages
voice_busy = False  # True while generating or playing

def stop_speaking():
    """Stop current voice."""
    global current_voice_process
    if current_voice_process and current_voice_process.poll() is None:
        current_voice_process.kill()
        current_voice_process = None

async def speak(message: str, context_window: str = None):
    """Speak a message. FOOLPROOF: only one voice at a time, minimum 5s gap.
    If already speaking or too soon after last voice, silently drops the message."""
    global voice_busy, last_voice_time, current_voice_process

    # Drop if already speaking
    if voice_busy:
        print(f"  [VOICE] Dropped (already speaking): {message[:40]}...", flush=True)
        return

    # Drop if too soon after last voice
    if time.time() - last_voice_time < VOICE_MIN_GAP:
        print(f"  [VOICE] Dropped (too soon, {time.time() - last_voice_time:.0f}s < {VOICE_MIN_GAP}s): {message[:40]}...", flush=True)
        return

    # Check if message is still relevant
    if context_window:
        current_title = get_active_window_title()
        if current_title != context_window:
            print(f"  [VOICE] Dropped (user left that window)", flush=True)
            return

    voice_busy = True
    last_voice_time = time.time()

    def _generate_and_play():
        global current_voice_process, voice_busy
        try:
            print(f'\nSpeaking: "{message}"', flush=True)
            audio = elevenlabs_client.text_to_speech.convert(
                text=message, voice_id=VOICE_ID,
                model_id="eleven_multilingual_v2", output_format="mp3_44100_128",
            )
            audio_bytes = b"".join(audio)
            import tempfile, subprocess
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as f:
                f.write(audio_bytes)
                f.flush()
                proc = subprocess.Popen(["afplay", f.name])
                current_voice_process = proc
                proc.wait(timeout=15)
            print("Done speaking.", flush=True)
        except Exception as e:
            print(f"[VOICE ERROR] {repr(e)}", flush=True)
        finally:
            current_voice_process = None
            voice_busy = False

    threading.Thread(target=_generate_and_play, daemon=True).start()


def speak_sync(message: str):
    """LangChain tools call this but it does NOT speak -- the monitoring loop handles all voice.
    This prevents double voice (tool speaks + monitoring loop speaks)."""
    print(f'\n  [TOOL: speak] "{message}" (text only, monitoring loop handles voice)', flush=True)
    # Do NOT add to voice queue -- the monitoring loop's nudge_and_speak will handle it
    return


# ============================================================
# LANGCHAIN AGENT TOOLS (6 tools)
# ============================================================

@tool
def speak_to_user(message: str) -> str:
    """Speak a message to the user via ElevenLabs voice. Use for gentle nudges,
    observations, encouragement. Keep to 1-2 sentences MAX."""
    speak_sync(message)
    return f"Spoke to user: {message}"

@tool
def ask_user(question: str) -> str:
    """Ask the user a question via voice. User responds by speaking back or clicking buttons.
    Use when you need input -- unsure apps, checking if stuck, offering choices."""
    speak_sync(question)
    return f"Asked user via voice: {question}"

@tool
def suggest_break(duration_minutes: int, activity: str) -> str:
    """Suggest a break with a specific GOOD activity. Use when executive function
    tank is depleted (focused long then crashing), or 3+ nudges ignored.
    ALWAYS suggest physical: stretch, walk, water, fresh air. NEVER phone/social media."""
    msg = f"Time for a {duration_minutes}-minute break. {activity}"
    print(f'\n  [TOOL: suggest_break] {duration_minutes}min - {activity}')
    speak_sync(msg)
    session_state["break_active"] = True
    session_state["break_start"] = time.time()
    return f"Suggested {duration_minutes} min break: {activity}"

@tool
def chunk_task(task_name: str, tiny_next_step: str) -> str:
    """Break user's task into smallest possible next step. Use when overwhelmed
    (never started), stuck (was working then stopped), or avoiding (drifts within 1-2 min).
    Step must be SO small it feels effortless. Forms='fill first field'. Reading='read abstract'.
    Coding='write function signature'. Writing='write one bad sentence'."""
    msg = f"Here's what to do next: {tiny_next_step}"
    print(f'\n  [TOOL: chunk_task] {task_name} -> {tiny_next_step}')
    speak_sync(msg)
    return f"Suggested tiny step for '{task_name}': {tiny_next_step}"

@tool
def search_adhd_strategy(situation: str) -> str:
    """Search for ADHD-specific strategies via Tavily when standard approaches fail.
    Use sparingly -- maybe once per session. Examples: 'executive function depletion',
    'task initiation paralysis', 'boredom with repetitive tasks'."""
    if not TAVILY_AVAILABLE:
        return "Tavily not available. Use your built-in ADHD knowledge instead."
    try:
        tavily = TavilySearchResults(max_results=3)
        results = tavily.invoke(f"ADHD {situation} strategy evidence-based")
        summaries = [r["content"][:200] for r in results if isinstance(r, dict) and "content" in r]
        return "Research findings: " + " | ".join(summaries) if summaries else "No results found."
    except Exception as e:
        return f"Search failed: {e}. Use built-in ADHD knowledge."

@tool
def suggest_dnd(reason: str) -> str:
    """Suggest user enable Do Not Disturb. Use when notifications keep pulling
    user away -- pattern of fast window switches to messaging apps."""
    msg = f"You might want to turn on Do Not Disturb. {reason}"
    print(f'\n  [TOOL: suggest_dnd] {reason}')
    speak_sync(msg)
    return f"Suggested DND: {reason}"


# ============================================================
# CREATE LANGCHAIN AGENT
# ============================================================

AGENT_SYSTEM_PROMPT = """You are Anchor, an AI body double with deep knowledge of ADHD neuroscience.
You understand executive function depletion (Barkley's fuel tank model), time blindness,
task initiation paralysis, hyperfocus risks, and good vs bad breaks.
You are warm, supportive, never accusatory.

6 TOOLS -- choose based on WHY the user is struggling:
1. speak_to_user: Gentle nudges, encouragement. Most common tool.
2. ask_user: Need user input via voice. Unsure apps, checking if stuck.
3. suggest_break: Brain depleted. 20+ min focused then crashing, 3+ drifts in 5 min, 3+ nudges ignored. Physical activity only, never phone.
4. chunk_task: Overwhelmed, stuck, or avoiding. SMALLEST possible next step specific to their task.
5. search_adhd_strategy: Standard approaches failing. Research-backed fresh approach. Use sparingly.
6. suggest_dnd: Notifications keep pulling user away.

DIAGNOSIS -- diagnose BEFORE choosing tool:
* DEPLETED: Focused 20+ min then drifting -> suggest_break
* STUCK: Was working then hit wall -> chunk_task
* OVERWHELMED: Never started, bouncing between apps -> chunk_task with smallest first step
* BORED: Drifting to fun apps -> speak_to_user with empathy + mini-goal
* AVOIDING: Returns but drifts within 1-2 min -> speak_to_user to name it + chunk_task
* NOTIFICATION PULL: Fast switch, event says "NOTIFICATION PULL" -> suggest_dnd or speak_to_user gently
* SILENT DRIFT: No keyboard/mouse -> ask_user "still with me?"

RULES:
- 1st drift: MUST use speak_to_user after detecting the drift. Gently point out they drifted.
- 2nd drift: speak_to_user with gentle nudge.
- 3rd+ drift: MUST use tool. Diagnose and pick right one.
- 3+ nudges in 5 min: ALWAYS suggest_break. Nagging makes ADHD worse.
- Messages MUST be specific to user's actual task. Never generic.
- 1-2 sentences MAX. NEVER repeat same phrasing.
- NEVER mention apps user hasn't visited. Use ACTUAL app names from history.
- If ever_on_task is False: "you're on [actual app] -- ready to open your [task]?" or countdown "3, 2, 1 let's go!"
- If ever_on_task is True: user already worked on their task. Do NOT say "let's get started", "3, 2, 1", or "ready to open?" -- instead say "You were making progress. Ready to jump back in?" or "Need a break or want to get back?"
- Task chunking: forms="fill first field", reading="read abstract", coding="write function signature"
- Acknowledge task tedium: "Forms are repetitive" / "Dense papers are hard"
- Can chain tools: search_adhd_strategy then speak_to_user with findings.
- After break: chunk_task "What's the ONE small thing you'll do first?"

When user is focused: NO tool calls (stay silent)."""


def create_anchor_agent():
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.7)
    tools = [speak_to_user, ask_user, suggest_break, chunk_task, search_adhd_strategy, suggest_dnd]
    prompt = ChatPromptTemplate.from_messages([
        ("system", AGENT_SYSTEM_PROMPT),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=3, handle_parsing_errors=True)


agent_executor = create_anchor_agent()
print("[AGENT] LangChain agent with 6 tools created.")


def speak_sync(message: str):
    """Synchronous voice for use inside LangChain tools (which are sync).
    Plays audio in a background thread so it doesn't block the monitoring loop."""
    try:
        print(f'\n  [TOOL: speak] "{message}"', flush=True)
        audio = elevenlabs_client.text_to_speech.convert(
            text=message, voice_id=VOICE_ID,
            model_id="eleven_multilingual_v2", output_format="mp3_44100_128",
        )
        # Consume the generator into bytes so we can pass to thread
        audio_bytes = b"".join(audio)
        # Play in background thread so monitoring loop isn't blocked
        import threading
        def _play():
            try:
                import subprocess
                import tempfile
                with tempfile.NamedTemporaryFile(suffix=".mp3", delete=True) as f:
                    f.write(audio_bytes)
                    f.flush()
                    subprocess.run(["afplay", f.name], timeout=15)
                print("  [TOOL: speak] Done.", flush=True)
            except Exception as e:
                print(f"  [VOICE PLAY ERROR] {repr(e)}", flush=True)
        threading.Thread(target=_play, daemon=True).start()
    except Exception as e:
        import traceback
        print(f"  [VOICE ERROR] {repr(e)}", flush=True)
        traceback.print_exc()


# ============================================================
# LANGCHAIN AGENT TOOLS (6 tools)
# ============================================================

@tool
def speak_to_user(message: str) -> str:
    """Speak a message to the user via ElevenLabs voice. Use for gentle nudges,
    observations, encouragement. Keep to 1-2 sentences MAX."""
    speak_sync(message)
    return f"Spoke to user: {message}"

@tool
def ask_user(question: str) -> str:
    """Ask the user a question via voice. User responds by speaking back or clicking buttons.
    Use when you need input -- unsure apps, checking if stuck, offering choices."""
    speak_sync(question)
    return f"Asked user via voice: {question}"

@tool
def suggest_break(duration_minutes: int, activity: str) -> str:
    """Suggest a break with a specific GOOD activity. Use when executive function
    tank is depleted (focused long then crashing), or 3+ nudges ignored.
    ALWAYS suggest physical: stretch, walk, water, fresh air. NEVER phone/social media."""
    msg = f"Time for a {duration_minutes}-minute break. {activity}"
    print(f'\n  [TOOL: suggest_break] {duration_minutes}min - {activity}')
    speak_sync(msg)
    session_state["break_active"] = True
    session_state["break_start"] = time.time()
    return f"Suggested {duration_minutes} min break: {activity}"

@tool
def chunk_task(task_name: str, tiny_next_step: str) -> str:
    """Break user's task into smallest possible next step. Use when overwhelmed
    (never started), stuck (was working then stopped), or avoiding (drifts within 1-2 min).
    Step must be SO small it feels effortless. Forms='fill first field'. Reading='read abstract'.
    Coding='write function signature'. Writing='write one bad sentence'."""
    msg = f"Here's what to do next: {tiny_next_step}"
    print(f'\n  [TOOL: chunk_task] {task_name} -> {tiny_next_step}')
    speak_sync(msg)
    return f"Suggested tiny step for '{task_name}': {tiny_next_step}"

@tool
def search_adhd_strategy(situation: str) -> str:
    """Search for ADHD-specific strategies via Tavily when standard approaches fail.
    Use sparingly -- maybe once per session. Examples: 'executive function depletion',
    'task initiation paralysis', 'boredom with repetitive tasks'."""
    if not TAVILY_AVAILABLE:
        return "Tavily not available. Use your built-in ADHD knowledge instead."
    try:
        tavily = TavilySearchResults(max_results=3)
        results = tavily.invoke(f"ADHD {situation} strategy evidence-based")
        summaries = [r["content"][:200] for r in results if isinstance(r, dict) and "content" in r]
        return "Research findings: " + " | ".join(summaries) if summaries else "No results found."
    except Exception as e:
        return f"Search failed: {e}. Use built-in ADHD knowledge."

@tool
def suggest_dnd(reason: str) -> str:
    """Suggest user enable Do Not Disturb. Use when notifications keep pulling
    user away -- pattern of fast window switches to messaging apps."""
    msg = f"You might want to turn on Do Not Disturb. {reason}"
    print(f'\n  [TOOL: suggest_dnd] {reason}')
    speak_sync(msg)
    return f"Suggested DND: {reason}"


# ============================================================
# CREATE LANGCHAIN AGENT
# ============================================================

AGENT_SYSTEM_PROMPT = """You are Anchor, an AI body double with deep knowledge of ADHD neuroscience.
You understand executive function depletion (Barkley's fuel tank model), time blindness,
task initiation paralysis, hyperfocus risks, and good vs bad breaks.
You are warm, supportive, never accusatory.

6 TOOLS -- choose based on WHY the user is struggling:
1. speak_to_user: Gentle nudges, encouragement. Most common tool.
2. ask_user: Need user input via voice. Unsure apps, checking if stuck.
3. suggest_break: Brain depleted. 20+ min focused then crashing, 3+ drifts in 5 min, 3+ nudges ignored. Physical activity only, never phone.
4. chunk_task: Overwhelmed, stuck, or avoiding. SMALLEST possible next step specific to their task.
5. search_adhd_strategy: Standard approaches failing. Research-backed fresh approach. Use sparingly.
6. suggest_dnd: Notifications keep pulling user away.

DIAGNOSIS -- diagnose BEFORE choosing tool:
* DEPLETED: Focused 20+ min then drifting -> suggest_break
* STUCK: Was working then hit wall -> chunk_task
* OVERWHELMED: Never started, bouncing between apps -> chunk_task with smallest first step
* BORED: Drifting to fun apps -> speak_to_user with empathy + mini-goal
* AVOIDING: Returns but drifts within 1-2 min -> speak_to_user to name it + chunk_task
* NOTIFICATION PULL: Fast switch, event says "NOTIFICATION PULL" -> suggest_dnd or speak_to_user gently
* SILENT DRIFT: No keyboard/mouse -> ask_user "still with me?"

RULES:
- 1st drift: MUST use speak_to_user after detecting the drift. Gently point out they drifted.
- 2nd drift: speak_to_user with gentle nudge.
- 3rd+ drift: MUST use tool. Diagnose and pick right one.
- 3+ nudges in 5 min: ALWAYS suggest_break. Nagging makes ADHD worse.
- Messages MUST be specific to user's actual task. Never generic.
- 1-2 sentences MAX. NEVER repeat same phrasing.
- NEVER mention apps user hasn't visited. Use ACTUAL app names from history.
- If ever_on_task is False: "you're on [actual app] -- ready to open your [task]?" or "3, 2, 1 let's go!"
- If ever_on_task is True: Do NOT use "let's get started" or "3, 2, 1" or "ready to open?" -- say "You were making progress. Ready to jump back in?"
- Task chunking: forms="fill first field", reading="read abstract", coding="write function signature"
- Acknowledge task tedium: "Forms are repetitive" / "Dense papers are hard"
- Can chain tools: search_adhd_strategy then speak_to_user with findings.
- After break: chunk_task "What's the ONE small thing you'll do first?"

When user is focused: NO tool calls (stay silent).

CRITICAL: You MUST call a tool (speak_to_user, ask_user, suggest_break, or chunk_task) in ALL of these situations:
- drift_count >= 2: User has drifted multiple times.
- ever_on_task is False and elapsed >= 0.7 min: User hasn't even opened their task after 40+ seconds. Use chunk_task or speak_to_user to help them start.
- User has been on a drift app for 1+ minute (sustained drift event).
- User spoke via voice: ALWAYS respond with speak_to_user or ask_user.
- 3+ nudges recently: Use suggest_break instead of another nudge.

Do NOT just output text -- you MUST invoke a tool. Outputting text without calling a tool means the user hears NOTHING. The ONLY way to communicate with the user is through tools. If you want to say something, call speak_to_user. If you want to stay silent, call no tools."""


def create_anchor_agent():
    llm = ChatGoogleGenerativeAI(model="gemini-2.5-flash", temperature=0.7)
    tools = [speak_to_user, ask_user, suggest_break, chunk_task, search_adhd_strategy, suggest_dnd]
    prompt = ChatPromptTemplate.from_messages([
        ("system", AGENT_SYSTEM_PROMPT),
        ("human", "{input}"),
        MessagesPlaceholder(variable_name="agent_scratchpad"),
    ])
    agent = create_tool_calling_agent(llm, tools, prompt)
    return AgentExecutor(agent=agent, tools=tools, verbose=True, max_iterations=3, handle_parsing_errors=True)



# ============================================================
# WEBSOCKET -- send events to React frontend
# ============================================================
async def broadcast(event: dict):
    disconnected = []
    for ws in connected_clients:
        try:
            await asyncio.wait_for(ws.send_json(event), timeout=2)
        except:
            disconnected.append(ws)
    for ws in disconnected:
        try:
            connected_clients.remove(ws)
        except ValueError:
            pass


async def nudge_and_speak(decision: dict, extra_broadcast: dict = None, context_window: str = None):
    """Broadcast nudge to frontend first (so Smiski shows up), then queue voice.
    context_window: if set, voice is skipped if user already left that window."""
    nudge_event = {
        "type": "nudge",
        "nudge_type": decision.get("action", "speak"),
        "message": decision.get("message", ""),
        "options": decision.get("options", []),
    }
    if extra_broadcast:
        nudge_event.update(extra_broadcast)
    try:
        await broadcast(nudge_event)
    except Exception as e:
        print(f"  [BROADCAST ERROR] {repr(e)}")
    if not decision.get("already_spoken") and decision.get("message"):
        await speak(decision["message"], context_window=context_window)


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

            # Clear waiting state on any user response
            session_state["waiting_for_response"] = False

            if action == "pull_me_back" or action == "i_drifted":
                # If break is active, "pull me back" means skip the break
                if session_state.get("break_active"):
                    session_state["break_active"] = False
                    session_state["drift_count"] = 0
                    await broadcast({"type": "break_ended"})
                add_observation("User confirmed: drifted / pull me back", "user_response")
                session_state["drift_count"] = session_state.get("drift_count", 0) + 1
                session_state["total_drift_count"] = session_state.get("total_drift_count", 0) + 1
                drift_count = session_state["drift_count"]
                # Cache this window as drift so it's recognized instantly next time
                last_unsure_title = session_state.get("last_unsure_window", "")
                if last_unsure_title:
                    classification_cache[last_unsure_title] = {"verdict": "drift", "confidence": 1.0, "reason": "User confirmed drift"}
                    print(f"  [CACHE] Remembered '{last_unsure_title[:40]}' as DRIFT")
                await broadcast({"type": "status", "value": "drifted"})
                task = session_state.get("task", "your task")

                if drift_count >= 3:
                    # Force break on 3+ drifts
                    message = f"You've drifted {drift_count} times. Your brain needs a reset. Take a 5-minute break -- stretch, grab water. Stay off your phone!"
                    session_state["break_active"] = True
                    session_state["break_start"] = time.time()
                    decision = {"action": "suggest_break", "message": message, "options": ["Take a break", "Keep going"]}
                    await nudge_and_speak(decision, {"drift_count": drift_count, "nudge_type": "suggest_break"})
                    await broadcast({"type": "break_started", "duration": 300})
                else:
                    message = f"No worries! Let's get back to {task}."
                    decision = {"action": "speak", "message": message}
                    await nudge_and_speak(decision)

            elif action == "its_for_my_task":
                add_observation("User confirmed: current window is for task", "user_response")
                session_state["ever_on_task"] = True
                # Cache this window as relevant so it's recognized instantly next time
                last_unsure_title = session_state.get("last_unsure_window", "")
                if last_unsure_title:
                    classification_cache[last_unsure_title] = {"verdict": "relevant", "confidence": 1.0, "reason": "User confirmed relevant"}
                    print(f"  [CACHE] Remembered '{last_unsure_title[:40]}' as RELEVANT")
                await broadcast({"type": "status", "value": "focused"})

            elif action == "taking_break":
                session_state["break_active"] = True
                session_state["break_start"] = time.time()
                add_observation("User started a 5-minute break", "break_started")
                await broadcast({"type": "break_started", "duration": 300})

            elif action == "skip_break":
                session_state["break_active"] = False
                session_state["drift_count"] = 0
                add_observation("User skipped break", "user_response")
                await broadcast({"type": "break_ended"})
                await broadcast({"type": "status", "value": "focused"})

            elif action == "im_ready":
                # Only end break if explicitly skipping, not on regular "I'm ready"
                if session_state.get("break_active"):
                    session_state["break_active"] = False
                    session_state["drift_count"] = 0
                    add_observation("User ended break early", "user_response")
                    await broadcast({"type": "break_ended"})
                await broadcast({"type": "status", "value": "focused"})

            elif action == "got_it":
                add_observation("User clicked: Got it (notification acknowledged)", "user_response")
                await broadcast({"type": "status", "value": "focused"})

            elif action == "user_speech":
                user_text = data.get("text", "").strip()
                if user_text and session_active:
                    print(f'\n  [VOICE IN] User said: "{user_text}"')
                    add_observation(f'User said via voice: "{user_text}"', "user_voice")

                    # Feed to agent for response
                    event_summary = f'User spoke via voice: "{user_text}". Respond to what they said in context of the session.'
                    decision = await anchor_agent_decide_async(event_summary)
                    if decision["action"] != "stay_silent":
                        print(f'  [AGENT] Responding to voice: {decision["message"]}')
                        await nudge_and_speak(decision)

    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        print(f"Frontend disconnected. Total clients: {len(connected_clients)}")


# ============================================================
# AUDIO WEBSOCKET -- receives mic audio, sends to ElevenLabs STT
# ============================================================
@app.websocket("/ws-audio")
async def audio_websocket(websocket: WebSocket):
    await websocket.accept()
    print("[AUDIO-WS] Client connected for STT")

    try:
        while True:
            # Receive binary audio data from frontend
            data = await websocket.receive_bytes()
            if len(data) < 2000:
                continue  # Skip tiny chunks (likely silence)

            print(f"  [AUDIO-WS] Received {len(data)} bytes, sending to ElevenLabs STT...")

            try:
                # Send to ElevenLabs STT (file must be a tuple: filename, bytes, mimetype)
                import io
                audio_file = ("audio.webm", io.BytesIO(data), "audio/webm")
                result = await asyncio.to_thread(
                    elevenlabs_client.speech_to_text.convert,
                    file=audio_file,
                    model_id="scribe_v1",
                    language_code="en",
                )
                text = result.text.strip() if hasattr(result, 'text') else str(result).strip()

                if text and len(text) > 1:
                    print(f'  [STT] Transcribed: "{text}"')

                    # Send transcription back to frontend
                    await websocket.send_json({"type": "transcription", "text": text})

                    # Also process as user speech if session is active
                    if session_active:
                        add_observation(f'User said via voice: "{text}"', "user_voice")
                        event_summary = f'User spoke via voice: "{text}". Respond to what they said in context of the session.'
                        decision = await anchor_agent_decide_async(event_summary)
                        if decision["action"] != "stay_silent":
                            print(f'  [AGENT] Responding to voice: {decision["message"]}')
                            await nudge_and_speak(decision)
                else:
                    print("  [STT] No speech detected in chunk")

            except Exception as e:
                import traceback
                print(f"  [STT ERROR] {repr(e)}")
                if hasattr(e, 'body'):
                    print(f"  [STT ERROR body] {e.body}")
                if hasattr(e, 'status_code'):
                    print(f"  [STT ERROR status] {e.status_code}")
                traceback.print_exc()

    except WebSocketDisconnect:
        print("[AUDIO-WS] Client disconnected")


# ============================================================
# MONITORING LOOP (runs in background during session)
# ============================================================
async def monitoring_loop():
    """The main loop that watches the screen and triggers the pipeline"""
    global session_active

    last_title = ""
    last_relevant_window = ""
    relevant_window_start = None
    last_window_change_time = time.time()
    last_phone_nudge_time = 0
    phone_detect_count = 0

    print("\n[MONITOR] Starting monitoring loop...")

    while session_active:
        title = await asyncio.to_thread(get_active_window_title)
        elapsed = round((time.time() - session_state["start_time"]) / 60, 1)

        # Check if break should end
        if session_state.get("break_active") and session_state.get("break_start"):
            break_duration = time.time() - session_state["break_start"]
            if break_duration >= 300:  # 5 minutes
                session_state["break_active"] = False
                session_state["drift_count"] = 0  # Reset drift count after break
                add_observation("Break ended", "break_ended")
                await broadcast({"type": "break_ended"})
                task = session_state.get("task", "your task")
                encouragement_messages = [
                    f"Nice reset! What's one small step on {task}?",
                    f"Welcome back! You've got this. Let's jump into {task}.",
                    f"Break done! Ready to get back to {task}?",
                    f"Refreshed? Pick the easiest part of {task} and start there.",
                ]
                message = encouragement_messages[int(time.time()) % len(encouragement_messages)]
                decision = {"action": "speak", "message": message, "options": ["Let's go"]}
                await nudge_and_speak(decision)

        # During breaks, still monitor but don't nudge for drifts (they're on break)
        # If they skip break via button, break_active will be set to False

        # If waiting for user response to "is this for your task?"
        if session_state.get("waiting_for_response"):
            # If window changed or 15s passed, clear waiting -- don't keep asking
            if title != last_title:
                session_state["waiting_for_response"] = False
            elif time.time() - session_state.get("waiting_since", time.time()) >= 15:
                # Waited 15s, no response -- assume it's for their task and move on
                session_state["waiting_for_response"] = False
                print(f"  [MONITOR] No response after 15s -- moving on")
            else:
                await asyncio.sleep(5)
                continue

        # Window changed
        if title != last_title:

            time_since_last_switch = time.time() - last_window_change_time
            time_since_last_activity = time.time() - last_activity_time
            is_notification_pull = time_since_last_switch < 3 and time_since_last_activity < 3
            last_window_change_time = time.time()

            session_state["sustained_drift_start"] = None
            session_state["sustained_drift_nudged"] = False
            session_state["idle_nudged"] = False

            result = await asyncio.to_thread(
                classify_window,
                session_state["task_context"],
                title,
                session_state.get("expected_notifications", "")
            )
            verdict = result["verdict"]
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")
            cached = result.get("from_cache", False)

            if verdict == "drift":
                session_state["drift_count"] += 1  # Cycle counter (resets after break)
                session_state["total_drift_count"] = session_state.get("total_drift_count", 0) + 1  # Session total (never resets)
            elif verdict == "relevant":
                session_state["ever_on_task"] = True

            if verdict == "relevant":
                if title != last_relevant_window:
                    relevant_window_start = time.time()
                    last_relevant_window = title
            else:
                relevant_window_start = None
                last_relevant_window = ""

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

            if verdict == "relevant":
                stop_speaking()  # Stop any playing nudge -- user self-corrected
                session_state["waiting_for_response"] = False  # Clear any pending question
                add_observation(f"Window: {title} --> relevant ({confidence:.0%})")
                await broadcast({"type": "status", "value": "focused"})
                print(f"  [MONITOR] [{elapsed}m] RELEVANT: {title[:60]}")

                # Track consecutive relevant visits -- every 5th time, say "I'm watching you"
                session_state["relevant_streak"] = session_state.get("relevant_streak", 0) + 1
                if session_state["relevant_streak"] % 5 == 0 and session_state["relevant_streak"] > 0:
                    encouragements = [
                        "I'm watching you... and you're crushing it!",
                        "Still here, still focused. You're doing great!",
                        "5 tabs in a row on task. I see you!",
                    ]
                    msg = random.choice(encouragements)
                    print(f"  [MONITOR] Encouragement after {session_state['relevant_streak']} relevant tabs")
                    await nudge_and_speak({"action": "speak", "message": msg}, {"nudge_type": "encouragement"})

            elif verdict == "drift":
                session_state["relevant_streak"] = 0  # Reset streak on drift
                add_observation(f"Window: {title} --> drift ({confidence:.0%}). Reason: {reason}.")
                print(f"  [MONITOR] [{elapsed}m] DRIFT: {title[:60]}", flush=True)

                # Don't nudge during breaks
                if session_state.get("break_active"):
                    last_title = title
                    continue

                # Quick check: wait 2 seconds and re-read window -- if user already came back, skip nudge
                await asyncio.sleep(2)
                current_title = await asyncio.to_thread(get_active_window_title)
                if current_title != title:
                    print(f"  [MONITOR] User self-corrected in 2s -- skipping nudge", flush=True)
                    last_title = current_title
                    # Still count the drift but don't nudge
                    await asyncio.sleep(3)
                    continue

                try:
                    drift_count = session_state["drift_count"]
                    task = session_state.get("task", "your task")
                    # Extract meaningful site/content name from title
                    # "Google Chrome - YouTube - Google Chrome - User" -> "YouTube"
                    # "Google Chrome - Reddit - Google Chrome" -> "Reddit"
                    parts = [p.strip() for p in title.split(" - ") if p.strip().lower() not in ("google chrome", "mozilla firefox", "safari", "microsoft edge")]
                    window_short = parts[0][:40] if parts else title.split(" - ")[0].strip()[:40]

                    if drift_count >= 3:
                        break_messages = [
                            f"{drift_count} drifts. Take a 5-minute break. Try splashing cold water on your face -- it resets your nervous system.",
                            f"Your brain needs a reset. 5 minutes -- do some jumping jacks or walk outside. Physical movement restores focus faster than scrolling.",
                            f"Break time! Try the 5-4-3-2-1 grounding trick: name 5 things you see, 4 you hear, 3 you touch. It calms the ADHD mind.",
                            f"Time to recharge. Stand up, stretch your arms overhead, take 3 deep breaths. Your prefrontal cortex will thank you.",
                            f"{drift_count} drifts means your fuel tank is low. Grab water and do 10 squats -- it sends blood to your brain and sharpens focus.",
                        ]
                        message = random.choice(break_messages)
                        print(f"  [MONITOR] Drift #{drift_count} — suggesting break", flush=True)
                        session_state["break_active"] = True
                        session_state["break_start"] = time.time()
                        decision = {"action": "suggest_break", "message": message, "options": ["Take a break", "Keep going"]}
                        await nudge_and_speak(decision, {"drift_count": drift_count, "nudge_type": "suggest_break"})
                        await broadcast({"type": "break_started", "duration": 300})
                    else:
                        drift_messages = [
                            f"Hey, you're on {window_short}. Back to {task}?",
                            f"{window_short}? Let's get back to {task}.",
                            f"Drifted to {window_short}. Break or get back?",
                            f"Caught you on {window_short}! Ready to switch back?",
                            f"Quick detour to {window_short}. {task} is waiting.",
                        ]
                        message = random.choice(drift_messages)
                        print(f"  [MONITOR] Drift #{drift_count}: {window_short} — instant nudge", flush=True)
                        decision = {"action": "speak", "message": message, "options": ["Pull me back", "Taking a break"]}
                        await nudge_and_speak(decision, {"drift_count": drift_count}, context_window=title)

                        # Agent follow-up disabled -- causes double voice messages
                        # Instant nudges are sufficient for the demo

                    await broadcast({"type": "status", "value": "drifted"})
                except Exception as e:
                    print(f"  [DRIFT ERROR] {repr(e)}", flush=True)
                    import traceback
                    traceback.print_exc()

            elif verdict == "unsure":
                add_observation(f"Window: {title} --> unsure ({confidence:.0%}). Reason: {reason}.")
                print(f"  [MONITOR] [{elapsed}m] UNSURE: {title[:60]}")

                # Don't ask about Anchor app or empty windows (marked with is_anchor flag)
                is_silent_unsure = result.get("is_anchor", False)
                parts = [p.strip() for p in title.split(" - ") if p.strip().lower() not in ("google chrome", "mozilla firefox", "safari", "microsoft edge", "electron", "")]
                app_name = parts[0][:40] if parts else ""
                is_empty = not app_name or app_name.lower() in ("new tab", "missing value", "unknown", "")

                if not is_empty and not is_silent_unsure and not session_state.get("break_active") and not session_state.get("waiting_for_response"):
                    session_state["last_unsure_window"] = title
                    task = session_state.get("task", "your task")
                    message = f"I see you're on {app_name}. Is this for {task}, or did you drift?"
                    print(f"  [MONITOR] Asking about unsure window: {app_name}")
                    decision = {"action": "ask", "message": message, "options": ["Yes, it's for my task", "I drifted"]}
                    await nudge_and_speak(decision, {"drift_count": session_state["drift_count"]}, context_window=title)
                    # Pause other nudges while waiting for user response
                    session_state["waiting_for_response"] = True
                    session_state["waiting_since"] = time.time()

            last_title = title

        else:
            # Same window -- check timeouts
            # Skip ALL timeout checks during breaks
            if session_state.get("break_active"):
                await asyncio.sleep(5)
                continue

            if relevant_window_start and last_relevant_window:
                time_on_window = (time.time() - relevant_window_start) / 60
                long_stay_apps = ["claude", "chatgpt", "youtube", "openai"]
                is_long_stay = any(app in last_relevant_window.lower() for app in long_stay_apps)

                if is_long_stay and time_on_window >= 15:
                    event_summary = f"User has been on '{last_relevant_window}' for {time_on_window:.1f} minutes. Might have drifted within this app."
                    print(f"  [MONITOR] Long stay: {time_on_window:.1f}min on {last_relevant_window[:40]}")

                    decision = await anchor_agent_decide_async(event_summary)
                    if decision["action"] != "stay_silent":
                        await nudge_and_speak(decision)
                    relevant_window_start = time.time()

            if last_title and last_title in classification_cache:
                last_verdict = classification_cache[last_title].get("verdict")
                if last_verdict == "drift" and relevant_window_start is None:
                    if not session_state.get("sustained_drift_start"):
                        session_state["sustained_drift_start"] = time.time()

                    sustained_minutes = (time.time() - session_state["sustained_drift_start"]) / 60

                    if sustained_minutes >= 0.5 and not session_state.get("sustained_drift_nudged"):  # 30 seconds
                        task = session_state.get("task", "your task")
                        parts = [p.strip() for p in last_title.split(" - ") if p.strip().lower() not in ("google chrome", "mozilla firefox", "safari", "microsoft edge", "electron")]
                        app = parts[0][:30] if parts else last_title[:30]
                        print(f"  [MONITOR] Sustained drift: {sustained_minutes:.1f}min on {app}")
                        sustained_msgs = [
                            f"You've been on {app} for a while. {task} is waiting.",
                            f"Still on {app}? Let's get back to {task}.",
                            f"Been on {app} for {sustained_minutes:.0f} minutes. Ready to switch back?",
                        ]
                        message = random.choice(sustained_msgs)
                        decision = {"action": "speak", "message": message, "options": ["Pull me back", "Taking a break"]}
                        await nudge_and_speak(decision, {"drift_count": session_state.get("drift_count", 0)})
                        session_state["sustained_drift_nudged"] = True

            activity_type = session_state.get("task_context", {}).get("activity_type", "mixed")
            idle_thresholds = {
                "writing": 1, "coding": 3, "browsing": 1, "reading": 7, "mixed": 1,
            }
            idle_threshold = idle_thresholds.get(activity_type, 3)

            idle_minutes = (time.time() - last_activity_time) / 60
            if idle_minutes >= idle_threshold and session_state.get("ever_on_task") and not session_state.get("idle_nudged"):
                print(f"  [MONITOR] Silent drift: {idle_minutes:.1f}min idle")
                silent_msgs = [
                    "Your screen has been quiet. Still with me?",
                    "Haven't seen you move in a bit. Need a break?",
                    "Still there? Just checking in.",
                ]
                message = random.choice(silent_msgs)
                decision = {"action": "ask", "message": message, "options": ["I'm here", "Taking a break"]}
                await nudge_and_speak(decision, {"nudge_type": "silent_drift"})
                session_state["idle_nudged"] = True

            if not session_state.get("ever_on_task") and elapsed >= 0.083 and not session_state.get("waiting_for_response"):  # ~5 seconds, skip if waiting for unsure answer
                last_initiation_nudge = session_state.get("last_initiation_nudge_time", 0)
                time_since_last = time.time() - last_initiation_nudge if last_initiation_nudge else 999

                if time_since_last >= 30:  # Re-nudge every 30 seconds if user still hasn't started
                    nudge_count = session_state.get("initiation_nudge_count", 0)
                    task = session_state.get("task", "your task")

                    # Check if user is on Anchor screen
                    is_on_anchor = "anchor" in title.lower() or "stay focused" in title.lower()

                    # Short, casual messages -- context-aware, natural phrasing
                    if is_on_anchor and nudge_count == 0:
                        first_nudges = [
                            f"I see you're still here with me. Let's get started -- {task}!",
                            f"You're on Anchor, great! Time to switch and start {task}.",
                            f"Ready to go? Let's do this -- {task}!",
                        ]
                    else:
                        first_nudges = [
                            f"Hey! Time to start {task}.",
                            f"Let's go -- {task}!",
                            f"Ready? {task} is waiting for you.",
                        ]
                    second_nudges = [
                        f"Still haven't started. Just {task} -- that's all.",
                        f"Starting is the hard part. Just begin.",
                        f"One step. Start {task}. That's it.",
                    ]
                    third_nudges = [
                        f"What's blocking you on {task}?",
                        f"Just look at it for 10 seconds. No pressure.",
                        f"Don't even start yet. Just open it.",
                    ]
                    later_nudges = [
                        f"Still here? {task}. I believe in you.",
                        f"One tiny move toward {task}. That's enough.",
                        f"Come on, you got this. {task}.",
                    ]
                    if nudge_count == 0:
                        message = random.choice(first_nudges)
                    elif nudge_count == 1:
                        message = random.choice(second_nudges)
                    elif nudge_count == 2:
                        message = random.choice(third_nudges)
                    else:
                        message = random.choice(later_nudges)

                    print(f"  [MONITOR] Task initiation: {elapsed}min, reminder #{nudge_count + 1}")

                    # Re-check ever_on_task before speaking
                    if not session_state.get("ever_on_task"):
                        decision = {"action": "speak", "message": message}
                        await nudge_and_speak(decision, {"nudge_type": "task_initiation", "options": ["I'm ready"]})
                    session_state["last_initiation_nudge_time"] = time.time()
                    session_state["initiation_nudge_count"] = nudge_count + 1

            last_break = session_state.get("last_break_time", session_state.get("start_time", time.time()))
            minutes_since_break = (time.time() - last_break) / 60
            if minutes_since_break >= 40 and session_state.get("ever_on_task"):
                event_summary = f"User has been focused for {minutes_since_break:.0f} minutes without a break. Possible hyperfocus."
                print(f"  [MONITOR] Hyperfocus: {minutes_since_break:.0f}min without break")

                decision = await anchor_agent_decide_async(event_summary)
                if decision["action"] != "stay_silent":
                    await nudge_and_speak(decision, {"nudge_type": "suggest_break", "options": ["Take a break", "Keep going"]})
                session_state["last_break_time"] = time.time()

        # ── Phone detection via webcam ──────────────────────────────
        current_activity = latest_activity.get("activity", "")
        current_confidence = latest_activity.get("confidence", 0)
        time_since_phone_nudge = time.time() - last_phone_nudge_time

        if (current_activity in ("phone", "phone_scrolling") and current_confidence >= 0.75
                and not session_state.get("break_active")
                and not session_state.get("waiting_for_response")
                and time_since_phone_nudge > 20):
            phone_detect_count += 1
            if phone_detect_count >= 1:
                print(f"  [MONITOR] Phone detected! (confidence: {current_confidence:.0%}, count: {phone_detect_count})")
                add_observation(f"Phone usage detected via webcam (confidence {current_confidence:.0%})", "phone_detected")

                phone_messages = [
                    f"Hey, looks like you picked up your phone. Your task '{session_state.get('task', 'work')}' is waiting for you!",
                    f"Phone break? No worries — but let's get back to '{session_state.get('task', 'work')}' when you're ready.",
                    f"I see you're on your phone. Want to get back to '{session_state.get('task', 'work')}'?",
                ]
                message = random.choice(phone_messages)

                await speak(message)
                await broadcast({
                    "type": "phone_detected",
                    "message": message,
                    "source": "phone",
                    "options": ["I'm back", "Taking a break"]
                })
                last_phone_nudge_time = time.time()
                phone_detect_count = 0
        else:
            if current_activity != "phone":
                phone_detect_count = 0

        await asyncio.sleep(5)

    print("[MONITOR] Monitoring loop stopped.")


# ============================================================
# SESSION SUMMARY
# ============================================================
def build_session_summary() -> dict:
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
        "drift_count": session_state.get("total_drift_count", session_state.get("drift_count", 0)),
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
    """Returns current frame as PNG for debugging"""
    if latest_jpeg_frame is None:
        return {"error": "No frame available yet"}
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
    global session_active, session_state, observation_history, classification_cache, monitoring_task

    if session_active:
        return {"error": "Session already active. End it first."}

    print(f"\n[SESSION] Starting: '{request.task}' for {request.duration} min")

    # Load user profile from past sessions (Pattern Agent)
    threading.Thread(target=analyze_patterns, daemon=True).start()

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


# ============================================================
# PATTERN AGENT (post-session cross-session learning)
# ============================================================
try:
    from supermemory import Supermemory
    supermemory_client = Supermemory(api_key=os.getenv("SUPERMEMORY_API_KEY")) if os.getenv("SUPERMEMORY_API_KEY") else None
    if supermemory_client:
        print("[PATTERN] Supermemory connected.")
except Exception:
    supermemory_client = None

user_profile = {}  # Loaded at session start, updated at session end


def save_session_to_supermemory(summary: dict):
    """Save session log to Supermemory for cross-session pattern analysis."""
    if not supermemory_client:
        return
    try:
        content = json.dumps({
            "task": summary.get("task", ""),
            "date": summary.get("session_date", ""),
            "day": summary.get("day_of_week", ""),
            "time": summary.get("time_of_day", ""),
            "total_min": summary.get("total_time_min", 0),
            "focused_min": summary.get("focused_time_min", 0),
            "drifts": summary.get("drift_count", 0),
            "nudges": summary.get("nudge_count", 0),
            "top_trigger": summary.get("top_drift_trigger", ""),
            "streak_min": summary.get("longest_streak_min", 0),
        })
        supermemory_client.add(
            content=f"ADHD Anchor session log: {content}",
            container_tags=["adhd-anchor-sessions"],
            metadata={"type": "session_log", "task": summary.get("task", "")}
        )
        print(f"  [PATTERN] Session saved to Supermemory")
    except Exception as e:
        print(f"  [PATTERN] Save error: {repr(e)[:60]}")


def analyze_patterns():
    """Read past sessions from Supermemory and discover patterns using Gemini."""
    global user_profile
    if not supermemory_client:
        return
    try:
        results = supermemory_client.search.execute(
            q="ADHD Anchor session log",
            container_tags=["adhd-anchor-sessions"],
            limit=20
        )
        if not results or not hasattr(results, 'results') or not results.results:
            print("  [PATTERN] No past sessions found.")
            return

        sessions_text = "\n".join([
            r.content[:300] if hasattr(r, 'content') else str(r)[:300]
            for r in results.results
        ])

        prompt = f"""Analyze these ADHD focus session logs and find patterns.

SESSION LOGS:
{sessions_text}

Return a JSON profile with:
- "avg_focus_min": average focused minutes per session
- "common_drift_triggers": list of top 3 apps/sites that cause drifts
- "best_time_of_day": when they focus best (morning/afternoon/evening)
- "focus_by_task_type": object mapping task types to avg focus minutes
- "self_correction_rate": how often they return to task without nudge (0-1)
- "recommended_break_interval": suggested minutes before proactive break
- "tips": list of 2-3 personalized ADHD tips based on their patterns

Return ONLY JSON."""

        response = gemini_client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        text = response.text.strip().replace("```json", "").replace("```", "").strip()
        user_profile = json.loads(text)
        print(f"  [PATTERN] Profile updated: {json.dumps(user_profile)[:100]}")
    except Exception as e:
        print(f"  [PATTERN] Analysis error: {repr(e)[:60]}")


@app.post("/session/end")
async def end_session():
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

    # Pattern Agent: save session and analyze in background
    threading.Thread(target=save_session_to_supermemory, args=(summary,), daemon=True).start()
    threading.Thread(target=analyze_patterns, daemon=True).start()

    # HMM: save session sequence and retrain
    def _hmm_update():
        anchor_hmm.save_session_sequence(observation_history)
        anchor_hmm.train()
    threading.Thread(target=_hmm_update, daemon=True).start()

    await broadcast({"type": "session_ended", "summary": summary})
    return {"status": "session_ended", "summary": summary}


@app.get("/session/status")
async def get_session_status():
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
