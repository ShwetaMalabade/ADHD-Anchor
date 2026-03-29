import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { ScrollText, StickyNote, X, Mic, MicOff, Trash2 } from "lucide-react";

export interface LogEntry {
  id: number;
  timestamp: string;
  elapsed: string;
  type: "session" | "window" | "nudge" | "response" | "break" | "status";
  icon: string;
  message: string;
}

export interface NoteEntry {
  id: number;
  timestamp: string;
  text: string;
}

interface Props {
  entries: LogEntry[];
  notes: NoteEntry[];
  isListening: boolean;
  onDeleteNote: (id: number) => void;
}

type ActivePanel = "none" | "log" | "notes";

const typeColors: Record<string, string> = {
  session: "text-sage",
  window: "text-foreground",
  nudge: "text-amber-600",
  response: "text-blue-500",
  break: "text-sage",
  status: "text-muted-foreground",
};

const DemoLog = ({ entries, notes, isListening, onDeleteNote }: Props) => {
  const [activePanel, setActivePanel] = useState<ActivePanel>("none");
  const logScrollRef = useRef<HTMLDivElement>(null);
  const notesScrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (activePanel === "log" && logScrollRef.current) {
      logScrollRef.current.scrollTop = logScrollRef.current.scrollHeight;
    }
  }, [entries.length, activePanel]);

  useEffect(() => {
    if (activePanel === "notes" && notesScrollRef.current) {
      notesScrollRef.current.scrollTop = notesScrollRef.current.scrollHeight;
    }
  }, [notes.length, activePanel]);

  const togglePanel = (panel: "log" | "notes") => {
    setActivePanel((prev) => (prev === panel ? "none" : panel));
  };

  return (
    <div className="fixed top-4 left-1/2 -translate-x-1/2 z-[60] flex flex-col items-center">
      {/* Buttons */}
      <div className="flex items-center gap-2">
        <button
          onClick={() => togglePanel("log")}
          className={`flex items-center gap-2 rounded-2xl border shadow-md px-4 py-2.5 text-sm font-medium transition-colors ${
            activePanel === "log"
              ? "bg-sage/10 border-sage/40 text-sage"
              : "bg-card border-border text-foreground hover:bg-secondary"
          }`}
        >
          <ScrollText size={16} />
          Log
          {entries.length > 0 && (
            <span className="rounded-full bg-sage/20 text-sage text-xs px-2 py-0.5 font-semibold tabular-nums">
              {entries.length}
            </span>
          )}
        </button>

        <button
          onClick={() => togglePanel("notes")}
          className={`flex items-center gap-2 rounded-2xl border shadow-md px-4 py-2.5 text-sm font-medium transition-colors ${
            activePanel === "notes"
              ? "bg-amber-50 border-amber-300/50 text-amber-700"
              : "bg-card border-border text-foreground hover:bg-secondary"
          }`}
        >
          <StickyNote size={16} />
          Notes
          {notes.length > 0 && (
            <span className="rounded-full bg-amber-100 text-amber-700 text-xs px-2 py-0.5 font-semibold tabular-nums">
              {notes.length}
            </span>
          )}
          {isListening && (
            <span className="relative flex h-2.5 w-2.5">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-red-400 opacity-75" />
              <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-red-500" />
            </span>
          )}
        </button>
      </div>

      {/* Panel — drops down directly below buttons, centered */}
      <AnimatePresence mode="wait">
        {activePanel === "log" && (
          <motion.div
            key="log-panel"
            initial={{ opacity: 0, y: -10, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.97 }}
            transition={{ type: "spring", damping: 28, stiffness: 300 }}
            className="mt-2 w-[420px] max-h-[calc(100vh-96px)] rounded-2xl bg-card border border-border shadow-2xl flex flex-col"
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-border flex-shrink-0">
              <div>
                <h3 className="text-sm font-semibold text-foreground">Session Log</h3>
                <p className="text-xs text-muted-foreground mt-0.5">Live event history</p>
              </div>
              <button
                onClick={() => setActivePanel("none")}
                className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
              >
                <X size={16} />
              </button>
            </div>
            <div ref={logScrollRef} className="flex-1 overflow-y-auto px-5 py-3 space-y-1">
              {entries.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-8">
                  Start a session to see events here
                </p>
              ) : (
                entries.map((entry) => (
                  <div key={entry.id} className="flex gap-3 py-2 border-b border-border/40 last:border-0">
                    <span className="text-base leading-none mt-0.5 flex-shrink-0">{entry.icon}</span>
                    <div className="min-w-0 flex-1">
                      <p className={`text-[13px] leading-snug ${typeColors[entry.type] || "text-foreground"}`}>
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

        {activePanel === "notes" && (
          <motion.div
            key="notes-panel"
            initial={{ opacity: 0, y: -10, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -10, scale: 0.97 }}
            transition={{ type: "spring", damping: 28, stiffness: 300 }}
            className="mt-2 w-[420px] max-h-[calc(100vh-96px)] rounded-2xl bg-card border border-border shadow-2xl flex flex-col"
          >
            <div className="flex items-center justify-between px-5 py-4 border-b border-border flex-shrink-0">
              <div>
                <h3 className="text-sm font-semibold text-foreground">Voice Notes</h3>
                <p className="text-xs text-muted-foreground mt-0.5">
                  {isListening ? (
                    <span className="flex items-center gap-1.5">
                      <Mic size={11} className="text-red-500" />
                      Listening &mdash; say <strong>"note"</strong> then your idea
                    </span>
                  ) : (
                    <span className="flex items-center gap-1.5">
                      <MicOff size={11} />
                      Mic active during focus sessions
                    </span>
                  )}
                </p>
              </div>
              <button
                onClick={() => setActivePanel("none")}
                className="rounded-lg p-1.5 text-muted-foreground hover:text-foreground hover:bg-secondary transition-colors"
              >
                <X size={16} />
              </button>
            </div>
            <div ref={notesScrollRef} className="flex-1 overflow-y-auto px-5 py-3 space-y-2">
              {notes.length === 0 ? (
                <div className="text-center py-8 space-y-2">
                  <StickyNote size={28} className="mx-auto text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">No notes yet</p>
                  <p className="text-xs text-muted-foreground/70">
                    Say <strong>"note"</strong> or <strong>"please note"</strong> followed by your idea during a focus session
                  </p>
                </div>
              ) : (
                notes.map((note) => (
                  <div
                    key={note.id}
                    className="group flex gap-3 py-3 px-3 rounded-xl bg-amber-50/50 border border-amber-200/30"
                  >
                    <span className="text-base leading-none mt-0.5 flex-shrink-0">📝</span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] leading-snug text-foreground">{note.text}</p>
                      <p className="text-[10px] text-muted-foreground mt-1 tabular-nums">{note.timestamp}</p>
                    </div>
                    <button
                      onClick={() => onDeleteNote(note.id)}
                      className="opacity-0 group-hover:opacity-100 rounded-lg p-1 text-muted-foreground hover:text-red-500 transition-all flex-shrink-0 self-start"
                    >
                      <Trash2 size={13} />
                    </button>
                  </div>
                ))
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
};

export default DemoLog;
