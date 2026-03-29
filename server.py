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

    # Skip Anchor's own windows -- these are never task-relevant
    anchor_keywords = ["anchor", "adhd-anchor", "stay focused"]
    if any(kw in window_title.lower() for kw in anchor_keywords):
        result = {"verdict": "unsure", "confidence": 0.5, "reason": "Anchor app itself -- not the user's task"}
        return result

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


async def anchor_agent_decide_async(new_event_summary: str) -> dict:
    """Async wrapper so monitoring loop doesn't block."""
    return await asyncio.to_thread(anchor_agent_decide, new_event_summary)


def anchor_agent_decide(new_event_summary: str) -> dict:
    """LangChain agent with 6 tools. Falls back to direct Gemini if LangChain fails."""

    add_observation(new_event_summary)

    # Progressive cooldown -- gets longer each time you ignore nudges
    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    recent_nudges = [o for o in observation_history
                     if o["type"] == "nudge"
                     and o["elapsed_min"] >= elapsed - 5]
    nudge_count_recent = len(recent_nudges)

    if nudge_count_recent >= 4:
        cooldown = 30
    elif nudge_count_recent >= 3:
        cooldown = 20
    elif nudge_count_recent >= 2:
        cooldown = 15
    else:
        cooldown = 10

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
# VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"  # Roger - Laid-Back, Casual
# VOICE_ID = "jqcCZkN6Knx8BJ5TBdYR"
VOICE_ID = "XcXEQzuLXRU9RcfWzEJt"

current_voice_process = None  # Track the afplay process so we can kill it

def stop_speaking():
    """Stop any currently playing voice immediately."""
    global current_voice_process
    if current_voice_process and current_voice_process.poll() is None:
        current_voice_process.kill()
        print("  [VOICE] Stopped (user self-corrected)", flush=True)
        current_voice_process = None

async def speak(message: str):
    """Generate and play voice in a background thread -- NEVER blocks the event loop.
    Can be cancelled by calling stop_speaking() when user returns to task."""
    global current_voice_process
    def _generate_and_play():
        global current_voice_process
        try:
            # Stop any currently playing voice to prevent overlap
            stop_speaking()
            print(f'\nSpeaking: "{message}"', flush=True)
            audio = elevenlabs_client.text_to_speech.convert(
                text=message,
                voice_id=VOICE_ID,
                model_id="eleven_multilingual_v2",
                output_format="mp3_44100_128",
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
    # Fire and forget in background thread
    threading.Thread(target=_generate_and_play, daemon=True).start()


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


agent_executor = create_anchor_agent()
print("[AGENT] LangChain agent with 6 tools created.")


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


async def nudge_and_speak(decision: dict, extra_broadcast: dict = None):
    """Broadcast nudge to frontend first (so Smiski shows up), then speak via ElevenLabs.
    This keeps the visual and audio in sync -- Smiski appears while voice plays."""
    nudge_event = {
        "type": "nudge",
        "nudge_type": decision.get("action", "speak"),
        "message": decision.get("message", ""),
        "options": decision.get("options", []),
    }
    if extra_broadcast:
        nudge_event.update(extra_broadcast)
    # Broadcast FIRST so Smiski walks in immediately
    try:
        await broadcast(nudge_event)
    except Exception as e:
        print(f"  [BROADCAST ERROR] {repr(e)}")
    # Then speak (voice plays while Smiski is visible) -- with timeout so it doesn't hang
    if not decision.get("already_spoken") and decision.get("message"):
        try:
            await asyncio.wait_for(speak(decision["message"]), timeout=15)
        except asyncio.TimeoutError:
            print("  [VOICE] Timed out after 15s")
        except Exception as e:
            print(f"  [VOICE ERROR in nudge_and_speak] {repr(e)}")


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
                add_observation("User started a quick break", "break_started")
                await broadcast({"type": "break_started", "duration": 60})

            elif action == "skip_break" or action == "im_ready":
                session_state["break_active"] = False
                session_state["drift_count"] = 0
                add_observation("User skipped break / is ready", "user_response")
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

                    text_lower = user_text.lower()
                    comeback_phrases = [
                        "come back", "i'm back", "okay", "got it", "i'll focus",
                        "back to work", "on it", "let me focus", "sorry", "my bad",
                        "i will come back", "yeah", "yes", "fine", "alright",
                    ]
                    is_comeback = any(phrase in text_lower for phrase in comeback_phrases)

                    if is_comeback:
                        print(f'  [VOICE] User acknowledged distraction: "{user_text}" — noted')
                        add_observation(
                            f'User acknowledged distraction and committed to return: "{user_text}"',
                            "user_response",
                        )
                        await broadcast({"type": "status", "value": "focused"})

                    event_summary = f'User spoke via voice: "{user_text}". Respond to what they said in context of the session.'
                    if is_comeback:
                        event_summary += (
                            " The user is acknowledging they got distracted and wants to come back."
                            " Be encouraging and brief — don't lecture."
                        )
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
            if break_duration >= 60:  # 1 minute (demo mode)
                session_state["break_active"] = False
                session_state["drift_count"] = 0  # Reset drift count after break
                add_observation("Break ended", "break_ended")
                await broadcast({"type": "break_ended"})
                task = session_state.get("task", "your task")
                encouragement_messages = [
                    f"Great reset! Your brain just got a fresh tank of focus fuel. What's the ONE small thing you'll do first on {task}? Just that one thing, nothing else.",
                    f"Welcome back! That break just gave your brain what it needed. You've got this. Pick the easiest part of {task} and start there. Momentum builds fast once you begin.",
                    f"Break done! Fun fact: ADHD research shows 3 minutes of movement restores more focus than 30 minutes of scrolling. You just did the smart thing. Now, what's one tiny step on {task}?",
                ]
                message = encouragement_messages[int(time.time()) % len(encouragement_messages)]
                decision = {"action": "speak", "message": message, "options": ["Let's go"]}
                await nudge_and_speak(decision)

        # During breaks, still monitor but don't nudge for drifts (they're on break)
        # If they skip break via button, break_active will be set to False

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
                session_state["drift_count"] += 1
            elif verdict == "relevant":
                session_state["ever_on_task"] = True
                session_state["initiation_nudge_count"] = 0

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
                add_observation(f"Window: {title} --> relevant ({confidence:.0%})")
                await broadcast({"type": "status", "value": "focused"})
                print(f"  [MONITOR] [{elapsed}m] RELEVANT: {title[:60]}")

            elif verdict == "drift":
                add_observation(f"Window: {title} --> drift ({confidence:.0%}). Reason: {reason}.")
                print(f"  [MONITOR] [{elapsed}m] DRIFT: {title[:60]}", flush=True)

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
                            f"You've drifted {drift_count} times now. Your brain needs a reset. Take a quick break -- stretch, grab water. Stay off your phone!",
                            f"Hey, {drift_count} drifts. Your brain is running low on fuel. Quick 5-minute reset -- walk around, get fresh air.",
                            f"You keep getting pulled away. Take 5 minutes -- jumping jacks or water. Physical breaks restore focus better than willpower.",
                        ]
                        message = random.choice(break_messages)
                        print(f"  [MONITOR] Drift #{drift_count} — suggesting break", flush=True)
                        session_state["break_active"] = True
                        session_state["break_start"] = time.time()
                        decision = {"action": "suggest_break", "message": message, "options": ["Take a break", "Keep going"]}
                        await nudge_and_speak(decision, {"drift_count": drift_count, "nudge_type": "suggest_break"})
                        await broadcast({"type": "break_started", "duration": 60})
                    else:
                        drift_messages = [
                            f"You just switched to {window_short}. That's not your task. Ready to get back to {task}?",
                            f"I see you're on {window_short} now. Your task is {task} -- want to jump back?",
                            f"Looks like {window_short} pulled you away from {task}. Break or get back?",
                        ]
                        message = random.choice(drift_messages)
                        print(f"  [MONITOR] Drift #{drift_count}: {window_short} — instant nudge", flush=True)
                        decision = {"action": "speak", "message": message, "options": ["Pull me back", "Taking a break"]}
                        await nudge_and_speak(decision, {"drift_count": drift_count})
                    await broadcast({"type": "status", "value": "drifted"})
                except Exception as e:
                    print(f"  [DRIFT ERROR] {repr(e)}", flush=True)
                    import traceback
                    traceback.print_exc()

            elif verdict == "unsure":
                add_observation(f"Window: {title} --> unsure ({confidence:.0%}). Reason: {reason}.")
                print(f"  [MONITOR] [{elapsed}m] UNSURE: {title[:60]}")

            last_title = title

        else:
            # Same window -- check timeouts

            if relevant_window_start and last_relevant_window:
                time_on_window = (time.time() - relevant_window_start) / 60
                long_stay_apps = ["claude", "chatgpt", "youtube", "openai"]
                is_long_stay = any(app in last_relevant_window.lower() for app in long_stay_apps)

                if is_long_stay and time_on_window >= 3:  # 3 min (demo)
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

                    if sustained_minutes >= 0.25 and not session_state.get("sustained_drift_nudged"):  # 15 seconds (demo)
                        event_summary = f"User has been on drift app '{last_title}' for {sustained_minutes:.1f} minutes without leaving."
                        print(f"  [MONITOR] Sustained drift: {sustained_minutes:.1f}min on {last_title[:40]}")

                        decision = await anchor_agent_decide_async(event_summary)
                        if decision["action"] != "stay_silent":
                            print(f"  [AGENT] SUSTAINED DRIFT: {decision['message']}")
                            await nudge_and_speak(decision)
                        session_state["sustained_drift_nudged"] = True

            activity_type = session_state.get("task_context", {}).get("activity_type", "mixed")
            idle_thresholds = {
                "writing": 1, "coding": 3, "browsing": 1, "reading": 7, "mixed": 1,
            }
            idle_threshold = idle_thresholds.get(activity_type, 3)

            idle_minutes = (time.time() - last_activity_time) / 60
            if idle_minutes >= idle_threshold and session_state.get("ever_on_task") and not session_state.get("idle_nudged"):
                event_summary = f"User has the correct window open but has had NO keyboard or mouse activity for {idle_minutes:.1f} minutes (threshold for {activity_type} task: {idle_threshold} min)."
                print(f"  [MONITOR] Silent drift: {idle_minutes:.1f}min idle")

                decision = await anchor_agent_decide_async(event_summary)
                if decision["action"] != "stay_silent":
                    print(f"  [AGENT] SILENT DRIFT: {decision['message']}")
                    await nudge_and_speak(decision, {"nudge_type": "silent_drift", "options": ["I'm here", "Taking a break"]})
                session_state["idle_nudged"] = True

            if not session_state.get("ever_on_task") and elapsed >= 0.083:  # ~5 seconds
                last_initiation_nudge = session_state.get("last_initiation_nudge_time", 0)
                time_since_last = time.time() - last_initiation_nudge if last_initiation_nudge else 999

                if time_since_last >= 15:  # Re-nudge every 15 seconds (demo)
                    nudge_count = session_state.get("initiation_nudge_count", 0)
                    task = session_state.get("task", "your task")

                    # Fast instant responses -- no slow agent call
                    if nudge_count == 0:
                        message = f"Hey! Ready to start {task}? Let's open it up. 3, 2, 1, go!"
                    elif nudge_count == 1:
                        message = f"Still haven't started {task}. Getting started is the hardest part with ADHD. Just open the app -- that's your only job right now."
                    elif nudge_count == 2:
                        message = f"It's been a while. What's making it hard to start {task}? Sometimes just doing the tiniest first step helps break the paralysis."
                    else:
                        message = f"You've been putting off {task} for a bit. That's okay -- ADHD makes starting really hard. How about just looking at it for 30 seconds? No pressure to do anything."

                    print(f"  [MONITOR] Task initiation: {elapsed}min, reminder #{nudge_count + 1}")

                    # Re-check ever_on_task before speaking
                    if not session_state.get("ever_on_task"):
                        decision = {"action": "speak", "message": message}
                        await nudge_and_speak(decision, {"nudge_type": "task_initiation", "options": ["I'm ready"]})
                    session_state["last_initiation_nudge_time"] = time.time()
                    session_state["initiation_nudge_count"] = nudge_count + 1

            last_break = session_state.get("last_break_time", session_state.get("start_time", time.time()))
            minutes_since_break = (time.time() - last_break) / 60
            if minutes_since_break >= 5 and session_state.get("ever_on_task"):  # 5 min (demo)
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

        if (current_activity == "phone" and current_confidence >= 0.7
                and not session_state.get("break_active")
                and time_since_phone_nudge > 30):
            phone_detect_count += 1
            # Give them a moment — only nudge after 2+ consecutive detections
            if phone_detect_count >= 2:
                print(f"  [MONITOR] Phone detected! (confidence: {current_confidence:.0%}, count: {phone_detect_count})")
                add_observation(f"Phone usage detected via webcam (confidence {current_confidence:.0%})", "phone_detected")

                phone_messages = [
                    f"Hey, looks like you picked up your phone. Your task '{session_state.get('task', 'work')}' is waiting for you!",
                    f"Phone break? No worries — but let's get back to '{session_state.get('task', 'work')}' when you're ready.",
                    f"I see you're on your phone. Want to get back to '{session_state.get('task', 'work')}'?",
                ]
                message = random.choice(phone_messages)

                stop_speaking()
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

        await asyncio.sleep(3)

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
