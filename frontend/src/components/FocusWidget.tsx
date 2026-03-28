import { useRef } from "react";
import { motion } from "framer-motion";
import { Pause, Play, Square } from "lucide-react";

interface Props {
  task: string;
  status: "focused" | "checking" | "drifted";
  onPause: () => void;
  onResume: () => void;
  onEnd: () => void;
  isPaused: boolean;
  elapsedSeconds: number;
}

const statusColors = {
  focused: "bg-sage",
  checking: "bg-amber",
  drifted: "bg-terracotta",
};

const FocusWidget = ({ task, status, onPause, onResume, onEnd, isPaused, elapsedSeconds }: Props) => {
  const dragRef = useRef<HTMLDivElement>(null);

  const formatTime = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h}h ${m}m focused`;
    return `${m} min focused`;
  };

  return (
    <motion.div
      ref={dragRef}
      drag
      dragMomentum={false}
      initial={{ opacity: 0, scale: 0.9, y: 20 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      exit={{ opacity: 0, scale: 0.9, y: 20 }}
      className="fixed bottom-6 right-6 z-40 cursor-grab active:cursor-grabbing"
    >
      <div className="rounded-2xl bg-card backdrop-blur-md border border-border shadow-lg p-4 min-w-[220px]">
        <div className="flex items-center gap-3">
          <div className={`h-2.5 w-2.5 rounded-full ${statusColors[status]} ${status !== "focused" ? "animate-gentle-pulse" : ""}`} />
          <span className="text-sm font-medium text-foreground truncate max-w-[140px]">
            {task}
          </span>
        </div>
        <div className="mt-2 flex items-center justify-between">
          <span className="text-xs text-muted-foreground">
            {isPaused ? "Paused" : formatTime(elapsedSeconds)}
          </span>
          <div className="flex gap-1.5">
            <button
              onClick={isPaused ? onResume : onPause}
              className="rounded-lg p-1.5 text-muted-foreground hover:bg-secondary transition-colors"
            >
              {isPaused ? <Play size={14} /> : <Pause size={14} />}
            </button>
            <button
              onClick={onEnd}
              className="rounded-lg p-1.5 text-muted-foreground hover:bg-secondary transition-colors"
            >
              <Square size={14} />
            </button>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

export default FocusWidget;
