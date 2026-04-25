// ── Constants ──
const SHOWMI_PORT = 8765;
const API_BASE = `http://localhost:${SHOWMI_PORT}`;

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

// Memory drawer
const memoryBtn = document.getElementById("memory-btn");
const memoryDrawer = document.getElementById("memory-drawer");
const memoryClose = document.getElementById("memory-close");
const memoryList = document.getElementById("memory-list");

// Record workflow
const recordBtn = document.getElementById("record-btn");

// Workflows drawer
const workflowsBtn = document.getElementById("workflows-btn");
const workflowsDrawer = document.getElementById("workflows-drawer");
const workflowsClose = document.getElementById("workflows-close");
const workflowsList = document.getElementById("workflows-list");

// Disconnected banner
const disconnectedBanner = document.getElementById("disconnected-banner");
const retryConnectBtn = document.getElementById("retry-connect-btn");

// Inline attach error
const attachError = document.getElementById("attach-error");

// ── Provider helpers ──
const PROVIDER_BASE_URLS = {
  anthropic: "https://api.anthropic.com",
  openai: "https://api.openai.com/v1",
  local: "",
};
const KNOWN_BASE_URLS = new Set(Object.values(PROVIDER_BASE_URLS).filter(Boolean));

function getProviderIconSVG(provider, size = 13) {
  if (provider === "anthropic") {
    // Official Anthropic brand mark
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="#c96442" xmlns="http://www.w3.org/2000/svg"><path d="M14.06 3.74L20.78 17h-4.03l-1.51-3.26H8.76L7.25 17H3.22l6.72-13.26h4.12zm-2.06 3.2L9.69 11.2h4.62L12 6.94z"/></svg>`;
  } else if (provider === "openai") {
    // Official OpenAI bloom mark
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="#10a37f" xmlns="http://www.w3.org/2000/svg"><path d="M22.28 9.82a5.98 5.98 0 0 0-.52-4.91 6.05 6.05 0 0 0-6.51-2.9A6.07 6.07 0 0 0 4.98 4.18a5.98 5.98 0 0 0-4 2.9 6.05 6.05 0 0 0 .74 7.1 5.98 5.98 0 0 0 .51 4.91 6.05 6.05 0 0 0 6.52 2.9A5.98 5.98 0 0 0 13.26 24a6.06 6.06 0 0 0 5.77-4.21 5.99 5.99 0 0 0 4-2.9 6.06 6.06 0 0 0-.75-7.07zM13.26 22.43a4.48 4.48 0 0 1-2.88-1.04l.14-.08 4.78-2.76a.8.8 0 0 0 .4-.68v-6.74l2.02 1.17a.07.07 0 0 1 .04.05v5.58a4.5 4.5 0 0 1-4.5 4.5zM3.6 18.3a4.47 4.47 0 0 1-.54-3.01l.14.08 4.78 2.76a.77.77 0 0 0 .78 0l5.84-3.37v2.33a.08.08 0 0 1-.03.06l-4.82 2.79A4.5 4.5 0 0 1 3.6 18.3zM2.34 7.9a4.49 4.49 0 0 1 2.37-1.97v5.67a.77.77 0 0 0 .39.68l5.81 3.36-2.02 1.17a.08.08 0 0 1-.07 0L4 14.1A4.51 4.51 0 0 1 2.34 7.9zm16.6 3.86-5.85-3.39 2.02-1.17a.08.08 0 0 1 .07 0l4.82 2.78a4.5 4.5 0 0 1-.7 8.12V12.4a.8.8 0 0 0-.37-.66zm2.01-3.02-.14-.09-4.77-2.78a.78.78 0 0 0-.79 0L9.41 9.23V6.9a.07.07 0 0 1 .03-.06l4.81-2.77a4.5 4.5 0 0 1 6.68 4.67zM8.48 12.86l-2.02-1.16a.08.08 0 0 1-.04-.06V6.08a4.5 4.5 0 0 1 7.38-3.45l-.14.08-4.78 2.76a.8.8 0 0 0-.4.68v6.71zm1.1-2.36 2.6-1.5 2.61 1.5v3l-2.6 1.5-2.61-1.5z"/></svg>`;
  } else {
    return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="20" height="14" rx="2"/><line x1="8" y1="21" x2="16" y2="21"/><line x1="12" y1="17" x2="12" y2="21"/></svg>`;
  }
}

// ── State ──
let eventSource = null;
let currentSessionId = null;
let sessions = [];
let models = [];
let editingModelId = null;
let lastToolCallPill = null; // most recent pill element, for attaching tool results

// Per-session state for concurrent chats
// { sessionId: { messages: [...elements], stepTraceEl, isWorking } }
let sessionStates = {};

function getSessionState(sessionId) {
  if (!sessionStates[sessionId]) {
    sessionStates[sessionId] = { stepTraceEl: null, isWorking: false };
  }
  return sessionStates[sessionId];
}

// ── Theme ──
function initTheme() {
  chrome.storage.local.get("showmi_theme", (result) => {
    document.documentElement.setAttribute("data-theme", result.showmi_theme || "light");
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
  messagesEl.innerHTML = "";
  messagesEl.appendChild(emptyStateEl);
  emptyStateEl.style.display = "";
  currentSessionId = null;
  setChatTitle("");
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
    sessions = await res.json();
    renderChats(sessions);
  } catch {
    sessions = [];
    chatsList.innerHTML = '<div class="no-chats">could not load chats</div>';
  }
}

function setChatTitle(title) {
  const el = document.getElementById("chat-title");
  if (el) el.textContent = title || "";
}

async function deleteChat(sessionId) {
  try {
    await fetch(`${API_BASE}/api/sessions/${sessionId}`, { method: "DELETE" });
    // If we deleted the active chat, reset to new chat
    if (sessionId === currentSessionId) {
      startNewChat();
    }
    delete sessionStates[sessionId];
    loadChats();
  } catch {}
}

async function deleteWorkflow(workflowId) {
  try {
    await fetch(`${API_BASE}/api/workflows/${workflowId}`, { method: "DELETE" });
    loadWorkflows();
  } catch {}
}

function renderChats(sessions) {
  if (!sessions.length) {
    chatsList.innerHTML = '<div class="no-chats">no chats yet</div>';
    return;
  }
  chatsList.innerHTML = "";
  sessions.forEach((s) => {
    const el = document.createElement("div");
    el.className = "chat-item" + (s.id === currentSessionId ? " active" : "");
    const date = new Date(s.created_at);
    const dateStr = date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) +
      " " + date.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });

    // Status indicator
    const status = s.status || "idle";
    let statusDot = "";
    if (status === "running") {
      statusDot = '<span class="chat-status running" title="Running"></span>';
    } else if (status === "error") {
      statusDot = '<span class="chat-status error" title="Error"></span>';
    } else if (status === "completed") {
      statusDot = '<span class="chat-status completed" title="Completed"></span>';
    }

    el.innerHTML = `
      <div class="chat-item-row">
        ${statusDot}
        <div class="chat-item-title">${escapeHtml(s.title || "untitled")}</div>
        <button class="chat-item-delete icon-btn" title="Delete">
          <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
      <div class="chat-item-date">${dateStr}</div>
    `;
    el.querySelector(".chat-item-delete").addEventListener("click", (e) => {
      e.stopPropagation();
      deleteChat(s.id);
    });
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
    currentSessionId = sessionId;
    connectSSE(sessionId);
    const session = sessions.find((s) => s.id === sessionId);
    setChatTitle(session ? session.title : "");

    // Check if this session has an active agent
    const state = getSessionState(sessionId);
    let stepTraceEl = null;

    messages.forEach((msg) => {
      if (msg.role === "user") {
        addMessage("user", msg.content);
      } else if (msg.metadata) {
        const meta = msg.metadata;
        if (meta.type === "step" && meta.actions) {
          stepTraceEl = addStepMessage(meta, stepTraceEl);
        } else if (meta.type === "result") {
          addResultMessage(meta);
          stepTraceEl = null;
        } else if (meta.type === "error") {
          addMessage("error", meta.message || msg.content);
          stepTraceEl = null;
        } else if (meta.type === "workflow_proposal") {
          renderWorkflowProposal(meta.workflow_markdown || msg.content, meta.manifest_yaml);
        } else if (meta.type === "tool_call") {
          addToolCallPill(meta.tool, meta.args || {}, meta.result || null);
        } else {
          addMessage("agent", msg.content);
        }
      } else {
        addMessage("agent", msg.content);
      }
    });

    // If session is still running, show thinking indicator
    if (state.isWorking) {
      showThinking();
    }

    updateInputState();
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
  const iconEl = document.getElementById("model-badge-icon");
  if (iconEl) iconEl.innerHTML = active ? getProviderIconSVG(active.provider, 11) : "";
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
  const provider = m ? m.provider : "anthropic";
  document.getElementById("edit-provider").value = provider;
  document.getElementById("edit-api-key").value = "";
  document.getElementById("edit-api-key").type = "password";
  document.getElementById("edit-api-key").placeholder = m ? (m.api_key_preview || "sk-...") : "sk-...";
  const existingUrl = m ? (m.base_url || "") : "";
  document.getElementById("edit-base-url").value =
    !m ? (PROVIDER_BASE_URLS[provider] || "") :
    (KNOWN_BASE_URLS.has(existingUrl) || !existingUrl) ? (PROVIDER_BASE_URLS[provider] || "") : existingUrl;
  document.getElementById("edit-model").value = m ? m.model : "";
  document.getElementById("edit-temperature").value = m ? m.temperature : 0.5;
  tempValue.textContent = m ? m.temperature : 0.5;
  updateProviderIcon();
  modelEditor.classList.remove("hidden");
}

function updateProviderIcon() {
  const provider = document.getElementById("edit-provider").value;
  const iconEl = document.getElementById("provider-icon");
  if (iconEl) iconEl.innerHTML = getProviderIconSVG(provider);
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

document.getElementById("edit-provider").addEventListener("change", () => {
  const provider = document.getElementById("edit-provider").value;
  const currentUrl = document.getElementById("edit-base-url").value.trim();
  if (!currentUrl || KNOWN_BASE_URLS.has(currentUrl)) {
    document.getElementById("edit-base-url").value = PROVIDER_BASE_URLS[provider] || "";
  }
  updateProviderIcon();
});

// Toggle password visibility
document.querySelector(".toggle-visibility").addEventListener("click", () => {
  const input = document.getElementById("edit-api-key");
  input.type = input.type === "password" ? "text" : "password";
});

// ── Server connection (SSE + REST) ──
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

function connectSSE(sessionId) {
  if (eventSource) { eventSource.close(); eventSource = null; }
  if (!sessionId) return;

  eventSource = new EventSource(`${API_BASE}/api/sessions/${sessionId}/events`);

  eventSource.onopen = () => {
    setStatus("connected");
    hideDisconnectedBanner();
    updateSendState();
  };

  eventSource.onmessage = (e) => {
    try { handleServerMessage(JSON.parse(e.data)); } catch {}
  };

  eventSource.onerror = () => {
    setStatus("disconnected");
    // EventSource auto-reconnects — show banner only if fully closed
    if (eventSource && eventSource.readyState === EventSource.CLOSED) {
      showDisconnectedBanner();
    }
  };
}

async function checkServerHealth() {
  try {
    const res = await fetch(`${API_BASE}/health`);
    if (res.ok) { setStatus("connected"); hideDisconnectedBanner(); return true; }
  } catch {}
  setStatus("disconnected");
  return false;
}

// ── Retry + copy commands ──
retryConnectBtn.addEventListener("click", async () => {
  hideDisconnectedBanner();
  if (await checkServerHealth()) {
    if (currentSessionId) connectSSE(currentSessionId);
  }
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
  const msgSessionId = data.session_id;

  switch (data.type) {
    case "step": {
      const state = getSessionState(msgSessionId);
      // Only render if this is the active chat
      if (msgSessionId === currentSessionId) {
        removeThinking();
        const trace = addStepMessage(data, state.stepTraceEl);
        state.stepTraceEl = trace;
        showThinking();
      }
      break;
    }

    case "result": {
      const state = getSessionState(msgSessionId);
      state.isWorking = false;
      state.stepTraceEl = null;
      if (msgSessionId === currentSessionId) {
        removeThinking();
        addResultMessage(data);
        updateInputState();
      }
      break;
    }

    case "error": {
      const state = getSessionState(msgSessionId);
      state.isWorking = false;
      state.stepTraceEl = null;
      if (msgSessionId === currentSessionId) {
        removeThinking();
        addMessage("error", data.message || "Unknown error");
        updateInputState();
      }
      break;
    }

    case "cancelled": {
      const state = getSessionState(msgSessionId);
      state.isWorking = false;
      state.stepTraceEl = null;
      if (msgSessionId === currentSessionId) {
        removeThinking();
        addMessage("system", "Task cancelled.");
        updateInputState();
      }
      break;
    }

    case "tool_call_start":
      addToolCallPill(data.tool, data.args || {});
      break;

    case "tool_call_result":
      if (lastToolCallPill) {
        attachToolResult(lastToolCallPill, data.tool, data.result || "");
        lastToolCallPill = null;
      }
      break;

    case "orchestrator_message":
      removeThinking();
      addMessage("agent", data.content || "");
      break;

    case "orchestrator_ready": {
      // Orchestrator is idle, waiting for next user message
      const state = getSessionState(msgSessionId);
      state.isWorking = false;
      if (msgSessionId === currentSessionId) {
        removeThinking();
        updateInputState();
      }
      break;
    }

    case "orchestrator_command":
      if (data.command === "start_recording") {
        removeThinking();
        addMessage("agent", data.instruction || "Please demonstrate the workflow. Click Stop when done.");
        startRecordingFromOrchestrator();
      }
      break;

    case "planning_message":
      removeThinking();
      addMessage("agent", data.content || "");
      showThinking();
      break;

    case "planning_tool_call": {
      removeThinking();
      const tool = data.tool;
      const args = data.args || {};
      if (tool === "ask_multiple_choice") {
        renderMultipleChoice(args.question, args.choices || [], data.session_id);
      } else if (tool === "ask_free_text" || tool === "ask_question") {
        if (args.choices && args.choices.length > 0) {
          renderMultipleChoice(args.question, args.choices, data.session_id);
        } else {
          addMessage("agent", args.question || "");
          inputEl.placeholder = args.placeholder || "type your answer...";
          inputEl.focus();
        }
      } else if (tool === "propose_workflow") {
        lastProposedManifest = args.manifest_yaml || "";
        lastProposedMarkdown = args.workflow_markdown || args.workflow_content || "";
        renderWorkflowProposal(lastProposedMarkdown, lastProposedManifest);
        if (planningBar) planningBar.classList.remove("hidden");
      }
      break;
    }

    case "planning_complete":
      removeThinking();
      exitPlanningMode();
      addMessage("system", data.workflow_id ? `Workflow saved: ${data.workflow_id}` : "Workflow discarded.");
      break;

    case "planning_error":
      removeThinking();
      addMessage("error", data.message || "Planning error");
      exitPlanningMode();
      break;

    default:
      if (data.content) addMessage("agent", data.content);
  }
}

// ── Active-tab helper ──
async function getActiveTabInfo() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url && !tab.url.startsWith("chrome://") && !tab.url.startsWith("chrome-extension://")) {
      return { url: tab.url, title: tab.title || "" };
    }
  } catch {}
  return null;
}

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text) return;

  if (emptyStateEl) emptyStateEl.style.display = "none";
  addMessage("user", text);
  inputEl.value = "";
  autoResizeInput();
  showThinking();

  // Showmi runs in its own Chrome tab group, never on the user's tabs. Make
  // sure the group + an attached tab exist before the agent starts.
  if (currentMode !== "planning") {
    try {
      await ensureAgentTab();
    } catch (err) {
      removeThinking();
      addMessage("error", `Attach failed: ${err.message}`);
      updateInputState();
      return;
    }
  }

  if (currentMode === "planning") {
    await fetch(`${API_BASE}/api/sessions/${currentSessionId}/planning/respond`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content: text }),
    });
    return;
  }

  try {
    const flashToggle = document.getElementById("flash-toggle");
    const activeTab = await getActiveTabInfo();
    const res = await fetch(`${API_BASE}/api/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        content: text,
        session_id: currentSessionId || undefined,
        active_tab: activeTab,
        flash_mode: flashToggle ? flashToggle.checked : false,
      }),
    });
    const data = await res.json();

    if (!res.ok) {
      removeThinking();
      addMessage("error", data.error || "Request failed.");
      return;
    }

    if (data.session_id && !currentSessionId) {
      currentSessionId = data.session_id;
      connectSSE(currentSessionId);
    }

    if (currentSessionId) {
      const state = getSessionState(currentSessionId);
      state.isWorking = true;
      state.stepTraceEl = null;
    }
  } catch (err) {
    removeThinking();
    addMessage("error", `Connection error: ${err.message}`);
  }

  updateInputState();
}

// ── Rendering ──
function addMessage(role, text) {
  const el = document.createElement("div");
  el.className = "message " + role;
  el.innerHTML = renderMarkdown(text);
  messagesEl.appendChild(el);
  scrollToBottom();
}

function ensureStepTrace(existingTrace) {
  if (existingTrace && existingTrace.parentNode) {
    return existingTrace;
  }
  const trace = document.createElement("div");
  trace.className = "step-trace";
  messagesEl.appendChild(trace);
  return trace;
}

function addStepMessage(data, existingTrace) {
  const trace = ensureStepTrace(existingTrace);

  // Mark previous active steps as completed
  trace.querySelectorAll(".step-item.active").forEach((el) => {
    el.classList.remove("active");
    el.classList.add("completed");
    const loading = el.querySelector(".step-loading");
    if (loading) loading.remove();
  });

  const item = document.createElement("div");
  const hasErrors = data.actions && Array.isArray(data.actions) && data.actions.some((a) => a && a.error);
  item.className = "step-item active" + (hasErrors ? " has-error" : "");

  let html = '<div class="step-dot"></div>';
  html += `<span class="step-number">step ${data.step_number || "?"}</span>`;

  if (data.goal) {
    html += `<div class="step-goal">${escapeHtml(data.goal)}</div>`;
  }

  if (data.actions && Array.isArray(data.actions)) {
    data.actions.forEach((a) => {
      if (!a || typeof a !== "object") return;

      // Format the action name
      let actionText = "";
      if (a.action && typeof a.action === "object") {
        const keys = Object.keys(a.action);
        if (keys.length > 0) {
          actionText = keys[0]; // e.g. "go_to_url", "click_element"
          const params = a.action[keys[0]];
          if (params && typeof params === "object") {
            const paramStr = Object.entries(params)
              .map(([k, v]) => `${k}: ${typeof v === "string" ? v : JSON.stringify(v)}`)
              .join(", ");
            if (paramStr) actionText += ` (${paramStr})`;
          } else if (params) {
            actionText += ` (${params})`;
          }
        }
      } else if (a.action) {
        actionText = String(a.action);
      }

      if (a.error) {
        html += `<div class="step-error"><span class="step-error-icon">!</span> ${escapeHtml(String(a.error))}</div>`;
      } else if (actionText) {
        html += `<div class="step-actions">${escapeHtml(actionText)}</div>`;
      }

      if (a.extracted) {
        html += `<div class="step-extracted">${escapeHtml(String(a.extracted).substring(0, 200))}</div>`;
      }
    });
  }

  if (data.url && data.url !== "unknown") {
    html += `<div class="step-url">${escapeHtml(data.url)}</div>`;
  }

  html += '<div class="step-loading"><div class="step-loading-bar"></div><span>processing</span></div>';

  item.innerHTML = html;
  trace.appendChild(item);
  scrollToBottom();

  return trace;
}

function addResultMessage(data) {
  // Complete any active step traces in the DOM
  const activeSteps = messagesEl.querySelectorAll(".step-item.active");
  activeSteps.forEach((el) => {
    el.classList.remove("active");
    el.classList.add("completed");
    const loading = el.querySelector(".step-loading");
    if (loading) loading.remove();
  });

  const el = document.createElement("div");
  el.className = "result-block";

  let html = '<div class="result-label">result</div>';
  html += `<div class="result-text">${renderMarkdown(data.summary || "Task completed.")}</div>`;

  // Filter out None/null/empty errors
  const realErrors = (data.errors || []).filter((e) => {
    if (!e) return false;
    const s = String(e).trim().toLowerCase();
    return s !== "" && s !== "none" && s !== "null";
  });

  const meta = [];
  if (data.steps_taken != null) meta.push(`${data.steps_taken} steps`);
  if (realErrors.length) meta.push(`${realErrors.length} error(s)`);
  if (meta.length) {
    html += `<div class="result-meta">${meta.join(" &middot; ")}</div>`;
  }

  // Errors are already shown inline on each step — no need to repeat here

  el.innerHTML = html;

  if (data.gif_url) {
    const replayBtn = document.createElement("button");
    replayBtn.className = "replay-btn";
    replayBtn.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor"><polygon points="5 3 19 12 5 21 5 3"/></svg> watch replay';
    replayBtn.addEventListener("click", () => {
      chrome.tabs.create({ url: API_BASE + data.gif_url });
    });
    el.appendChild(replayBtn);
  }

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
  // Check if current session is working
  const isWorking = currentSessionId ? getSessionState(currentSessionId).isWorking : false;
  inputEl.disabled = isWorking;
  inputEl.placeholder = isWorking ? "agent is working..." : "what should i do?";
  updateSendState();
  if (!isWorking) inputEl.focus();
}

function updateSendState() {
  const hasText = inputEl.value.trim().length > 0;
  const connected = statusEl && statusEl.classList.contains("connected");
  const isWorking = currentSessionId ? getSessionState(currentSessionId).isWorking : false;

  if (isWorking) {
    sendBtn.disabled = false;
    sendBtn.classList.add("is-stop");
    sendBtn.title = "Stop";
  } else {
    sendBtn.classList.remove("is-stop");
    sendBtn.disabled = !hasText || !connected;
    sendBtn.title = "Send";
  }
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

sendBtn.addEventListener("click", () => {
  if (sendBtn.classList.contains("is-stop")) {
    cancelTask();
  } else {
    sendMessage();
  }
});

async function cancelTask() {
  if (!currentSessionId) return;
  sendBtn.disabled = true;
  try {
    await fetch(`${API_BASE}/api/sessions/${currentSessionId}/cancel`, { method: "POST" });
  } catch {}
}

// ── Tool call pill (inline in chat) ──
const TOOL_LABELS = {
  run_browser_agent: "browser agent",
  run_workflow:      "run workflow",
  list_workflows:    "list workflows",
  start_recording:   "recording",
  start_planning:    "planning",
  save_as_workflow:  "save workflow",
  store_memory:      "memory",
  query_memories:    "recall memories",
  evict_memory:      "forget memory",
  update_workflow:   "update workflow",
};

const TOOL_ICONS = {
  run_browser_agent: "⚡",
  run_workflow:      "▶",
  list_workflows:    "◎",
  start_recording:   "⏺",
  start_planning:    "⚙",
  save_as_workflow:  "◈",
  store_memory:      "◆",
  query_memories:    "◈",
  evict_memory:      "✕",
  update_workflow:   "✎",
};

function addToolCallPill(toolName, args, result) {
  // retrieve_memories: show each injected memory as a sub-item (legacy path)
  if (toolName === "retrieve_memories") {
    const memories = args.memories || [];
    if (memories.length === 0) return;
    const TYPE_LABELS = { episodic: "past run", procedural: "how-to", semantic: "fact" };
    const el = document.createElement("div");
    el.className = "tool-call-pill memory-context-pill";
    const items = memories.map((m) =>
      `<span class="memory-context-item">` +
        `<span class="memory-context-type">${escapeHtml(TYPE_LABELS[m.type] || m.type)}</span>` +
        `<span class="memory-context-text">${escapeHtml(m.content)}</span>` +
      `</span>`
    ).join("");
    el.innerHTML =
      `<span class="tool-call-icon">◈</span>` +
      `<span class="tool-call-label">context from memory</span>` +
      `<span class="memory-context-items">${items}</span>`;
    messagesEl.appendChild(el);
    scrollToBottom();
    return;
  }

  // store_memory gets the special accented memory pill
  if (toolName === "store_memory") {
    const type = args.type || "procedural";
    const content = args.content || "";
    const typeLabel = { episodic: "past run", procedural: "how-to", semantic: "fact" }[type] || type;
    const el = document.createElement("div");
    el.className = "tool-call-pill memory-pill";
    el.innerHTML =
      `<span class="tool-call-icon">◆</span>` +
      `<span class="tool-call-label">${escapeHtml(typeLabel)}</span>` +
      `<span class="tool-call-detail">${escapeHtml(content)}</span>` +
      `<div class="tool-call-output"></div>`;
    messagesEl.appendChild(el);
    lastToolCallPill = el;
    if (result) attachToolResult(el, toolName, result);
    scrollToBottom();
    return;
  }

  // Generic pill for all other tools
  let detail = "";
  if (toolName === "run_browser_agent") detail = (args.task || "").substring(0, 100);
  else if (toolName === "run_workflow")  detail = args.workflow_id || "";
  else if (toolName === "list_workflows")   detail = "";
  else if (toolName === "save_as_workflow") detail = args.name || "";
  else if (toolName === "start_recording") detail = (args.instruction || "").substring(0, 60);
  else if (toolName === "query_memories") detail = args.query || "";

  const el = document.createElement("div");
  el.className = "tool-call-pill";
  el.innerHTML =
    `<span class="tool-call-icon">${escapeHtml(TOOL_ICONS[toolName] || "●")}</span>` +
    `<span class="tool-call-label">${escapeHtml(TOOL_LABELS[toolName] || toolName)}</span>` +
    (detail ? `<span class="tool-call-detail">${escapeHtml(detail)}</span>` : "") +
    `<span class="tool-call-expand-icon"></span>` +
    `<div class="tool-call-output"></div>`;
  messagesEl.appendChild(el);
  lastToolCallPill = el;
  if (result) attachToolResult(el, toolName, result);
  scrollToBottom();
}

function parseMemoriesResult(text) {
  const lines = text.split("\n").filter((l) => l.startsWith("- ["));
  return lines.map((line) => {
    const match = line.match(/^- \[(.+?)\] (.+)$/);
    if (match) return { type: match[1].toLowerCase(), content: match[2] };
    return { type: "memory", content: line.replace(/^- /, "") };
  });
}

function attachToolResult(pillEl, toolName, resultText) {
  if (!resultText || resultText === "") return;
  // Skip output for browser agent / workflow — steps already show in UI
  if (toolName === "run_browser_agent" || toolName === "run_workflow") return;

  const outputEl = pillEl.querySelector(".tool-call-output");
  if (!outputEl) return;

  // Special formatting for query_memories
  if (toolName === "query_memories") {
    const memories = parseMemoriesResult(resultText);
    if (memories.length > 0) {
      outputEl.innerHTML = memories.map((m) =>
        `<div class="memory-result-item">` +
          `<span class="memory-result-type">${escapeHtml(m.type)}</span>` +
          `<span class="memory-result-text">${escapeHtml(m.content)}</span>` +
        `</div>`
      ).join("");
    } else {
      outputEl.textContent = resultText;
    }
  } else {
    outputEl.textContent = resultText;
  }

  // Make pill expandable
  pillEl.classList.add("expandable");
  const chevron = pillEl.querySelector(".tool-call-expand-icon");
  if (chevron) chevron.textContent = "▸";

  pillEl.addEventListener("click", () => {
    pillEl.classList.toggle("expanded");
    scrollToBottom();
  });
}

// ── Memory drawer ──
if (memoryBtn) {
  memoryBtn.addEventListener("click", () => {
    memoryDrawer.classList.remove("hidden");
    loadMemories();
  });
}

if (memoryClose) {
  memoryClose.addEventListener("click", () => {
    memoryDrawer.classList.add("hidden");
  });
}

async function deleteMemory(memoryId) {
  try {
    await fetch(`${API_BASE}/memory/${memoryId}`, { method: "DELETE" });
    loadMemories();
  } catch {}
}

async function loadMemories() {
  if (!memoryList) return;
  memoryList.innerHTML = '<div class="wf-picker-loading">loading...</div>';
  try {
    const resp = await fetch(`${API_BASE}/memory`);
    const data = await resp.json();
    const entries = data.entries || [];
    if (!entries.length) {
      memoryList.innerHTML = '<div class="wf-picker-empty">no memories yet</div>';
      return;
    }
    memoryList.innerHTML = "";
    for (const m of entries) {
      const item = document.createElement("div");
      item.className = "memory-item";
      const typeLabel = { episodic: "past run", procedural: "how-to", semantic: "fact" }[m.type] || m.type;
      const date = m.last_used_at ? new Date(m.last_used_at).toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "";
      item.innerHTML =
        `<div class="memory-item-header">` +
          `<span class="memory-item-type ${m.type}">${escapeHtml(typeLabel)}</span>` +
          `<span class="memory-item-meta">${m.num_uses} uses${date ? " · " + date : ""}</span>` +
          `<button class="memory-item-delete icon-btn" title="Delete memory">` +
            `<svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">` +
              `<line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>` +
            `</svg>` +
          `</button>` +
        `</div>` +
        `<div class="memory-item-content">${escapeHtml(m.content)}</div>`;
      item.querySelector(".memory-item-delete").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteMemory(m.id);
      });
      memoryList.appendChild(item);
    }
  } catch {
    memoryList.innerHTML = '<div class="wf-picker-empty">failed to load memories</div>';
  }
}

// ── Workflow picker ──
// ── Workflows drawer ──

if (workflowsBtn) {
  workflowsBtn.addEventListener("click", () => {
    workflowsDrawer.classList.remove("hidden");
    loadWorkflows();
  });
}

if (workflowsClose) {
  workflowsClose.addEventListener("click", () => {
    workflowsDrawer.classList.add("hidden");
  });
}

async function loadWorkflows() {
  if (!workflowsList) return;
  workflowsList.innerHTML = '<div class="wf-picker-loading">loading...</div>';
  try {
    const resp = await fetch(`${API_BASE}/api/workflows`);
    const data = await resp.json();
    const workflows = data.workflows || [];

    if (workflows.length === 0) {
      workflowsList.innerHTML = '<div class="wf-picker-empty">no workflows saved</div>';
      return;
    }

    workflowsList.innerHTML = "";
    for (const wf of workflows) {
      const item = document.createElement("div");
      item.className = "chat-item";
      item.innerHTML = `
        <div class="chat-item-row">
          <div class="chat-item-title">${escapeHtml(wf.name)}</div>
          <button class="chat-item-delete icon-btn" title="Delete">
            <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
        <div class="chat-item-date">${escapeHtml(wf.description || "")}</div>
      `;
      item.querySelector(".chat-item-delete").addEventListener("click", (e) => {
        e.stopPropagation();
        deleteWorkflow(wf.id);
      });
      item.addEventListener("click", async (e) => {
        if (e.target.closest(".chat-item-delete")) return;
        workflowsDrawer.classList.add("hidden");
        currentSessionId = null;
        messagesEl.querySelectorAll(".msg, .thinking").forEach(el => el.remove());
        if (emptyStateEl) emptyStateEl.style.display = "none";
        const text = `Run workflow: ${wf.name}`;
        addMessage("user", text);
        showThinking();
        // Workflows run inside the Showmi tab group, on a fresh tab if needed.
        try {
          await ensureAgentTab();
        } catch (err) {
          removeThinking();
          addMessage("error", `Attach failed: ${err.message}`);
          return;
        }
        try {
          const r = await fetch(`${API_BASE}/api/chat`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ content: text }),
          });
          const data = await r.json();
          if (data.session_id) {
            currentSessionId = data.session_id;
            connectSSE(currentSessionId);
            const state = getSessionState(currentSessionId);
            state.isWorking = true;
          }
        } catch {
          removeThinking();
          addMessage("error", "Connection error.");
        }
      });
      workflowsList.appendChild(item);
    }
  } catch {
    workflowsList.innerHTML = '<div class="wf-picker-empty">failed to load workflows</div>';
  }
}

// ── Periodic drawer refresh ──
// Refresh chat list status every 3 seconds if drawer is open
setInterval(() => {
  if (!chatsDrawer.classList.contains("hidden")) {
    loadChats();
  }
}, 3000);

// ── Record workflow button ──

if (recordBtn) {
  recordBtn.addEventListener("click", () => {
    if (isRecording) return;
    currentSessionId = null;
    messagesEl.querySelectorAll(".msg, .thinking").forEach(el => el.remove());
    if (emptyStateEl) emptyStateEl.style.display = "none";
    const text = "I want to show you a new workflow, start recording";
    addMessage("user", text);
    showThinking();
    (async () => {
      // Recording observes the user's current tab via content scripts only —
      // no chrome.debugger needed, so nothing to attach here.
      const activeTab = await getActiveTabInfo();
      fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: text, active_tab: activeTab }),
      }).then(r => r.json()).then(data => {
        if (data.session_id) {
          currentSessionId = data.session_id;
          connectSSE(currentSessionId);
          const state = getSessionState(currentSessionId);
          state.isWorking = true;
        }
      }).catch(() => { removeThinking(); addMessage("error", "Connection error."); });
    })();
  });
}

// ── Recording (orchestrator-driven) ──

const recordingOverlay = document.getElementById("recording-overlay");
const overlayStopBtn = document.getElementById("overlay-stop-btn");
const overlayEventCount = document.getElementById("overlay-event-count");

// Planning mode
const planningBar = document.getElementById("planning-bar");
const planningApprove = document.getElementById("planning-approve");
const planningReject = document.getElementById("planning-reject");

let isRecording = false;
let currentMode = "browser"; // "browser" | "planning"
let lastProposedManifest = null;
let lastProposedMarkdown = null;
let mediaRecorder = null;
let audioChunks = [];

function _initRecorder(stream) {
  audioChunks = [];
  mediaRecorder = new MediaRecorder(stream, { mimeType: "audio/webm;codecs=opus" });
  mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) audioChunks.push(e.data);
  };
  mediaRecorder.start(1000);
  return { ok: true };
}

async function startAudioCapture() {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    return _initRecorder(stream);
  } catch (err) {
    if (err?.name === "NotAllowedError") {
      // Sidepanel can't trigger the permission prompt — use iframe workaround
      const result = await new Promise((resolve) => {
        chrome.runtime.sendMessage({ type: "REQUEST_MIC_PERMISSION" }, resolve);
      });
      if (result?.granted) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          return _initRecorder(stream);
        } catch (retryErr) {
          mediaRecorder = null;
          return { ok: false, reason: `Mic error after permission grant: ${retryErr?.message || retryErr?.name}` };
        }
      }
      return { ok: false, reason: "Microphone permission denied. Please allow mic access when prompted and try again." };
    }
    mediaRecorder = null;
    if (err?.name === "NotFoundError") {
      return { ok: false, reason: "No microphone found. Connect a mic and try again." };
    }
    return { ok: false, reason: `Mic error: ${err?.message || err?.name}` };
  }
}

function stopAudioCapture() {
  return new Promise((resolve) => {
    if (!mediaRecorder || mediaRecorder.state === "inactive") {
      resolve("");
      return;
    }
    mediaRecorder.onstop = () => {
      mediaRecorder.stream.getTracks().forEach((t) => t.stop());
      if (audioChunks.length === 0) { resolve(""); return; }
      const blob = new Blob(audioChunks, { type: "audio/webm;codecs=opus" });
      const reader = new FileReader();
      reader.onloadend = () => resolve(reader.result);
      reader.readAsDataURL(blob);
    };
    mediaRecorder.stop();
  });
}

// Start recording (triggered by orchestrator command)
async function startRecordingFromOrchestrator() {
  chrome.runtime.sendMessage({ type: "START_RECORDING" }, async (response) => {
    if (response?.ok) {
      isRecording = true;
      if (recordingOverlay) recordingOverlay.classList.remove("hidden");
      if (overlayEventCount) overlayEventCount.textContent = "0";
      document.body.classList.add("recording-active");
      inputEl.disabled = true;
      sendBtn.disabled = true;

      const mic = await startAudioCapture();
      if (mic.ok) {
        inputEl.placeholder = "recording... speak to narrate";
      } else {
        inputEl.placeholder = "recording... (no mic)";
        addMessage("system", mic.reason);
      }
    }
  });
}

// Stop recording (sends recording_complete to server → orchestrator queue)
if (overlayStopBtn) {
  overlayStopBtn.addEventListener("click", () => {
    chrome.runtime.sendMessage({ type: "STOP_RECORDING" }, async (data) => {
      isRecording = false;
      if (recordingOverlay) recordingOverlay.classList.add("hidden");
      document.body.classList.remove("recording-active");

      if (!data || !data.events || data.events.length === 0) {
        addMessage("error", "No events were recorded.");
        updateInputState();
        return;
      }

      const audio_b64 = await stopAudioCapture();

      if (emptyStateEl) emptyStateEl.style.display = "none";
      addMessage("system", `Recorded ${data.events.length} events.`);
      showThinking();

      // Send recording back to orchestrator via REST
      try {
        await fetch(`${API_BASE}/api/sessions/${currentSessionId}/recording`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            start_url: data.startUrl,
            events: data.events,
            audio_b64: audio_b64,
          }),
        });
        enterPlanningMode();
      } catch (err) {
        removeThinking();
        addMessage("error", `Failed to send recording: ${err.message}`);
        updateInputState();
      }
    });
  });
}

// Live event feed during recording
const _REC_ICONS = {
  click: "tap", input: "edit", select: "list", keypress: "key",
  submit: "send", navigation: "nav", tab_switch: "tab",
};

chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "RECORDING_EVENT_COUNT" && isRecording) {
    if (overlayEventCount) overlayEventCount.textContent = String(msg.count);

    // Render live event in chat area
    if (msg.event) {
      const ev = msg.event;
      const label = _REC_ICONS[ev.type] || ev.type;
      let detail = ev.target ? escapeHtml(String(ev.target).substring(0, 60)) : "";
      if (ev.type === "input" && ev.value) detail = escapeHtml(ev.value.substring(0, 40));
      if (ev.type === "navigation") detail = escapeHtml((ev.url || "").replace(/^https?:\/\//, "").substring(0, 50));

      const el = document.createElement("div");
      el.className = "rec-event";
      el.innerHTML = `<span class="rec-event-type">${label}</span>${detail ? ` <span class="rec-event-detail">${detail}</span>` : ""}`;
      messagesEl.appendChild(el);
      scrollToBottom();
    }
  }
});

// ── Planning mode ──

function enterPlanningMode() {
  currentMode = "planning";
  // Planning bar stays hidden until a workflow is proposed
  inputEl.disabled = false;
  inputEl.placeholder = "reply to the planning agent...";
  sendBtn.disabled = false;
}

function exitPlanningMode() {
  currentMode = "browser";
  lastProposedManifest = null;
  lastProposedMarkdown = null;
  if (planningBar) planningBar.classList.add("hidden");
  if (planningApprove) {
    planningApprove._pending = false;
    planningApprove.disabled = false;
    planningApprove.textContent = "approve";
  }
  if (planningReject) {
    planningReject._pending = false;
    planningReject.disabled = false;
    planningReject.textContent = "reject";
  }
  updateInputState();
}

function renderMultipleChoice(question, choices, sessionId) {
  const wrapper = document.createElement("div");
  wrapper.className = "msg agent";

  const q = document.createElement("div");
  q.className = "msg-content";
  q.innerHTML = renderMarkdown(question);
  wrapper.appendChild(q);

  const choicesRow = document.createElement("div");
  choicesRow.className = "planning-choices";
  choices.forEach((choice) => {
    const btn = document.createElement("button");
    btn.className = "planning-choice-btn";
    btn.textContent = choice;
    btn.addEventListener("click", () => {
      // Disable all choice buttons after selection
      choicesRow.querySelectorAll("button").forEach((b) => { b.disabled = true; b.classList.remove("selected"); });
      btn.classList.add("selected");
      // Send response via REST
      fetch(`${API_BASE}/api/sessions/${sessionId}/planning/respond`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ content: choice }),
      }).catch(() => {});
      showThinking();
    });
    choicesRow.appendChild(btn);
  });
  wrapper.appendChild(choicesRow);

  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderWorkflowProposal(content, manifestYaml) {
  const wrapper = document.createElement("div");
  wrapper.className = "msg agent";

  // Show manifest if provided
  if (manifestYaml) {
    const manifestCard = document.createElement("details");
    manifestCard.className = "workflow-card";
    manifestCard.open = false;
    const manifestSummary = document.createElement("summary");
    manifestSummary.textContent = "Manifest (parameters)";
    manifestCard.appendChild(manifestSummary);
    const manifestBody = document.createElement("pre");
    manifestBody.className = "code-block";
    manifestBody.textContent = manifestYaml;
    manifestCard.appendChild(manifestBody);
    wrapper.appendChild(manifestCard);
  }

  const card = document.createElement("details");
  card.className = "workflow-card";
  card.open = true;
  const summary = document.createElement("summary");
  summary.textContent = "Proposed Workflow";
  card.appendChild(summary);
  const body = document.createElement("div");
  body.className = "workflow-card-body";
  body.innerHTML = renderMarkdown(content);
  card.appendChild(body);
  wrapper.appendChild(card);
  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function renderScriptProposal(manifestYaml, scriptCode) {
  const wrapper = document.createElement("div");
  wrapper.className = "msg agent";

  // Manifest card
  const manifestCard = document.createElement("details");
  manifestCard.className = "workflow-card";
  manifestCard.open = false;
  const manifestSummary = document.createElement("summary");
  manifestSummary.textContent = "Manifest (parameters)";
  manifestCard.appendChild(manifestSummary);
  const manifestBody = document.createElement("pre");
  manifestBody.className = "code-block";
  manifestBody.textContent = manifestYaml;
  manifestCard.appendChild(manifestBody);
  wrapper.appendChild(manifestCard);

  // Script card
  const scriptCard = document.createElement("details");
  scriptCard.className = "workflow-card";
  scriptCard.open = true;
  const scriptSummary = document.createElement("summary");
  scriptSummary.textContent = "Workflow Script";
  scriptCard.appendChild(scriptSummary);
  const scriptBody = document.createElement("pre");
  scriptBody.className = "code-block";
  const codeEl = document.createElement("code");
  codeEl.textContent = scriptCode;
  scriptBody.appendChild(codeEl);
  scriptCard.appendChild(scriptBody);
  wrapper.appendChild(scriptCard);

  messagesEl.appendChild(wrapper);
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

// Planning action buttons
if (planningApprove) {
  planningApprove.addEventListener("click", async () => {
    if (planningApprove._pending || !lastProposedMarkdown) return;
    planningApprove._pending = true;
    planningApprove.disabled = true;
    planningApprove.textContent = "saving...";

    let fileContent = lastProposedMarkdown;
    if (lastProposedManifest) {
      const yaml = lastProposedManifest.trim();
      fileContent = `---\n${yaml}\n---\n\n${lastProposedMarkdown.trim()}\n`;
    }

    try {
      // Single REST call: saves workflow AND signals planning agent
      const res = await fetch(`${API_BASE}/api/sessions/${currentSessionId}/planning/approve`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_content: fileContent }),
      });
      const result = await res.json();
      if (res.ok && result.ok) {
        addMessage("system", `Workflow saved: ${result.id}`);
      } else {
        addMessage("error", result.error || "Failed to save workflow.");
      }
    } catch (err) {
      addMessage("error", `Save failed: ${err.message}`);
    }
    exitPlanningMode();
  });
}

if (planningReject) {
  planningReject.addEventListener("click", async () => {
    if (planningReject._pending) return;
    planningReject._pending = true;
    try {
      await fetch(`${API_BASE}/api/sessions/${currentSessionId}/planning/reject`, { method: "POST" });
    } catch {}
    addMessage("system", "Workflow discarded.");
    exitPlanningMode();
  });
}

// ── Auto-attach helpers (chrome.debugger) ──

function showAttachError(text) {
  if (!attachError) return;
  if (!text) {
    attachError.classList.add("hidden");
    attachError.textContent = "";
  } else {
    attachError.textContent = text;
    attachError.classList.remove("hidden");
  }
}

async function registerAttachedTabWithServer(tabId) {
  const r = await fetch(`${API_BASE}/api/session/attach`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ tab_id: tabId }),
  });
  if (!r.ok) {
    const data = await r.json().catch(() => ({}));
    throw new Error(data.error || `Server rejected attach (${r.status})`);
  }
}

// Make sure the Showmi tab group exists and there's an attached agent tab in
// it, then return that tab's id. Reused across chat and workflow flows so the
// agent always operates inside its own group, never on the user's tabs.
async function ensureAgentTab() {
  showAttachError("");
  const resp = await new Promise((resolve) => {
    chrome.runtime.sendMessage({ type: "ENSURE_AGENT_TAB" }, resolve);
  });
  if (!resp || !resp.ok) throw new Error(resp?.error || "Attach failed.");
  await registerAttachedTabWithServer(resp.tabId);
  return resp.tabId;
}

// ── Init ──
initTheme();
fetchModels();
checkServerHealth();
