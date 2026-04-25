chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});

// ── CDP bridge (chrome.debugger ↔ showmi server) ──
// Mirrors the Claude for Chrome extension's transport: chrome.debugger.attach
// on the user's tab, and a WebSocket relay to the showmi server which acts as
// a CDP reverse-proxy for browser-use. No --remote-debugging-port, no new
// Chrome window — the user's real profile/logins drive the task.

const SHOWMI_WS_BASE = "ws://localhost:8765";
const DEBUGGER_PROTOCOL_VERSION = "1.3";

const attachedTabs = new Map(); // tabId → WebSocket (bridge)
const intentionallyDetached = new Set(); // tabIds we've chosen to detach — skip reconnect
let controlWs = null;
let controlWsConnecting = null;

// ── Agent tab group ──
// Showmi only ever drives tabs inside its own Chrome tab group. The group
// gives the user a clear visual boundary in the tab strip, and lets us refuse
// to attach the debugger to anything outside it. Mirrors the Claude for
// Chrome extension's UX.
const SHOWMI_GROUP_TITLE = "Showmi";
const SHOWMI_GROUP_COLOR = "blue";
let agentGroupId = null;            // chrome.tabGroups id, or null
const agentTabIds = new Set();      // tab ids known to live in the agent group

async function ensureAgentGroup() {
  // 1. Reuse cached group if still valid.
  if (agentGroupId !== null) {
    try {
      await chrome.tabGroups.get(agentGroupId);
      return agentGroupId;
    } catch {
      agentGroupId = null;
      agentTabIds.clear();
    }
  }
  // 2. Re-discover by title — survives MV3 service-worker restarts so the
  //    user doesn't see a fresh group every time the worker wakes up.
  try {
    const groups = await chrome.tabGroups.query({ title: SHOWMI_GROUP_TITLE });
    if (groups && groups.length > 0) {
      agentGroupId = groups[0].id;
      const tabs = await chrome.tabs.query({ groupId: agentGroupId });
      agentTabIds.clear();
      for (const t of tabs) agentTabIds.add(t.id);
      return agentGroupId;
    }
  } catch {
    // tabGroups.query may not exist on older Chrome — handled by manifest min version.
  }
  // 3. Create a new group seeded with one blank tab. (A group can't exist
  //    without at least one tab, so the seed is the group's anchor.)
  const seed = await chrome.tabs.create({ url: "about:blank", active: true });
  if (!seed?.id) throw new Error("Could not create Showmi seed tab.");
  const groupId = await chrome.tabs.group({ tabIds: [seed.id] });
  await chrome.tabGroups.update(groupId, {
    title: SHOWMI_GROUP_TITLE,
    color: SHOWMI_GROUP_COLOR,
    collapsed: false,
  });
  agentGroupId = groupId;
  agentTabIds.clear();
  agentTabIds.add(seed.id);
  return agentGroupId;
}

async function ensureAgentTab() {
  await ensureAgentGroup();
  // Reuse a still-living group tab if there is one.
  for (const tabId of Array.from(agentTabIds)) {
    let tab = null;
    try { tab = await chrome.tabs.get(tabId); } catch { tab = null; }
    if (!tab || tab.groupId !== agentGroupId) {
      agentTabIds.delete(tabId);
      continue;
    }
    if (!attachedTabs.has(tabId)) await attachTabInternal(tabId);
    return tabId;
  }
  // No live agent tab. Create one and put it in the group before attaching.
  const tab = await chrome.tabs.create({ url: "about:blank", active: true });
  if (!tab?.id) throw new Error("Could not create Showmi tab.");
  await chrome.tabs.group({ groupId: agentGroupId, tabIds: [tab.id] });
  agentTabIds.add(tab.id);
  await attachTabInternal(tab.id);
  return tab.id;
}

async function ensureControlWs() {
  if (controlWs && controlWs.readyState === WebSocket.OPEN) return controlWs;
  if (controlWsConnecting) return controlWsConnecting;

  controlWsConnecting = new Promise((resolve, reject) => {
    let ws;
    try {
      ws = new WebSocket(`${SHOWMI_WS_BASE}/cdp-control`);
    } catch (e) {
      controlWsConnecting = null;
      reject(e);
      return;
    }
    ws.onopen = () => {
      controlWs = ws;
      controlWsConnecting = null;
      resolve(ws);
    };
    ws.onmessage = (event) => handleControlMessage(event.data);
    ws.onclose = () => {
      if (controlWs === ws) controlWs = null;
    };
    ws.onerror = () => {
      if (!controlWs) {
        controlWsConnecting = null;
        reject(new Error("Control WS failed to open — is `showmi start` running?"));
      }
    };
  });
  return controlWsConnecting;
}

function sendControl(obj) {
  if (controlWs && controlWs.readyState === WebSocket.OPEN) {
    controlWs.send(JSON.stringify(obj));
  }
}

async function handleControlMessage(raw) {
  let msg;
  try { msg = JSON.parse(raw); } catch { return; }
  const { type, reqId } = msg;

  if (type === "CREATE_TAB") {
    try {
      await ensureAgentGroup();
      const tab = await chrome.tabs.create({ url: msg.url || "about:blank" });
      await chrome.tabs.group({ groupId: agentGroupId, tabIds: [tab.id] });
      agentTabIds.add(tab.id);
      await attachTabInternal(tab.id);
      sendControl({ type: "CREATE_TAB_OK", reqId, tabId: tab.id });
    } catch (err) {
      sendControl({ type: "CREATE_TAB_ERR", reqId, error: String(err?.message || err) });
    }
  } else if (type === "CLOSE_TAB") {
    try {
      await detachTabInternal(msg.tabId);
      await chrome.tabs.remove(msg.tabId);
      sendControl({ type: "CLOSE_TAB_OK", reqId, tabId: msg.tabId });
    } catch (err) {
      sendControl({ type: "CLOSE_TAB_ERR", reqId, error: String(err?.message || err) });
    }
  } else if (type === "ENSURE_AGENT_TAB") {
    // Server is starting a browser task and needs an agent tab. Create the
    // Showmi group + a tab if neither exists yet, otherwise reuse.
    try {
      const tabId = await ensureAgentTab();
      sendControl({ type: "ENSURE_AGENT_TAB_OK", reqId, tabId });
    } catch (err) {
      sendControl({ type: "ENSURE_AGENT_TAB_ERR", reqId, error: String(err?.message || err) });
    }
  } else if (type === "ACTIVATE_TAB") {
    try {
      await chrome.tabs.update(msg.tabId, { active: true });
    } catch {}
  }
}

function sendOnDebugger(tabId, method, params) {
  return new Promise((resolve) => {
    chrome.debugger.sendCommand({ tabId }, method, params || {}, (result) => {
      if (chrome.runtime.lastError) {
        resolve({ error: chrome.runtime.lastError.message || "debugger error" });
      } else {
        resolve({ result: result || {} });
      }
    });
  });
}

async function openBridgeWs(tabId) {
  return new Promise((resolve, reject) => {
    let opened = false;
    const ws = new WebSocket(`${SHOWMI_WS_BASE}/cdp-bridge?tabId=${tabId}`);
    ws.onopen = () => {
      opened = true;
      attachedTabs.set(tabId, ws);
      resolve(ws);
    };
    ws.onmessage = async (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      const { id, method, params } = msg;
      const { result, error } = await sendOnDebugger(tabId, method, params);
      if (ws.readyState !== WebSocket.OPEN) return;
      if (error) {
        ws.send(JSON.stringify({ id, error: { code: -32000, message: error } }));
      } else {
        ws.send(JSON.stringify({ id, result }));
      }
    };
    ws.onclose = () => {
      const current = attachedTabs.get(tabId);
      if (current === ws) attachedTabs.delete(tabId);
      // If we intentionally detached this tab, we're done.
      if (intentionallyDetached.has(tabId)) {
        intentionallyDetached.delete(tabId);
        return;
      }
      // Bridge dropped unexpectedly. If chrome.debugger is still attached to
      // this tab, reopen the bridge so the agent can keep driving it.
      if (!opened) return; // Never connected — reject already fired.
      reopenBridgeIfStillDebugged(tabId);
    };
    ws.onerror = () => {
      if (!opened) reject(new Error("Bridge WS failed to open — is the showmi server running?"));
    };
  });
}

async function reopenBridgeIfStillDebugged(tabId) {
  // Confirm the debugger is still attached (Chrome may have auto-detached on
  // tab close, target crash, or user closing DevTools).
  const targets = await new Promise((resolve) =>
    chrome.debugger.getTargets((ts) => resolve(ts || []))
  );
  const stillAttached = targets.some((t) => t.tabId === tabId && t.attached);
  if (!stillAttached) return;
  try {
    await ensureControlWs();
    await openBridgeWs(tabId);
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    sendControl({
      type: "TAB_ATTACHED",
      tabId,
      url: tab?.url || "about:blank",
      title: tab?.title || "",
    });
  } catch (err) {
    console.warn(`[showmi] bridge reconnect failed for tabId=${tabId}:`, err);
  }
}

async function attachTabInternal(tabId) {
  // Group fence: refuse to attach to anything outside the Showmi group.
  // The only exception is when no group exists yet — in that case the caller
  // (ensureAgentTab / CREATE_TAB) is responsible for assigning the tab to the
  // group before this point.
  if (agentGroupId !== null) {
    let tab = null;
    try { tab = await chrome.tabs.get(tabId); } catch { tab = null; }
    if (!tab || tab.groupId !== agentGroupId) {
      throw new Error("Showmi only attaches to tabs in its own group.");
    }
  }
  if (!attachedTabs.has(tabId)) {
    await new Promise((resolve, reject) => {
      chrome.debugger.attach({ tabId }, DEBUGGER_PROTOCOL_VERSION, () => {
        if (chrome.runtime.lastError) {
          reject(new Error(chrome.runtime.lastError.message));
        } else {
          resolve();
        }
      });
    });
    try {
      await ensureControlWs();
    } catch (err) {
      try { chrome.debugger.detach({ tabId }); } catch {}
      throw err;
    }
    try {
      await openBridgeWs(tabId);
    } catch (err) {
      try { chrome.debugger.detach({ tabId }); } catch {}
      throw err;
    }
  } else {
    // Already attached in this worker — make sure the server still has us.
    // (Server may have restarted while the extension kept state.)
    try { await ensureControlWs(); } catch {}
  }
  const tab = await chrome.tabs.get(tabId).catch(() => null);
  agentTabIds.add(tabId);
  sendControl({
    type: "TAB_ATTACHED",
    tabId,
    url: tab?.url || "about:blank",
    title: tab?.title || "",
  });
  return tabId;
}

async function detachTabInternal(tabId) {
  intentionallyDetached.add(tabId);
  agentTabIds.delete(tabId);
  const ws = attachedTabs.get(tabId);
  if (ws) {
    try { ws.close(); } catch {}
    attachedTabs.delete(tabId);
  }
  try {
    await new Promise((resolve) => {
      chrome.debugger.detach({ tabId }, () => {
        void chrome.runtime.lastError;
        resolve();
      });
    });
  } catch {}
  sendControl({ type: "TAB_DETACHED", tabId });
}

// Forward chrome.debugger events → bridge WS for that tab.
chrome.debugger.onEvent.addListener((source, method, params) => {
  const ws = attachedTabs.get(source.tabId);
  if (!ws || ws.readyState !== WebSocket.OPEN) return;
  ws.send(JSON.stringify({ method, params }));
});

chrome.debugger.onDetach.addListener((source, reason) => {
  const tabId = source.tabId;
  if (!tabId) return;
  // Debugger is gone — don't try to reopen the bridge.
  intentionallyDetached.add(tabId);
  const ws = attachedTabs.get(tabId);
  if (ws) { try { ws.close(); } catch {} attachedTabs.delete(tabId); }
  sendControl({ type: "TAB_DETACHED", tabId, reason });
});

// Propagate URL / title updates on attached tabs, and watch for an attached
// tab being moved out of the Showmi group — that's the user reclaiming it.
chrome.tabs.onUpdated.addListener((tabId, changeInfo, tab) => {
  if (!attachedTabs.has(tabId)) return;
  if (changeInfo.url || changeInfo.title) {
    sendControl({
      type: "TAB_UPDATED",
      tabId,
      url: tab?.url || "about:blank",
      title: tab?.title || "",
    });
  }
  if (
    agentGroupId !== null &&
    typeof changeInfo.groupId === "number" &&
    changeInfo.groupId !== agentGroupId
  ) {
    // User dragged the tab out of the Showmi group. Treat as a release.
    detachTabInternal(tabId);
  }
});

chrome.tabs.onRemoved.addListener((tabId) => {
  if (attachedTabs.has(tabId)) {
    detachTabInternal(tabId);
  }
  agentTabIds.delete(tabId);
  if (agentTabIds.size === 0) {
    // Last group tab is gone — Chrome will dispose the group itself; drop our
    // cached id so the next request creates a fresh one.
    agentGroupId = null;
  }
});

// Catch tabs the agent spawns indirectly (e.g. clicking a link with
// target=_blank). Chrome puts them next to the opener but does NOT inherit
// the group, so we move them in ourselves.
chrome.tabs.onCreated.addListener((tab) => {
  if (agentGroupId === null) return;
  if (!tab?.id || typeof tab.openerTabId !== "number") return;
  if (!agentTabIds.has(tab.openerTabId)) return;
  chrome.tabs.group({ groupId: agentGroupId, tabIds: [tab.id] }).then(
    () => { agentTabIds.add(tab.id); },
    () => { /* tab may have been pinned, in another window, etc — leave it */ }
  );
});

// Keep-alive: MV3 service workers die after ~30s idle. Receiving the alarm
// event wakes the worker briefly, which is enough to keep open WebSockets
// from the GC reaper during long-running agent runs.
chrome.alarms.create("showmi-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name !== "showmi-keepalive") return;
  if (controlWs && controlWs.readyState === WebSocket.OPEN) {
    sendControl({ type: "PING" });
  }
});

function getAttachedTabIds() {
  return Array.from(attachedTabs.keys());
}

// ── Recording state ──

let isRecording = false;
let recordedEvents = [];
let recordingStartUrl = "";

// Named listener refs (so we can remove them on stop)
let onTabUpdatedListener = null;
let onTabActivatedListener = null;

// ── Screenshot capture ──

async function captureScreenshot() {
  try {
    return await chrome.tabs.captureVisibleTab(null, { format: "jpeg", quality: 50 });
  } catch {
    return null;
  }
}

function broadcastEventCount() {
  const last = recordedEvents[recordedEvents.length - 1];
  chrome.runtime.sendMessage({
    type: "RECORDING_EVENT_COUNT",
    count: recordedEvents.length,
    event: last ? {
      type: last.type,
      target: last.target?.text || last.target?.aria_label || last.target?.placeholder || last.target?.tag || "",
      value: last.value || "",
      url: last.url || "",
    } : null,
  }).catch(() => {});
}

// ── Inject recorder into a single tab (best-effort) ──

async function injectRecorder(tabId) {
  try {
    await chrome.scripting.executeScript({
      target: { tabId },
      files: ["recorder.js"],
    });
  } catch {
    // Tab may be chrome://, devtools, etc — skip silently
  }
}

// ── Start recording ──

async function startRecording() {
  isRecording = true;
  recordedEvents = [];

  // Get start URL from active tab
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    recordingStartUrl = tab?.url || "";
  } catch {
    recordingStartUrl = "";
  }

  // Inject recorder into all open tabs
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.url && !tab.url.startsWith("chrome://") && !tab.url.startsWith("chrome-extension://")) {
      await injectRecorder(tab.id);
    }
  }

  // Listen for new page loads to re-inject
  onTabUpdatedListener = async (tabId, changeInfo, tab) => {
    if (!isRecording) return;
    if (changeInfo.status === "complete" && tab.url && !tab.url.startsWith("chrome://") && !tab.url.startsWith("chrome-extension://")) {
      await injectRecorder(tabId);
      const screenshot = await captureScreenshot();
      const event = {
        type: "navigation",
        timestamp: new Date().toISOString(),
        url: tab.url,
        page_title: tab.title || "",
        target: {},
        value: "",
      };
      if (screenshot) event.screenshot = screenshot;
      recordedEvents.push(event);
      broadcastEventCount();
    }
  };
  chrome.tabs.onUpdated.addListener(onTabUpdatedListener);

  // Listen for tab switches
  onTabActivatedListener = async (activeInfo) => {
    if (!isRecording) return;
    try {
      const tab = await chrome.tabs.get(activeInfo.tabId);
      recordedEvents.push({
        type: "tab_switch",
        timestamp: new Date().toISOString(),
        url: tab.url || "",
        page_title: tab.title || "",
        target: {},
        value: "",
      });
    } catch {
      // Tab may have been closed
    }
  };
  chrome.tabs.onActivated.addListener(onTabActivatedListener);
}

// ── Stop recording ──

async function stopRecording() {
  isRecording = false;

  // Remove tab listeners
  if (onTabUpdatedListener) {
    chrome.tabs.onUpdated.removeListener(onTabUpdatedListener);
    onTabUpdatedListener = null;
  }
  if (onTabActivatedListener) {
    chrome.tabs.onActivated.removeListener(onTabActivatedListener);
    onTabActivatedListener = null;
  }

  // Tell all tabs to stop recording
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    try {
      await chrome.tabs.sendMessage(tab.id, { type: "RECORDER_STOP" });
    } catch {
      // Tab may not have content script
    }
  }

  return {
    startUrl: recordingStartUrl,
    events: recordedEvents,
  };
}

// ── Message router ──

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {

  if (msg.type === "DETACH_ACTIVE_TAB") {
    (async () => {
      try {
        const tabId = typeof msg.tabId === "number" ? msg.tabId : getAttachedTabIds()[0];
        if (tabId != null) await detachTabInternal(tabId);
        sendResponse({ ok: true });
      } catch (err) {
        sendResponse({ ok: false, error: String(err?.message || err) });
      }
    })();
    return true;
  }

  if (msg.type === "GET_ATTACH_STATUS") {
    sendResponse({ attachedTabIds: getAttachedTabIds() });
    return; // sync
  }

  if (msg.type === "START_RECORDING") {
    startRecording().then(() => sendResponse({ ok: true }));
    return true; // async response
  }

  if (msg.type === "STOP_RECORDING") {
    stopRecording().then((data) => sendResponse(data));
    return true; // async response
  }

  if (msg.type === "REQUEST_MIC_PERMISSION") {
    (async () => {
      try {
        const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
        if (!tab || !tab.url || tab.url.startsWith("chrome://") || tab.url.startsWith("chrome-extension://")) {
          sendResponse({ granted: false, error: "NoSuitableTab" });
          return;
        }

        const iframeUrl = chrome.runtime.getURL("mic-permission.html");

        await chrome.scripting.executeScript({
          target: { tabId: tab.id },
          func: (url) => {
            if (document.getElementById("__showmi_mic_iframe")) return;
            const iframe = document.createElement("iframe");
            iframe.id = "__showmi_mic_iframe";
            iframe.src = url;
            iframe.allow = "microphone";
            iframe.style.cssText = "display:none;width:0;height:0;border:none;position:fixed;top:-9999px;";
            document.body.appendChild(iframe);
          },
          args: [iframeUrl],
        });

        const result = await new Promise((resolve) => {
          const timeout = setTimeout(() => resolve({ granted: false, error: "Timeout" }), 30000);
          const listener = (message) => {
            if (message.type === "MIC_PERMISSION_RESULT") {
              clearTimeout(timeout);
              chrome.runtime.onMessage.removeListener(listener);
              resolve(message);
            }
          };
          chrome.runtime.onMessage.addListener(listener);
        });

        try {
          await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: () => {
              const el = document.getElementById("__showmi_mic_iframe");
              if (el) el.remove();
            },
          });
        } catch { /* tab may have navigated */ }

        sendResponse(result);
      } catch (err) {
        sendResponse({ granted: false, error: err?.message || "Unknown" });
      }
    })();
    return true;
  }

  if (msg.type === "RECORDER_EVENT") {
    if (isRecording) {
      const event = msg.event;
      // Capture screenshot on key interaction events
      const screenshotTypes = ["click", "submit", "select"];
      if (screenshotTypes.includes(event.type)) {
        captureScreenshot().then((dataUrl) => {
          if (dataUrl) event.screenshot = dataUrl;
          recordedEvents.push(event);
          broadcastEventCount();
        });
      } else {
        recordedEvents.push(event);
        broadcastEventCount();
      }
    }
    return; // no async response needed
  }
});
