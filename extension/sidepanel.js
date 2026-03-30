// ── DOM refs ──
const messagesEl = document.getElementById("messages");
const inputEl = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const statusEl = document.getElementById("status-indicator");
const settingsBtn = document.getElementById("settings-btn");
const settingsOverlay = document.getElementById("settings-overlay");
const settingsClose = document.getElementById("settings-close");
const settingsSave = document.getElementById("settings-save");
const tempSlider = document.getElementById("setting-temperature");
const tempValue = document.getElementById("temp-value");

// ── State ──
let ws = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;
let isAgentWorking = false;
let settings = {
  provider: "anthropic",
  api_key: "",
  base_url: "",
  model: "",
  temperature: 0.5,
};

// ── Settings ──
function loadSettings() {
  chrome.storage.local.get("stockholm_settings", (result) => {
    if (result.stockholm_settings) {
      settings = { ...settings, ...result.stockholm_settings };
    }
    applySettingsToUI();
  });
}

function applySettingsToUI() {
  document.getElementById("setting-provider").value = settings.provider;
  document.getElementById("setting-api-key").value = settings.api_key;
  document.getElementById("setting-base-url").value = settings.base_url;
  document.getElementById("setting-model").value = settings.model;
  document.getElementById("setting-temperature").value = settings.temperature;
  tempValue.textContent = settings.temperature;
}

function saveSettings() {
  settings.provider = document.getElementById("setting-provider").value;
  settings.api_key = document.getElementById("setting-api-key").value;
  settings.base_url = document.getElementById("setting-base-url").value;
  settings.model = document.getElementById("setting-model").value;
  settings.temperature = parseFloat(document.getElementById("setting-temperature").value);
  chrome.storage.local.set({ stockholm_settings: settings });
  settingsOverlay.classList.add("hidden");
}

settingsBtn.addEventListener("click", () => settingsOverlay.classList.remove("hidden"));
settingsClose.addEventListener("click", () => settingsOverlay.classList.add("hidden"));
settingsOverlay.addEventListener("click", (e) => {
  if (e.target === settingsOverlay) settingsOverlay.classList.add("hidden");
});
settingsSave.addEventListener("click", saveSettings);
tempSlider.addEventListener("input", () => {
  tempValue.textContent = tempSlider.value;
});

// ── WebSocket ──
function setStatus(state) {
  statusEl.className = "status " + state;
  statusEl.title = state.charAt(0).toUpperCase() + state.slice(1);
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  setStatus("connecting");
  ws = new WebSocket("ws://localhost:8765/ws");

  ws.addEventListener("open", () => {
    setStatus("connected");
    reconnectDelay = 1000;
  });

  ws.addEventListener("close", () => {
    setStatus("disconnected");
    ws = null;
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {
    // close event will fire after this
  });

  ws.addEventListener("message", (event) => {
    try {
      const data = JSON.parse(event.data);
      handleServerMessage(data);
    } catch {
      // ignore malformed messages
    }
  });
}

function scheduleReconnect() {
  setTimeout(() => {
    connect();
    reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT_DELAY);
  }, reconnectDelay);
}

// ── Message handling ──
function handleServerMessage(data) {
  removeThinking();

  switch (data.type) {
    case "step":
      addStepMessage(data);
      showThinking();
      break;
    case "result":
      addResultMessage(data);
      isAgentWorking = false;
      updateInputState();
      break;
    case "error":
      addMessage("error", data.message || "Unknown error");
      isAgentWorking = false;
      updateInputState();
      break;
    default:
      if (data.content) addMessage("agent", data.content);
  }
}

async function getActiveTab() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url && !tab.url.startsWith("chrome://")) {
      return { url: tab.url, title: tab.title || "" };
    }
  } catch {}
  return null;
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  addMessage("user", text);
  inputEl.value = "";
  autoResizeInput();

  const activeTab = await getActiveTab();

  const payload = {
    type: "task",
    content: text,
    settings: {
      provider: settings.provider,
      model: settings.model,
      base_url: settings.base_url,
      api_key: settings.api_key,
      temperature: settings.temperature,
    },
    active_tab: activeTab,
  };

  ws.send(JSON.stringify(payload));
  isAgentWorking = true;
  updateInputState();
  showThinking();
}

// ── Rendering ──
function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = "message " + role;
  el.innerHTML = renderMarkdown(text);
  messagesEl.appendChild(el);
  scrollToBottom();
}

function addStepMessage(data) {
  const el = document.createElement("div");
  el.className = "message agent";

  let html = '<div class="step-header">';
  html += `<span class="step-number">${data.step_number || "?"}</span>`;
  html += "<span>Step</span>";
  html += "</div>";

  if (data.goal) {
    html += `<div class="step-goal">${escapeHtml(data.goal)}</div>`;
  }
  if (data.actions) {
    const actions = Array.isArray(data.actions) ? data.actions.join(", ") : data.actions;
    html += `<div class="step-actions">${escapeHtml(actions)}</div>`;
  }
  if (data.url) {
    html += `<div class="step-url">${escapeHtml(data.url)}</div>`;
  }

  el.innerHTML = html;
  messagesEl.appendChild(el);
  scrollToBottom();
}

function addResultMessage(data) {
  const el = document.createElement("div");
  el.className = "message agent";

  let html = '<div class="result-summary">';
  html += renderMarkdown(data.summary || "Task completed.");
  html += "</div>";

  const meta = [];
  if (data.steps_taken != null) meta.push(`${data.steps_taken} steps`);
  if (data.errors && data.errors.length) meta.push(`${data.errors.length} error(s)`);
  if (meta.length) {
    html += `<div class="result-meta">${meta.join(" \u00b7 ")}</div>`;
  }

  el.innerHTML = html;
  messagesEl.appendChild(el);
  scrollToBottom();
}

function showThinking() {
  if (document.querySelector(".thinking")) return;
  const el = document.createElement("div");
  el.className = "thinking";
  el.innerHTML = "<span></span><span></span><span></span>";
  messagesEl.appendChild(el);
  scrollToBottom();
}

function removeThinking() {
  const el = document.querySelector(".thinking");
  if (el) el.remove();
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

function updateInputState() {
  sendBtn.disabled = isAgentWorking;
  inputEl.disabled = isAgentWorking;
  inputEl.placeholder = isAgentWorking ? "Agent is working..." : "Describe what you want to do...";
}

// ── Markdown-lite renderer ──
function escapeHtml(str) {
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
  let html = escapeHtml(text);

  // Code blocks (``` ... ```)
  html = html.replace(/```([\s\S]*?)```/g, (_m, code) => `<pre>${code.trim()}</pre>`);

  // Inline code
  html = html.replace(/`([^`]+)`/g, (_m, code) => `<code>${code}</code>`);

  // Bold
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");

  // Line breaks
  html = html.replace(/\n/g, "<br>");

  return html;
}

// ── Input handling ──
inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

inputEl.addEventListener("input", autoResizeInput);

function autoResizeInput() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 120) + "px";
}

sendBtn.addEventListener("click", sendMessage);

// ── Init ──
loadSettings();
connect();
