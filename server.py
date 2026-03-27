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
from datetime import datetime
from collections import Counter
from typing import Optional


from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from dotenv import load_dotenv
load_dotenv()

# ============================================================
# INITIALIZE
# ============================================================
app = FastAPI(title="Anchor Backend")

# Allow React frontend (localhost:3000) to talk to this server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, restrict this
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
    duration: int = 60          # minutes
    dnd: bool = False
    expected_notifications: str = ""
    session_type: str = "solid"  # quick_sprint, solid, deep_dive, just_start

class UserResponse(BaseModel):
    action: str  # "pull_me_back", "taking_break", "im_ready", "got_it"

# ============================================================
# SESSION STATE
# ============================================================
session_active = False
session_state = {}
observation_history = []
classification_cache = {}
monitoring_task = None  # asyncio background task


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
            # Some apps don't have window titles, just return app name
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
- "always_ok": apps that are always fine regardless of task (music players, calculator, etc.)

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

    # Check always-ok apps
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

    # Don't call agent if we nudged less than 30 seconds ago
    if session_state.get("last_nudge_time"):
        since_nudge = time.time() - session_state["last_nudge_time"]
        if since_nudge < 30:
            return {"action": "stay_silent", "message": "", "options": [], "reason": "Recently nudged"}

    # Don't intervene during breaks
    if session_state.get("break_active"):
        return {"action": "stay_silent", "message": "", "options": [], "reason": "On break"}

    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    history = get_history_text()

    prompt = f"""You are Anchor, a warm AI body double helping someone stay focused.
Read the full session history and decide what to do RIGHT NOW.

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

FULL SESSION HISTORY:
{history}

LATEST EVENT:
{new_event_summary}

Think through:
1. What is the user's current state?
2. WHY might they be in this state?
3. Should you speak, ask a question, suggest a break, or stay silent?
4. What tone? (gentle, encouraging, direct, playful)

RULES:
- If user is FOCUSED and on a task-relevant app, STAY SILENT. Never interrupt good focus.
- FIRST drift of the ENTIRE session: stay silent, give them a chance to self-correct.
- SECOND drift: speak with a gentle nudge like "Hey, you left your [task] for [app]. Break or get back?"
- THIRD or more drift: you MUST intervene, even if they self-corrected previous times. Say something like "You keep bouncing away. Something blocking you, or do you need a real break?"
- If they've been on a "relevant" app (Claude/ChatGPT/YouTube) for 15+ minutes, gently check in.
- If 3+ minutes passed and they never opened a task-relevant app, that's task initiation paralysis. Help them start.
- If focused 40+ minutes without break, proactively suggest a break.
- If the drift app matches their expected notification source, be extra gentle: "Looks like [app] pulled you out. Take a minute, I'll remind you to come back."
- If the classifier returned "unsure", ASK the user: "Are you using [app] for your task or did you drift?"
- NEVER be accusatory. You're a supportive friend, not a monitor.
- Keep messages to 1-2 sentences MAX.
- DO NOT keep staying silent on repeated drifts. If drift_count >= 2, you MUST speak or ask.

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
# VOICE (ElevenLabs placeholder)
# ============================================================
async def speak(message: str):
    """
    Speak a message via ElevenLabs.
    TODO: Replace with actual ElevenLabs API call on Saturday.
    For now, just logs the message.
    """
    print(f"  [VOICE] Would say: \"{message}\"")
    # Saturday: Person C adds ElevenLabs here
    # import elevenlabs
    # audio = elevenlabs.generate(text=message, voice="warm_voice")
    # elevenlabs.play(audio)


# ============================================================
# WEBSOCKET -- send events to React frontend
# ============================================================
async def broadcast(event: dict):
    """Send an event to all connected React frontends"""
    disconnected = []
    for client in connected_clients:
        try:
            await client.send_json(event)
        except:
            disconnected.append(client)
    for client in disconnected:
        connected_clients.remove(client)


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """React frontend connects here for real-time updates"""
    await websocket.accept()
    connected_clients.append(websocket)
    print(f"Frontend connected. Total clients: {len(connected_clients)}")

    try:
        while True:
            # Listen for messages from frontend (button clicks)
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
                session_state["ever_on_task"] = True
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
                add_observation(f"Window: {title} --> drift ({confidence:.0%}). Reason: {reason}")
                event_summary = f"Window changed to: {title} --> drift ({confidence:.0%}). Reason: {reason}. Total drifts: {session_state['drift_count']}"

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
                add_observation(f"Window: {title} --> unsure ({confidence:.0%}). Reason: {reason}")
                event_summary = f"Window changed to: {title} --> unsure ({confidence:.0%}). Reason: {reason}"

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

            # Long stay on relevant app (15+ min, using 2 min for testing)
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

            # Task initiation (3 min, never on task)
            if not session_state.get("ever_on_task") and elapsed >= 3:
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
                session_state["ever_on_task"] = True  # prevent re-trigger

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
                session_state["last_break_time"] = time.time()  # prevent re-trigger

        await asyncio.sleep(5)

    print("[MONITOR] Monitoring loop stopped.")


# ============================================================
# SESSION SUMMARY
# ============================================================
def build_session_summary() -> dict:
    """Build the end-of-session summary from observation history"""
    total_time = round((time.time() - session_state["start_time"]) / 60, 1)

    # Count events
    drift_observations = [o for o in observation_history
                          if o["type"] == "event" and "drift" in o.get("summary", "").lower()]
    nudge_observations = [o for o in observation_history if o["type"] == "nudge"]

    # Find top drift app
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

    # Calculate longest focus streak
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

    # Build timeline segments
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

    # Build task context
    print(f"\n[SESSION] Starting: '{request.task}' for {request.duration} min")
    task_context = create_task_context(request.task)
    print(f"[SESSION] Context built: {task_context.get('domain', 'unknown')}")

    # Initialize session state
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
        "dnd_enabled": request.dnd
    }

    # Reset history and cache
    observation_history = []
    classification_cache = {}

    add_observation(
        f"Session started: '{request.task}', Duration: {request.duration}min, "
        f"DND: {request.dnd}, Expecting: {request.expected_notifications or 'nothing'}",
        "session_started"
    )

    # Start monitoring in background
    session_active = True
    monitoring_task = asyncio.create_task(monitoring_loop())

    # Notify frontend
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

    # Stop monitoring
    session_active = False
    if monitoring_task:
        monitoring_task.cancel()
        try:
            await monitoring_task
        except asyncio.CancelledError:
            pass

    # Build summary
    summary = build_session_summary()

    print(f"\n[SESSION] Ended. Focus: {summary['focused_time_min']}min, Drifts: {summary['drift_count']}")

    # Notify frontend
    await broadcast({
        "type": "session_ended",
        "summary": summary
    })

    # TODO: Saturday -- send summary to Pattern Agent for cross-session analysis
    # pattern_agent.analyze(summary)

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
    print("=" * 60)

    uvicorn.run(app, host="0.0.0.0", port=8000)