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

type Screen = "welcome" | "start" | "dnd" | "focusing" | "break" | "summary" | "done";
type FocusStatus = "focused" | "checking" | "drifted";
type NudgeType = "none" | "drift" | "notification" | "initiation";

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
            const source = data.source || "something";
            setFocusStatus("drifted");
            setDriftCount((c) => c + 1);
            setDriftTriggers((prev) => [...prev, source]);
            nudgeCounter.current += 1;
            setActiveNudge({
              text: data.message || `Hey, you drifted to ${source}. Break or get back?`,
              id: nudgeCounter.current,
            });
          } else if (data.type === "wave_detected") {
            nudgeCounter.current += 1;
            setActiveNudge({
              text: data.message || "Hey! 👋 Great to see you! Ready to crush this session?",
              id: nudgeCounter.current,
            });
          } else if (data.type === "session_summary") {
            handleEndSession();
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
        if (wsConnected.current) return; // real events take over
        const isNotification = Math.random() > 0.6;
        const source = DRIFT_SOURCES[Math.floor(Math.random() * DRIFT_SOURCES.length)];
        setFocusStatus("checking");
        setTimeout(() => {
          setFocusStatus("drifted");
          setDriftCount((c) => c + 1);
          setDriftTriggers((prev) => [...prev, source]);
          nudgeCounter.current += 1;
          setActiveNudge({
            text: isNotification
              ? `Looks like ${source} pulled you out. Take a minute then come back.`
              : `Hey, you left your task for ${source}. Break or get back?`,
            id: nudgeCounter.current,
          });
          setNudge(isNotification ? "notification" : "drift");
        }, 3000);
      }, delay);
    };
    scheduleDrift();
    return () => { if (driftTimer.current) clearTimeout(driftTimer.current); };
  }, [screen, isPaused, driftCount]);

  // Show initiation nudge after 15s if still focusing
  useEffect(() => {
    if (screen !== "focusing") return;
    const t = setTimeout(() => {
      if (nudge === "none" && focusStatus === "focused") {
        setNudge("initiation");
      }
    }, 15000);
    return () => clearTimeout(t);
  }, [screen]);

  const handleStartSession = (t: string, d: number) => {
    setTask(t);
    setDurationMin(d);
    setScreen("dnd");
  };

  const handleDNDContinue = () => {
    setScreen("focusing");
  };

  const handlePullBack = () => {
    setNudge("none");
    setFocusStatus("focused");
    setActiveNudge(null);
  };

  const handleTakeBreak = () => {
    setNudge("none");
    setActiveNudge(null);
    setScreen("break");
  };

  const handleBreakEnd = () => {
    setFocusStatus("focused");
    setScreen("focusing");
  };

  const handleEndSession = () => {
    if (driftTimer.current) clearTimeout(driftTimer.current);
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
      <SmiskiCompanion
        nudgeText={activeNudge?.text}
        nudgeId={activeNudge?.id}
        onTakeBreak={handleTakeBreak}
        onPullBack={handlePullBack}
        onEndSession={handleEndSession}
        sessionActive={screen === "focusing"}
      />
      <AnimatePresence mode="wait">
        {screen === "welcome" && (
          <WelcomeScreen
            key="welcome"
            onComplete={() => setScreen("start")}
            onGreet={() => {
              nudgeCounter.current += 1;
              setActiveNudge({ text: "Hi! 👋 Come along with me, let's get focused!", id: nudgeCounter.current });
            }}
          />
        )}

        {screen === "start" && (
          <SessionStart key="start" onStart={handleStartSession} />
        )}

        {screen === "dnd" && (
          <DNDPrompt key="dnd" onContinue={handleDNDContinue} />
        )}

        {screen === "focusing" && (
          <div key="focusing" className="min-h-screen">
            <AgoraRoom />
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
