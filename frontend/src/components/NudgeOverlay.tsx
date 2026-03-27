import { motion } from "framer-motion";

interface Props {
  type: "drift" | "notification";
  message?: string;
  source?: string;
  onTakeBreak: () => void;
  onPullBack: () => void;
  onDismiss: () => void;
}

const NudgeOverlay = ({ type, source, onTakeBreak, onPullBack, onDismiss }: Props) => {
  return (
    <motion.div
      initial={{ opacity: 0, y: 40, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 40, scale: 0.95 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="fixed bottom-6 right-6 z-50 w-full max-w-[320px]"
    >
      <div className="rounded-2xl bg-card border border-border shadow-xl p-6 space-y-4">
        <div className="h-1 w-12 bg-terracotta/40 rounded-full mx-auto" />
        
        {type === "drift" ? (
          <>
            <p className="text-center text-base font-medium text-foreground leading-relaxed">
              Hey, you left your notebook for {source || "something else"}. Break or get back?
            </p>
            <div className="flex gap-3">
              <button
                onClick={onTakeBreak}
                className="flex-1 rounded-2xl border border-border py-3 font-medium text-foreground transition-all hover:bg-secondary active:scale-[0.98]"
              >
                Taking a break
              </button>
              <button
                onClick={onPullBack}
                className="btn-primary-action flex-1 rounded-2xl py-3 font-semibold"
              >
                Pull me back
              </button>
            </div>
          </>
        ) : (
          <>
            <p className="text-center text-base font-medium text-foreground leading-relaxed">
              Looks like {source || "a notification"} pulled you out. Take a minute to respond, I'll remind you to come back.
            </p>
            <button
              onClick={onDismiss}
              className="btn-primary-action w-full rounded-2xl py-3 font-semibold"
            >
              Got it
            </button>
          </>
        )}
      </div>
    </motion.div>
  );
};

export default NudgeOverlay;
