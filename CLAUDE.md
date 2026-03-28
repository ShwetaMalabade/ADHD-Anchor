# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ADHD Anchor is an AI-powered focus assistant designed to help people with ADHD stay on task. It monitors the user's active screen window, classifies whether each window is task-relevant or a drift, and uses an AI agent to decide when/how to intervene with voice nudges. The system is privacy-first -- webcam frames are processed for landmarks only and never stored.

## Architecture

The system has two main parts:

**Python Backend** (`server.py`) -- FastAPI server that runs the full pipeline:
1. **Window Watcher** -- reads macOS active window title via `osascript` (AppleScript)
2. **Classifier** -- Gemini 2.5 Flash classifies windows as `relevant`, `drift`, or `unsure` relative to the user's declared task
3. **LangChain Agent** -- Gemini-powered agent with 6 tools (`speak_to_user`, `ask_user`, `suggest_break`, `chunk_task`, `search_adhd_strategy`, `suggest_dnd`) that reasons over full session history to decide interventions
4. **Voice** -- ElevenLabs TTS for spoken nudges
5. **Activity Monitor** (`activity_monitor.py`) -- MediaPipe-based webcam activity detection (typing, idle, phone, away) using hand/pose/face landmarks
6. **WebSocket** -- pushes events to the React frontend in real-time
7. **Input Tracking** -- pynput for keyboard/mouse activity detection

**React Frontend** (`frontend/`) -- Vite + React + TypeScript + shadcn/ui + Tailwind CSS:
- Single-page app with session flow: Start -> DND prompt -> Focusing -> Break -> Summary
- Components: `SessionStart`, `DNDPrompt`, `FocusWidget`, `NudgeOverlay`, `BreakTimer`, `SessionSummary`, `AgoraRoom` (voice)
- Currently simulates drift events client-side; connects to backend via WebSocket

**Standalone scripts** (for testing individual pipeline stages):
- `classifier.py` -- live classifier test (watches screen, classifies windows)
- `anchor_agent.py` -- full pipeline test without server (watcher + classifier + agent)
- `voice.py` -- ElevenLabs voice output test
- `test_window_reader.py` -- permission check for macOS window reading

## Commands

### Backend
```bash
# Install Python dependencies
pip install fastapi uvicorn google-genai elevenlabs python-dotenv pynput langchain langchain-google-genai langchain-community tavily-python

# Run the server
python server.py
```

### Frontend
```bash
cd frontend
npm install        # or: bun install
npm run dev        # Vite dev server
npm run build      # Production build
npm run lint       # ESLint
npm run test       # Vitest (run once)
npm run test:watch # Vitest (watch mode)
```

## Required Environment Variables

Set in `.env` at project root:
- `GEMINI_API_KEY` -- required for classifier and agent (Gemini 2.5 Flash)
- `ELEVENLABS_API_KEY` -- required for voice output
- `TAVILY_API_KEY` -- optional, enables the `search_adhd_strategy` tool

## macOS Permissions

The window watcher requires **Accessibility** permission for your Terminal/IDE in System Settings > Privacy & Security > Accessibility. The activity monitor requires **Camera** permission.

## Key Design Decisions

- Window classification uses content/title analysis, not just app name (e.g., "YouTube - MIT lecture" can be relevant)
- The agent uses progressive cooldown for nudges (30s -> 60s -> 120s -> 300s) to avoid nagging
- First drift is always silent (chance to self-correct); intervention escalates from 2nd drift onward
- Classification results are cached per window title to avoid redundant API calls
- The agent is ADHD-aware: understands executive function depletion, task initiation paralysis, hyperfocus risks
