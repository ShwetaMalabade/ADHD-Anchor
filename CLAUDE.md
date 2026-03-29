# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

ADHD Anchor is an AI-powered focus assistant designed to help people with ADHD stay on task. It monitors the user's active screen window, classifies whether each window is task-relevant or a drift, and uses an AI agent to decide when/how to intervene with voice nudges. The system is privacy-first -- webcam frames are processed for landmarks only and never stored.

## Architecture

The system has three main parts:

**Python Backend** (`server.py`) -- FastAPI server that runs the full pipeline:
1. **Window Watcher** -- reads active window title via `osascript` (macOS) or `win32gui` (Windows)
2. **Classifier** -- Gemini 2.5 Flash classifies windows as `relevant`, `drift`, or `unsure` relative to the user's declared task; results cached per window title
3. **LangChain Agent** -- Gemini-powered agent with 6 tools (`speak_to_user`, `ask_user`, `suggest_break`, `chunk_task`, `search_adhd_strategy`, `suggest_dnd`) that reasons over full session history. Falls back to direct Gemini call if LangChain fails.
4. **Voice** -- ElevenLabs TTS (Roger voice) for spoken nudges; ElevenLabs Scribe v1 STT for voice input via `/ws-audio`
5. **Activity Monitor** (`activity_monitor.py`) -- MediaPipe Tasks API (hand/pose/face landmarkers) detects states: `focused`, `typing`, `idle`, `phone`, `phone_scrolling`, `looking_down`, `away`. YOLO disabled due to Metal GPU conflicts on Apple Silicon.
6. **WebSocket** (`/ws`) -- pushes session events to frontend in real-time
7. **Input Tracking** -- pynput keyboard/mouse listeners run in background threads

**React Frontend** (`frontend/`) -- Vite + React + TypeScript + shadcn/ui + Tailwind CSS:
- Session flow: `welcome` → `start` → `dnd` → `focusing` → `break` → `summary` → `done`
- Key page: `src/pages/Index.tsx` -- manages all session state, WebSocket connection, and audio WebSocket
- Components in `src/components/`: `SessionStart`, `DNDPrompt`, `FocusWidget`, `NudgeOverlay`, `BreakTimer`, `TaskInitiationNudge`, `SessionSummary`, `AgoraRoom` (Agora RTC voice), `SmiskiCompanion`

**Chrome Extension** (`chrome-extension/`) -- Browser companion with background service worker, content script injection, and Smiski companion overlay. Wires into backend session state.

**Standalone test scripts**:
- `classifier.py` -- live window classification test
- `anchor_agent.py` -- full pipeline test (watcher + classifier + agent, no server)
- `voice.py` -- ElevenLabs TTS test
- `test_window_reader.py` -- macOS Accessibility permission check

## Commands

### Backend
```bash
pip install -r requirements.txt
python server.py   # http://localhost:8000
```

### Frontend
```bash
cd frontend
npm install        # or: bun install
npm run dev        # Vite dev server at http://localhost:5173
npm run build
npm run lint
npm run test       # Vitest (run once)
npm run test:watch # Vitest (watch mode)
```

## API Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| POST | `/session/start` | Start focus session (`task`, `duration`, `dnd`, `session_type`) |
| POST | `/session/end` | End session, returns summary |
| GET | `/session/status` | Current session state |
| WS | `/ws` | Real-time event stream to frontend |
| WS | `/ws-audio` | Receive mic audio chunks for STT |
| GET | `/video_feed` | MJPEG webcam stream |
| GET | `/activity` | Current activity detection state |
| POST | `/camera/start` / `/camera/stop` | Toggle webcam monitoring |

## Required Environment Variables

Set in `.env` at project root:
- `GEMINI_API_KEY` -- required (Gemini 2.5 Flash for classifier and agent)
- `ELEVENLABS_API_KEY` -- required (TTS/STT)
- `TAVILY_API_KEY` -- optional, enables `search_adhd_strategy` tool

## macOS Permissions

- **Accessibility** (System Settings > Privacy & Security > Accessibility) -- required for window title reading
- **Camera** -- required for activity monitor

## Key Design Decisions

- Window classification uses full title, not just app name ("YouTube - MIT lecture" = relevant; "YouTube - best headphones" = drift)
- Intervention escalation: 1st drift = silent; 2nd drift = gentle nudge; 3rd+ = must use a tool
- Progressive cooldown: 30s → 60s → 120s → 300s to avoid nagging; 3+ nudges in 5 min triggers break suggestion
- Agent is ADHD-aware: models Barkley's fuel tank, task initiation paralysis, hyperfocus risks
