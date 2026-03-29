// ── ADHD Anchor — Content Script ─────────────────────────────────────────────
// Injected into every page. Renders the Smiski overlay and responds to
// messages from the background service worker.

(function () {
  // Don't run inside iframes
  if (window !== window.top) return;
  // Don't inject twice
  if (document.getElementById("adhd-anchor-root")) return;

  // ── Constants ────────────────────────────────────────────────────────────────

  const BODY_COLOR = "hsl(75, 40%, 82%)";
  const STROKE_COLOR = "hsl(75, 22%, 62%)";

  const MOTIVATION_QUOTES = [
    "You're doing amazing. Keep going ✨",
    "Focus is a superpower. You've got this 💪",
    "One task at a time. Progress is progress 🌱",
    "Deep work = deep results. Stay with it 🎯",
    "Your future self will thank you for this 🌟",
    "Almost there — don't stop now 🏁",
  ];

  // ── State ─────────────────────────────────────────────────────────────────────

  let uiState = "hidden"; // "hidden" | "present" | "menu"
  let isAlertActive = false;
  let exitTimer = null;
  let alertExitTimer = null;

  // ── Build DOM ─────────────────────────────────────────────────────────────────

  const root = document.createElement("div");
  root.id = "adhd-anchor-root";

  const tab = document.createElement("button");
  tab.id = "adhd-anchor-tab";
  tab.title = "Summon focus buddy";
  tab.addEventListener("click", handleTabClick);

  const charWrap = document.createElement("div");
  charWrap.id = "adhd-anchor-character";
  charWrap.addEventListener("click", handleTabClick);
  charWrap.addEventListener("transitionstart", () => charWrap.classList.add("smiski-walking"));
  charWrap.addEventListener("transitionend", () => charWrap.classList.remove("smiski-walking"));

  charWrap.innerHTML = buildSVG();

  const content = document.createElement("div");
  content.id = "adhd-anchor-content";

  const bubble = document.createElement("div");
  bubble.id = "adhd-anchor-bubble";

  const actions = document.createElement("div");
  actions.id = "adhd-anchor-actions";

  const breakBtn = document.createElement("button");
  breakBtn.className = "adhd-anchor-btn";
  breakBtn.textContent = "Take a break";
  breakBtn.addEventListener("click", handleTakeBreak);

  const pullBtn = document.createElement("button");
  pullBtn.className = "adhd-anchor-btn primary";
  pullBtn.textContent = "Pull me back";
  pullBtn.addEventListener("click", handlePullBack);

  actions.appendChild(breakBtn);
  actions.appendChild(pullBtn);

  const menu = document.createElement("div");
  menu.id = "adhd-anchor-menu";
  menu.innerHTML = `
    <div id="adhd-anchor-menu-title">
      <span>Focus Buddy</span>
      <button id="adhd-anchor-menu-close">✕</button>
    </div>
    <button class="adhd-anchor-menu-btn" id="adhd-menu-break">☕ Take a break</button>
    <button class="adhd-anchor-menu-btn" id="adhd-menu-quote">⚡ Motivation quote</button>
    <button class="adhd-anchor-menu-btn" id="adhd-menu-end">↪ End session</button>
  `;

  content.appendChild(bubble);
  content.appendChild(actions);
  content.appendChild(menu);

  root.appendChild(charWrap);
  root.appendChild(content);
  document.body.appendChild(root);
  document.body.appendChild(tab);

  // Wire up menu buttons after DOM insertion
  document.getElementById("adhd-anchor-menu-close").addEventListener("click", walkOut);
  document.getElementById("adhd-menu-break").addEventListener("click", handleTakeBreak);
  document.getElementById("adhd-menu-quote").addEventListener("click", handleMotivation);
  document.getElementById("adhd-menu-end").addEventListener("click", handleEndSession);

  // ── SVG builder ───────────────────────────────────────────────────────────────

  function buildSVG() {
    const b = BODY_COLOR;
    const s = STROKE_COLOR;
    return `
    <svg id="adhd-anchor-svg" width="72" height="96" viewBox="0 0 72 96" fill="none" xmlns="http://www.w3.org/2000/svg">
      <!-- Ground shadow -->
      <ellipse cx="36" cy="93" rx="16" ry="4" class="smiski-shadow" />

      <!-- Back leg (behind body) -->
      <g class="smiski-back-leg">
        <ellipse cx="44" cy="84" rx="6.5" ry="10.5" fill="${b}" stroke="${s}" stroke-width="1" />
      </g>

      <!-- Body bob group: torso + arms + head -->
      <g class="smiski-body-group">
        <!-- Torso -->
        <ellipse cx="36" cy="65" rx="13.5" ry="15" fill="${b}" stroke="${s}" stroke-width="1" />

        <!-- Left arm -->
        <g class="smiski-left-arm">
          <ellipse cx="17" cy="66" rx="5.5" ry="9" fill="${b}" stroke="${s}" stroke-width="1" />
        </g>

        <!-- Right arm (opposite phase) -->
        <g class="smiski-right-arm">
          <ellipse cx="55" cy="66" rx="5.5" ry="9" fill="${b}" stroke="${s}" stroke-width="1" />
        </g>

        <!-- Head -->
        <circle cx="36" cy="29" r="20" fill="${b}" stroke="${s}" stroke-width="1" />

        <!-- Eyes — slightly asymmetric -->
        <circle cx="29" cy="28" r="3.2" fill="#3d3d3d" />
        <circle cx="41" cy="27.5" r="3.2" fill="#3d3d3d" />

        <!-- Eye shine -->
        <circle cx="30.2" cy="26.5" r="1.2" fill="white" />
        <circle cx="42.2" cy="26" r="1.2" fill="white" />

        <!-- Blush -->
        <ellipse cx="21" cy="34" rx="3.5" ry="2.2" fill="hsl(15, 65%, 70%)" fill-opacity="0.5" />
        <ellipse cx="51" cy="34" rx="3.5" ry="2.2" fill="hsl(15, 65%, 70%)" fill-opacity="0.5" />
      </g>

      <!-- Front leg (in front of body) -->
      <g class="smiski-front-leg">
        <ellipse cx="28" cy="84" rx="6.5" ry="10.5" fill="${b}" stroke="${s}" stroke-width="1" />
      </g>
    </svg>`;
  }

  // ── Core helpers ──────────────────────────────────────────────────────────────

  function walkIn(text, urgent = false) {
    if (uiState !== "hidden" && !urgent) return;
    if (exitTimer) { clearTimeout(exitTimer); exitTimer = null; }
    if (alertExitTimer) { clearTimeout(alertExitTimer); alertExitTimer = null; }

    bubble.textContent = text;
    hideBubble();
    hideActions();
    hideMenu();
    isAlertActive = false;

    uiState = "present";
    charWrap.classList.add("visible");

    // Bubble appears after character walks in (~450ms transition)
    setTimeout(() => {
      showBubble();
      if (urgent) {
        showActions();
        isAlertActive = true;
        triggerUrgentWiggle();
      }
      content.classList.add("visible");
    }, 500);
  }

  function walkOut(delay = 0) {
    const go = () => {
      hideBubble();
      hideActions();
      hideMenu();
      isAlertActive = false;
      content.classList.remove("visible");
      uiState = "hidden";
      charWrap.classList.remove("visible");
    };
    if (delay > 0) {
      exitTimer = setTimeout(go, delay);
    } else {
      if (exitTimer) clearTimeout(exitTimer);
      go();
    }
  }

  function showBubble()  { bubble.classList.add("visible"); }
  function hideBubble()  { bubble.classList.remove("visible"); }
  function showActions() { actions.classList.add("visible"); }
  function hideActions() { actions.classList.remove("visible"); }
  function showMenu()    { menu.classList.add("visible"); }
  function hideMenu()    { menu.classList.remove("visible"); }

  function triggerUrgentWiggle() {
    const svg = document.getElementById("adhd-anchor-svg");
    if (!svg) return;
    svg.classList.remove("smiski-urgent");
    // Force reflow so animation replays
    void svg.offsetWidth;
    svg.classList.add("smiski-urgent");
    setTimeout(() => svg.classList.remove("smiski-urgent"), 600);
  }

  // ── Handlers ──────────────────────────────────────────────────────────────────

  function handleTabClick() {
    if (uiState === "hidden") {
      if (exitTimer) clearTimeout(exitTimer);
      charWrap.classList.add("visible");
      uiState = "present";
      setTimeout(() => {
        uiState = "menu";
        showMenu();
        content.classList.add("visible");
      }, 500);
    } else {
      walkOut();
    }
  }

  function handleTakeBreak() {
    if (alertExitTimer) clearTimeout(alertExitTimer);
    sendAction("take_break");
    walkOut();
  }

  function handlePullBack() {
    if (alertExitTimer) clearTimeout(alertExitTimer);
    // Tell background to close this tab and focus the app
    chrome.runtime.sendMessage({ type: "pull_back_close_tab" });
    walkOut();
  }

  function handleMotivation() {
    const q = MOTIVATION_QUOTES[Math.floor(Math.random() * MOTIVATION_QUOTES.length)];
    bubble.textContent = q;
    hideActions();
    hideMenu();
    uiState = "present";
    showBubble();
    content.classList.add("visible");
    walkOut(5000);
  }

  function handleEndSession() {
    sendAction("session_end");
    walkOut();
  }

  function sendAction(action) {
    chrome.runtime.sendMessage({ type: "user_action", action });
  }

  // ── Backend event handler ─────────────────────────────────────────────────────

  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "backend_event") {
      const data = msg.payload;

      if (data.type === "nudge" && data.nudge_type === "encouragement") {
        walkIn(data.message || "Still on it 💪 I'm watching over you");
        walkOut(5000);

      } else if (data.type === "nudge" || data.type === "phone_detected") {
        const source = data.source || "something";
        const text = data.message || `Hey, you drifted to ${source}. Break or get back?`;
        walkIn(text, true);
        alertExitTimer = setTimeout(() => {
          hideBubble();
          hideActions();
          isAlertActive = false;
          walkOut(200);
        }, 20000);

      } else if (data.type === "wave_detected") {
        walkIn(data.message || "Hey! 👋 Great to see you! Ready to crush this session?");
        walkOut(6000);

      } else if (data.type === "session_started") {
        walkIn("Let's focus! 💪 I'm here if you need me");
        walkOut(4000);

      } else if (data.type === "session_summary") {
        walkIn("Great session! 🎉 Time to review how you did.");
        walkOut(5000);
      }
    }
  });

  // ── Report current page to background ────────────────────────────────────────

  const currentHost = window.location.hostname;

  const DISTRACTION_HOSTS_LOCAL = new Set([
    "youtube.com", "www.youtube.com",
    "reddit.com", "www.reddit.com",
    "twitter.com", "www.twitter.com", "x.com",
    "instagram.com", "www.instagram.com",
    "tiktok.com", "www.tiktok.com",
    "facebook.com", "www.facebook.com",
    "netflix.com", "www.netflix.com",
    "twitch.tv", "www.twitch.tv",
    "hulu.com", "www.hulu.com",
    "discord.com", "www.discord.com",
  ]);

  const isDistractionSite = DISTRACTION_HOSTS_LOCAL.has(currentHost);

  chrome.runtime.sendMessage({
    type: "page_report",
    hostname: currentHost,
  });

  // ── Greeting on first load ────────────────────────────────────────────────────
  // Skip on distraction sites — the drift nudge from background.js shows instead

  // Encouragement on relevant pages is handled by the backend (every 5th tab).
  // The backend sends a nudge with nudge_type="encouragement" which the
  // backend_event handler above forwards to Smiski.

})();
