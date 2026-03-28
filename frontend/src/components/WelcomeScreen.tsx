import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Anchor } from "lucide-react";

interface Props {
  onComplete: () => void;
  onGreet?: () => void;
}

const BACKEND_URL = "http://localhost:8000";

const ACTIVITY_COLORS: Record<string, string> = {
  focused: "#6fcf97",
  typing: "#6fcf97",
  phone: "#eb5757",
  phone_scrolling: "#eb5757",
  looking_down: "#f2994a",
  idle: "#56ccf2",
  away: "#828282",
  looking_away: "#56ccf2",
};

const WelcomeScreen = ({ onComplete, onGreet }: Props) => {
  const [cameraReady, setCameraReady] = useState(false);
  const [cameraError, setCameraError] = useState(false);
  const [greeting, setGreeting] = useState("");
  const [showGreeting, setShowGreeting] = useState(false);
  const [countdown, setCountdown] = useState(10);
  const [fadeOut, setFadeOut] = useState(false);
  const [activity, setActivity] = useState<{ activity: string; confidence: number; details: Record<string, unknown> } | null>(null);
  const [greeted, setGreeted] = useState(false);
  const [micListening, setMicListening] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);
  const greetedRef = useRef(false);
  const onCompleteRef = useRef(onComplete);
  onCompleteRef.current = onComplete;

  // Start camera on mount
  useEffect(() => {
    const startCamera = async () => {
      try {
        await fetch(`${BACKEND_URL}/camera/start`, { method: "POST" });
        setTimeout(() => setCameraReady(true), 1500);
      } catch (e) {
        console.error("Could not start camera:", e);
        setTimeout(() => setCameraReady(true), 2000);
      }
    };
    startCamera();
  }, []);

  // Stable ref so interval/recognition callbacks always get the latest version
  const triggerGreetRef = useRef<() => void>(() => {});
  const triggerGreet = useCallback(() => {
    if (greetedRef.current) return;
    greetedRef.current = true;
    setGreeted(true);
    console.log("[WelcomeScreen] Greeting triggered — calling onGreet");
    onGreet?.();
    setTimeout(() => {
      setFadeOut(true);
      setTimeout(() => onComplete(), 800);
    }, 2800);
  }, [onGreet, onComplete]);
  triggerGreetRef.current = triggerGreet;

  // Poll activity + hand raise
  useEffect(() => {
    if (!cameraReady) return;
    const poll = async () => {
      try {
        const res = await fetch(`${BACKEND_URL}/activity`);
        const data = await res.json();
        setActivity(data);
        if (data.details?.hand_raised) {
          console.log("[WelcomeScreen] Hand raised detected");
          triggerGreetRef.current();
        }
      } catch {}
    };
    poll();
    const interval = setInterval(poll, 1000);
    return () => clearInterval(interval);
  }, [cameraReady]);

  // Speech recognition — restart on end so it keeps listening
  useEffect(() => {
    if (!cameraReady) return;
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) { console.warn("[WelcomeScreen] SpeechRecognition not supported"); return; }

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    let recognition: any = null;
    let stopped = false;

    const start = () => {
      if (stopped) return;
      recognition = new SR();
      recognition.continuous = false;  // single-shot + restart = more reliable
      recognition.interimResults = false;
      recognition.lang = "en-US";

      recognition.onstart = () => { setMicListening(true); console.log("[Speech] Listening..."); };
      recognition.onend = () => { setMicListening(false); if (!stopped && !greetedRef.current) start(); };
      recognition.onerror = (e: { error: string }) => {
        console.warn("[Speech] Error:", e.error);
        setMicListening(false);
      };
      recognition.onresult = (e: { results: { [key: number]: { [key: number]: { transcript: string } } }; resultIndex: number }) => {
        const transcript = e.results[e.resultIndex][0].transcript.toLowerCase().trim();
        console.log("[Speech] Heard:", transcript);
        if (/\b(hi|hello|hey)\b/.test(transcript)) triggerGreetRef.current();
      };

      try { recognition.start(); } catch {}
    };

    start();
    return () => { stopped = true; setMicListening(false); try { recognition?.stop(); } catch {} };
  }, [cameraReady]);

  // Show greeting after camera is ready
  useEffect(() => {
    if (!cameraReady) return;
    const hour = new Date().getHours();
    if (hour < 12) setGreeting("Good morning");
    else if (hour < 17) setGreeting("Good afternoon");
    else setGreeting("Good evening");
    setTimeout(() => setShowGreeting(true), 500);
  }, [cameraReady]);

  // Countdown timer
  useEffect(() => {
    if (!showGreeting) return;
    if (countdown <= 0) {
      setFadeOut(true);
      setTimeout(() => onCompleteRef.current(), 800);
      return;
    }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [showGreeting, countdown]);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: fadeOut ? 0 : 1 }}
      transition={{ duration: 0.8 }}
      className="fixed inset-0 z-50 bg-background flex items-center justify-center"
    >
      <div className="relative w-full max-w-2xl mx-auto px-6">
        {/* Webcam feed with landmarks */}
        <motion.div
          initial={{ opacity: 0, scale: 0.95 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ delay: 0.3, duration: 0.6 }}
          className="relative rounded-3xl overflow-hidden shadow-2xl border border-border bg-card"
        >
          {cameraReady && !cameraError ? (
            <img
              ref={imgRef}
              src={`${BACKEND_URL}/video_feed`}
              alt="Activity Monitor"
              className="w-full aspect-video object-cover"
              onError={() => {
                setCameraError(true);
              }}
            />
          ) : cameraReady && cameraError ? (
            <div className="w-full aspect-video bg-gradient-to-br from-sage/10 to-amber/10 flex flex-col items-center justify-center gap-2">
              <span className="text-3xl">📷</span>
              <p className="text-sm text-muted-foreground">Camera unavailable</p>
              <p className="text-xs text-muted-foreground/60">Start the backend to enable webcam</p>
            </div>
          ) : (
            <div className="w-full aspect-video bg-card flex items-center justify-center">
              <motion.div
                animate={{ opacity: [0.3, 1, 0.3] }}
                transition={{ duration: 2, repeat: Infinity }}
                className="text-muted-foreground text-sm"
              >
                Starting camera...
              </motion.div>
            </div>
          )}

          <div className="absolute bottom-0 left-0 right-0 h-32 bg-gradient-to-t from-background/90 to-transparent" />

          {/* Wave-back reaction when greeted */}
          <AnimatePresence>
            {greeted && (
              <motion.div
                initial={{ scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0, opacity: 0 }}
                className="absolute inset-0 flex items-center justify-center pointer-events-none"
              >
                <motion.span
                  className="text-6xl"
                  animate={{ rotate: [0, 20, -10, 20, 0] }}
                  transition={{ duration: 0.8, repeat: 2 }}
                >
                  👋
                </motion.span>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Privacy badge */}
          <div className="absolute top-4 right-4 bg-card/80 backdrop-blur-sm rounded-full px-3 py-1.5 flex items-center gap-1.5 border border-border">
            <div className="h-2 w-2 rounded-full bg-sage animate-pulse" />
            <span className="text-xs text-muted-foreground">No recording</span>
          </div>

          {/* Mic listening indicator */}
          {cameraReady && (
            <div className="absolute bottom-36 left-0 right-0 flex justify-center">
              <motion.div
                animate={{ opacity: micListening ? [0.6, 1, 0.6] : 0.5 }}
                transition={{ duration: 1.2, repeat: micListening ? Infinity : 0 }}
                className="bg-black/60 backdrop-blur-sm rounded-full px-4 py-2 flex items-center gap-2"
              >
                <span className="text-sm">{micListening ? "🎤" : "🎙️"}</span>
                <span className="text-xs text-white/80">
                  Say <span className="font-semibold text-white">"Hi"</span> or raise your hand to start
                </span>
              </motion.div>
            </div>
          )}

          {/* Live detection badge */}
          {activity && (
            <div className="absolute top-4 left-4 bg-black/70 backdrop-blur-sm rounded-xl px-3 py-2 border border-white/10 space-y-1 min-w-[160px]">
              <div className="flex items-center gap-2">
                <div
                  className="h-2.5 w-2.5 rounded-full flex-shrink-0"
                  style={{ backgroundColor: ACTIVITY_COLORS[activity.activity] ?? "#aaa" }}
                />
                <span className="text-xs font-semibold text-white uppercase tracking-wide">
                  {activity.activity.replace("_", " ")}
                </span>
                <span className="text-xs text-white/50 ml-auto">
                  {Math.round(activity.confidence * 100)}%
                </span>
              </div>
              <div className="flex flex-wrap gap-x-3 gap-y-0.5">
                {activity.details.phone_visible !== undefined && (
                  <span className={`text-[10px] ${activity.details.phone_visible ? "text-red-400 font-bold" : "text-white/40"}`}>
                    📱 {activity.details.phone_visible ? "PHONE DETECTED" : "no phone"}
                  </span>
                )}
                {typeof activity.details.hands_detected === "number" && (
                  <span className="text-[10px] text-white/40">hands: {activity.details.hands_detected as number}</span>
                )}
                {typeof activity.details.head_tilt === "number" && (
                  <span className="text-[10px] text-white/40">tilt: {(activity.details.head_tilt as number).toFixed(2)}</span>
                )}
              </div>
            </div>
          )}
        </motion.div>

        {/* Greeting text */}
        <AnimatePresence>
          {showGreeting && (
            <motion.div
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.2, duration: 0.5 }}
              className="mt-8 text-center space-y-3"
            >
              <p className="text-muted-foreground text-sm tracking-wide uppercase flex items-center justify-center gap-1.5">
                <Anchor size={14} />
                Anchor
              </p>
              <h1 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
                {greeting} <span className="text-sage">&#x1f44b;</span>
              </h1>
              <p className="text-muted-foreground text-base">
                I'm here to help you stay focused. Let's get started.
              </p>

              {/* Countdown */}
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                transition={{ delay: 0.5 }}
                className="pt-4 space-y-3"
              >
                <div className="flex items-center justify-center gap-2">
                  <div className="h-1 flex-1 max-w-[200px] rounded-full bg-secondary overflow-hidden">
                    <motion.div
                      className="h-full bg-sage rounded-full"
                      initial={{ width: "100%" }}
                      animate={{ width: "0%" }}
                      transition={{ duration: 10, ease: "linear" }}
                    />
                  </div>
                  <span className="text-xs text-muted-foreground tabular-nums w-6">{countdown}s</span>
                </div>

                <button
                  onClick={() => {
                    setFadeOut(true);
                    setTimeout(() => onComplete(), 500);
                  }}
                  className="text-sm text-muted-foreground hover:text-foreground transition-colors underline underline-offset-4"
                >
                  Skip to session
                </button>
              </motion.div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
};

export default WelcomeScreen;
