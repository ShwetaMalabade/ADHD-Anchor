"""Calendar tool for Anchor agent -- adds events to macOS Calendar app"""
import subprocess
from langchain_core.tools import tool


def make_calendar_tool(speak_sync_fn):
    """Create the calendar tool with access to speak_sync"""

    @tool
    def add_calendar_event(title: str, date: str, time_str: str, duration_minutes: int = 30, notes: str = "") -> str:
        """Add an event or reminder to the user's macOS Calendar app (syncs with Google/Outlook).
        Use when the user asks to add a reminder, meeting, event, or deadline to their calendar.
        Parse the user's natural language to extract: title, date (YYYY-MM-DD), time (HH:MM 24hr), duration.
        If the user says 'tomorrow', calculate the actual date. If they say '3pm' convert to '15:00'.
        If they don't specify duration, default to 30 min.
        Args:
            title: Event title (e.g. "Team meeting", "Submit assignment")
            date: Date in YYYY-MM-DD format
            time_str: Time in HH:MM 24-hour format (e.g. "14:30" for 2:30 PM)
            duration_minutes: Duration in minutes (default 30)
            notes: Optional notes
        """
        print(f'\n  [TOOL: calendar] Adding: "{title}" on {date} at {time_str} ({duration_minutes}min)')

        safe_title = title.replace('"', '\\"')
        safe_notes = notes.replace('"', '\\"') if notes else ""
        year, month, day = date.split("-")
        hour, minute = time_str.split(":")

        for cal_name in ["Calendar", "Home", "Personal", "Work"]:
            script = f'''
            tell application "Calendar"
                tell calendar "{cal_name}"
                    set startDate to current date
                    set year of startDate to {year}
                    set month of startDate to {month}
                    set day of startDate to {day}
                    set hours of startDate to {hour}
                    set minutes of startDate to {minute}
                    set seconds of startDate to 0
                    set endDate to startDate + ({duration_minutes} * 60)
                    make new event at end with properties {{summary:"{safe_title}", start date:startDate, end date:endDate, description:"{safe_notes}"}}
                end tell
                reload calendars
            end tell
            '''
            try:
                result = subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0:
                    msg = f"Done! I've added '{title}' to your calendar on {date} at {time_str}."
                    speak_sync_fn(msg)
                    return f"Calendar event created: {title} on {date} at {time_str}"
            except:
                continue

        msg = "I couldn't find a calendar to add the event to. Please check your Calendar app."
        speak_sync_fn(msg)
        return "Calendar error: no accessible calendar found"

    return add_calendar_event
