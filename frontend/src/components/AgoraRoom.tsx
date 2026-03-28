import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Users } from "lucide-react";

const mockPeople = Array.from({ length: 8 }, (_, i) => ({
  id: i,
  color: ["bg-sage", "bg-amber", "bg-terracotta", "bg-sage", "bg-amber", "bg-sage", "bg-terracotta", "bg-sage"][i],
}));

const AgoraRoom = () => {
  const [joined, setJoined] = useState(false);
  const [expanded, setExpanded] = useState(false);

  return (
    <motion.div
      initial={{ opacity: 0, x: 20 }}
      animate={{ opacity: 1, x: 0 }}
      className="fixed top-6 right-6 z-30"
    >
      <button
        onClick={() => setExpanded(!expanded)}
        className="rounded-2xl bg-card backdrop-blur-md border border-border shadow-md px-4 py-2.5 flex items-center gap-2 text-sm text-muted-foreground hover:text-foreground transition-colors"
      >
        <Users size={15} />
        {/* Mini avatar dots */}
        <div className="flex -space-x-1.5">
          {mockPeople.slice(0, 4).map((p) => (
            <div key={p.id} className={`h-3 w-3 rounded-full ${p.color} border border-card`} />
          ))}
        </div>
        <span>{mockPeople.length} focusing</span>
      </button>

      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ opacity: 0, y: -8, scale: 0.95 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: -8, scale: 0.95 }}
            className="absolute right-0 mt-2 rounded-2xl bg-card border border-border shadow-xl p-5 w-64"
          >
            <p className="text-sm font-medium text-foreground mb-3">Focus Room</p>
            <div className="flex flex-wrap gap-2 mb-4">
              {mockPeople.map((p) => (
                <div
                  key={p.id}
                  className={`h-7 w-7 rounded-full ${p.color} opacity-60`}
                />
              ))}
            </div>
            <p className="text-xs text-muted-foreground mb-3">
              {joined ? "You're in the room. Focus together." : "Join others who are focusing right now."}
            </p>
            <button
              onClick={() => setJoined(!joined)}
              className={`w-full rounded-xl py-2 text-sm font-medium transition-all active:scale-[0.98] ${
                joined
                  ? "border border-border text-foreground hover:bg-secondary"
                  : "btn-primary-action"
              }`}
            >
              {joined ? "Leave room" : "Join focus room"}
            </button>
          </motion.div>
        )}
      </AnimatePresence>
    </motion.div>
  );
};

export default AgoraRoom;
