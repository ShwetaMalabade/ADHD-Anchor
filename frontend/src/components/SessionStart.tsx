import { useState } from "react";
import { motion } from "framer-motion";
import { Anchor } from "lucide-react";

interface Props {
  onStart: (task: string, duration: number) => void;
}

const durations = [
  { label: "30 min", value: 30 },
  { label: "1 hr", value: 60 },
  { label: "2 hr", value: 120 },
];

const SessionStart = ({ onStart }: Props) => {
  const [task, setTask] = useState("");
  const [duration, setDuration] = useState(60);
  const [customMode, setCustomMode] = useState(false);
  const [customMin, setCustomMin] = useState("");

  const handleStart = () => {
    if (!task.trim()) return;
    const finalDuration = customMode && customMin ? parseInt(customMin) : duration;
    onStart(task.trim(), finalDuration);
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.3, ease: "easeOut" }}
      className="relative z-10 flex min-h-screen items-center justify-center px-6"
    >
      <div className="w-full max-w-md space-y-10 text-center">
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.15, duration: 0.4 }}
        >
          <p className="text-muted-foreground mb-2 text-sm tracking-wide uppercase flex items-center justify-center gap-1.5">
            <Anchor size={14} />
            Anchor
          </p>
          <h1 className="text-3xl font-semibold tracking-tight text-foreground md:text-4xl">
            What are you working on?
          </h1>
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.25, duration: 0.4 }}
        >
          <input
            type="text"
            value={task}
            onChange={(e) => setTask(e.target.value)}
            placeholder="e.g. Reading my TPU paper for class..."
            className="w-full rounded-2xl border border-input bg-card px-6 py-4 text-lg text-foreground placeholder:text-muted-foreground/60 outline-none transition-shadow focus:ring-2 focus:ring-ring/30 focus:border-ring/50"
            onKeyDown={(e) => e.key === "Enter" && handleStart()}
          />
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.35, duration: 0.4 }}
          className="space-y-3"
        >
          <p className="text-sm text-muted-foreground">How long do you want to focus?</p>
          <div className="flex items-center justify-center gap-3">
            {durations.map((d) => (
              <button
                key={d.value}
                onClick={() => { setDuration(d.value); setCustomMode(false); }}
                className={`rounded-full px-5 py-2.5 text-sm font-medium transition-all ${
                  duration === d.value && !customMode
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "bg-secondary text-foreground hover:bg-sage-hover"
                }`}
              >
                {d.label}
              </button>
            ))}
            <button
              onClick={() => setCustomMode(true)}
              className={`rounded-full px-5 py-2.5 text-sm font-medium transition-all ${
                customMode
                  ? "bg-primary text-primary-foreground shadow-sm"
                  : "bg-secondary text-foreground hover:bg-sage-hover"
              }`}
            >
              Custom
            </button>
          </div>
          {customMode && (
            <motion.div
              initial={{ opacity: 0, height: 0 }}
              animate={{ opacity: 1, height: "auto" }}
              className="flex items-center justify-center gap-2"
            >
              <input
                type="number"
                value={customMin}
                onChange={(e) => setCustomMin(e.target.value)}
                placeholder="45"
                className="w-20 rounded-xl border border-input bg-card px-4 py-2 text-center text-foreground outline-none focus:ring-2 focus:ring-ring/30"
              />
              <span className="text-sm text-muted-foreground">minutes</span>
            </motion.div>
          )}
        </motion.div>

        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 0.45, duration: 0.4 }}
        >
          <button
            onClick={handleStart}
            disabled={!task.trim()}
            className="btn-primary-action w-full rounded-2xl py-4 text-lg font-semibold"
          >
            Start Session
          </button>
        </motion.div>
      </div>
    </motion.div>
  );
};

export default SessionStart;
