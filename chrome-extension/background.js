// ── ADHD Anchor — Background Service Worker ──────────────────────────────────
// Owns the single WebSocket connection to the backend.
// Routes messages between backend ↔ content scripts.

const WS_URL = "ws://localhost:8000/ws";

const DISTRACTION_HOSTS = new Set([
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

let ws = null;
let wsReady = false;
let reconnectTimeout = null;

// ── WebSocket management ──────────────────────────────────────────────────────

function connectWS() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  try {
    ws = new WebSocket(WS_URL);

    ws.onopen = () => {
      wsReady = true;
      if (reconnectTimeout) { clearTimeout(reconnectTimeout); reconnectTimeout = null; }
      broadcastToTabs({ type: "ws_status", connected: true });
    };

    ws.onmessage = (e) => {
      let data;
      try { data = JSON.parse(e.data); } catch { return; }

      // Forward backend events to ALL content scripts so every open tab can react
      chrome.tabs.query({}, (tabs) => {
        for (const tab of tabs) {
          if (tab.id) {
            chrome.tabs.sendMessage(tab.id, { type: "backend_event", payload: data }).catch(() => {});
          }
        }
      });

      // Track session state
      if (data.type === "session_started") {
        chrome.storage.local.set({ sessionActive: true });
      } else if (data.type === "session_summary" || data.type === "session_ended") {
        chrome.storage.local.set({ sessionActive: false });
      }
    };

    ws.onerror = () => { wsReady = false; };

    ws.onclose = () => {
      wsReady = false;
      ws = null;
      broadcastToTabs({ type: "ws_status", connected: false });
      // Reconnect after 5 seconds
      reconnectTimeout = setTimeout(connectWS, 5000);
    };
  } catch (err) {
    reconnectTimeout = setTimeout(connectWS, 5000);
  }
}

function sendToBackend(payload) {
  if (ws && ws.readyState === WebSocket.OPEN) {
    ws.send(JSON.stringify(payload));
    return true;
  }
  return false;
}

function broadcastToTabs(msg) {
  chrome.tabs.query({}, (tabs) => {
    for (const tab of tabs) {
      if (tab.id) chrome.tabs.sendMessage(tab.id, msg).catch(() => {});
    }
  });
}

// ── Navigation detection ──────────────────────────────────────────────────────
// When a tab navigates to a distraction site while a session is active,
// immediately tell the backend so it can fire a nudge.

const DISTRACTION_NAMES = {
  "youtube.com": "YouTube", "www.youtube.com": "YouTube",
  "reddit.com": "Reddit", "www.reddit.com": "Reddit",
  "twitter.com": "Twitter", "www.twitter.com": "Twitter", "x.com": "Twitter/X",
  "instagram.com": "Instagram", "www.instagram.com": "Instagram",
  "tiktok.com": "TikTok", "www.tiktok.com": "TikTok",
  "facebook.com": "Facebook", "www.facebook.com": "Facebook",
  "netflix.com": "Netflix", "www.netflix.com": "Netflix",
  "twitch.tv": "Twitch", "www.twitch.tv": "Twitch",
  "hulu.com": "Hulu", "www.hulu.com": "Hulu",
  "discord.com": "Discord", "www.discord.com": "Discord",
};

chrome.webNavigation.onCommitted.addListener((details) => {
  if (details.frameId !== 0) return; // top-level frame only
  try {
    const host = new URL(details.url).hostname;
    if (!DISTRACTION_HOSTS.has(host)) return;

    chrome.storage.local.get("sessionActive", ({ sessionActive }) => {
      if (!sessionActive) return;

      const name = DISTRACTION_NAMES[host] || host;

      // Tell backend if connected
      sendToBackend({ type: "page_visit", hostname: host, url: details.url });

      // Local fallback — fires immediately regardless of backend
      const nudgeEvent = {
        type: "nudge",
        source: name,
        message: `Hey, you drifted to ${name}. Break or get back?`,
      };
      chrome.tabs.sendMessage(details.tabId, {
        type: "backend_event",
        payload: nudgeEvent,
      }).catch(() => {
        // Content script may not be ready yet — retry after a short delay
        setTimeout(() => {
          chrome.tabs.sendMessage(details.tabId, {
            type: "backend_event",
            payload: nudgeEvent,
          }).catch(() => {});
        }, 1500);
      });
    });
  } catch {}
});

// ── Messages from content scripts ─────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  switch (msg.type) {
    case "page_report":
      // Content script reporting its page on load
      chrome.storage.local.get("sessionActive", ({ sessionActive }) => {
        if (sessionActive && DISTRACTION_HOSTS.has(msg.hostname)) {
          sendToBackend({ type: "page_visit", hostname: msg.hostname });
        }
      });
      break;

    case "user_action":
      // "pull_back" or "take_break" button pressed in content script
      sendToBackend(msg);
      if (msg.action === "session_start") {
        chrome.storage.local.set({ sessionActive: true });
      }
      break;

    case "get_state":
      chrome.storage.local.get(["sessionActive"], sendResponse);
      return true; // async response

    case "connect_ws":
      connectWS();
      break;
  }
});

// ── Init ──────────────────────────────────────────────────────────────────────

connectWS();
