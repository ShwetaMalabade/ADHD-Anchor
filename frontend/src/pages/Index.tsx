import { useState, useEffect, useCallback, useRef } from "react";
import { AnimatePresence } from "framer-motion";
import BackgroundBlob from "@/components/BackgroundBlob";
import SessionStart from "@/components/SessionStart";
import DNDPrompt from "@/components/DNDPrompt";
import FocusWidget from "@/components/FocusWidget";
import TaskInitiationNudge from "@/components/TaskInitiationNudge";
import BreakTimer from "@/components/BreakTimer";
import SessionSummary from "@/components/SessionSummary";
import AgoraRoom from "@/components/AgoraRoom";
import SmiskiCompanion from "@/components/SmiskiCompanion";
import WelcomeScreen from "@/components/WelcomeScreen";
import DemoLog, { type LogEntry, type NoteEntry } from "@/components/DemoLog";

type Screen = "welcome" | "start" | "dnd" | "focusing" | "break" | "summary" | "done";
type FocusStatus = "focused" | "checking" | "drifted";
type NudgeType = "none" | "drift" | "notification" | "initiation";

const BACKEND_URL = "http://localhost:8000";
const DRIFT_SOURCES = ["YouTube", "Reddit", "Twitter", "Instagram", "TikTok"];

const Index = () => {
  const [screen, setScreen] = useState<Screen>("welcome");
  const [task, setTask] = useState("");
  const [durationMin, setDurationMin] = useState(60);
  const [elapsed, setElapsed] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [focusStatus, setFocusStatus] = useState<FocusStatus>("focused");
  const [nudge, setNudge] = useState<NudgeType>("none");
  const [activeNudge, setActiveNudge] = useState<{ text: string; id: number } | null>(null);
  const nudgeCounter = useRef(0);
  const [driftCount, setDriftCount] = useState(0);
  const [driftTriggers, setDriftTriggers] = useState<string[]>([]);
  const [timeline, setTimeline] = useState<{ minute: number; focused: boolean; type?: "focus" | "drift" | "break" }[]>([]);
  const [longestStreak, setLongestStreak] = useState(0);
  const currentStreak = useRef(0);
  const driftTimer = useRef<ReturnType<typeof setTimeout>>();
  const wsRef = useRef<WebSocket | null>(null);
  const wsConnected = useRef(false);
  const [sessionLog, setSessionLog] = useState<LogEntry[]>([]);
  const logIdCounter = useRef(0);
  const elapsedRef = useRef(0);
  const [notes, setNotes] = useState<NoteEntry[]>([]);
  const noteIdCounter = useRef(0);
  const [isListeningForNotes, setIsListeningForNotes] = useState(false);
  const [noteEvent, setNoteEvent] = useState<{ text: string; id: number } | null>(null);
  const buddyMode = useRef<"idle" | "awaiting_note" | "awaiting_reply">("idle");
  const [buddyPromptEvent, setBuddyPromptEvent] = useState<{ id: number } | null>(null);
  const buddyPromptCounter = useRef(0);
  const [buddyAckEvent, setBuddyAckEvent] = useState<{ text: string; id: number } | null>(null);
  const buddyAckCounter = useRef(0);

  useEffect(() => { elapsedRef.current = elapsed; }, [elapsed]);

  const addLog = (type: LogEntry["type"], icon: string, message: string) => {
    logIdCounter.current += 1;
    const ts = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
    const sec = elapsedRef.current;
    const elapsedStr = sec >= 60 ? `${Math.floor(sec / 60)}m ${sec % 60}s elapsed` : `${sec}s elapsed`;
    setSessionLog((prev) => [...prev, { id: logIdCounter.current, timestamp: ts, elapsed: elapsedStr, type, icon, message }]);
  };

  const addNote = (text: string) => {
    noteIdCounter.current += 1;
    const ts = new Date().toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });
    const id = noteIdCounter.current;
    setNotes((prev) => [...prev, { id, timestamp: ts, text }]);
    addLog("response", "\u{1F4DD}", `Note saved: "${text}"`);
    setNoteEvent({ text, id });
  };

  const deleteNote = (id: number) => {
    setNotes((prev) => prev.filter((n) => n.id !== id));
  };

  // Voice-triggered note-taking during focus sessions
  useEffect(() => {
    if (screen !== "focusing" || isPaused) {
      setIsListeningForNotes(false);
      return;
    }

    const SpeechRecognition = (window as unknown as Record<string, unknown>).SpeechRecognition
      || (window as unknown as Record<string, unknown>).webkitSpeechRecognition;
    if (!SpeechRecognition) return;

    type SREvent = { results: { transcript: string; isFinal?: boolean }[][]; resultIndex: number };
    const recognition = new (SpeechRecognition as new () => {
      continuous: boolean;
      interimResults: boolean;
      onresult: ((e: SREvent) => void) | null;
      onerror: ((e: { error: string }) => void) | null;
      onend: (() => void) | null;
      start: () => void;
      stop: () => void;
    })();
    recognition.continuous = true;
    recognition.interimResults = false;

    let alive = true;

    recognition.onresult = (e: SREvent) => {
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const transcript = e.results[i][0].transcript.toLowerCase().trim();
        const raw = e.results[i][0].transcript.trim();

        // "hey buddy" trigger — Smiski walks in and asks what to note
        if (buddyMode.current === "idle" && /hey\s+buddy/i.test(transcript)) {
          buddyMode.current = "awaiting_note";
          buddyPromptCounter.current += 1;
          setBuddyPromptEvent({ id: buddyPromptCounter.current });
          addLog("response", "\u{1F44B}", "User: \"Hey buddy!\"");
          continue;
        }

        // Awaiting note content — capture whatever they say as the note
        if (buddyMode.current === "awaiting_note") {
          const noteText = raw.charAt(0).toUpperCase() + raw.slice(1);
          addNote(noteText);
          buddyMode.current = "awaiting_reply";
          continue;
        }

        // Awaiting follow-up reply — acknowledge and go back to idle
        if (buddyMode.current === "awaiting_reply") {
          buddyMode.current = "idle";
          buddyAckCounter.current += 1;
          setBuddyAckEvent({ text: raw, id: buddyAckCounter.current });
          addLog("response", "\u{1F4AC}", `User replied: "${raw}"`);
          continue;
        }

        // Direct trigger: "note <content>", "please note <content>", "hey buddy note <content>"
        const match = transcript.match(/(?:hey\s+buddy\s+)?(?:please\s+)?note\s+(?:that\s+)?(.+)/);
        if (match && match[1]) {
          const noteMatch = raw.match(/(?:[Hh]ey\s+[Bb]uddy\s+)?(?:[Pp]lease\s+)?[Nn]ote\s+(?:[Tt]hat\s+)?(.+)/);
          const noteText = noteMatch ? noteMatch[1] : match[1];
          addNote(noteText.charAt(0).toUpperCase() + noteText.slice(1));
        }
      }
    };

    recognition.onerror = (e: { error: string }) => {
      if (e.error === "no-speech" || e.error === "aborted") return;
      console.warn("[NOTES] Speech recognition error:", e.error);
    };

    recognition.onend = () => {
      if (alive) {
        try { recognition.start(); } catch {}
      }
    };

    try {
      recognition.start();
      setIsListeningForNotes(true);
    } catch {
      setIsListeningForNotes(false);
    }

    return () => {
      alive = false;
      setIsListeningForNotes(false);
      try { recognition.stop(); } catch {}
    };
  }, [screen, isPaused]);

  // Timer
  useEffect(() => {
    if (screen !== "focusing" || isPaused) return;
    const t = setInterval(() => setElapsed((e) => e + 1), 1000);
    return () => clearInterval(t);
  }, [screen, isPaused]);

  // Record timeline every minute
  useEffect(() => {
    if (screen !== "focusing" || isPaused) return;
    if (elapsed > 0 && elapsed % 60 === 0) {
      const minute = Math.floor(elapsed / 60);
      const isFocused = focusStatus === "focused";
      setTimeline((prev) => [...prev, { minute, focused: isFocused, type: isFocused ? "focus" : "drift" }]);
      if (isFocused) {
        currentStreak.current += 1;
        setLongestStreak((prev) => Math.max(prev, currentStreak.current));
      } else {
        currentStreak.current = 0;
      }
    }
  }, [elapsed, screen, isPaused, focusStatus]);

  // WebSocket — connect when session starts, handle backend events
  useEffect(() => {
    if (screen !== "focusing") {
      wsConnected.current = false;
      return;
    }
    let ws: WebSocket;
    try {
      ws = new WebSocket("ws://localhost:8000/ws");
      ws.onopen = () => {
        wsConnected.current = true;
        wsRef.current = ws;
      };
      ws.onmessage = (e) => {
        try {
          const data = JSON.parse(e.data);

          if (data.type === "nudge" || data.type === "phone_detected") {
            setFocusStatus("drifted");
            if (data.drift_count != null) {
              setDriftCount(data.drift_count);
            }
            nudgeCounter.current += 1;
            const msg = data.message || "Hey, you drifted. Break or get back?";
            setActiveNudge({ text: msg, id: nudgeCounter.current });
            addLog("nudge", "\u{1F5E3}\uFE0F", `Anchor: "${msg}"`);
          } else if (data.type === "status") {
            setFocusStatus(data.value);
            const statusIcon = data.value === "focused" ? "\u2705" : data.value === "drifted" ? "\u{1F6A8}" : "\u{1F7E1}";
            addLog("status", statusIcon, `Status \u2192 ${data.value}`);
          } else if (data.type === "classification") {
            const verdictIcon = data.verdict === "relevant" ? "\u2705" : data.verdict === "drift" ? "\u{1F6A8}" : "\u{1F7E1}";
            const conf = data.confidence != null ? ` (${Math.round(data.confidence * 100)}%)` : "";
            addLog("window", verdictIcon, `${data.window || "Unknown window"} \u2192 ${data.verdict}${conf}`);
            if (data.verdict === "relevant") {
              everOnTaskRef.current = true;
            } else if (data.verdict === "drift") {
              if (data.drift_count != null) setDriftCount(data.drift_count);
              const appName = (data.window || "").split(" - ")[0].trim() || "Unknown";
              setDriftTriggers((prev) => [...prev, appName]);
            }
          } else if (data.type === "break_started") {
            setNudge("none");
            setActiveNudge(null);
            setScreen("break");
            addLog("break", "\u2615", "Break started (5 min)");
          } else if (data.type === "break_ended") {
            setFocusStatus("focused");
            setScreen("focusing");
            addLog("break", "\u23F0", "Break ended \u2014 back to work");
          } else if (data.type === "session_ended") {
            if (driftTimer.current) clearTimeout(driftTimer.current);
            addLog("session", "\u{1F3C1}", "Session ended");
            setScreen("summary");
          } else if (data.type === "wave_detected") {
            nudgeCounter.current += 1;
            const msg = data.message || "Hey! Great to see you! Ready to crush this session?";
            setActiveNudge({ text: msg, id: nudgeCounter.current });
            addLog("nudge", "\u{1F44B}", msg);
          }
        } catch {}
      };
      ws.onerror = () => { wsConnected.current = false; };
      ws.onclose = () => { wsConnected.current = false; wsRef.current = null; };
    } catch {}
    return () => {
      wsConnected.current = false;
      wsRef.current = null;
      ws?.close();
    };
  }, [screen]);

  // Simulate drift events — fallback when WebSocket isn't connected
  useEffect(() => {
    if (screen !== "focusing" || isPaused) return;
    const scheduleDrift = () => {
      const delay = 45000 + Math.random() * 90000;
      driftTimer.current = setTimeout(() => {
        if (wsConnected.current) return;
        const isNotification = Math.random() > 0.6;
        const source = DRIFT_SOURCES[Math.floor(Math.random() * DRIFT_SOURCES.length)];
        setFocusStatus("checking");
        addLog("window", "\u{1F6A8}", `Switched to ${source} \u2192 drift (simulated)`);
        setTimeout(() => {
          setFocusStatus("drifted");
          setDriftCount((c) => c + 1);
          setDriftTriggers((prev) => [...prev, source]);
          nudgeCounter.current += 1;
          const msg = isNotification
            ? `Looks like ${source} pulled you out. Take a minute then come back.`
            : `Hey, you left your task for ${source}. Break or get back?`;
          setActiveNudge({ text: msg, id: nudgeCounter.current });
          setNudge(isNotification ? "notification" : "drift");
          addLog("nudge", "\u{1F5E3}\uFE0F", `Anchor: "${msg}"`);
        }, 3000);
      }, delay);
    };
    scheduleDrift();
    return () => { if (driftTimer.current) clearTimeout(driftTimer.current); };
  }, [screen, isPaused, driftCount]);

  // Show initiation nudge after 15s ONLY if user never opened their task
  // (backend handles this via task_initiation detection, so this is just
  // a fallback for when WebSocket isn't connected)
  const everOnTaskRef = useRef(false);
  useEffect(() => {
    if (screen !== "focusing") return;
    const t = setTimeout(() => {
      if (nudge === "none" && !everOnTaskRef.current && !wsConnected.current) {
        setNudge("initiation");
      }
    }, 15000);
    return () => clearTimeout(t);
  }, [screen]);

  // Send user speech transcript to backend
  const handleUserSpeech = (transcript: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      console.log("[SPEECH] Sending to backend:", transcript);
      wsRef.current.send(JSON.stringify({ action: "user_speech", text: transcript }));
    }
  };

  const handleStartSession = (t: string, d: number) => {
    setTask(t);
    setDurationMin(d);
    setScreen("dnd");
  };

  const handleDNDContinue = (dnd: boolean = true, expectedNotifications: string = "") => {
    fetch(`${BACKEND_URL}/session/start`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        task,
        duration: durationMin,
        dnd,
        expected_notifications: expectedNotifications,
      }),
    }).catch((e) => console.warn("Backend session start failed:", e));
    addLog("session", "\u{1F680}", `Session started: "${task}" (${durationMin} min, DND: ${dnd ? "on" : "off"})`);
    setScreen("focusing");
  };

  const handlePullBack = () => {
    setNudge("none");
    setFocusStatus("focused");
    setActiveNudge(null);
    addLog("response", "\u{1F464}", "User clicked: Pull me back");
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "pull_me_back" }));
    }
  };

  const handleTakeBreak = () => {
    setNudge("none");
    setActiveNudge(null);
    addLog("response", "\u{1F464}", "User clicked: Take a break");
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "taking_break" }));
    }
    setScreen("break");
  };

  const handleBreakEnd = () => {
    setFocusStatus("focused");
    addLog("break", "\u23F0", "Break ended \u2014 resuming focus");
    setScreen("focusing");
  };

  const handleEndSession = () => {
    if (driftTimer.current) clearTimeout(driftTimer.current);
    addLog("session", "\u{1F3C1}", "Session ended by user");
    fetch(`${BACKEND_URL}/session/end`, { method: "POST" }).catch(() => {});
    setScreen("summary");
  };

  const topTrigger = () => {
    const counts: Record<string, number> = {};
    driftTriggers.forEach((t) => { counts[t] = (counts[t] || 0) + 1; });
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    return { name: sorted[0]?.[0] || "", count: sorted[0]?.[1] || 0 };
  };

  const resetSession = () => {
    setScreen("start");
    setTask("");
    setElapsed(0);
    setIsPaused(false);
    setFocusStatus("focused");
    setNudge("none");
    setDriftCount(0);
    setDriftTriggers([]);
    setTimeline([]);
    setLongestStreak(0);
    currentStreak.current = 0;
    setSessionLog([]);
    setNotes([]);
    setNoteEvent(null);
    buddyMode.current = "idle";
    setBuddyPromptEvent(null);
    setBuddyAckEvent(null);
  };

  const finalTimeline = timeline.length > 0 ? timeline : [
    ...Array.from({ length: 25 }, (_, i) => ({ minute: i + 1, focused: true, type: "focus" as const })),
    { minute: 26, focused: false, type: "drift" as const },
    ...Array.from({ length: 12 }, (_, i) => ({ minute: 27 + i, focused: true, type: "focus" as const })),
    { minute: 39, focused: false, type: "drift" as const },
    { minute: 40, focused: false, type: "drift" as const },
    ...Array.from({ length: 38 }, (_, i) => ({ minute: 41 + i, focused: true, type: "focus" as const })),
    { minute: 79, focused: false, type: "drift" as const },
    ...Array.from({ length: 23 }, (_, i) => ({ minute: 80 + i, focused: true, type: "focus" as const })),
  ];

  const trigger = topTrigger();

  return (
    <div className="min-h-screen bg-background">
      <BackgroundBlob />
      {(screen === "focusing" || screen === "break" || screen === "summary") && (
        <DemoLog entries={sessionLog} notes={notes} isListening={isListeningForNotes} onDeleteNote={deleteNote} />
      )}
      <SmiskiCompanion
        nudgeText={activeNudge?.text}
        nudgeId={activeNudge?.id}
        noteEvent={noteEvent}
        buddyPromptEvent={buddyPromptEvent}
        buddyAckEvent={buddyAckEvent}
        onTakeBreak={handleTakeBreak}
        onPullBack={handlePullBack}
        onEndSession={handleEndSession}
        sessionActive={screen === "focusing"}
        suppressMountGreeting={screen === "welcome"}
      />
      <AnimatePresence mode="wait">
        {screen === "welcome" && (
          <WelcomeScreen key="welcome" onComplete={() => setScreen("start")} />
        )}

        {screen === "start" && (
          <SessionStart key="start" onStart={handleStartSession} />
        )}

        {screen === "dnd" && (
          <DNDPrompt key="dnd" onContinue={handleDNDContinue} />
        )}

        {screen === "focusing" && (
          <div key="focusing" className="min-h-screen">
            <AgoraRoom onUserSpeech={handleUserSpeech} />
            <FocusWidget
              task={task}
              status={focusStatus}
              isPaused={isPaused}
              elapsedSeconds={elapsed}
              onPause={() => setIsPaused(true)}
              onResume={() => setIsPaused(false)}
              onEnd={handleEndSession}
            />
            <AnimatePresence>
              {nudge === "initiation" && (
                <TaskInitiationNudge
                  key="init-nudge"
                  onReady={() => setNudge("none")}
                />
              )}
            </AnimatePresence>
          </div>
        )}

        {screen === "break" && (
          <div key="break" className="min-h-screen">
            <BreakTimer onBreakEnd={handleBreakEnd} />
          </div>
        )}

        {screen === "summary" && (
          <SessionSummary
            key="summary"
            data={{
              totalFocusedSeconds: elapsed || 6120,
              totalSessionSeconds: elapsed || 7200,
              driftCount: driftCount || 4,
              longestStreakMinutes: longestStreak || 38,
              topDriftTrigger: trigger.name || "YouTube",
              topDriftTriggerCount: trigger.count || 3,
              avgReturnTimeMinutes: 2,
              timeline: finalTimeline,
            }}
            onNewSession={resetSession}
            onDone={() => { resetSession(); setScreen("done"); }}
          />
        )}

        {screen === "done" && (
          <SessionStart key="done-start" onStart={handleStartSession} />
        )}
      </AnimatePresence>
    </div>
  );
};

export default Index;
