"""
Anchor - Window Title Reader Test (macOS)
Run this first to check if permissions work.
If it fails, go to System Preferences > Privacy & Security > Accessibility
and add your Terminal/IDE to the allowed list.
"""

import subprocess
import time

def get_active_window_title():
    """Get the currently active window title on macOS"""
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
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return result.stdout.strip()
        else:
            return f"ERROR: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return "ERROR: Script timed out"
    except Exception as e:
        return f"ERROR: {str(e)}"

# Test it - prints your active window every 3 seconds
if __name__ == "__main__":
    print("=" * 50)
    print("ANCHOR - Window Title Reader Test")
    print("=" * 50)
    print("Switch between different apps and watch the output.")
    print("Press Ctrl+C to stop.\n")
    
    try:
        while True:
            title = get_active_window_title()
            timestamp = time.strftime("%H:%M:%S")
            print(f"[{timestamp}] Active: {title}")
            time.sleep(3)
    except KeyboardInterrupt:
        print("\nStopped. If you saw window titles above, permissions are working!")
        print("If you saw ERROR messages, check System Preferences > Privacy & Security > Accessibility")