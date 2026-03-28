import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Anchor } from "lucide-react";

interface Props {
  onComplete: () => void;
}

const BACKEND_URL = "http://localhost:8000";

const WelcomeScreen = ({ onComplete }: Props) => {
  const [cameraReady, setCameraReady] = useState(false);
  const [greeting, setGreeting] = useState("");
  const [showGreeting, setShowGreeting] = useState(false);
  const [countdown, setCountdown] = useState(10);
  const [fadeOut, setFadeOut] = useState(false);
  const imgRef = useRef<HTMLImageElement>(null);

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
      setTimeout(() => onComplete(), 800);
      return;
    }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [showGreeting, countdown, onComplete]);

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
          {cameraReady ? (
            <img
              ref={imgRef}
              src={`${BACKEND_URL}/video_feed`}
              alt="Activity Monitor"
              className="w-full aspect-video object-cover"
              onError={() => {
                if (imgRef.current) {
                  imgRef.current.style.display = "none";
                }
              }}
            />
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

          {/* Privacy badge */}
          <div className="absolute top-4 right-4 bg-card/80 backdrop-blur-sm rounded-full px-3 py-1.5 flex items-center gap-1.5 border border-border">
            <div className="h-2 w-2 rounded-full bg-sage animate-pulse" />
            <span className="text-xs text-muted-foreground">No recording</span>
          </div>
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
