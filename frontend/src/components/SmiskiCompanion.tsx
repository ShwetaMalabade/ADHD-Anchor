import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Coffee, Zap, LogOut, X } from "lucide-react";

type SmiskiState = "hidden" | "present" | "menu";

interface Props {
  nudgeText?: string;
  nudgeId?: number;
  task?: string;
  elapsedSeconds?: number;
  driftCount?: number;
  onTakeBreak: () => void;
  onPullBack: () => void;
  onEndSession: () => void;
  sessionActive: boolean;
  suppressMountGreeting?: boolean;
}

const BACKEND_URL = "http://localhost:8000";

async function fetchBuddyMessage(
  scenario: string,
  task = "",
  elapsedSeconds = 0,
  driftCount = 0,
  fallback: string,
): Promise<string> {
  try {
    const params = new URLSearchParams({
      scenario,
      task,
      elapsed_seconds: String(elapsedSeconds),
      drift_count: String(driftCount),
    });
    const res = await fetch(`${BACKEND_URL}/buddy/message?${params}`);
    if (!res.ok) return fallback;
    const data = await res.json();
    return data.message || fallback;
  } catch {
    return fallback;
  }
}

// Motivation quotes shown every ~10 min — warm check-ins, not corporate slogans
const MOTIVATION_QUOTES = [
  "hey, just checking in 🌿 you're doing really well",
  "I know this stuff takes effort. honestly, proud of you for sticking with it 💙",
  "take a little breath. you're making real progress today ✨",
  "you don't have to be perfect — just keep going. that's all it takes 🌱",
  "still here with you! you've got this, one step at a time 🤝",
  "small wins still count. you're moving forward and that matters 🌟",
];

// Cheer messages shown every 5 consecutive non-drift windows
const CHEER_MESSAGES = [
  "you've been so focused lately 🔥 keep riding this wave!",
  "okay wow, you're genuinely in the zone right now 💫",
  "I see you staying on track — this is awesome 🙌",
  "look at you go! seriously impressive focus right now ✨",
  "this is what flow looks like. you're doing amazing 🌟",
];

// Pale yellow-green matching real Smiski figurines
const BODY = "hsl(75, 40%, 82%)";
const STROKE = "hsl(75, 22%, 62%)";

interface CharacterProps {
  isWalking: boolean;
  isUrgent: boolean;
}

const SmiskiCharacter = ({ isWalking, isUrgent }: CharacterProps) => {
  const w = { duration: 0.38, repeat: Infinity, ease: "easeInOut" as const };

  return (
    <motion.svg
      width="72"
      height="96"
      viewBox="0 0 72 96"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      // Urgent wiggle on the whole character
      animate={isUrgent ? { rotate: [0, -7, 7, -5, 5, -2, 2, 0] } : { rotate: 0 }}
      transition={isUrgent ? { duration: 0.55 } : {}}
    >
      {/* Ground shadow */}
      <ellipse cx="36" cy="93" rx="16" ry="4" fill={BODY} fillOpacity="0.25" />

      {/* Back leg — renders first so it's behind the body */}
      <motion.g
        style={{ transformOrigin: "44px 76px" }}
        animate={isWalking ? { rotate: [18, -14, 18] } : { rotate: 6 }}
        transition={isWalking ? w : {}}
      >
        <ellipse cx="44" cy="84" rx="6.5" ry="10.5" fill={BODY} stroke={STROKE} strokeWidth="1" />
      </motion.g>

      {/* Body bob group — torso, arms, head all bob together */}
      <motion.g
        animate={isWalking ? { y: [0, -4, 0, -4, 0] } : { y: 0 }}
        transition={isWalking ? w : {}}
      >
        {/* Torso */}
        <ellipse
          cx="36" cy="65"
          rx="13.5" ry="15"
          fill={BODY} stroke={STROKE} strokeWidth="1"
        />

        {/* Left arm */}
        <motion.g
          style={{ transformOrigin: "24px 59px" }}
          animate={isWalking ? { rotate: [18, -8, 18] } : { rotate: 12 }}
          transition={isWalking ? w : {}}
        >
          <ellipse cx="17" cy="66" rx="5.5" ry="9" fill={BODY} stroke={STROKE} strokeWidth="1" />
        </motion.g>

        {/* Right arm — opposite phase */}
        <motion.g
          style={{ transformOrigin: "48px 59px" }}
          animate={isWalking ? { rotate: [-8, 18, -8] } : { rotate: -12 }}
          transition={isWalking ? w : {}}
        >
          <ellipse cx="55" cy="66" rx="5.5" ry="9" fill={BODY} stroke={STROKE} strokeWidth="1" />
        </motion.g>

        {/* Head */}
        <circle cx="36" cy="29" r="20" fill={BODY} stroke={STROKE} strokeWidth="1" />

        {/* Eyes — slightly asymmetric for personality */}
        <circle cx="29" cy="28" r="3.2" fill="#3d3d3d" />
        <circle cx="41" cy="27.5" r="3.2" fill="#3d3d3d" />

        {/* Eye shine */}
        <circle cx="30.2" cy="26.5" r="1.2" fill="white" />
        <circle cx="42.2" cy="26" r="1.2" fill="white" />

        {/* Blush */}
        <ellipse cx="21" cy="34" rx="3.5" ry="2.2" fill="hsl(15, 65%, 70%)" fillOpacity="0.5" />
        <ellipse cx="51" cy="34" rx="3.5" ry="2.2" fill="hsl(15, 65%, 70%)" fillOpacity="0.5" />
      </motion.g>

      {/* Front leg — renders last so it's in front of body */}
      <motion.g
        style={{ transformOrigin: "28px 76px" }}
        animate={isWalking ? { rotate: [-14, 18, -14] } : { rotate: -6 }}
        transition={isWalking ? w : {}}
      >
        <ellipse cx="28" cy="84" rx="6.5" ry="10.5" fill={BODY} stroke={STROKE} strokeWidth="1" />
      </motion.g>
    </motion.svg>
  );
};

const SmiskiCompanion = ({
  nudgeText,
  nudgeId,
  task = "",
  elapsedSeconds = 0,
  driftCount = 0,
  onTakeBreak,
  onPullBack,
  onEndSession,
  sessionActive,
  suppressMountGreeting = false,
}: Props) => {
  const [smiskiState, setSmiskiStateRaw] = useState<SmiskiState>("hidden");
  const [isWalking, setIsWalking] = useState(false);
  const [isUrgent, setIsUrgent] = useState(false);
  const [isAlertActive, setIsAlertActive] = useState(false);
  const [bubbleText, setBubbleText] = useState("");
  const [showBubble, setShowBubble] = useState(false);

  // Ref-backed state setter so callbacks always read current value
  const stateRef = useRef<SmiskiState>("hidden");
  const setSmiskiState = (s: SmiskiState) => {
    stateRef.current = s;
    setSmiskiStateRaw(s);
  };

  const hasGreeted = useRef(false);
  const sessionStartShown = useRef(false);
  const prevSessionActive = useRef(false);
  const exitTimer = useRef<ReturnType<typeof setTimeout>>();
  const alertExitTimer = useRef<ReturnType<typeof setTimeout>>();
  const quoteInterval = useRef<ReturnType<typeof setInterval>>();

  // ── Helpers ──────────────────────────────────────────────────────────────

  const walkOut = (delay = 0) => {
    const go = () => {
      setShowBubble(false);
      setIsAlertActive(false);
      setSmiskiState("hidden");
    };
    if (delay > 0) {
      exitTimer.current = setTimeout(go, delay);
    } else {
      if (exitTimer.current) clearTimeout(exitTimer.current);
      go();
    }
  };

  const walkIn = (text: string, urgent = false) => {
    // Don't interrupt an active alert with a lower-priority message
    if (stateRef.current !== "hidden" && !urgent) return;
    if (exitTimer.current) clearTimeout(exitTimer.current);
    if (alertExitTimer.current) clearTimeout(alertExitTimer.current);
    setBubbleText(text);
    setIsAlertActive(false);
    setShowBubble(false);
    setSmiskiState("present");
    // Bubble and buttons appear after character walks in (~700ms spring)
    setTimeout(() => {
      setShowBubble(true);
      if (urgent) {
        setIsAlertActive(true);
        setIsUrgent(true);
        setTimeout(() => setIsUrgent(false), 600);
      }
    }, 700);
  };

  // ── Effects ───────────────────────────────────────────────────────────────

  // Mount greeting — skipped on welcome screen so user can trigger it themselves
  useEffect(() => {
    if (hasGreeted.current || suppressMountGreeting) return;
    hasGreeted.current = true;
    setTimeout(async () => {
      const msg = await fetchBuddyMessage(
        "greeting", task, elapsedSeconds, driftCount,
        "hey! I'm your little focus buddy 🌿 I'll be right here cheering you on"
      );
      walkIn(msg);
      walkOut(5500);
    }, 1500);
  }, [suppressMountGreeting]);

  // Session start — fires exactly once when sessionActive first becomes true
  useEffect(() => {
    if (sessionActive && !sessionStartShown.current) {
      sessionStartShown.current = true;
      prevSessionActive.current = true;
      fetchBuddyMessage(
        "session_start", task, elapsedSeconds, driftCount,
        "okay, let's do this! I'm right here with you the whole time 💪"
      ).then((msg) => {
        walkIn(msg);
        walkOut(4500);
      });
    }
  }, [sessionActive, task]);

  // Timed motivation quotes — every 10 minutes
  useEffect(() => {
    if (!sessionActive) {
      if (quoteInterval.current) clearInterval(quoteInterval.current);
      return;
    }
    quoteInterval.current = setInterval(async () => {
      if (stateRef.current !== "hidden") return;
      const fallback = MOTIVATION_QUOTES[Math.floor(Math.random() * MOTIVATION_QUOTES.length)];
      const msg = await fetchBuddyMessage("motivation", task, elapsedSeconds, driftCount, fallback);
      walkIn(msg);
      walkOut(6000);
    }, 10 * 60 * 1000);
    return () => { if (quoteInterval.current) clearInterval(quoteInterval.current); };
  }, [sessionActive]);

  // Nudge / drift alert
  useEffect(() => {
    if (nudgeId == null || !nudgeText) return;
    walkIn(nudgeText, true);
    // Auto-dismiss after 20s if user doesn't respond
    alertExitTimer.current = setTimeout(() => {
      setShowBubble(false);
      setIsAlertActive(false);
      walkOut(200);
    }, 20000);
    return () => { if (alertExitTimer.current) clearTimeout(alertExitTimer.current); };
  }, [nudgeId]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleTabClick = () => {
    if (stateRef.current === "hidden") {
      // Walk in then show menu
      if (exitTimer.current) clearTimeout(exitTimer.current);
      setSmiskiState("present");
      setTimeout(() => setSmiskiState("menu"), 750);
    } else {
      walkOut();
    }
  };

  const handlePullBack = () => {
    if (alertExitTimer.current) clearTimeout(alertExitTimer.current);
    onPullBack();
    walkOut();
  };

  const handleTakeBreak = () => {
    if (alertExitTimer.current) clearTimeout(alertExitTimer.current);
    walkOut();
    onTakeBreak();
  };

  const handleMotivation = async () => {
    const pool = [...MOTIVATION_QUOTES, ...CHEER_MESSAGES];
    const fallback = pool[Math.floor(Math.random() * pool.length)];
    const msg = await fetchBuddyMessage("motivation", task, elapsedSeconds, driftCount, fallback);
    setBubbleText(msg);
    setSmiskiState("present");
    setShowBubble(true);
    walkOut(5500);
  };

  // ── Derived values ────────────────────────────────────────────────────────

  const characterX = smiskiState === "hidden" ? -86 : 0;

  return (
    <>
      {/* Left-edge tab — always shows, covered by character when present */}
      <button
        onClick={handleTabClick}
        title="Summon focus buddy"
        className="fixed left-0 z-40 w-4 h-20 rounded-r-2xl transition-colors shadow-sm"
        style={{
          top: "calc(20% + 12px)",
          backgroundColor: "hsl(75, 30%, 72%, 0.4)",
        }}
      />

      {/* Main container — anchored top-left at 20% */}
      <div
        className="fixed left-0 z-50 flex items-start pointer-events-none"
        style={{ top: "20%" }}
      >
        {/* Character — slides in/out on x axis */}
        <motion.div
          className="pointer-events-auto cursor-pointer flex-shrink-0"
          animate={{ x: characterX }}
          transition={{ type: "spring", stiffness: 190, damping: 24 }}
          onAnimationStart={() => setIsWalking(true)}
          onAnimationComplete={() => setIsWalking(false)}
          onClick={handleTabClick}
        >
          <SmiskiCharacter isWalking={isWalking} isUrgent={isUrgent} />
        </motion.div>

        {/* Content panel — to the right of the character */}
        <div className="ml-3 mt-3 flex flex-col gap-2">

          {/* Speech bubble */}
          <AnimatePresence>
            {showBubble && (
              <motion.div
                key="smiski-bubble"
                initial={{ opacity: 0, x: -12, scale: 0.9 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, x: -12, scale: 0.9 }}
                transition={{ duration: 0.22, ease: "easeOut" }}
                className="pointer-events-auto max-w-[210px] rounded-2xl rounded-tl-sm bg-card border border-border shadow-lg px-4 py-3"
              >
                <p className="text-sm text-foreground leading-relaxed">{bubbleText}</p>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Alert action buttons */}
          <AnimatePresence>
            {isAlertActive && (
              <motion.div
                key="smiski-alert-btns"
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: -8 }}
                transition={{ delay: 0.1, duration: 0.2 }}
                className="pointer-events-auto flex flex-col gap-1.5"
              >
                <button
                  onClick={handleTakeBreak}
                  className="rounded-xl bg-card border border-border px-3 py-2 text-xs font-medium text-foreground hover:bg-secondary transition-colors shadow-sm text-left"
                >
                  Take a break
                </button>
                <button
                  onClick={handlePullBack}
                  className="btn-primary-action rounded-xl px-3 py-2 text-xs font-semibold shadow-sm"
                >
                  Pull me back
                </button>
              </motion.div>
            )}
          </AnimatePresence>

          {/* Menu panel */}
          <AnimatePresence>
            {smiskiState === "menu" && (
              <motion.div
                key="smiski-menu"
                initial={{ opacity: 0, x: -12, scale: 0.92 }}
                animate={{ opacity: 1, x: 0, scale: 1 }}
                exit={{ opacity: 0, x: -12, scale: 0.92 }}
                transition={{ duration: 0.2, ease: "easeOut" }}
                className="pointer-events-auto w-52 rounded-2xl bg-card border border-border shadow-xl p-4"
              >
                <div className="flex items-center justify-between mb-3">
                  <span className="text-xs font-semibold text-muted-foreground tracking-wide uppercase">
                    Focus Buddy
                  </span>
                  <button
                    onClick={() => walkOut()}
                    className="text-muted-foreground hover:text-foreground transition-colors"
                  >
                    <X size={13} />
                  </button>
                </div>
                <div className="space-y-1.5">
                  <button
                    onClick={handleTakeBreak}
                    className="w-full rounded-xl border border-border py-2.5 text-sm font-medium text-foreground hover:bg-secondary transition-colors flex items-center gap-2.5 px-3"
                  >
                    <Coffee size={14} className="text-muted-foreground" />
                    Take a break
                  </button>
                  <button
                    onClick={handleMotivation}
                    className="w-full rounded-xl border border-border py-2.5 text-sm font-medium text-foreground hover:bg-secondary transition-colors flex items-center gap-2.5 px-3"
                  >
                    <Zap size={14} className="text-muted-foreground" />
                    Motivation quote
                  </button>
                  <button
                    onClick={() => { walkOut(); onEndSession(); }}
                    className="w-full rounded-xl border border-border py-2.5 text-sm font-medium text-foreground hover:bg-secondary transition-colors flex items-center gap-2.5 px-3"
                  >
                    <LogOut size={14} className="text-muted-foreground" />
                    End session
                  </button>
                </div>
              </motion.div>
            )}
          </AnimatePresence>

        </div>
      </div>
    </>
  );
};

export default SmiskiCompanion;
