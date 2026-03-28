import { useState, useEffect, useRef } from "react";
import { AnimatePresence } from "framer-motion";
import BackgroundBlob from "@/components/BackgroundBlob";
import WelcomeScreen from "@/components/WelcomeScreen";
import SessionStart from "@/components/SessionStart";
import DNDPrompt from "@/components/DNDPrompt";
import FocusWidget from "@/components/FocusWidget";
import NudgeOverlay from "@/components/NudgeOverlay";
import TaskInitiationNudge from "@/components/TaskInitiationNudge";
import BreakTimer from "@/components/BreakTimer";
import SessionSummary from "@/components/SessionSummary";
import AgoraRoom from "@/components/AgoraRoom";

const BACKEND_URL = "http://localhost:8000";
const WS_URL = "ws://localhost:8000/ws";

type Screen = "welcome" | "start" | "dnd" | "focusing" | "break" | "summary" | "done";
type FocusStatus = "focused" | "checking" | "drifted";
type NudgeInfo = {
  type: "none" | "drift" | "notification" | "ask" | "suggest_break" | "silent_drift" | "task_initiation" | "initiation";
  message: string;
  options: string[];
};

const Index = () => {
  const [screen, setScreen] = useState<Screen>("welcome");
  const [task, setTask] = useState("");
  const [durationMin, setDurationMin] = useState(60);
  const [elapsed, setElapsed] = useState(0);
  const [isPaused, setIsPaused] = useState(false);
  const [focusStatus, setFocusStatus] = useState<FocusStatus>("focused");
  const [nudge, setNudge] = useState<NudgeInfo>({ type: "none", message: "", options: [] });
  const [driftCount, setDriftCount] = useState(0);
  const [driftTriggers, setDriftTriggers] = useState<string[]>([]);
  const [timeline, setTimeline] = useState<{ minute: number; focused: boolean; type?: "focus" | "drift" | "break" }[]>([]);
  const [longestStreak, setLongestStreak] = useState(0);
  const [summaryData, setSummaryData] = useState<any>(null);
  const currentStreak = useRef(0);
  const wsRef = useRef<WebSocket | null>(null);

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

  // Connect WebSocket when focusing
  useEffect(() => {
    if (screen !== "focusing") return;

    const ws = new WebSocket(WS_URL);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log("[WS] Connected to backend");
    };

    ws.onmessage = (event) => {
      const data = JSON.parse(event.data);
      console.log("[WS] Received:", data);

      switch (data.type) {
        case "status":
          if (data.value === "focused") {
            setFocusStatus("focused");
          } else if (data.value === "drifted") {
            setFocusStatus("drifted");
          }
          break;

        case "classification":
          if (data.verdict === "drift") {
            setFocusStatus("checking");
            setDriftCount(data.drift_count || 0);
            const app = data.window?.split(" - ")?.[0] || "another app";
            setDriftTriggers((prev) => [...prev, app]);
          } else if (data.verdict === "relevant") {
            setFocusStatus("focused");
          }
          break;

        case "nudge":
          setFocusStatus("drifted");
          const nudgeType = data.nudge_type || "drift";
          setNudge({
            type: nudgeType === "speak" ? "drift"
              : nudgeType === "ask" ? "ask"
              : nudgeType === "suggest_break" ? "suggest_break"
              : nudgeType === "silent_drift" ? "silent_drift"
              : nudgeType === "task_initiation" ? "task_initiation"
              : "drift",
            message: data.message || "",
            options: data.options || [],
          });
          break;

        case "break_started":
          setScreen("break");
          break;

        case "break_ended":
          setFocusStatus("focused");
          setScreen("focusing");
          break;

        case "session_ended":
          if (data.summary) {
            setSummaryData(data.summary);
          }
          setScreen("summary");
          break;
      }
    };

    ws.onclose = () => {
      console.log("[WS] Disconnected");
    };

    return () => {
      ws.close();
      wsRef.current = null;
    };
  }, [screen]);

  // Send action to backend via WebSocket
  const sendAction = (action: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action }));
    }
  };

  const handleStartSession = async (t: string, d: number) => {
    setTask(t);
    setDurationMin(d);
    setScreen("dnd");
  };

  const handleDNDContinue = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/session/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          task,
          duration: durationMin,
          dnd: false,
          expected_notifications: "",
        }),
      });
      const data = await res.json();
      console.log("[SESSION] Started:", data);
    } catch (e) {
      console.error("[SESSION] Failed to start:", e);
    }
    setScreen("focusing");
  };

  const handlePullBack = () => {
    setNudge({ type: "none", message: "", options: [] });
    setFocusStatus("focused");
    sendAction("pull_me_back");
  };

  const handleTakeBreak = () => {
    setNudge({ type: "none", message: "", options: [] });
    sendAction("taking_break");
    setScreen("break");
  };

  const handleDismiss = () => {
    setNudge({ type: "none", message: "", options: [] });
    setFocusStatus("focused");
    sendAction("got_it");
  };

  const handleBreakEnd = () => {
    setFocusStatus("focused");
    setScreen("focusing");
  };

  const handleEndSession = async () => {
    try {
      const res = await fetch(`${BACKEND_URL}/session/end`, { method: "POST" });
      const data = await res.json();
      console.log("[SESSION] Ended:", data);
      if (data.summary) {
        setSummaryData(data.summary);
      }
    } catch (e) {
      console.error("[SESSION] Failed to end:", e);
    }
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
    setNudge({ type: "none", message: "", options: [] });
    setDriftCount(0);
    setDriftTriggers([]);
    setTimeline([]);
    setLongestStreak(0);
    setSummaryData(null);
    currentStreak.current = 0;
  };

  const trigger = topTrigger();

  const finalTimeline = summaryData?.timeline?.length > 0
    ? summaryData.timeline.map((seg: any, i: number) => ({
        minute: i + 1,
        focused: seg.type === "focused",
        type: seg.type === "focused" ? "focus" as const : seg.type === "break" ? "break" as const : "drift" as const,
      }))
    : timeline.length > 0 ? timeline : [
        ...Array.from({ length: 25 }, (_, i) => ({ minute: i + 1, focused: true, type: "focus" as const })),
        { minute: 26, focused: false, type: "drift" as const },
        ...Array.from({ length: 12 }, (_, i) => ({ minute: 27 + i, focused: true, type: "focus" as const })),
        { minute: 39, focused: false, type: "drift" as const },
        { minute: 40, focused: false, type: "drift" as const },
        ...Array.from({ length: 38 }, (_, i) => ({ minute: 41 + i, focused: true, type: "focus" as const })),
        { minute: 79, focused: false, type: "drift" as const },
        ...Array.from({ length: 23 }, (_, i) => ({ minute: 80 + i, focused: true, type: "focus" as const })),
      ];

  return (
    <div className="min-h-screen bg-background">
      <BackgroundBlob />
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
              {nudge.type !== "none" && nudge.type !== "initiation" && (
                <NudgeOverlay
                  key="nudge"
                  type={nudge.type as any}
                  message={nudge.message}
                  options={nudge.options}
                  onTakeBreak={handleTakeBreak}
                  onPullBack={handlePullBack}
                  onDismiss={handleDismiss}
                />
              )}
              {nudge.type === "initiation" && (
                <TaskInitiationNudge
                  key="init-nudge"
                  onReady={() => {
                    setNudge({ type: "none", message: "", options: [] });
                    sendAction("im_ready");
                  }}
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
              totalFocusedSeconds: summaryData ? Math.round(summaryData.focused_time_min * 60) : elapsed || 6120,
              totalSessionSeconds: summaryData ? Math.round(summaryData.total_time_min * 60) : elapsed || 7200,
              driftCount: summaryData?.drift_count ?? driftCount ?? 4,
              longestStreakMinutes: summaryData ? Math.round(summaryData.longest_streak_min) : longestStreak || 38,
              topDriftTrigger: summaryData?.top_drift_trigger || trigger.name || "YouTube",
              topDriftTriggerCount: summaryData?.top_drift_trigger_count || trigger.count || 3,
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
