import { motion } from "framer-motion";
import { Anchor } from "lucide-react";

interface SessionData {
  totalFocusedSeconds: number;
  driftCount: number;
  longestStreakMinutes: number;
  topDriftTrigger: string;
  topDriftTriggerCount?: number;
  avgReturnTimeMinutes?: number;
  totalSessionSeconds?: number;
  timeline: { minute: number; focused: boolean; type?: "focus" | "drift" | "break" }[];
}

interface Props {
  data: SessionData;
  onNewSession: () => void;
  onDone: () => void;
}

const SessionSummary = ({ data, onNewSession, onDone }: Props) => {
  const formatDuration = (s: number) => {
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    if (h > 0) return `${h}h ${m}m`;
    return `${m}m`;
  };

  const totalSession = data.totalSessionSeconds || data.totalFocusedSeconds;
  const focusPercent = totalSession > 0 ? data.totalFocusedSeconds / totalSession : 1;
  const showCongrats = focusPercent > 0.7;
  const sessionMinutes = Math.floor(totalSession / 60);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.3 }}
      className="relative z-10 flex min-h-screen items-center justify-center px-6"
    >
      <div className="w-full max-w-md space-y-8 text-center">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.2 }}
        >
          <p className="text-muted-foreground mb-2 text-sm tracking-wide uppercase flex items-center justify-center gap-1.5">
            <Anchor size={14} />
            Anchor
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            Session Complete
          </h1>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35 }}
          className="space-y-4"
        >
          {showCongrats && (
            <p className="text-sage font-medium text-sm">That's a solid session ✨</p>
          )}
          <div className="space-y-1">
            <p className="text-4xl font-bold text-foreground">{formatDuration(data.totalFocusedSeconds)}</p>
            <p className="text-sm text-muted-foreground">focused</p>
          </div>

          <div className="space-y-3 text-left rounded-2xl bg-card border border-border p-5">
            <div className="flex items-center gap-3">
              <div className="h-2.5 w-2.5 rounded-full bg-terracotta" />
              <span className="text-foreground">{data.driftCount} drifts caught</span>
            </div>
            <div className="flex items-center gap-3">
              <div className="h-2.5 w-2.5 rounded-full bg-sage" />
              <span className="text-foreground">Longest streak: {data.longestStreakMinutes} minutes</span>
            </div>
            {data.topDriftTrigger && (
              <div className="flex items-center gap-3">
                <div className="h-2.5 w-2.5 rounded-full bg-terracotta" />
                <span className="text-foreground">
                  Top drift trigger: {data.topDriftTrigger}
                  {data.topDriftTriggerCount && data.driftCount > 0
                    ? ` (${data.topDriftTriggerCount} of ${data.driftCount} drifts)`
                    : ""}
                </span>
              </div>
            )}
            <div className="flex items-center gap-3">
              <div className="h-2.5 w-2.5 rounded-full bg-sage" />
              <span className="text-foreground">Avg return time: {data.avgReturnTimeMinutes || 2} min</span>
            </div>
          </div>
        </motion.div>

        {/* Segmented timeline bar */}
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.5 }}
          className="rounded-2xl bg-card border border-border p-5"
        >
          <p className="text-xs text-muted-foreground mb-3">Focus timeline</p>
          <div className="flex h-5 w-full rounded-full overflow-hidden gap-[1px]">
            {data.timeline.map((point, i) => {
              const segType = point.type || (point.focused ? "focus" : "drift");
              const colorClass = segType === "focus"
                ? "bg-sage"
                : segType === "drift"
                ? "bg-terracotta"
                : "bg-muted";
              return (
                <div
                  key={i}
                  className={`flex-1 ${colorClass} ${i === 0 ? "rounded-l-full" : ""} ${i === data.timeline.length - 1 ? "rounded-r-full" : ""}`}
                />
              );
            })}
          </div>
          <div className="flex justify-between mt-2">
            <span className="text-xs text-muted-foreground">0 min</span>
            <span className="text-xs text-muted-foreground">{sessionMinutes} min</span>
          </div>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.6 }}
          className="rounded-2xl bg-card border border-border p-5 border-l-[3px] border-l-sage text-left"
        >
          <p className="text-sm text-foreground leading-relaxed">
            You tend to drift around the 25-minute mark. A short break at 20 minutes might help next time.
          </p>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.7 }}
          className="flex gap-3"
        >
          <button
            onClick={onNewSession}
            className="btn-primary-action flex-1 rounded-2xl py-3.5 font-semibold"
          >
            Start new session
          </button>
          <button
            onClick={onDone}
            className="flex-1 rounded-2xl border border-border py-3.5 font-medium text-foreground transition-all hover:bg-secondary active:scale-[0.98]"
          >
            Done for today
          </button>
        </motion.div>
      </div>
    </motion.div>
  );
};

export default SessionSummary;
