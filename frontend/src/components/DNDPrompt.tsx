import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface Props {
  onContinue: (dnd: boolean, expectedNotifications: string) => void;
}

const DNDPrompt = ({ onContinue }: Props) => {
  const [step, setStep] = useState<"ask" | "expecting" | "transition">("ask");
  const [expectation, setExpectation] = useState("");

  const handleLetsBegin = () => {
    setStep("transition");
    setTimeout(() => onContinue(false, expectation), 1500);
  };

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.3 }}
      className="fixed inset-0 z-50 flex items-end justify-center bg-foreground/10 backdrop-blur-sm sm:items-center"
    >
      <motion.div
        initial={{ y: 60, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        exit={{ y: 60, opacity: 0 }}
        transition={{ type: "spring", damping: 25, stiffness: 300 }}
        className="w-full max-w-md rounded-t-3xl bg-card p-8 shadow-xl sm:rounded-3xl"
      >
        <AnimatePresence mode="wait">
          {step === "ask" ? (
            <motion.div
              key="ask"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0, x: -20 }}
              className="space-y-6 text-center"
            >
              <div className="mx-auto h-12 w-12 rounded-full bg-sage-light flex items-center justify-center">
                <span className="text-2xl">🔕</span>
              </div>
              <div className="space-y-2">
                <h2 className="text-xl font-semibold text-foreground">Quick thing before we start</h2>
                <p className="text-muted-foreground">
                  Want to turn on Do Not Disturb? Notifications are the #1 focus killer.
                </p>
              </div>
              <div className="space-y-3">
                <button
                  onClick={() => onContinue(true, "")}
                  className="btn-primary-action w-full rounded-2xl py-3.5 font-semibold"
                >
                  Done, turned it on
                </button>
                <button
                  onClick={() => setStep("expecting")}
                  className="w-full rounded-2xl border border-border py-3.5 font-medium text-foreground transition-all hover:bg-secondary active:scale-[0.98]"
                >
                  Keep them on, I'm expecting something
                </button>
              </div>
            </motion.div>
          ) : step === "expecting" ? (
            <motion.div
              key="expecting"
              initial={{ opacity: 0, x: 20 }}
              animate={{ opacity: 1, x: 0 }}
              exit={{ opacity: 0 }}
              className="space-y-6 text-center"
            >
              <div className="space-y-2">
                <h2 className="text-xl font-semibold text-foreground">No worries!</h2>
                <p className="text-muted-foreground">What are you expecting? I'll keep an eye out.</p>
              </div>
              <input
                type="text"
                value={expectation}
                onChange={(e) => setExpectation(e.target.value)}
                placeholder="e.g. Slack from my teammate about the project"
                className="w-full rounded-2xl border border-input bg-background px-5 py-3.5 text-foreground placeholder:text-muted-foreground/60 outline-none transition-shadow focus:ring-2 focus:ring-ring/30"
              />
              <button
                onClick={handleLetsBegin}
                className="btn-primary-action w-full rounded-2xl py-3.5 font-semibold"
              >
                Let's begin
              </button>
            </motion.div>
          ) : (
            <motion.div
              key="transition"
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              className="py-8 text-center"
            >
              <p className="text-lg font-medium text-sage">
                Got it. Starting your session now...
              </p>
            </motion.div>
          )}
        </AnimatePresence>
      </motion.div>
    </motion.div>
  );
};

export default DNDPrompt;
