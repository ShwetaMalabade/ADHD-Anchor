// ── ADHD Anchor — Popup Script ────────────────────────────────────────────────

const sessionDot    = document.getElementById("session-dot");
const sessionStatus = document.getElementById("session-status");
const wsDot         = document.getElementById("ws-dot");
const wsStatus      = document.getElementById("ws-status");
const btnStart      = document.getElementById("btn-start-session");
const btnReconnect  = document.getElementById("btn-reconnect");

// Load current state from storage
chrome.storage.local.get(["sessionActive", "wsConnected"], ({ sessionActive, wsConnected }) => {
  if (sessionActive) {
    sessionDot.classList.add("active");
    sessionStatus.textContent = "Session active";
    btnStart.textContent = "End session";
  }
  if (wsConnected) {
    wsDot.classList.add("connected");
    wsStatus.textContent = "Backend connected";
  } else {
    wsStatus.textContent = "Backend not connected";
  }
});

btnStart.addEventListener("click", () => {
  chrome.storage.local.get("sessionActive", ({ sessionActive }) => {
    if (sessionActive) {
      chrome.storage.local.set({ sessionActive: false });
      sessionDot.classList.remove("active");
      sessionStatus.textContent = "No active session";
      btnStart.textContent = "Start focus session";
    } else {
      chrome.storage.local.set({ sessionActive: true });
      sessionDot.classList.add("active");
      sessionStatus.textContent = "Session active";
      btnStart.textContent = "End session";
      // Notify background so it can tell the WS server
      chrome.runtime.sendMessage({ type: "user_action", action: "session_start" });
    }
  });
});

btnReconnect.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "connect_ws" });
  wsStatus.textContent = "Reconnecting…";
  wsDot.classList.remove("connected");
});
