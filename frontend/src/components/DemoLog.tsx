import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ScrollText, X, ChevronRight } from "lucide-react";

export interface LogEntry {
  id: number;
  timestamp: string;
  elapsed: string;
  type: "session" | "window" | "nudge" | "response" | "break" | "status";
  icon: string;
  message: string;
}

interface Props {
  entries: LogEntry[];
}

const typeColors: Record<string, string> = {
  session: "text-sage",
  window: "text-foreground",
  nudge: "text-amber-600",
  response: "text-blue-500",
  break: "text-sage",
  status: "text-muted-foreground",
};

const DemoLog = ({ entries }: Props) => {
  const [isOpen, setIsOpen] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (isOpen && scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [entries.length, isOpen]);

  return (
    <>
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="fixed top-4 left-4 z-[60] flex items-center gap-2 rounded-2xl bg-card border border-border shadow-md px-4 py-2.5 text-sm font-medium text-foreground hover:bg-secondary transition-colors"
      >
        <ScrollText size={16} className="text-sage" />
        Demo Log
        {entries.length > 0 && (
          <span className="ml-1 rounded-full bg-sage/20 text-sage text-xs px-2 py-0.5 font-semibold tabular-nums">
            {entries.length}
          </span>
        )}
        <ChevronRight
          size={14}
          className={`text-muted-foreground transition-transform duration-200 ${isOpen ? "rotate-180" : ""}`}
        />
      </button>

      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, x: -340 }}
            animate={{ opacity: 1, x: 0 }}
            exit={{ opacity: 0, x: -340 }}
            transition={{ type: "spring", damping: 28, stiffness: 300 }}
            className="fixed top-16 left-4 z-[60] w-[400px] max-h-[calc(100vh-96px)] rounded-2xl bg-card border border-border shadow-2xl flex flex-col"
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-border flex-shrink-0">
              <div>
                <h3 className="text-sm font-semibold text-foreground">Session Log</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  Live event history for demo
                </p>
              </div>
              <button
                onClick={() => setIsOpen(false)}
                className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
              >
                <X size={16} />
              </button>
            </div>

            <div
              ref={scrollRef}
              className="flex-1 overflow-y-auto px-5 py-3 space-y-1"
            >
              {entries.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">
                  Start a session to see events here
                </p>
              ) : (
                entries.map((entry) => (
                  <div
                    key={entry.id}
                    className="flex gap-3 py-2 border-b border-border/40 last:border-0"
                  >
                    <span className="text-base leading-none mt-0.5 flex-shrink-0">
                      {entry.icon}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p
                        className={`text-[13px] leading-snug ${typeColors[entry.type] || "text-foreground"}`}
                      >
                        {entry.message}
                      </p>
                      <p className="text-[10px] text-muted-foreground mt-0.5 tabular-nums">
                        {entry.timestamp} &middot; {entry.elapsed}
                      </p>
                    </div>
                  </div>
                ))
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};

export default DemoLog;
