import { motion } from "framer-motion";

interface Props {
  type: "drift" | "notification" | "ask" | "suggest_break" | "silent_drift" | "task_initiation";
  message?: string;
  source?: string;
  options?: string[];
  onTakeBreak: () => void;
  onPullBack: () => void;
  onDismiss: () => void;
}

const NudgeOverlay = ({ type, message, source, options, onTakeBreak, onPullBack, onDismiss }: Props) => {
  const displayMessage = message || (
    type === "drift"
      ? `Hey, you left your notebook for ${source || "something else"}. Break or get back?`
      : type === "notification"
      ? `Looks like ${source || "a notification"} pulled you out. Take a minute to respond, I'll remind you to come back.`
      : type === "suggest_break"
      ? "You've been going hard. Time for a quick break -- stand up, stretch, grab some water."
      : type === "silent_drift"
      ? "Your screen has been quiet for a while. Still with me?"
      : type === "task_initiation"
      ? "Getting started is the hardest part. Want to open the file together?"
      : "Hey, need a hand getting back on track?"
  );

  const showTwoButtons = type === "drift" || type === "ask" || type === "silent_drift";

  return (
    <motion.div
      initial={{ opacity: 0, y: 40, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 40, scale: 0.95 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="fixed bottom-6 right-6 z-50 w-full max-w-[320px]"
    >
      <div className="rounded-2xl bg-card border border-border shadow-xl p-6 space-y-4">
        <div className={`h-1 w-12 ${type === "suggest_break" ? "bg-sage/40" : "bg-terracotta/40"} rounded-full mx-auto`} />

        <p className="text-center text-base font-medium text-foreground leading-relaxed">
          {displayMessage}
        </p>

        {type === "suggest_break" ? (
          <div className="flex gap-3">
            <button
              onClick={onTakeBreak}
              className="btn-primary-action flex-1 rounded-2xl py-3 font-semibold"
            >
              {options?.[0] || "Take a break"}
            </button>
            <button
              onClick={onDismiss}
              className="flex-1 rounded-2xl border border-border py-3 font-medium text-foreground transition-all hover:bg-secondary active:scale-[0.98]"
            >
              {options?.[1] || "Keep going"}
            </button>
          </div>
        ) : showTwoButtons ? (
          <div className="flex gap-3">
            <button
              onClick={onTakeBreak}
              className="flex-1 rounded-2xl border border-border py-3 font-medium text-foreground transition-all hover:bg-secondary active:scale-[0.98]"
            >
              {options?.[0] || "Taking a break"}
            </button>
            <button
              onClick={onPullBack}
              className="btn-primary-action flex-1 rounded-2xl py-3 font-semibold"
            >
              {options?.[1] || "Pull me back"}
            </button>
          </div>
        ) : (
          <button
            onClick={onDismiss}
            className="btn-primary-action w-full rounded-2xl py-3 font-semibold"
          >
            {options?.[0] || "Got it"}
          </button>
        )}
      </div>
    </motion.div>
  );
};

export default NudgeOverlay;
