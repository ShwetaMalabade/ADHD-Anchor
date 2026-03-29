import { useState, useEffect, useRef } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Coffee, Zap, LogOut, X } from "lucide-react";

type SmiskiState = "hidden" | "present" | "menu";

interface Props {
  nudgeText?: string;
  nudgeId?: number;
  noteEvent?: { text: string; id: number } | null;
  buddyPromptEvent?: { id: number } | null;
  buddyAckEvent?: { text: string; id: number } | null;
  onTakeBreak: () => void;
  onPullBack: () => void;
  onEndSession: () => void;
  sessionActive: boolean;
  suppressMountGreeting?: boolean;
}

const MOTIVATION_QUOTES = [
  "You're doing amazing. Keep going ✨",
  "Focus is a superpower. You've got this 💪",
  "One task at a time. Progress is progress 🌱",
  "Deep work = deep results. Stay with it 🎯",
  "Your future self will thank you for this 🌟",
  "Almost there — don't stop now 🏁",
];

const NOTE_FOLLOWUPS: Array<{ pattern: RegExp; response: string }> = [
  { pattern: /\b(tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday|next week|next month|january|february|march|april|may|june|july|august|september|october|november|december|\d{1,2}\/\d{1,2}|\d{1,2}(st|nd|rd|th))\b/i, response: "📅 Want me to add this to your calendar?" },
  { pattern: /\b(deadline|due|by\s+\w+day|before|until|submit|deliver|turn\s+in)\b/i, response: "⏰ Should I set a reminder for this deadline?" },
  { pattern: /\b(meeting|call|zoom|standup|sync|interview|presentation|demo)\b/i, response: "📞 Should I block time for this?" },
  { pattern: /\b(email|message|reply|respond|text|slack|send|follow\s*up)\b/i, response: "📧 Want a reminder to follow up on that?" },
  { pattern: /\b(need to|have to|should|must|todo|fix|update|review|check|finish|complete|implement|build|create|write)\b/i, response: "✅ Should I add this to your task list?" },
  { pattern: /\b(idea|thought|maybe|what if|could|might|consider|brainstorm)\b/i, response: "💡 Great idea! Saved for when you're ready" },
  { pattern: /\b(buy|purchase|order|shop|store|grocery|pick\s*up)\b/i, response: "🛒 Want me to add this to a shopping list?" },
  { pattern: /\b(read|article|paper|book|blog|video|watch|listen)\b/i, response: "📚 I'll save this for your reading list!" },
];

function generateNoteFollowUp(text: string): string {
  for (const { pattern, response } of NOTE_FOLLOWUPS) {
    if (pattern.test(text)) return response;
  }
  return "📝 Got it! I've noted that for you ✨";
}

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
      animate={isUrgent ? { rotate: [0, -7, 7, -5, 5, -2, 2, 0] } : { rotate: 0 }}
      transition={isUrgent ? { duration: 0.55 } : {}}
    >
      {/* Ground shadow */}
      <ellipse cx="36" cy="93" rx="16" ry="4" fill={BODY} fillOpacity="0.25" />

      {/* Back leg */}
      <motion.g
        style={{ transformOrigin: "44px 76px" }}
        animate={isWalking ? { rotate: [18, -14, 18] } : { rotate: 6 }}
        transition={isWalking ? w : {}}
      >
        <ellipse cx="44" cy="84" rx="6.5" ry="10.5" fill={BODY} stroke={STROKE} strokeWidth="1" />
      </motion.g>

      {/* Body bob group */}
      <motion.g
        animate={isWalking ? { y: [0, -4, 0, -4, 0] } : { y: 0 }}
        transition={isWalking ? w : {}}
      >
        {/* Torso */}
        <ellipse cx="36" cy="65" rx="13.5" ry="15" fill={BODY} stroke={STROKE} strokeWidth="1" />

        {/* Left arm */}
        <motion.g
          style={{ transformOrigin: "24px 59px" }}
          animate={isWalking ? { rotate: [18, -8, 18] } : { rotate: 12 }}
          transition={isWalking ? w : {}}
        >
          <ellipse cx="17" cy="66" rx="5.5" ry="9" fill={BODY} stroke={STROKE} strokeWidth="1" />
        </motion.g>

        {/* Right arm */}
        <motion.g
          style={{ transformOrigin: "48px 59px" }}
          animate={isWalking ? { rotate: [-8, 18, -8] } : { rotate: -12 }}
          transition={isWalking ? w : {}}
        >
          <ellipse cx="55" cy="66" rx="5.5" ry="9" fill={BODY} stroke={STROKE} strokeWidth="1" />
        </motion.g>

        {/* Head */}
        <circle cx="36" cy="29" r="20" fill={BODY} stroke={STROKE} strokeWidth="1" />

        {/* Eyes */}
        <circle cx="29" cy="28" r="3.2" fill="#3d3d3d" />
        <circle cx="41" cy="27.5" r="3.2" fill="#3d3d3d" />

        {/* Eye shine */}
        <circle cx="30.2" cy="26.5" r="1.2" fill="white" />
        <circle cx="42.2" cy="26" r="1.2" fill="white" />

        {/* Blush */}
        <ellipse cx="21" cy="34" rx="3.5" ry="2.2" fill="hsl(15, 65%, 70%)" fillOpacity="0.5" />
        <ellipse cx="51" cy="34" rx="3.5" ry="2.2" fill="hsl(15, 65%, 70%)" fillOpacity="0.5" />
      </motion.g>

      {/* Front leg */}
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

const BUDDY_PROMPTS = [
  "What would you like me to note? \u{1F4DD}",
  "I'm listening! What's on your mind? \u{1F4AD}",
  "Sure thing! What should I jot down? \u270D\uFE0F",
  "Go ahead, I'm all ears! \u{1F442}",
];

const BUDDY_ACKS = [
  "Noted! Back to it \u{1F4AA}",
  "Got it! Let's keep going \u2728",
  "Okay! I'll remember that \u{1F4CC}",
  "All saved! You're doing great \u{1F31F}",
];

function generateAckFromReply(reply: string): string {
  const lower = reply.toLowerCase();
  if (/\b(yes|yeah|yep|sure|please|do it|go ahead)\b/.test(lower)) {
    return "On it! I'll take care of that \u2705";
  }
  if (/\b(no|nah|nope|not now|later|skip|never\s*mind)\b/.test(lower)) {
    return "No worries! Back to focusing \u{1F4AA}";
  }
  if (/\b(thanks|thank you|thx)\b/.test(lower)) {
    return "Anytime! Let's keep crushing it \u{1F525}";
  }
  return BUDDY_ACKS[Math.floor(Math.random() * BUDDY_ACKS.length)];
}

const SmiskiCompanion = ({
  nudgeText,
  nudgeId,
  noteEvent,
  buddyPromptEvent,
  buddyAckEvent,
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
  const [isThinking, setIsThinking] = useState(false);

  const stateRef = useRef<SmiskiState>("hidden");
  const setSmiskiState = (s: SmiskiState) => {
    stateRef.current = s;
    setSmiskiStateRaw(s);
  };

  const hasGreeted = useRef(false);
  const prevSessionActive = useRef(false);
  const exitTimer = useRef<ReturnType<typeof setTimeout>>();
  const alertExitTimer = useRef<ReturnType<typeof setTimeout>>();
  const quoteInterval = useRef<ReturnType<typeof setInterval>>();
  const noteTimers = useRef<ReturnType<typeof setTimeout>[]>([]);
  const buddyBusy = useRef(false); // true while "hey buddy" conversation flow is active

  // ── Helpers ──────────────────────────────────────────────────────────────

  const walkOut = (delay = 0) => {
    const go = () => {
      setShowBubble(false);
      setIsAlertActive(false);
      setIsThinking(false);
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
    if (stateRef.current !== "hidden" && !urgent) return;
    if (exitTimer.current) clearTimeout(exitTimer.current);
    if (alertExitTimer.current) clearTimeout(alertExitTimer.current);
    setBubbleText(text);
    setIsAlertActive(false);
    setShowBubble(false);
    setIsThinking(false);
    setSmiskiState("present");
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

  // Mount greeting
  useEffect(() => {
    if (hasGreeted.current || suppressMountGreeting) return;
    hasGreeted.current = true;
    setTimeout(() => {
      walkIn("Hi! I'm your focus buddy ✨ I'll keep you on track today");
      walkOut(5000);
    }, 11500);
  }, []);

  // Session start
  useEffect(() => {
    if (sessionActive && !prevSessionActive.current) {
      prevSessionActive.current = true;
      walkIn("Let's focus! 💪 I'm here if you need me");
      walkOut(4000);
    }
    if (!sessionActive) prevSessionActive.current = false;
  }, [sessionActive]);

  // Timed motivation quotes
  useEffect(() => {
    if (!sessionActive) {
      if (quoteInterval.current) clearInterval(quoteInterval.current);
      return;
    }
    quoteInterval.current = setInterval(() => {
      if (stateRef.current !== "hidden" || buddyBusy.current) return;
      const q = MOTIVATION_QUOTES[Math.floor(Math.random() * MOTIVATION_QUOTES.length)];
      walkIn(q);
      walkOut(5500);
    }, 10 * 60 * 1000);
    return () => { if (quoteInterval.current) clearInterval(quoteInterval.current); };
  }, [sessionActive]);

  // Nudge / drift alert — skip if buddy conversation is in progress
  useEffect(() => {
    if (nudgeId == null || !nudgeText) return;
    if (buddyBusy.current) return;
    walkIn(nudgeText, true);
    alertExitTimer.current = setTimeout(() => {
      setShowBubble(false);
      setIsAlertActive(false);
      walkOut(200);
    }, 20000);
    return () => { if (alertExitTimer.current) clearTimeout(alertExitTimer.current); };
  }, [nudgeId]);

  // Note event — thinking animation then smart follow-up
  // When triggered from "hey buddy" flow (buddyPromptEvent active), Smiski is already
  // on screen, so we show thinking clouds then follow-up WITHOUT auto-dismiss (waiting for reply).
  // When triggered directly ("note something"), Smiski walks in fresh and auto-dismisses.
  useEffect(() => {
    if (!noteEvent?.id) return;

    const followUp = generateNoteFollowUp(noteEvent.text);
    const isFromBuddy = buddyPromptEvent?.id != null && buddyPromptEvent.id > 0;

    // Clear any previous note timers
    noteTimers.current.forEach(clearTimeout);
    noteTimers.current = [];

    // Cancel existing states
    if (exitTimer.current) clearTimeout(exitTimer.current);
    if (alertExitTimer.current) clearTimeout(alertExitTimer.current);

    // Show thinking clouds (Smiski is already present if from buddy mode)
    setShowBubble(false);
    setIsAlertActive(false);
    setIsThinking(true);
    setSmiskiState("present");

    // After thinking, show the follow-up
    const showFollowUpTimer = setTimeout(() => {
      setIsThinking(false);
      setBubbleText(followUp);
      setShowBubble(true);
    }, 2000);
    noteTimers.current.push(showFollowUpTimer);

    // Only auto-dismiss if NOT from buddy mode (buddy mode waits for reply)
    if (!isFromBuddy) {
      const dismissTimer = setTimeout(() => {
        setShowBubble(false);
        walkOut(200);
      }, 7500);
      noteTimers.current.push(dismissTimer);
    }

    return () => {
      noteTimers.current.forEach(clearTimeout);
      noteTimers.current = [];
    };
  }, [noteEvent?.id]);

  // "Hey buddy" — Smiski walks in and asks what to note
  useEffect(() => {
    if (!buddyPromptEvent?.id) return;

    buddyBusy.current = true;

    noteTimers.current.forEach(clearTimeout);
    noteTimers.current = [];
    if (exitTimer.current) clearTimeout(exitTimer.current);
    if (alertExitTimer.current) clearTimeout(alertExitTimer.current);

    const prompt = BUDDY_PROMPTS[Math.floor(Math.random() * BUDDY_PROMPTS.length)];
    walkIn(prompt);
    // Stay visible while waiting for user's note (no auto-dismiss)
  }, [buddyPromptEvent?.id]);

  // Follow-up reply acknowledged — Smiski says ack and walks out
  useEffect(() => {
    if (!buddyAckEvent?.id) return;

    noteTimers.current.forEach(clearTimeout);
    noteTimers.current = [];
    if (exitTimer.current) clearTimeout(exitTimer.current);

    const ack = generateAckFromReply(buddyAckEvent.text);
    setBubbleText(ack);
    setIsThinking(false);
    setShowBubble(true);
    setSmiskiState("present");

    // Release buddy lock after walkout completes
    const releaseTimer = setTimeout(() => {
      buddyBusy.current = false;
    }, 3700);
    noteTimers.current.push(releaseTimer);

    walkOut(3500);
  }, [buddyAckEvent?.id]);

  // ── Handlers ─────────────────────────────────────────────────────────────

  const handleTabClick = () => {
    if (stateRef.current === "hidden") {
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

  const handleMotivation = () => {
    const q = MOTIVATION_QUOTES[Math.floor(Math.random() * MOTIVATION_QUOTES.length)];
    setBubbleText(q);
    setSmiskiState("present");
    setShowBubble(true);
    walkOut(5000);
  };

  // ── Derived values ────────────────────────────────────────────────────────

  const characterX = smiskiState === "hidden" ? -86 : 0;

  return (
    <>
      {/* Left-edge tab */}
      <button
        onClick={handleTabClick}
        title="Summon focus buddy"
        className="fixed left-0 z-40 w-4 h-20 rounded-r-2xl transition-colors shadow-sm"
        style={{
          top: "calc(20% + 12px)",
          backgroundColor: "hsl(75, 30%, 72%, 0.4)",
        }}
      />

      {/* Main container */}
      <div
        className="fixed left-0 z-50 flex items-start pointer-events-none"
        style={{ top: "20%" }}
      >
        {/* Character with thought bubbles */}
        <motion.div
          className="pointer-events-auto cursor-pointer flex-shrink-0 relative"
          animate={{ x: characterX }}
          transition={{ type: "spring", stiffness: 190, damping: 24 }}
          onAnimationStart={() => setIsWalking(true)}
          onAnimationComplete={() => setIsWalking(false)}
          onClick={handleTabClick}
        >
          {/* Thought bubbles — appear above the character's head when thinking */}
          <AnimatePresence>
            {isThinking && smiskiState !== "hidden" && (
              <>
                <motion.div
                  key="thought-1"
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: [1, 1.15, 1], opacity: 1 }}
                  exit={{ scale: 0, opacity: 0 }}
                  transition={{ delay: 0.7, duration: 0.3, scale: { delay: 0.7, duration: 1.2, repeat: Infinity } }}
                  className="absolute z-10"
                  style={{ top: "6px", right: "2px" }}
                >
                  <div className="w-[7px] h-[7px] rounded-full bg-card border border-border shadow-sm" />
                </motion.div>
                <motion.div
                  key="thought-2"
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: [1, 1.1, 1], opacity: 1 }}
                  exit={{ scale: 0, opacity: 0 }}
                  transition={{ delay: 1.1, duration: 0.3, scale: { delay: 1.1, duration: 1.4, repeat: Infinity } }}
                  className="absolute z-10"
                  style={{ top: "-6px", right: "-4px" }}
                >
                  <div className="w-[11px] h-[11px] rounded-full bg-card border border-border shadow-sm" />
                </motion.div>
                <motion.div
                  key="thought-3"
                  initial={{ scale: 0, opacity: 0 }}
                  animate={{ scale: [1, 1.08, 1], opacity: 1 }}
                  exit={{ scale: 0, opacity: 0 }}
                  transition={{ delay: 1.5, duration: 0.3, scale: { delay: 1.5, duration: 1.6, repeat: Infinity } }}
                  className="absolute z-10"
                  style={{ top: "-22px", right: "-8px" }}
                >
                  <div className="w-[16px] h-[16px] rounded-full bg-card border border-border shadow-md flex items-center justify-center">
                    <span className="text-[8px]">💭</span>
                  </div>
                </motion.div>
              </>
            )}
          </AnimatePresence>

          <SmiskiCharacter isWalking={isWalking} isUrgent={isUrgent} />
        </motion.div>

        {/* Content panel */}
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
