// ── Constants ──
const API_BASE = "http://localhost:8765";

// ── DOM refs ──
const messagesEl = document.getElementById("messages");
const emptyStateEl = document.getElementById("empty-state");
const inputEl = document.getElementById("message-input");
const sendBtn = document.getElementById("send-btn");
const statusEl = document.getElementById("status-indicator");
const themeBtn = document.getElementById("theme-btn");
const newChatBtn = document.getElementById("new-chat-btn");

// Chats drawer
const chatsBtn = document.getElementById("chats-btn");
const chatsDrawer = document.getElementById("chats-drawer");
const chatsClose = document.getElementById("chats-close");
const chatsList = document.getElementById("chats-list");

// Model selector
const modelBadge = document.getElementById("model-badge");
const modelBadgeName = document.getElementById("model-badge-name");
const modelDropdown = document.getElementById("model-dropdown");
const modelDropdownList = document.getElementById("model-dropdown-list");
const manageModelsBtn = document.getElementById("manage-models-btn");

// Models overlay
const modelsOverlay = document.getElementById("models-overlay");
const modelsClose = document.getElementById("models-close");
const modelsListFull = document.getElementById("models-list-full");
const addModelBtn = document.getElementById("add-model-btn");
const modelEditor = document.getElementById("model-editor");
const editorTitle = document.getElementById("editor-title");
const editorCancel = document.getElementById("editor-cancel");
const editorSave = document.getElementById("editor-save");
const tempSlider = document.getElementById("edit-temperature");
const tempValue = document.getElementById("temp-value");

// Disconnected banner
const disconnectedBanner = document.getElementById("disconnected-banner");
const retryConnectBtn = document.getElementById("retry-connect-btn");

// ── State ──
let ws = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;
let failedConnections = 0;
let isAgentWorking = false;
let stepTraceEl = null;
let currentSessionId = null;
let models = [];
let editingModelId = null;

// ── Theme ──
function initTheme() {
  chrome.storage.local.get("showmi_theme", (result) => {
    document.documentElement.setAttribute("data-theme", result.showmi_theme || "dark");
  });
}

function toggleTheme() {
  const next = document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark";
  document.documentElement.setAttribute("data-theme", next);
  chrome.storage.local.set({ showmi_theme: next });
}

themeBtn.addEventListener("click", toggleTheme);

// ── New chat ──
newChatBtn.addEventListener("click", () => {
  startNewChat();
});

function startNewChat() {
  // Clear messages
  messagesEl.innerHTML = "";
  messagesEl.appendChild(emptyStateEl);
  emptyStateEl.style.display = "";
  stepTraceEl = null;
  currentSessionId = null;
  isAgentWorking = false;
  updateInputState();
}

// ── Chats drawer ──
chatsBtn.addEventListener("click", () => {
  chatsDrawer.classList.remove("hidden");
  loadChats();
});

chatsClose.addEventListener("click", () => {
  chatsDrawer.classList.add("hidden");
});

async function loadChats() {
  try {
    const res = await fetch(`${API_BASE}/api/sessions`);
    const sessions = await res.json();
    renderChats(sessions);
  } catch {
    chatsList.innerHTML = '<div class="no-chats">could not load chats</div>';
  }
}

function renderChats(sessions) {
  if (!sessions.length) {
    chatsList.innerHTML = '<div class="no-chats">no chats yet</div>';
    return;
  }
  chatsList.innerHTML = "";
  sessions.forEach((s) => {
    const el = document.createElement("div");
    el.className = "chat-item";
    const date = new Date(s.created_at);
    const dateStr = date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " " + date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
    el.innerHTML = `
      <div class="chat-item-title">${escapeHtml(s.title || "untitled")}</div>
      <div class="chat-item-date">${dateStr}</div>
    `;
    el.addEventListener("click", () => loadChat(s.id));
    chatsList.appendChild(el);
  });
}

async function loadChat(sessionId) {
  chatsDrawer.classList.add("hidden");
  try {
    const res = await fetch(`${API_BASE}/api/sessions/${sessionId}/messages`);
    const messages = await res.json();
    messagesEl.innerHTML = "";
    emptyStateEl.style.display = "none";
    stepTraceEl = null;
    currentSessionId = sessionId;

    messages.forEach((msg) => {
      if (msg.role === "user") {
        addMessage("user", msg.content);
      } else if (msg.metadata) {
        const meta = msg.metadata;
        if (meta.type === "step" && meta.actions) {
          addStepMessage(meta);
        } else if (meta.type === "result") {
          addResultMessage(meta);
        } else if (meta.type === "error") {
          addMessage("error", meta.message || msg.content);
        } else {
          addMessage("agent", msg.content);
        }
      } else {
        addMessage("agent", msg.content);
      }
    });
  } catch {
    addMessage("error", "Failed to load chat history");
  }
}

// ── Model selector (badge dropdown) ──
modelBadge.addEventListener("click", (e) => {
  e.stopPropagation();
  modelDropdown.classList.toggle("hidden");
  if (!modelDropdown.classList.contains("hidden")) {
    refreshModelDropdown();
  }
});

// Close dropdown on outside click
document.addEventListener("click", (e) => {
  if (!e.target.closest(".model-selector")) {
    modelDropdown.classList.add("hidden");
  }
});

manageModelsBtn.addEventListener("click", () => {
  modelDropdown.classList.add("hidden");
  openModelsOverlay();
});

function refreshModelDropdown() {
  fetchModels().then(() => {
    modelDropdownList.innerHTML = "";
    if (!models.length) {
      modelDropdownList.innerHTML = '<div class="model-dropdown-item" style="color:var(--text-dim);cursor:default">no models</div>';
      return;
    }
    models.forEach((m) => {
      const item = document.createElement("div");
      item.className = "model-dropdown-item" + (m.is_active ? " active" : "");
      item.innerHTML = `
        <span>${escapeHtml(m.name || m.model || "untitled")}</span>
        ${m.is_active ? '<span class="check">&#10003;</span>' : ""}
      `;
      item.addEventListener("click", () => activateModel(m.id));
      modelDropdownList.appendChild(item);
    });
  });
}

async function activateModel(id) {
  try {
    await fetch(`${API_BASE}/api/models/${id}/activate`, { method: "PUT" });
    await fetchModels();
    modelDropdown.classList.add("hidden");
  } catch {}
}

async function fetchModels() {
  try {
    const res = await fetch(`${API_BASE}/api/models`);
    models = await res.json();
    updateModelBadge();
  } catch {
    models = [];
    updateModelBadge();
  }
}

function updateModelBadge() {
  const active = models.find((m) => m.is_active);
  modelBadgeName.textContent = active ? (active.name || active.model || "untitled") : "no model";
}

// ── Models overlay ──
function openModelsOverlay() {
  modelsOverlay.classList.remove("hidden");
  hideEditor();
  fetchModels().then(renderModelsListFull);
}

modelsClose.addEventListener("click", () => {
  modelsOverlay.classList.add("hidden");
});

modelsOverlay.addEventListener("click", (e) => {
  if (e.target === modelsOverlay) modelsOverlay.classList.add("hidden");
});

function renderModelsListFull() {
  if (!models.length) {
    modelsListFull.innerHTML = '<div class="no-models">no models configured</div>';
    return;
  }
  modelsListFull.innerHTML = "";
  models.forEach((m) => {
    const card = document.createElement("div");
    card.className = "model-card" + (m.is_active ? " selected" : "");
    card.innerHTML = `
      <div class="model-card-info">
        <div class="model-card-name">${escapeHtml(m.name || m.model || "untitled")}</div>
        <div class="model-card-detail">${escapeHtml(m.provider)} &middot; ${escapeHtml(m.model || "—")} &middot; ${m.api_key_preview || ""}</div>
      </div>
      <div class="model-card-actions">
        ${m.is_active ? '<span class="selected-badge">active</span>' : ""}
        <button class="icon-btn edit-btn" title="Edit">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/>
            <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/>
          </svg>
        </button>
        <button class="icon-btn delete-btn" title="Delete">
          <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
    `;

    // Click card to activate
    card.addEventListener("click", (e) => {
      if (e.target.closest(".edit-btn") || e.target.closest(".delete-btn")) return;
      activateModel(m.id).then(() => {
        fetchModels().then(renderModelsListFull);
      });
    });

    card.querySelector(".edit-btn").addEventListener("click", () => openEditor(m));
    card.querySelector(".delete-btn").addEventListener("click", () => deleteModel(m.id));

    modelsListFull.appendChild(card);
  });
}

async function deleteModel(id) {
  try {
    await fetch(`${API_BASE}/api/models/${id}`, { method: "DELETE" });
    await fetchModels();
    renderModelsListFull();
  } catch {}
}

// ── Model editor ──
addModelBtn.addEventListener("click", () => openEditor(null));

function openEditor(m) {
  editingModelId = m ? m.id : null;
  editorTitle.textContent = m ? "edit model" : "new model";
  document.getElementById("edit-name").value = m ? m.name : "";
  document.getElementById("edit-provider").value = m ? m.provider : "anthropic";
  document.getElementById("edit-api-key").value = "";
  document.getElementById("edit-api-key").type = "password";
  document.getElementById("edit-api-key").placeholder = m ? (m.api_key_preview || "sk-...") : "sk-...";
  document.getElementById("edit-base-url").value = m ? m.base_url : "";
  document.getElementById("edit-model").value = m ? m.model : "";
  document.getElementById("edit-temperature").value = m ? m.temperature : 0.5;
  tempValue.textContent = m ? m.temperature : 0.5;
  modelEditor.classList.remove("hidden");
}

function hideEditor() {
  modelEditor.classList.add("hidden");
  editingModelId = null;
}

async function saveEditorData() {
  const data = {
    name: document.getElementById("edit-name").value.trim(),
    provider: document.getElementById("edit-provider").value,
    api_key: document.getElementById("edit-api-key").value,
    base_url: document.getElementById("edit-base-url").value.trim(),
    model: document.getElementById("edit-model").value.trim(),
    temperature: parseFloat(document.getElementById("edit-temperature").value),
  };

  if (!data.name) data.name = data.model || data.provider;

  try {
    if (editingModelId) {
      await fetch(`${API_BASE}/api/models/${editingModelId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    } else {
      await fetch(`${API_BASE}/api/models`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(data),
      });
    }
    await fetchModels();
    renderModelsListFull();
    hideEditor();
  } catch {}
}

editorCancel.addEventListener("click", hideEditor);
editorSave.addEventListener("click", saveEditorData);
tempSlider.addEventListener("input", () => {
  tempValue.textContent = tempSlider.value;
});

// Toggle password visibility
document.querySelector(".toggle-visibility").addEventListener("click", () => {
  const input = document.getElementById("edit-api-key");
  input.type = input.type === "password" ? "text" : "password";
});

// ── WebSocket ──
function setStatus(state) {
  statusEl.className = "status " + state;
  statusEl.title = state.charAt(0).toUpperCase() + state.slice(1);
}

function showDisconnectedBanner() {
  disconnectedBanner.classList.remove("hidden");
  messagesEl.style.display = "none";
  document.getElementById("input-area").style.display = "none";
}

function hideDisconnectedBanner() {
  disconnectedBanner.classList.add("hidden");
  messagesEl.style.display = "";
  document.getElementById("input-area").style.display = "";
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;

  setStatus("connecting");
  ws = new WebSocket("ws://localhost:8765/ws");

  ws.addEventListener("open", () => {
    setStatus("connected");
    reconnectDelay = 1000;
    failedConnections = 0;
    hideDisconnectedBanner();
    updateSendState();
  });

  ws.addEventListener("close", () => {
    setStatus("disconnected");
    ws = null;
    failedConnections++;
    updateSendState();
    if (failedConnections >= 2) {
      showDisconnectedBanner();
    }
    scheduleReconnect();
  });

  ws.addEventListener("error", () => {});

  ws.addEventListener("message", (event) => {
    try {
      handleServerMessage(JSON.parse(event.data));
    } catch {}
  });
}

function scheduleReconnect() {
  setTimeout(() => {
    connect();
    reconnectDelay = Math.min(reconnectDelay * 1.5, MAX_RECONNECT_DELAY);
  }, reconnectDelay);
}

// ── Retry + copy commands ──
retryConnectBtn.addEventListener("click", () => {
  failedConnections = 0;
  reconnectDelay = 1000;
  hideDisconnectedBanner();
  if (ws) { try { ws.close(); } catch {} }
  ws = null;
  connect();
});

document.querySelectorAll(".disconnected-cmd").forEach((el) => {
  el.addEventListener("click", () => {
    const wrap = el.closest(".disconnected-cmd-wrap");
    navigator.clipboard.writeText(el.textContent.trim()).then(() => {
      wrap.classList.add("copied");
      setTimeout(() => wrap.classList.remove("copied"), 1500);
    });
  });
});

// ── Message handling ──
function handleServerMessage(data) {
  switch (data.type) {
    case "session":
      currentSessionId = data.session_id;
      break;
    case "step":
      removeThinking();
      addStepMessage(data);
      showThinking();
      break;
    case "result":
      removeThinking();
      addResultMessage(data);
      isAgentWorking = false;
      stepTraceEl = null;
      updateInputState();
      break;
    case "error":
      removeThinking();
      addMessage("error", data.message || "Unknown error");
      isAgentWorking = false;
      stepTraceEl = null;
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

  // Hide empty state
  if (emptyStateEl) emptyStateEl.style.display = "none";

  addMessage("user", text);
  inputEl.value = "";
  autoResizeInput();

  const activeTab = await getActiveTab();

  // Server will use the active model from DB — no need to send settings
  const payload = {
    type: "task",
    content: text,
    settings: {},
    active_tab: activeTab,
  };

  ws.send(JSON.stringify(payload));
  isAgentWorking = true;
  stepTraceEl = null;
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

function ensureStepTrace() {
  if (!stepTraceEl) {
    stepTraceEl = document.createElement("div");
    stepTraceEl.className = "step-trace";
    messagesEl.appendChild(stepTraceEl);
  }
  return stepTraceEl;
}

function addStepMessage(data) {
  const trace = ensureStepTrace();

  // Mark previous active steps as completed
  trace.querySelectorAll(".step-item.active").forEach((el) => {
    el.classList.remove("active");
    el.classList.add("completed");
    const loading = el.querySelector(".step-loading");
    if (loading) loading.remove();
  });

  const item = document.createElement("div");
  item.className = "step-item active";

  let html = '<div class="step-dot"></div>';
  html += `<span class="step-number">step ${data.step_number || "?"}</span>`;

  if (data.goal) {
    html += `<div class="step-goal">${escapeHtml(data.goal)}</div>`;
  }
  if (data.actions) {
    const actions = Array.isArray(data.actions) ? data.actions.map((a) => {
      if (typeof a === "object" && a.action) return JSON.stringify(a.action);
      return String(a);
    }).join(", ") : data.actions;
    html += `<div class="step-actions">${escapeHtml(actions)}</div>`;
  }
  if (data.url && data.url !== "unknown") {
    html += `<div class="step-url">${escapeHtml(data.url)}</div>`;
  }

  html += '<div class="step-loading"><div class="step-loading-bar"></div><span>processing</span></div>';

  item.innerHTML = html;
  trace.appendChild(item);
  scrollToBottom();
}

function addResultMessage(data) {
  if (stepTraceEl) {
    stepTraceEl.querySelectorAll(".step-item.active").forEach((el) => {
      el.classList.remove("active");
      el.classList.add("completed");
      const loading = el.querySelector(".step-loading");
      if (loading) loading.remove();
    });
  }

  const el = document.createElement("div");
  el.className = "result-block";

  let html = '<div class="result-label">result</div>';
  html += `<div class="result-text">${renderMarkdown(data.summary || "Task completed.")}</div>`;

  const meta = [];
  if (data.steps_taken != null) meta.push(`${data.steps_taken} steps`);
  if (data.errors && data.errors.length) meta.push(`${data.errors.length} error(s)`);
  if (meta.length) {
    html += `<div class="result-meta">${meta.join(" &middot; ")}</div>`;
  }

  el.innerHTML = html;
  messagesEl.appendChild(el);
  scrollToBottom();
}

function showThinking() {
  if (document.querySelector(".thinking-indicator")) return;
  const el = document.createElement("div");
  el.className = "thinking-indicator";
  el.innerHTML = '<div class="step-dot"></div><div class="thinking-dots"><span></span><span></span><span></span></div>';
  messagesEl.appendChild(el);
  scrollToBottom();
}

function removeThinking() {
  const el = document.querySelector(".thinking-indicator");
  if (el) el.remove();
}

function scrollToBottom() {
  requestAnimationFrame(() => {
    messagesEl.scrollTop = messagesEl.scrollHeight;
  });
}

function updateInputState() {
  inputEl.disabled = isAgentWorking;
  inputEl.placeholder = isAgentWorking ? "agent is working..." : "what should i do?";
  updateSendState();
  if (!isAgentWorking) inputEl.focus();
}

function updateSendState() {
  const hasText = inputEl.value.trim().length > 0;
  const connected = ws && ws.readyState === WebSocket.OPEN;
  sendBtn.disabled = isAgentWorking || !hasText || !connected;
}

// ── Markdown-lite ──
function escapeHtml(str) {
  return String(str).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/```([\s\S]*?)```/g, (_m, code) => `<pre>${code.trim()}</pre>`);
  html = html.replace(/`([^`]+)`/g, (_m, code) => `<code>${code}</code>`);
  html = html.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
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

inputEl.addEventListener("input", () => {
  autoResizeInput();
  updateSendState();
});

function autoResizeInput() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + "px";
}

sendBtn.addEventListener("click", sendMessage);

// ── Recording ──

const recordBtn = document.getElementById("record-btn");
const stopBtn = document.getElementById("stop-btn");
const eventCountEl = document.getElementById("event-count");

// Workflow review overlay
const workflowReview = document.getElementById("workflow-review");
const reviewClose = document.getElementById("review-close");
const wfNameInput = document.getElementById("wf-name");
const wfDescInput = document.getElementById("wf-description");
const wfParamsEl = document.getElementById("wf-params");
const wfStepsEl = document.getElementById("wf-steps");
const wfErrorEl = document.getElementById("wf-error");
const reviewDiscard = document.getElementById("review-discard");
const reviewSave = document.getElementById("review-save");

let isRecording = false;
let compiledWorkflow = null;

// Start recording
recordBtn.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "START_RECORDING" }, (response) => {
    if (response?.ok) {
      isRecording = true;
      recordBtn.classList.add("hidden");
      stopBtn.classList.remove("hidden");
      eventCountEl.textContent = "0";
      document.body.classList.add("recording-active");
      inputEl.disabled = true;
      inputEl.placeholder = "recording...";
      sendBtn.disabled = true;
    }
  });
});

// Stop recording
stopBtn.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "STOP_RECORDING" }, async (data) => {
    isRecording = false;
    stopBtn.classList.add("hidden");
    recordBtn.classList.remove("hidden");
    document.body.classList.remove("recording-active");

    if (!data || !data.events || data.events.length === 0) {
      addMessage("error", "No events were recorded.");
      updateInputState();
      return;
    }

    // Hide empty state
    if (emptyStateEl) emptyStateEl.style.display = "none";

    addMessage("system", `Compiling ${data.events.length} recorded events...`);
    showThinking();

    try {
      const res = await fetch(`${API_BASE}/api/workflows/compile`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: "Untitled Workflow",
          description: "",
          auto_parameterize: true,
          recording: {
            start_url: data.startUrl,
            events: data.events,
          },
        }),
      });

      removeThinking();

      if (!res.ok) {
        const err = await res.json();
        addMessage("error", err.error || "Compilation failed");
        updateInputState();
        return;
      }

      const result = await res.json();
      compiledWorkflow = result.workflow;
      showWorkflowReview(compiledWorkflow);
    } catch (e) {
      removeThinking();
      addMessage("error", `Compilation error: ${e.message}`);
      updateInputState();
    }
  });
});

// Live event count from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "RECORDING_EVENT_COUNT" && isRecording) {
    eventCountEl.textContent = String(msg.count);
  }
});

// ── Workflow review overlay ──

function showWorkflowReview(workflow) {
  wfNameInput.value = workflow.name || "";
  wfDescInput.value = workflow.description || "";
  wfErrorEl.classList.add("hidden");

  // Render parameters
  const params = workflow.parameters || [];
  if (params.length) {
    wfParamsEl.innerHTML = '<div class="wf-params-title">parameters</div>';
    params.forEach((p) => {
      const item = document.createElement("div");
      item.className = "wf-param-item";
      item.innerHTML = `<span class="wf-param-name">{{${escapeHtml(p.name)}}}</span> ${escapeHtml(p.description || "")}`;
      wfParamsEl.appendChild(item);
    });
  } else {
    wfParamsEl.innerHTML = "";
  }

  // Render steps (body markdown)
  wfStepsEl.innerHTML = renderMarkdown(workflow.body || "");

  workflowReview.classList.remove("hidden");
}

function hideWorkflowReview() {
  workflowReview.classList.add("hidden");
  compiledWorkflow = null;
  updateInputState();
}

reviewClose.addEventListener("click", hideWorkflowReview);
reviewDiscard.addEventListener("click", hideWorkflowReview);

workflowReview.addEventListener("click", (e) => {
  if (e.target === workflowReview) hideWorkflowReview();
});

reviewSave.addEventListener("click", async () => {
  if (!compiledWorkflow) return;

  const name = wfNameInput.value.trim() || compiledWorkflow.name;
  const description = wfDescInput.value.trim() || compiledWorkflow.description;

  try {
    reviewSave.disabled = true;
    reviewSave.textContent = "saving...";

    const res = await fetch(`${API_BASE}/api/workflows`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        name,
        description,
        parameters: compiledWorkflow.parameters,
        body: compiledWorkflow.body,
      }),
    });

    if (res.status === 409) {
      wfErrorEl.textContent = "A workflow with this name already exists. Change the name.";
      wfErrorEl.classList.remove("hidden");
      reviewSave.disabled = false;
      reviewSave.textContent = "save workflow";
      return;
    }

    if (!res.ok) {
      const err = await res.json();
      wfErrorEl.textContent = err.error || "Save failed";
      wfErrorEl.classList.remove("hidden");
      reviewSave.disabled = false;
      reviewSave.textContent = "save workflow";
      return;
    }

    // Success
    workflowReview.classList.add("hidden");
    compiledWorkflow = null;
    addMessage("system", `Workflow "${name}" saved.`);
    updateInputState();
  } catch (e) {
    wfErrorEl.textContent = `Error: ${e.message}`;
    wfErrorEl.classList.remove("hidden");
  } finally {
    reviewSave.disabled = false;
    reviewSave.textContent = "save workflow";
  }
});

// ── Init ──
initTheme();
fetchModels();
connect();
