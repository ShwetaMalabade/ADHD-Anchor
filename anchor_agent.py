"""
Anchor -- Full Live Pipeline
Window Watcher --> Classifier --> Anchor Agent (reasoning)

This is the real deal. It watches your ACTUAL screen, classifies every
window switch, and the Anchor Agent reasons about your full session
history to decide when and how to intervene.
"""

import os
import json
import time
import subprocess
from google import genai
from dotenv import load_dotenv
load_dotenv()

# Initialize Gemini
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# ============================================================
# WINDOW WATCHER
# ============================================================
def get_active_window_title():
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
classification_cache = {}

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
        response = client.models.generate_content(
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

    for app in task_context.get("always_ok", []):
        if app.lower() in window_title.lower():
            result = {"verdict": "relevant", "confidence": 0.99, "reason": f"{app} is always allowed"}
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
   - "Claude - personal skills assessment" when task is filling an application = RELEVANT (content matches task)
   - "Claude - Novel AI agent idea for yhack" when task is filling an application = DRIFT (content is about something else entirely)
   - "YouTube - MIT lecture on TPUs" when task is reading TPU paper = RELEVANT (content matches)
   - "YouTube - best headphones 2026" when task is reading TPU paper = DRIFT (content doesn't match)
   
2. Social messaging apps (WhatsApp, iMessage, Telegram, Discord, Facebook Messenger) = "unsure" by default.
   They COULD be task-related but usually aren't. Only classify as "relevant" if the window title contains clear evidence of task-related content.

3. Social media (Twitter, Instagram, Reddit, TikTok, Facebook feed) = "drift" unless the content in the title is clearly related to the task domain.

4. Shopping sites (Amazon, eBay, etc.) = "drift" always.

5. AI tools (ChatGPT, Claude, Gemini) = READ THE CONVERSATION TITLE in the window. If the conversation topic matches the task, "relevant". If it's about something unrelated, "drift". If no conversation title is visible, "unsure".

6. Terminal/command line = "relevant" ONLY if the user's task involves coding. Otherwise "drift".

Return ONLY JSON:
{{"verdict": "relevant" or "drift" or "unsure", "confidence": 0.0 to 1.0, "reason": "brief explanation"}}"""

    try:
        start = time.time()
        response = client.models.generate_content(
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
        return {"verdict": "unsure", "confidence": 0.5, "reason": "error", "from_cache": False}


# ============================================================
# ANCHOR AGENT
# ============================================================
observation_history = []
session_state = {}

def init_session(task, task_context, duration=60, expected_notifications="", dnd=False):
    """Start a new session"""
    global observation_history, session_state
    observation_history = []
    session_state = {
        "task": task,
        "task_context": task_context,
        "start_time": time.time(),
        "duration_minutes": duration,
        "drift_count": 0,
        "last_nudge_time": None,
        "break_active": False,
        "ever_on_task": False,
        "expected_notifications": expected_notifications,
        "dnd_enabled": dnd
    }
    observation_history.append({
        "time": time.strftime("%H:%M:%S"),
        "elapsed_min": 0,
        "type": "session_started",
        "summary": f"Task: '{task}', Duration: {duration}min, DND: {dnd}, Expecting: {expected_notifications or 'nothing'}"
    })


def add_observation(summary: str, event_type: str = "event"):
    """Add an observation to history"""
    elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
    observation_history.append({
        "time": time.strftime("%H:%M:%S"),
        "elapsed_min": elapsed,
        "type": event_type,
        "summary": summary
    })


def get_history_text(last_n=25) -> str:
    """Get formatted history for the agent"""
    recent = observation_history[-last_n:]
    return "\n".join(f"[{o['elapsed_min']}m] {o['summary']}" for o in recent)


def anchor_agent_decide(new_event_summary: str) -> dict:
    """
    THE AGENT. Reads full session history + new event and decides what to do.
    This is the function that makes Anchor an agent, not a script.
    """
    
    # Add the new event to history
    add_observation(new_event_summary)
    
    # Don't call the agent if we nudged less than 30 seconds ago
    if session_state.get("last_nudge_time"):
        since_nudge = time.time() - session_state["last_nudge_time"]
        if since_nudge < 30:
            return {"action": "stay_silent", "message": "", "reason": "Recently nudged, waiting"}
    
    # Don't intervene during breaks
    if session_state.get("break_active"):
        return {"action": "stay_silent", "message": "", "reason": "User is on break"}
    
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
1. What is the user's current state? (focused, drifting, stuck, tired, procrastinating, task initiation paralysis)
2. WHY might they be in this state?
3. Should you speak, ask a question, suggest a break, or stay silent?
4. What tone? (gentle, encouraging, direct, playful)

RULES:
- If user is FOCUSED and on a task-relevant app, STAY SILENT. Never interrupt good focus.
- FIRST drift of the ENTIRE session: stay silent for this one observation, give them a chance to self-correct.
- SECOND drift: speak with a gentle nudge like "Hey, you left your [task] for [app]. Break or get back?"
- THIRD or more drift: you MUST intervene, even if they self-corrected previous times. Repeated drift-and-return is a pattern that means they need help. Say something like "You keep bouncing away. Something blocking you, or do you need a real break?"
- If they've been on a "relevant" app (Claude/ChatGPT/YouTube) for 15+ minutes, gently check in -- they might have drifted WITHIN that app.
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
        response = client.models.generate_content(
            model="gemini-2.5-flash", contents=prompt
        )
        result_text = response.text.strip().replace("```json", "").replace("```", "").strip()
        decision = json.loads(result_text)
        
        if decision.get("action") != "stay_silent":
            session_state["last_nudge_time"] = time.time()
            add_observation(f"Anchor said: \"{decision.get('message', '')}\"", "nudge")
        
        return decision
    except Exception as e:
        return {"action": "stay_silent", "message": "", "reason": f"Agent error: {e}"}


# ============================================================
# LIVE PIPELINE
# ============================================================
if __name__ == "__main__":
    
    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: export GEMINI_API_KEY='your-key-here'")
        exit(1)
    
    print("=" * 60)
    print("ANCHOR -- FULL LIVE PIPELINE")
    print("Watcher --> Classifier --> Agent")
    print("=" * 60)
    
    # Step 1: Get task
    task = input("\nWhat are you working on? > ").strip()
    if not task:
        task = "Reading TPU paper for my Internet Services class"
        print(f"(Using default: '{task}')")
    
    # Step 2: Duration
    duration_input = input("How long? (30/60/120 min, default 60) > ").strip()
    duration = int(duration_input) if duration_input.isdigit() else 60
    
    # Step 3: DND
    dnd_input = input("Turn on Do Not Disturb? (y/n, default n) > ").strip().lower()
    dnd = dnd_input == "y"
    
    # Step 4: Expected notifications
    expected = ""
    if not dnd:
        expected = input("Expecting any notifications? (e.g. 'Slack from Priya', or press Enter for none) > ").strip()
    
    # Step 5: Build context
    print(f"\nBuilding task context...")
    context = create_task_context(task)
    print(f"Domain: {context.get('domain', 'unknown')}")
    print(f"Activity type: {context.get('activity_type', 'unknown')}")
    
    # Step 6: Initialize session
    init_session(task, context, duration, expected, dnd)
    
    print(f"\n{'=' * 60}")
    print("SESSION ACTIVE -- Monitoring your screen now")
    print("Switch between apps and watch the agent reason!")
    print("Press Ctrl+C to end session.")
    print(f"{'=' * 60}\n")
    
    last_title = ""
    last_relevant_window = ""
    relevant_window_start = None
    
    try:
        while True:
            title = get_active_window_title()
            timestamp = time.strftime("%H:%M:%S")
            elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
            
            # Only process when window CHANGES
            if title != last_title:
                result = classify_window(context, title)
                
                # ALWAYS save to history (agent needs this later)
                add_observation(f"Window: {title} --> {result['verdict']}")
                
                if result["verdict"] == "relevant":
                    # Just save, don't call agent
                    # The history is building up silently
                    print(f"✅ RELEVANT - agent not called, saving to history")
                
                elif result["verdict"] == "drift":
                    # NOW call the agent with full history
                    decision = anchor_agent_decide(event_summary)
                    # Agent reads all the "relevant" events too
                    # and makes a smart decision
                
                elif result["verdict"] == "unsure":
                    # Call agent to decide whether to ask user
                    decision = anchor_agent_decide(event_summary)
                
                # Show classification
                icon = "✅" if verdict == "relevant" else "🚨" if verdict == "drift" else "🟡"
                latency_str = "cached" if cached else f"{latency}ms"
                print(f"\n[{timestamp}] [{elapsed}m] {icon} {verdict.upper()} ({confidence:.0%}) | {latency_str}")
                print(f"  Window: {title}")
                print(f"  Classifier: {reason}")
                
                # Build event summary for the agent
                event_summary = f"Window changed to: {title} --> Classified as {verdict} ({confidence:.0%}). Reason: {reason}"
                
                # Ask the agent what to do
                print(f"\n  Agent thinking...")
                decision = anchor_agent_decide(event_summary)
                
                action = decision.get("action", "stay_silent")
                message = decision.get("message", "")
                options = decision.get("options", [])
                agent_reason = decision.get("reason", "")
                
                if action == "stay_silent":
                    print(f"  🤫 Agent: [staying silent]")
                    print(f"     Reason: {agent_reason}")
                elif action == "speak":
                    print(f"  🗣️  Agent SPEAKS: \"{message}\"")
                    print(f"     Reason: {agent_reason}")
                    print(f"     >>> This is where ElevenLabs would say this out loud <<<")
                elif action == "ask":
                    print(f"  ❓ Agent ASKS: \"{message}\"")
                    print(f"     Options: {options}")
                    print(f"     Reason: {agent_reason}")
                    print(f"     >>> This would show as a UI card with buttons <<<")
                elif action == "suggest_break":
                    print(f"  ☕ Agent SUGGESTS BREAK: \"{message}\"")
                    print(f"     Reason: {agent_reason}")
                    print(f"     >>> Break timer would start <<<")
                
                last_title = title
            
            else:
                # Same window -- check for long stay on "relevant" apps
                if relevant_window_start and last_relevant_window:
                    time_on_window = (time.time() - relevant_window_start) / 60
                    
                    # If on Claude/ChatGPT/YouTube for 15+ min, check in
                    long_stay_apps = ["claude", "chatgpt", "youtube", "openai"]
                    is_long_stay_app = any(app in last_relevant_window.lower() for app in long_stay_apps)
                    
                    if is_long_stay_app and time_on_window >= 2:
                        # Using 2 min for testing (would be 15 min in production)
                        event_summary = f"User has been on '{last_relevant_window}' for {time_on_window:.1f} minutes. This was classified as relevant but they might have drifted WITHIN this app."
                        
                        print(f"\n  ⏰ Long stay detected: {time_on_window:.1f}min on {last_relevant_window}")
                        print(f"  Agent thinking...")
                        
                        decision = anchor_agent_decide(event_summary)
                        action = decision.get("action", "stay_silent")
                        message = decision.get("message", "")
                        agent_reason = decision.get("reason", "")
                        
                        if action != "stay_silent":
                            print(f"  🗣️  Agent: \"{message}\"")
                            print(f"     Reason: {agent_reason}")
                        else:
                            print(f"  🤫 Agent: [staying silent]")
                            print(f"     Reason: {agent_reason}")
                        
                        # Reset so we don't keep checking every 5 seconds
                        relevant_window_start = time.time()
                
                # Check for task initiation (3 min passed, never on task)
                if not session_state["ever_on_task"] and elapsed >= 0.5:
                    # Using 0.5 min for testing (would be 3 min in production)
                    event_summary = f"User has been in session for {elapsed} minutes but has NEVER opened a task-relevant app. This looks like task initiation paralysis."
                    
                    print(f"\n  ⚠️  Task initiation check: {elapsed}min elapsed, never on task")
                    print(f"  Agent thinking...")
                    
                    decision = anchor_agent_decide(event_summary)
                    action = decision.get("action", "stay_silent")
                    message = decision.get("message", "")
                    
                    if action != "stay_silent":
                        print(f"  🗣️  Agent: \"{message}\"")
                    
                    # Mark as checked so we don't keep triggering
                    session_state["ever_on_task"] = True  # prevent re-trigger
            
            time.sleep(5)
    
    except KeyboardInterrupt:
        # Session Summary
        elapsed = round((time.time() - session_state["start_time"]) / 60, 1)
        
        drift_events = [o for o in observation_history if "drift" in o.get("summary", "").lower() and o["type"] != "nudge"]
        nudge_events = [o for o in observation_history if o["type"] == "nudge"]
        
        print(f"\n\n{'=' * 60}")
        print("SESSION COMPLETE")
        print(f"{'=' * 60}")
        print(f"Total time: {elapsed} minutes")
        print(f"Drifts detected: {session_state['drift_count']}")
        print(f"Agent interventions: {len(nudge_events)}")
        
        print(f"\nFull session timeline:")
        for o in observation_history:
            icon = "📝"
            if o["type"] == "nudge":
                icon = "🗣️"
            elif "drift" in o.get("summary", "").lower():
                icon = "🚨"
            elif "relevant" in o.get("summary", "").lower() or "focused" in o.get("summary", "").lower():
                icon = "✅"
            print(f"  [{o['elapsed_min']}m] {icon} {o['summary'][:80]}")
        
        print(f"\n{'=' * 60}")
        print("This session log would now be sent to the Pattern Agent")
        print("for cross-session analysis and profile building.")
        print(f"{'=' * 60}")