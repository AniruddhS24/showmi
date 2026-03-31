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

// Logs drawer
const logsBtn = document.getElementById("logs-btn");
const logsDrawer = document.getElementById("logs-drawer");
const logsClose = document.getElementById("logs-close");
const logsClear = document.getElementById("logs-clear");
const logsOutput = document.getElementById("logs-output");

// Workflows drawer
const workflowsBtn = document.getElementById("workflows-btn");
const workflowsDrawer = document.getElementById("workflows-drawer");
const workflowsClose = document.getElementById("workflows-close");
const workflowsList = document.getElementById("workflows-list");

// Disconnected banner
const disconnectedBanner = document.getElementById("disconnected-banner");
const retryConnectBtn = document.getElementById("retry-connect-btn");

// Tab context badge
const tabContextBadge = document.getElementById("tab-context-badge");
const tabContextUrl = document.getElementById("tab-context-url");
const tabContextRemove = document.getElementById("tab-context-remove");

// ── State ──
let ws = null;
let reconnectDelay = 1000;
const MAX_RECONNECT_DELAY = 30000;
let failedConnections = 0;
let currentSessionId = null;
let models = [];
let editingModelId = null;
let attachedTab = null; // { url, title } or null if user dismissed

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
  // Clear messages
  messagesEl.innerHTML = "";
  messagesEl.appendChild(emptyStateEl);
  emptyStateEl.style.display = "";
  currentSessionId = null;
  updateInputState();
  refreshTabContext();
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

// ── Logs drawer ──
let logsWs = null;

logsBtn.addEventListener("click", () => {
  const isOpen = !logsDrawer.classList.contains("hidden");
  if (isOpen) {
    closeLogsDrawer();
  } else {
    openLogsDrawer();
  }
});

logsClose.addEventListener("click", closeLogsDrawer);

logsClear.addEventListener("click", () => {
  logsOutput.textContent = "";
});

function openLogsDrawer() {
  logsDrawer.classList.remove("hidden");
  connectLogStream();
}

function closeLogsDrawer() {
  logsDrawer.classList.add("hidden");
  if (logsWs) {
    try { logsWs.close(); } catch {}
    logsWs = null;
  }
}

function connectLogStream() {
  if (logsWs && logsWs.readyState === WebSocket.OPEN) return;

  logsOutput.textContent = "";
  logsWs = new WebSocket("ws://localhost:8765/ws/logs");

  logsWs.addEventListener("message", (event) => {
    const line = event.data;
    const span = document.createElement("span");
    span.textContent = line + "\n";

    // Color-code by level
    if (/ ERROR /.test(line)) span.className = "log-line-error";
    else if (/ WARN/.test(line)) span.className = "log-line-warn";

    logsOutput.appendChild(span);

    // Auto-scroll to bottom
    logsOutput.scrollTop = logsOutput.scrollHeight;

    // Cap rendered lines
    while (logsOutput.childElementCount > 500) {
      logsOutput.removeChild(logsOutput.firstChild);
    }
  });

  logsWs.addEventListener("close", () => { logsWs = null; });
  logsWs.addEventListener("error", () => {});
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
  const msgSessionId = data.session_id;

  switch (data.type) {
    case "session":
      // Always track the session — for new chats this sets the ID,
      // for continued chats it confirms the existing one
      if (data.session_id) {
        currentSessionId = data.session_id;
        const state = getSessionState(data.session_id);
        state.isWorking = true;
      }
      break;

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

    case "orchestrator_message":
      removeThinking();
      addMessage("agent", data.content || "");
      break;

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
        lastProposedScript = "";  // Playwright disabled
        renderWorkflowProposal(lastProposedMarkdown, lastProposedManifest);
        if (planningBar) planningBar.classList.remove("hidden");
      } else if (tool === "propose_script") {
        // Legacy — treat as propose_workflow
        lastProposedManifest = args.manifest_yaml || "";
        lastProposedMarkdown = args.workflow_markdown || "";
        lastProposedScript = "";
        renderWorkflowProposal(lastProposedMarkdown, lastProposedManifest);
        if (planningBar) planningBar.classList.remove("hidden");
      } else if (tool === "finalize_workflow") {
        lastProposedManifest = "";
        lastProposedMarkdown = args.workflow_content || "";
        lastProposedScript = "";
        renderWorkflowProposal(lastProposedMarkdown);
        if (planningBar) planningBar.classList.remove("hidden");
      }
      break;
    }

    case "planning_complete":
      removeThinking();
      exitPlanningMode();
      if (planningApprove) { planningApprove.disabled = false; planningApprove.textContent = "approve"; }
      if (planningTest) { planningTest.disabled = false; planningTest.textContent = "test"; }
      addMessage("system", data.workflow_id ? `Workflow saved: ${data.workflow_id}` : "Workflow discarded.");
      break;

    case "planning_error":
      removeThinking();
      addMessage("error", data.message || "Planning error");
      exitPlanningMode();
      break;

    case "test_start":
      addMessage("system", "Running workflow test...");
      if (planningTest) { planningTest.disabled = true; planningTest.textContent = "testing..."; }
      break;

    case "test_result": {
      if (planningTest) { planningTest.disabled = false; planningTest.textContent = "test"; }
      if (data.success) {
        addMessage("system", `Test passed in ${(data.duration_ms / 1000).toFixed(1)}s. ${data.return_value || ""}`);
      } else {
        let errorMsg = `Test failed: ${data.error || "Unknown error"}`;
        if (data.traceback) {
          errorMsg += `\n\`\`\`\n${data.traceback}\n\`\`\``;
        }
        addMessage("error", errorMsg);
        if (data.screenshot) {
          const img = document.createElement("img");
          img.src = `data:image/jpeg;base64,${data.screenshot}`;
          img.className = "test-error-screenshot";
          img.alt = "Screenshot at time of error";
          messagesEl.appendChild(img);
          scrollToBottom();
        }
      }
      showThinking(); // agent will respond to test result
      break;
    }

    default:
      if (data.content) addMessage("agent", data.content);
  }
}

// ── Tab context ──
async function refreshTabContext() {
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (tab && tab.url && !tab.url.startsWith("chrome://") && !tab.url.startsWith("chrome-extension://")) {
      attachedTab = { url: tab.url, title: tab.title || "" };
      showTabBadge();
    } else {
      attachedTab = null;
      hideTabBadge();
    }
  } catch {
    attachedTab = null;
    hideTabBadge();
  }
}

function showTabBadge() {
  if (!attachedTab) return;
  try {
    const url = new URL(attachedTab.url);
    tabContextUrl.textContent = url.hostname + (url.pathname !== "/" ? url.pathname : "");
  } catch {
    tabContextUrl.textContent = attachedTab.url;
  }
  tabContextBadge.classList.remove("hidden");
}

function hideTabBadge() {
  tabContextBadge.classList.add("hidden");
}

tabContextRemove.addEventListener("click", () => {
  attachedTab = null;
  hideTabBadge();
});

// Re-detect tab when user switches tabs
chrome.tabs.onActivated.addListener(() => {
  // Only auto-refresh if user hasn't explicitly dismissed
  if (attachedTab !== null || !tabContextBadge.classList.contains("hidden")) {
    refreshTabContext();
  }
});

chrome.tabs.onUpdated.addListener((_tabId, changeInfo) => {
  if (changeInfo.url && attachedTab !== null) {
    refreshTabContext();
  }
});

async function sendMessage() {
  const text = inputEl.value.trim();
  if (!text || !ws || ws.readyState !== WebSocket.OPEN) return;

  // Hide empty state
  if (emptyStateEl) emptyStateEl.style.display = "none";

  addMessage("user", text);
  inputEl.value = "";
  autoResizeInput();

  if (currentMode === "planning") {
    // In planning mode, send as planning response
    ws.send(JSON.stringify({
      type: "planning_response",
      session_id: currentSessionId,
      content: text,
    }));
    showThinking();
    return;
  }

  const payload = {
    type: "message",
    content: text,
    active_tab: attachedTab, // only included if badge is visible
  };

  // If we have an existing session, send session_id for multi-turn
  if (currentSessionId) {
    payload.session_id = currentSessionId;
  }

  ws.send(JSON.stringify(payload));

  // Mark current session as working
  if (currentSessionId) {
    const state = getSessionState(currentSessionId);
    state.isWorking = true;
    state.stepTraceEl = null;
  }

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
  const connected = ws && ws.readyState === WebSocket.OPEN;
  const isWorking = currentSessionId ? getSessionState(currentSessionId).isWorking : false;

  if (isWorking) {
    sendBtn.disabled = false;
    sendBtn.classList.add("is-stop");
    sendBtn.title = "Stop";
  } else {
    sendBtn.classList.remove("is-stop");
    sendBtn.title = "Send";
    sendBtn.disabled = !hasText || !connected;
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

function cancelTask() {
  if (!currentSessionId || !ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({
    type: "cancel",
    session_id: currentSessionId,
  }));
  sendBtn.disabled = true;
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
      const item = document.createElement("button");
      item.className = "chat-item";
      item.innerHTML = `<span class="chat-title">${wf.name}</span><span class="chat-date">${wf.description || ""}</span>`;
      item.addEventListener("click", () => {
        workflowsDrawer.classList.add("hidden");
        // Start a new session and run the workflow
        currentSessionId = null;
        messagesEl.querySelectorAll(".msg, .thinking").forEach(el => el.remove());
        if (emptyStateEl) emptyStateEl.style.display = "none";
        const text = `Run workflow: ${wf.name}`;
        addMessage("user", text);
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({
            type: "message",
            content: text,
          }));
          showThinking();
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

// ── Recording (orchestrator-driven) ──

const recordingOverlay = document.getElementById("recording-overlay");
const overlayStopBtn = document.getElementById("overlay-stop-btn");
const overlayEventCount = document.getElementById("overlay-event-count");

// Planning mode
const planningBar = document.getElementById("planning-bar");
const planningApprove = document.getElementById("planning-approve");
const planningReject = document.getElementById("planning-reject");
const planningTest = document.getElementById("planning-test");

let isRecording = false;
let currentMode = "browser"; // "browser" | "planning"
let lastProposedManifest = null;
let lastProposedScript = null;
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

      // Send recording back to orchestrator
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({
          type: "recording_complete",
          session_id: currentSessionId,
          recording: {
            start_url: data.startUrl,
            events: data.events,
            audio_b64: audio_b64,
          },
        }));
        enterPlanningMode();
      } else {
        removeThinking();
        addMessage("error", "Not connected to server.");
        updateInputState();
      }
    });
  });
}

// Live event count from background
chrome.runtime.onMessage.addListener((msg) => {
  if (msg.type === "RECORDING_EVENT_COUNT" && isRecording) {
    if (overlayEventCount) overlayEventCount.textContent = String(msg.count);
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
  lastProposedScript = null;
  lastProposedMarkdown = null;
  if (planningBar) planningBar.classList.add("hidden");
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
      // Send response
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "planning_response", session_id: sessionId, content: choice }));
      }
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
    if (!lastProposedMarkdown) return;
    planningApprove.disabled = true;
    planningApprove.textContent = "saving...";

    // Build file_content with YAML frontmatter
    let fileContent = lastProposedMarkdown;
    if (lastProposedManifest) {
      // manifest is already YAML — wrap it as frontmatter
      const yaml = lastProposedManifest.trim();
      fileContent = `---\n${yaml}\n---\n\n${lastProposedMarkdown.trim()}\n`;
    }

    try {
      const res = await fetch(`${API_BASE}/api/workflows`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file_content: fileContent }),
      });
      const result = await res.json();
      if (res.ok && result.ok) {
        addMessage("system", `Workflow saved: ${result.id}`);
        exitPlanningMode();
      } else if (res.status === 409) {
        // Already exists — update instead
        const name = fileContent.match(/name:\s*(.+)/)?.[1]?.trim() || "workflow";
        const slug = name.toLowerCase().replace(/[^\w\s-]/g, "").replace(/[\s_]+/g, "-").replace(/-+/g, "-").replace(/^-|-$/g, "");
        const updateRes = await fetch(`${API_BASE}/api/workflows/${slug}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ file_content: fileContent }),
        });
        const updateResult = await updateRes.json();
        if (updateRes.ok && updateResult.ok) {
          addMessage("system", `Workflow updated: ${updateResult.id}`);
          exitPlanningMode();
        } else {
          addMessage("error", updateResult.error || "Failed to save workflow.");
        }
      } else {
        addMessage("error", result.error || "Failed to save workflow.");
      }
    } catch (err) {
      addMessage("error", `Save failed: ${err.message}`);
    }

    planningApprove.disabled = false;
    planningApprove.textContent = "approve";

    // Signal planning agent to stop (best-effort)
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({
        type: "planning_action",
        session_id: currentSessionId,
        action: "approve",
      }));
    }
  });
}

if (planningReject) {
  planningReject.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "planning_action", session_id: currentSessionId, action: "reject" }));
    }
    addMessage("system", "Workflow discarded.");
    exitPlanningMode();
  });
}

if (planningTest) {
  planningTest.addEventListener("click", () => {
    if (ws && ws.readyState === WebSocket.OPEN && lastProposedMarkdown) {
      ws.send(JSON.stringify({ type: "planning_action", session_id: currentSessionId, action: "test" }));
      planningTest.disabled = true;
      planningTest.textContent = "testing...";
    } else {
      addMessage("system", "No workflow to test. Wait for a workflow proposal first.");
    }
  });
}

// ── Init ──
initTheme();
fetchModels();
connect();
refreshTabContext();
