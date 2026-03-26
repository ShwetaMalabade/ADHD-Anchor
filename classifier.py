"""
Anchor -- Live Pipeline Test
Combines the window watcher + classifier running on your REAL screen.
Declare a task, then switch between apps and watch the classifier judge you live.
"""

import os
import json
import time
import subprocess
import google.genai as genai
from dotenv import load_dotenv
load_dotenv()

# Initialize Gemini
client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

# Cache for classifications
classification_cache = {}


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
            # Some apps don't have a window title, just return app name
            fallback_script = '''
            tell application "System Events"
                return name of first application process whose frontmost is true
            end tell
            '''
            result2 = subprocess.run(
                ["osascript", "-e", fallback_script],
                capture_output=True, text=True, timeout=5
            )
            return result2.stdout.strip() if result2.returncode == 0 else "Unknown"
    except:
        return "Unknown"


def create_task_context(task_description: str) -> dict:
    """Build semantic understanding of the task"""
    prompt = f"""A user is about to start a focus session. Their task is: "{task_description}"

Analyze this task and return a JSON object with:
- "task": the task description cleaned up
- "domain": what field/topics this task involves (comma separated)
- "likely_tools": list of apps and tools they might legitimately use
- "likely_sites": list of websites they might legitimately visit
- "activity_type": one of "reading", "writing", "coding", "browsing", "mixed"
- "always_ok": apps that are always fine regardless of task (music players, calculator, etc.)

Return ONLY valid JSON, nothing else. No markdown, no backticks, no explanation."""

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        result_text = response.text.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        return json.loads(result_text)
    except:
        return {
            "task": task_description,
            "domain": "general",
            "likely_tools": [],
            "likely_sites": [],
            "activity_type": "mixed",
            "always_ok": ["Spotify", "Apple Music"]
        }


def classify_window(task_context: dict, window_title: str) -> dict:
    """Classify if a window is relevant to the task"""
    
    # Check cache first
    if window_title in classification_cache:
        cached = classification_cache[window_title].copy()
        cached["from_cache"] = True
        return cached
    
    # Check always-ok apps
    for app in task_context.get("always_ok", []):
        if app.lower() in window_title.lower():
            result = {
                "verdict": "relevant",
                "confidence": 0.99,
                "reason": f"{app} is always allowed"
            }
            classification_cache[window_title] = result
            return result
    
    prompt = f"""You are a focus assistant. A user is working on a task and just switched to a different window.
Decide if this new window is RELEVANT to their task or if they DRIFTED off.

USER'S TASK: {task_context.get('task', '')}
DOMAIN: {task_context.get('domain', '')}
LIKELY TOOLS: {', '.join(task_context.get('likely_tools', []))}
LIKELY SITES: {', '.join(task_context.get('likely_sites', []))}

CURRENT WINDOW: {window_title}

Rules:
- If the window is clearly related to the task domain, return "relevant"
- If the window is clearly unrelated (social media, shopping, entertainment), return "drift"
- If you're not sure, return "unsure"
- ChatGPT, Claude, and AI tools are usually "relevant" for research/writing/coding tasks
- YouTube is "relevant" ONLY if the video title suggests educational content related to the task
- News sites, social media, shopping, gaming are almost always "drift"
- Be generous with "relevant" -- if there's a reasonable chance they need this, call it relevant

Return ONLY a JSON object:
{{"verdict": "relevant" or "drift" or "unsure", "confidence": 0.0 to 1.0, "reason": "brief explanation"}}

No markdown, no backticks, just JSON."""

    try:
        start_time = time.time()
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )
        elapsed = time.time() - start_time
        
        result_text = response.text.strip()
        result_text = result_text.replace("```json", "").replace("```", "").strip()
        result = json.loads(result_text)
        result["latency_ms"] = round(elapsed * 1000)
        result["from_cache"] = False
        
        classification_cache[window_title] = result
        return result
    except:
        return {"verdict": "unsure", "confidence": 0.5, "reason": "error", "from_cache": False}


# ============================================================
# LIVE TEST
# ============================================================
if __name__ == "__main__":
    
    if not os.getenv("GEMINI_API_KEY"):
        print("ERROR: Set your Gemini API key first!")
        print("Run: export GEMINI_API_KEY='your-key-here'")
        exit(1)
    
    print("=" * 60)
    print("ANCHOR -- LIVE PIPELINE TEST")
    print("=" * 60)
    
    # Get task from user
    task = input("\nWhat are you working on? > ")
    
    if not task.strip():
        task = "Reading TPU paper for my Internet Services class"
        print(f"(Using default: '{task}')")
    
    print(f"\nBuilding task context for: '{task}'")
    print("(Calling Gemini...)\n")
    
    context = create_task_context(task)
    print("Task Context:")
    print(f"  Domain: {context.get('domain', 'unknown')}")
    print(f"  Activity: {context.get('activity_type', 'unknown')}")
    print(f"  Likely tools: {', '.join(context.get('likely_tools', []))}")
    print(f"  Always OK: {', '.join(context.get('always_ok', []))}")
    
    print("\n" + "=" * 60)
    print("MONITORING YOUR SCREEN NOW")
    print("Switch between apps and watch the classification!")
    print("Press Ctrl+C to stop.")
    print("=" * 60)
    
    last_title = ""
    drift_count = 0
    session_log = []
    session_start = time.time()
    
    try:
        while True:
            title = get_active_window_title()
            timestamp = time.strftime("%H:%M:%S")
            elapsed_min = round((time.time() - session_start) / 60, 1)
            
            # Only classify when window CHANGES (not every 5 seconds)
            if title != last_title:
                result = classify_window(context, title)
                verdict = result["verdict"].upper()
                confidence = result.get("confidence", 0)
                reason = result.get("reason", "")
                cached = result.get("from_cache", False)
                latency = result.get("latency_ms", 0)
                
                # Pick icon
                if verdict == "RELEVANT":
                    icon = "✅ FOCUSED"
                elif verdict == "DRIFT":
                    icon = "🚨 DRIFT "
                    drift_count += 1
                else:
                    icon = "🟡 UNSURE "
                
                latency_str = "cached" if cached else f"{latency}ms"
                
                print(f"\n[{timestamp}] [{elapsed_min}m] {icon} ({confidence:.0%}) | {latency_str}")
                print(f"  Window: {title}")
                print(f"  Reason: {reason}")
                
                if verdict == "DRIFT":
                    print(f"  >>> DRIFT #{drift_count}! This is where the voice would say:")
                    if drift_count == 1:
                        app = title.split("-")[-1].strip()
                        print(f'  >>> "Hey, you left your notebook for {app}. Break or get back?"')
                    elif drift_count >= 3:
                        print(f'  >>> "You\'ve drifted {drift_count} times. Something might be blocking you. What\'s going on?"')
                    else:
                        app = title.split("-")[-1].strip()
                        print(f'  >>> "You\'re on {app} again. Need a break?"')
                
                # Log it
                session_log.append({
                    "time": timestamp,
                    "elapsed_min": elapsed_min,
                    "window": title,
                    "verdict": result["verdict"],
                    "confidence": confidence
                })
                
                last_title = title
            
            time.sleep(5)
            
    except KeyboardInterrupt:
        # Session summary
        total_events = len(session_log)
        relevant_count = sum(1 for e in session_log if e["verdict"] == "relevant")
        drift_events = [e for e in session_log if e["verdict"] == "drift"]
        
        print("\n\n" + "=" * 60)
        print("SESSION SUMMARY")
        print("=" * 60)
        print(f"Total time: {round((time.time() - session_start) / 60, 1)} minutes")
        print(f"Window switches: {total_events}")
        print(f"On-task: {relevant_count}")
        print(f"Drifts caught: {len(drift_events)}")
        
        if drift_events:
            # Find most common drift app
            drift_apps = [e["window"].split("-")[-1].strip() for e in drift_events]
            from collections import Counter
            top_drift = Counter(drift_apps).most_common(1)[0]
            print(f"Top drift trigger: {top_drift[0]} ({top_drift[1]} times)")
        
        print(f"\nFull session log:")
        for event in session_log:
            icon = "✅" if event["verdict"] == "relevant" else "🚨" if event["verdict"] == "drift" else "🟡"
            print(f"  [{event['time']}] {icon} {event['window'][:60]}")
        
        print("\nSession ended. This is where the Pattern Agent would analyze your data.")