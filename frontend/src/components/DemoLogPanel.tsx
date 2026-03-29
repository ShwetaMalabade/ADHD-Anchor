import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";

export interface LogEntry {
  time: number;
  type: "classification" | "nudge" | "status" | "break" | "user_action" | "wave";
  message: string;
}

interface DemoLogPanelProps {
  logEntries: LogEntry[];
  elapsed: number;
}

const TYPE_STYLES: Record<LogEntry["type"], { label: string; color: string }> = {
  classification: { label: "APP", color: "bg-blue-500/20 text-blue-300" },
  nudge: { label: "NUDGE", color: "bg-terracotta/20 text-terracotta" },
  status: { label: "STATUS", color: "bg-sage/20 text-sage" },
  break: { label: "BREAK", color: "bg-amber/20 text-amber-300" },
  user_action: { label: "USER", color: "bg-purple-500/20 text-purple-300" },
  wave: { label: "WAVE", color: "bg-green-500/20 text-green-300" },
};

function formatTime(seconds: number) {
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

const DemoLogPanel = ({ logEntries, elapsed }: DemoLogPanelProps) => {
  const [open, setOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (open && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [logEntries, open]);

  return (
    <div className="fixed top-6 left-6 z-[45]">
      <button
        onClick={() => setOpen((o) => !o)}
        className="px-3 py-1.5 rounded-full bg-white/10 backdrop-blur-md border border-white/20 text-white/80 text-sm font-medium hover:bg-white/20 transition-colors"
      >
        {open ? "Close Log" : "Demo Log"}
      </button>

      <AnimatePresence>
        {open && (
          <motion.div
            initial={{ opacity: 0, y: -8, scale: 0.96 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.96 }}
            transition={{ duration: 0.2 }}
            className="mt-2 w-[420px] max-h-[60vh] rounded-xl bg-black/70 backdrop-blur-xl border border-white/15 overflow-hidden flex flex-col"
          >
            <div className="px-4 py-3 border-b border-white/10 flex items-center justify-between">
              <span className="text-white/90 text-sm font-semibold">
                Session History
              </span>
              <span className="text-white/50 text-xs font-mono">
                {formatTime(elapsed)} elapsed
              </span>
            </div>

            <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-1.5">
              {logEntries.length === 0 ? (
                <p className="text-white/40 text-xs text-center py-4">
                  Events will appear here as the session progresses...
                </p>
              ) : (
                logEntries.map((entry, i) => {
                  const style = TYPE_STYLES[entry.type];
                  return (
                    <div
                      key={i}
                      className="flex items-start gap-2 text-xs"
                    >
                      <span className="text-white/40 font-mono shrink-0 pt-0.5 w-10 text-right">
                        {formatTime(entry.time)}
                      </span>
                      <span
                        className={`shrink-0 px-1.5 py-0.5 rounded text-[10px] font-bold uppercase ${style.color}`}
                      >
                        {style.label}
                      </span>
                      <span className="text-white/75 leading-relaxed">
                        {entry.message}
                      </span>
                    </div>
                  );
                })
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default DemoLogPanel;
