import { useState, useEffect } from "react";
import { motion } from "framer-motion";

interface Props {
  durationSeconds?: number;
  onBreakEnd: () => void;
}

const RADIUS = 45;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

const BreakTimer = ({ durationSeconds = 60, onBreakEnd }: Props) => {
  const [remaining, setRemaining] = useState(durationSeconds);

  useEffect(() => {
    if (remaining <= 0) return;
    const t = setInterval(() => setRemaining((r) => r - 1), 1000);
    return () => clearInterval(t);
  }, [remaining]);

  const formatTime = (s: number) => {
    const m = Math.floor(s / 60);
    const sec = s % 60;
    return `${m}:${sec.toString().padStart(2, "0")}`;
  };

  const progress = remaining / durationSeconds;
  const dashOffset = CIRCUMFERENCE * (1 - progress);

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.9, y: 20 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.9, y: 20 }}
      className="fixed bottom-6 right-6 z-40"
    >
      <div className="rounded-2xl bg-card border border-border shadow-lg p-6 w-[280px] text-center relative overflow-hidden">
        <p className="text-sm font-medium text-muted-foreground mb-4">Break time</p>

        {/* Circular countdown ring */}
        <div className="relative mx-auto w-[120px] h-[120px] mb-4">
          <svg className="w-full h-full -rotate-90" viewBox="0 0 100 100">
            {/* Background ring */}
            <circle
              cx="50" cy="50" r={RADIUS}
              fill="none"
              stroke="hsl(var(--border))"
              strokeWidth="6"
            />
            {/* Progress ring */}
            <circle
              cx="50" cy="50" r={RADIUS}
              fill="none"
              stroke="hsl(var(--sage))"
              strokeWidth="6"
              strokeLinecap="round"
              strokeDasharray={CIRCUMFERENCE}
              strokeDashoffset={dashOffset}
              className={remaining > 0 ? "animate-breathe" : ""}
              style={{ transition: "stroke-dashoffset 1s linear", animationDuration: remaining > 0 ? "4s" : "0s" }}
            />
          </svg>
          {/* Time display */}
          <div className="absolute inset-0 flex items-center justify-center">
            <p className="text-2xl font-semibold text-foreground tabular-nums">
              {formatTime(remaining)}
            </p>
          </div>
        </div>

        {remaining <= 0 ? (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="space-y-3"
          >
            <p className="text-sm text-muted-foreground">Ready to get back?</p>
            <button
              onClick={onBreakEnd}
              className="btn-primary-action w-full rounded-2xl py-2.5 font-semibold text-sm"
            >
              Let's go
            </button>
          </motion.div>
        ) : (
          <div className="space-y-3">
            <p className="text-xs text-muted-foreground">Recharging...</p>
            <button
              onClick={onBreakEnd}
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              Skip break
            </button>
          </div>
        )}
      </div>
    </motion.div>
  );
};

export default BreakTimer;
