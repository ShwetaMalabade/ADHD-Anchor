"""
Anchor Backend -- FastAPI Server with LangChain Agent
- Window watcher reads your screen
- Classifier judges each window
- LangChain Agent with 6 tools reasons and acts autonomously
- ElevenLabs speaks the nudge
- WebSocket pushes events to React frontend

Run: python server.py
Install: pip install fastapi uvicorn google-genai elevenlabs python-dotenv pynput langchain langchain-google-genai langchain-community tavily-python
"""

import os
import json
import time
import asyncio
import subprocess
import threading
from datetime import datetime
from collections import Counter
from typing import Optional
from elevenlabs.client import ElevenLabs
from elevenlabs.play import play
from pynput import mouse, keyboard as kb

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from dotenv import load_dotenv
load_dotenv()

# LangChain imports
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain.agents import create_tool_calling_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool

# Tavily (optional -- works without it, better with it)
try:
    from langchain_community.tools.tavily_search import TavilySearchResults
    TAVILY_AVAILABLE = os.getenv("TAVILY_API_KEY") is not None
except ImportError:
    TAVILY_AVAILABLE = False


# ============================================================
# ACTIVITY TRACKING (keyboard + mouse via pynput)
# ============================================================
last_activity_time = time.time()

def on_activity(*args):
    global last_activity_time
    last_activity_time = time.time()

mouse.Listener(on_move=on_activity, on_click=on_activity, on_scroll=on_activity).start()
kb.Listener(on_press=on_activity).start()
print("[PYNPUT] Keyboard and mouse listeners started.")


# ============================================================
# INITIALIZE
# ============================================================
app = FastAPI(title="Anchor Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Gemini client (for classifier + fallback agent)
gemini_client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Connected WebSocket clients
connected_clients: list[WebSocket] = []


# ============================================================
# DATA MODELS
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
    script = '''
    tell application "System Events"
        set frontApp to name of first application process whose frontmost is true
        set frontAppName to name of first window of (first application process whose frontmost is true)
        return frontApp & " - " & frontAppName
    end tell
    '''
    try:
        result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=5)
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            fallback = '''
            tell application "System Events"
                return name of first application process whose frontmost is true
            end tell
            '''
            result2 = subprocess.run(["osascript", "-e", fallback], capture_output=True, text=True, timeout=5)
            return result2.stdout.strip() if result2.returncode == 0 else "Unknown"
    except:
        return "Unknown"


# ============================================================
# CLASSIFIER (direct Gemini -- faster for simple classification)
# ============================================================
def create_task_context(task_description: str) -> dict:
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
        response = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
        result_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(result_text)
    except:
        return {"task": task_description, "domain": "general", "likely_tools": [], "likely_sites": [], "activity_type": "mixed", "always_ok": ["Spotify", "Apple Music"]}


def classify_window(task_context: dict, window_title: str, expected_notifications: str = "") -> dict:
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
1. APP NAME alone does NOT determine relevance. The CONTENT in the window title determines relevance.
   "Claude - personal skills assessment" for application task = RELEVANT. "Claude - yhack idea" for application task = DRIFT.
2. Messaging apps (WhatsApp, iMessage, Telegram, Discord) = "unsure" by default.
3. Social media (Twitter, Instagram, Reddit, TikTok) = "drift" unless title content matches task.
4. Shopping sites = "drift" always.
5. AI tools (ChatGPT, Claude) = READ conversation title. Matches task = relevant. Unrelated = drift. No title = unsure.
6. Terminal = "relevant" ONLY if task involves coding. Otherwise "drift".

Return ONLY JSON:
{{"verdict": "relevant" or "drift" or "unsure", "confidence": 0.0 to 1.0, "reason": "brief explanation"}}"""

    try:
        start = time.time()
        response = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
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
# OBSERVATION HISTORY
# ============================================================
def add_observation(summary: str, event_type: str = "event"):
    if not session_state.get("start_time"):
        return
    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    observation_history.append({"time": time.strftime("%H:%M:%S"), "elapsed_min": elapsed, "type": event_type, "summary": summary})


def get_history_text(last_n: int = 25) -> str:
    recent = observation_history[-last_n:]
    return "\n".join(f"[{o['elapsed_min']}m] {o['summary']}" for o in recent)


# ============================================================
# LANGCHAIN AGENT TOOLS (6 tools)
# ============================================================

@tool
def speak_to_user(message: str) -> str:
    """Speak a message to the user via ElevenLabs voice. Use for gentle nudges,
    observations, encouragement. Keep to 1-2 sentences MAX."""
    print(f'\n  [TOOL: speak] "{message}"')
    return f"Spoke to user: {message}"

@tool
def ask_user(question: str) -> str:
    """Ask the user a question via voice. User responds by speaking back through Agora.
    Use when you need input -- unsure apps, checking if stuck, offering choices."""
    print(f'\n  [TOOL: ask] "{question}"')
    return f"Asked user via voice: {question}"

@tool
def suggest_break(duration_minutes: int, activity: str) -> str:
    """Suggest a break with a specific GOOD activity. Use when executive function
    tank is depleted (focused long then crashing), or 3+ nudges ignored.
    ALWAYS suggest physical: stretch, walk, water, fresh air. NEVER phone/social media."""
    print(f'\n  [TOOL: suggest_break] {duration_minutes}min - {activity}')
    return f"Suggested {duration_minutes} min break: {activity}"

@tool
def chunk_task(task_name: str, tiny_next_step: str) -> str:
    """Break user's task into smallest possible next step. Use when overwhelmed
    (never started), stuck (was working then stopped), or avoiding (drifts within 1-2 min).
    Step must be SO small it feels effortless. Forms='fill first field'. Reading='read abstract'.
    Coding='write function signature'. Writing='write one bad sentence'."""
    print(f'\n  [TOOL: chunk_task] {task_name} -> {tiny_next_step}')
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
    print(f'\n  [TOOL: suggest_dnd] {reason}')
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
- 1st drift: NO tools. Stay silent. Chance to self-correct.
- 2nd drift: speak_to_user with gentle nudge.
- 3rd+ drift: MUST use tool. Diagnose and pick right one.
- 3+ nudges in 5 min: ALWAYS suggest_break. Nagging makes ADHD worse.
- Messages MUST be specific to user's actual task. Never generic.
- 1-2 sentences MAX. NEVER repeat same phrasing.
- NEVER mention apps user hasn't visited. Use ACTUAL app names from history.
- If ever_on_task is False: "you're on [actual app] -- ready to open your [task]?"
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


# ============================================================
# ANCHOR AGENT DECIDE (LangChain + fallback)
# ============================================================
def anchor_agent_decide(new_event_summary: str) -> dict:
    """LangChain agent with 6 tools. Falls back to direct Gemini if LangChain fails."""

    add_observation(new_event_summary)

    # Progressive cooldown
    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    recent_nudges = [o for o in observation_history
                     if o["type"] == "nudge"
                     and o["elapsed_min"] >= elapsed - 5]
    nudge_count_recent = len(recent_nudges)

    if nudge_count_recent >= 4:
        cooldown = 300
    elif nudge_count_recent >= 3:
        cooldown = 120
    elif nudge_count_recent >= 2:
        cooldown = 60
    else:
        cooldown = 30

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

SESSION HISTORY:
{history}

LATEST EVENT:
{new_event_summary}

Diagnose why the user is in this state and use the appropriate tool(s).
If the user is focused, do nothing (no tool calls)."""

    try:
        result = agent_executor.invoke({"input": agent_input})
        output = result.get("output", "")
        steps = result.get("intermediate_steps", [])

        if not steps:
            return {"action": "stay_silent", "message": "", "options": [],
                    "reason": "Agent chose not to intervene"}

        # Parse tool calls
        action = "stay_silent"
        message = ""
        options = []
        reason = output

        for step in steps:
            tool_call, tool_result = step
            tool_name = tool_call.tool

            if tool_name == "speak_to_user":
                action = "speak"
                message = tool_call.tool_input.get("message", "")
            elif tool_name == "ask_user":
                action = "ask"
                message = tool_call.tool_input.get("question", "")
            elif tool_name == "suggest_break":
                action = "suggest_break"
                duration = tool_call.tool_input.get("duration_minutes", 3)
                activity = tool_call.tool_input.get("activity", "stretch and walk around")
                message = f"Time for a {duration}-minute reset. {activity}. Try to stay off your phone -- your brain needs actual rest."
            elif tool_name == "chunk_task":
                action = "speak"
                message = tool_call.tool_input.get("tiny_next_step", "")
            elif tool_name == "suggest_dnd":
                action = "ask"
                message = "Notifications keep pulling you away. Want to turn on Do Not Disturb for 15 minutes?"
            # search_adhd_strategy: agent uses findings in next tool call

        if action != "stay_silent" and message:
            session_state["last_nudge_time"] = time.time()
            add_observation(f"Anchor said: \"{message}\"", "nudge")

        return {"action": action, "message": message, "options": options, "reason": reason}

    except Exception as e:
        print(f"  [AGENT ERROR] {e}")
        print(f"  [AGENT] Falling back to direct Gemini...")
        return _agent_fallback(new_event_summary, elapsed, history, nudge_count_recent)


def _agent_fallback(event_summary: str, elapsed: float, history: str, nudge_count_recent: int) -> dict:
    """Fallback: direct Gemini call if LangChain agent fails"""
    prompt = f"""You are Anchor, an ADHD specialist body double. Read the session and decide what to do.

SESSION: Task: {session_state['task']}, Elapsed: {elapsed}min, Drifts: {session_state['drift_count']}, On-task: {session_state['ever_on_task']}, Recent nudges: {nudge_count_recent}

HISTORY:
{history}

EVENT: {event_summary}

Rules: 1st drift=silent, 2nd=speak, 3rd+=must intervene. Task-specific, never generic. 1-2 sentences. Actual app names only. If ever_on_task=False, don't say "you left your task."

Return ONLY JSON: {{"action": "speak" or "ask" or "suggest_break" or "stay_silent", "message": "", "options": [], "reason": ""}}"""

    try:
        response = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=prompt)
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
    except:
        return {"action": "stay_silent", "message": "", "options": [], "reason": "Both agents failed"}


# ============================================================
# VOICE (ElevenLabs)
# ============================================================
elevenlabs_client = ElevenLabs(api_key=os.getenv("ELEVENLABS_API_KEY"))
VOICE_ID = "CwhRBWXzGAHq8TQ4Fs17"  # Roger

async def speak(message: str):
    try:
        print(f'\nSpeaking: "{message}"')
        audio = elevenlabs_client.text_to_speech.convert(
            text=message, voice_id=VOICE_ID,
            model_id="eleven_multilingual_v2", output_format="mp3_44100_128",
        )
        await asyncio.to_thread(play, audio)
        print("Done.")
    except Exception as e:
        print("[VOICE ERROR]", repr(e))


# ============================================================
# WEBSOCKET
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
    await websocket.accept()
    connected_clients.append(websocket)
    print(f"Frontend connected. Total clients: {len(connected_clients)}")
    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action", "")
            if action == "pull_me_back":
                add_observation("User: Pull me back", "user_response")
                await broadcast({"type": "status", "value": "focused"})
            elif action == "taking_break":
                session_state["break_active"] = True
                session_state["break_start"] = time.time()
                add_observation("User started a break", "break_started")
                await broadcast({"type": "break_started", "duration": 300})
            elif action == "im_ready":
                add_observation("User: I'm ready", "user_response")
                await broadcast({"type": "status", "value": "focused"})
            elif action == "got_it":
                add_observation("User: Got it", "user_response")
                await broadcast({"type": "status", "value": "focused"})
    except WebSocketDisconnect:
        connected_clients.remove(websocket)
        print(f"Frontend disconnected. Total clients: {len(connected_clients)}")


# ============================================================
# MONITORING LOOP
# ============================================================
async def monitoring_loop():
    global session_active
    last_title = ""
    last_relevant_window = ""
    relevant_window_start = None
    last_window_change_time = time.time()

    print("\n[MONITOR] Starting monitoring loop...")

    while session_active:
        title = get_active_window_title()
        elapsed = round((time.time() - session_state["start_time"]) / 60, 1)

        # Break end check
        if session_state.get("break_active") and session_state.get("break_start"):
            if time.time() - session_state["break_start"] >= 300:
                session_state["break_active"] = False
                add_observation("Break ended", "break_ended")
                await broadcast({"type": "break_ended"})
                await speak("Break's over. What's the one small thing you'll do first?")
                await broadcast({"type": "nudge", "nudge_type": "speak", "message": "Break's over. What's the one small thing you'll do first?", "options": []})

        if session_state.get("break_active"):
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

            result = classify_window(session_state["task_context"], title, session_state.get("expected_notifications", ""))
            verdict = result["verdict"]
            confidence = result.get("confidence", 0)
            reason = result.get("reason", "")
            cached = result.get("from_cache", False)

            if verdict == "drift":
                session_state["drift_count"] += 1
            elif verdict == "relevant":
                session_state["ever_on_task"] = True

            if verdict == "relevant":
                if title != last_relevant_window:
                    relevant_window_start = time.time()
                    last_relevant_window = title
            else:
                relevant_window_start = None
                last_relevant_window = ""

            await broadcast({"type": "classification", "window": title, "verdict": verdict, "confidence": confidence, "reason": reason, "cached": cached, "drift_count": session_state["drift_count"], "elapsed_min": elapsed})

            if verdict == "relevant":
                add_observation(f"Window: {title} --> relevant ({confidence:.0%})")
                await broadcast({"type": "status", "value": "focused"})
                print(f"  [MONITOR] [{elapsed}m] RELEVANT: {title[:60]}")

            elif verdict == "drift":
                note = " NOTIFICATION PULL." if is_notification_pull else " User deliberately navigated."
                add_observation(f"Window: {title} --> drift ({confidence:.0%}). {reason}.{note}")
                event_summary = f"Window changed to: {title} --> drift ({confidence:.0%}). {reason}. Total drifts: {session_state['drift_count']}.{note}"
                print(f"  [MONITOR] [{elapsed}m] DRIFT: {title[:60]}")
                print(f"  [AGENT] Thinking...")
                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    print(f"  [AGENT] {decision['action'].upper()}: {decision['message']}")
                    await speak(decision["message"])
                    await broadcast({"type": "nudge", "nudge_type": decision["action"], "message": decision["message"], "options": decision.get("options", []), "drift_count": session_state["drift_count"]})
                    await broadcast({"type": "status", "value": "drifted"})
                else:
                    print(f"  [AGENT] Staying silent: {decision['reason']}")

            elif verdict == "unsure":
                note = " NOTIFICATION PULL." if is_notification_pull else ""
                add_observation(f"Window: {title} --> unsure ({confidence:.0%}). {reason}.{note}")
                event_summary = f"Window changed to: {title} --> unsure ({confidence:.0%}). {reason}.{note}"
                print(f"  [MONITOR] [{elapsed}m] UNSURE: {title[:60]}")
                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    print(f"  [AGENT] {decision['action'].upper()}: {decision['message']}")
                    await speak(decision["message"])
                    await broadcast({"type": "nudge", "nudge_type": decision["action"], "message": decision["message"], "options": decision.get("options", []), "drift_count": session_state["drift_count"]})

            last_title = title

        else:
            # Same window -- timeout checks

            # Long stay on relevant app (15+ min)
            if relevant_window_start and last_relevant_window:
                time_on_window = (time.time() - relevant_window_start) / 60
                if any(app in last_relevant_window.lower() for app in ["claude", "chatgpt", "youtube", "openai"]):
                    if time_on_window >= 15:
                        event_summary = f"User on '{last_relevant_window}' for {time_on_window:.1f} min. May have drifted within app."
                        print(f"  [MONITOR] Long stay: {time_on_window:.1f}min on {last_relevant_window[:40]}")
                        decision = anchor_agent_decide(event_summary)
                        if decision["action"] != "stay_silent":
                            await speak(decision["message"])
                            await broadcast({"type": "nudge", "nudge_type": decision["action"], "message": decision["message"], "options": decision.get("options", [])})
                        relevant_window_start = time.time()

            # Sustained drift (1+ min on drift app)
            if last_title and last_title in classification_cache:
                if classification_cache[last_title].get("verdict") == "drift" and relevant_window_start is None:
                    if not session_state.get("sustained_drift_start"):
                        session_state["sustained_drift_start"] = time.time()
                    sustained_min = (time.time() - session_state["sustained_drift_start"]) / 60
                    if sustained_min >= 1 and not session_state.get("sustained_drift_nudged"):
                        event_summary = f"User on drift app '{last_title}' for {sustained_min:.1f} min without leaving."
                        print(f"  [MONITOR] Sustained drift: {sustained_min:.1f}min on {last_title[:40]}")
                        decision = anchor_agent_decide(event_summary)
                        if decision["action"] != "stay_silent":
                            print(f"  [AGENT] SUSTAINED DRIFT: {decision['message']}")
                            await speak(decision["message"])
                            await broadcast({"type": "nudge", "nudge_type": decision["action"], "message": decision["message"], "options": decision.get("options", [])})
                        session_state["sustained_drift_nudged"] = True

            # Silent drift (no activity)
            activity_type = session_state.get("task_context", {}).get("activity_type", "mixed")
            idle_threshold = {"writing": 1, "coding": 3, "browsing": 1, "reading": 7, "mixed": 1}.get(activity_type, 3)
            idle_min = (time.time() - last_activity_time) / 60
            if idle_min >= idle_threshold and session_state.get("ever_on_task") and not session_state.get("idle_nudged"):
                event_summary = f"NO keyboard or mouse activity for {idle_min:.1f} min (threshold: {idle_threshold}min for {activity_type})."
                print(f"  [MONITOR] Silent drift: {idle_min:.1f}min idle ({activity_type})")
                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    print(f"  [AGENT] SILENT DRIFT: {decision['message']}")
                    await speak(decision["message"])
                    await broadcast({"type": "nudge", "nudge_type": "silent_drift", "message": decision["message"], "options": []})
                session_state["idle_nudged"] = True

            # Task initiation (1 min, never on task)
            if not session_state.get("ever_on_task") and not session_state.get("task_initiation_nudged") and elapsed >= 1:
                event_summary = f"Session for {elapsed} min, NEVER opened task-relevant app. Task initiation paralysis."
                print(f"  [MONITOR] Task initiation: {elapsed}min, never on task")
                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    await speak(decision["message"])
                    await broadcast({"type": "nudge", "nudge_type": "task_initiation", "message": decision["message"], "options": []})
                session_state["task_initiation_nudged"] = True

            # Hyperfocus (40+ min)
            last_break = session_state.get("last_break_time", session_state.get("start_time", time.time()))
            min_since_break = (time.time() - last_break) / 60
            if min_since_break >= 40 and session_state.get("ever_on_task"):
                event_summary = f"Focused {min_since_break:.0f} min without break. Possible hyperfocus."
                print(f"  [MONITOR] Hyperfocus: {min_since_break:.0f}min without break")
                decision = anchor_agent_decide(event_summary)
                if decision["action"] != "stay_silent":
                    await speak(decision["message"])
                    await broadcast({"type": "nudge", "nudge_type": "suggest_break", "message": decision["message"], "options": []})
                session_state["last_break_time"] = time.time()

        await asyncio.sleep(5)

    print("[MONITOR] Monitoring loop stopped.")


# ============================================================
# SESSION SUMMARY
# ============================================================
def build_session_summary() -> dict:
    total_time = round((time.time() - session_state["start_time"]) / 60, 1)
    drift_obs = [o for o in observation_history if o["type"] == "event" and "drift" in o.get("summary", "").lower()]
    nudge_obs = [o for o in observation_history if o["type"] == "nudge"]

    drift_apps = []
    for d in drift_obs:
        s = d.get("summary", "")
        if "Window:" in s:
            drift_apps.append(s.split("Window:")[1].split("-->")[0].strip().split("-")[-1].strip())

    top = Counter(drift_apps).most_common(1)
    top_trigger = top[0][0] if top else "None"
    top_count = top[0][1] if top else 0

    longest_streak = 0
    streak_start = None
    for obs in observation_history:
        if obs["type"] == "event":
            if "relevant" in obs.get("summary", "").lower():
                if streak_start is None: streak_start = obs["elapsed_min"]
            elif "drift" in obs.get("summary", "").lower():
                if streak_start is not None:
                    longest_streak = max(longest_streak, obs["elapsed_min"] - streak_start)
                    streak_start = None
    if streak_start: longest_streak = max(longest_streak, total_time - streak_start)

    timeline, current_type, seg_start = [], "focused", 0
    for obs in observation_history:
        if obs["type"] == "event":
            if "drift" in obs.get("summary", "").lower() and current_type != "drift":
                timeline.append({"start": seg_start, "end": obs["elapsed_min"], "type": current_type})
                seg_start, current_type = obs["elapsed_min"], "drift"
            elif "relevant" in obs.get("summary", "").lower() and current_type != "focused":
                timeline.append({"start": seg_start, "end": obs["elapsed_min"], "type": current_type})
                seg_start, current_type = obs["elapsed_min"], "focused"
        elif obs["type"] == "break_started":
            timeline.append({"start": seg_start, "end": obs["elapsed_min"], "type": current_type})
            seg_start, current_type = obs["elapsed_min"], "break"
        elif obs["type"] == "break_ended":
            timeline.append({"start": seg_start, "end": obs["elapsed_min"], "type": current_type})
            seg_start, current_type = obs["elapsed_min"], "focused"
    timeline.append({"start": seg_start, "end": total_time, "type": current_type})

    return {
        "total_time_min": total_time,
        "focused_time_min": round(sum(s["end"] - s["start"] for s in timeline if s["type"] == "focused"), 1),
        "drift_count": session_state.get("drift_count", 0),
        "nudge_count": len(nudge_obs),
        "longest_streak_min": round(longest_streak, 1),
        "top_drift_trigger": top_trigger,
        "top_drift_trigger_count": top_count,
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
    global session_active, session_state, observation_history, classification_cache, monitoring_task
    if session_active:
        return {"error": "Session already active. End it first."}

    print(f"\n[SESSION] Starting: '{request.task}' for {request.duration} min")
    task_context = create_task_context(request.task)
    print(f"[SESSION] Context built: {task_context.get('domain', 'unknown')}")

    session_state = {
        "task": request.task, "task_context": task_context,
        "start_time": time.time(), "duration_minutes": request.duration,
        "drift_count": 0, "last_nudge_time": None,
        "break_active": False, "break_start": None, "last_break_time": time.time(),
        "ever_on_task": False, "expected_notifications": request.expected_notifications,
        "dnd_enabled": request.dnd, "task_initiation_nudged": False,
        "sustained_drift_start": None, "sustained_drift_nudged": False, "idle_nudged": False,
    }
    observation_history = []
    classification_cache = {}
    add_observation(f"Session started: '{request.task}', Duration: {request.duration}min, DND: {request.dnd}, Expecting: {request.expected_notifications or 'nothing'}", "session_started")

    session_active = True
    monitoring_task = asyncio.create_task(monitoring_loop())
    await broadcast({"type": "session_started", "task": request.task, "duration": request.duration, "task_context": task_context})
    return {"status": "session_started", "task": request.task, "task_context": task_context, "duration": request.duration}

@app.post("/session/end")
async def end_session():
    global session_active, monitoring_task
    if not session_active:
        return {"error": "No active session"}
    session_active = False
    if monitoring_task:
        monitoring_task.cancel()
        try: await monitoring_task
        except asyncio.CancelledError: pass
    summary = build_session_summary()
    print(f"\n[SESSION] Ended. Focus: {summary['focused_time_min']}min, Drifts: {summary['drift_count']}")
    await broadcast({"type": "session_ended", "summary": summary})
    return {"status": "session_ended", "summary": summary}

@app.get("/session/status")
async def get_session_status():
    if not session_active:
        return {"active": False}
    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    return {"active": True, "task": session_state.get("task", ""), "elapsed_min": elapsed,
            "duration_min": session_state.get("duration_minutes", 0),
            "drift_count": session_state.get("drift_count", 0),
            "ever_on_task": session_state.get("ever_on_task", False),
            "break_active": session_state.get("break_active", False)}


# ============================================================
# RUN
# ============================================================
if __name__ == "__main__":
    import uvicorn
    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: Add GEMINI_API_KEY to .env")
        exit(1)
    print("=" * 60)
    print("ANCHOR BACKEND (LangChain Agent + 6 Tools)")
    print("=" * 60)
    print("Tools: speak_to_user, ask_user, suggest_break,")
    print("       chunk_task, search_adhd_strategy, suggest_dnd")
    print("Endpoints:")
    print("  POST /session/start  -- start a focus session")
    print("  POST /session/end    -- end session, get summary")
    print("  GET  /session/status -- check current status")
    print("  WS   /ws             -- real-time updates to frontend")
    print("=" * 60)
    uvicorn.run(app, host="0.0.0.0", port=8000)
