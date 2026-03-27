import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";

interface Props {
  onReady: () => void;
}

const TaskInitiationNudge = ({ onReady }: Props) => {
  const [countdown, setCountdown] = useState(3);
  const [counting, setCounting] = useState(false);

  useEffect(() => {
    if (!counting) return;
    if (countdown <= 0) {
      onReady();
      return;
    }
    const t = setTimeout(() => setCountdown((c) => c - 1), 1000);
    return () => clearTimeout(t);
  }, [counting, countdown, onReady]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 40, scale: 0.95 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 40, scale: 0.95 }}
      transition={{ duration: 0.2, ease: "easeOut" }}
      className="fixed bottom-6 right-6 z-50 w-full max-w-[320px]"
    >
      <div className="rounded-2xl bg-card border border-border shadow-xl p-6 space-y-5 text-center">
        <div className="h-1 w-12 bg-amber/40 rounded-full mx-auto" />
        <p className="text-base font-medium text-foreground leading-relaxed">
          Getting started is the hardest part. Want to open the file together?
        </p>

        <AnimatePresence mode="wait">
          {counting ? (
            <motion.div
              key="countdown"
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              className="space-y-2"
            >
              <motion.p
                key={countdown}
                initial={{ opacity: 0, scale: 0.7 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ type: "spring", stiffness: 300, damping: 20 }}
                className="text-4xl font-bold text-sage"
              >
                {countdown > 0 ? `${countdown}...` : "Let's go!"}
              </motion.p>
            </motion.div>
          ) : (
            <motion.button
              key="button"
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              onClick={() => setCounting(true)}
              className="btn-primary-action w-full rounded-2xl py-3 font-semibold"
            >
              I'm ready
            </motion.button>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
};

export default TaskInitiationNudge;
