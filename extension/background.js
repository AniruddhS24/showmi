chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true });

chrome.action.onClicked.addListener(async (tab) => {
  await chrome.sidePanel.open({ tabId: tab.id });
});

// ── Recording state ──

let isRecording = false;
let recordedEvents = [];
let recordingStartUrl = "";

// Named listener refs (so we can remove them on stop)
let onTabUpdatedListener = null;
let onTabActivatedListener = null;

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
      recordedEvents.push({
        type: "navigation",
        timestamp: new Date().toISOString(),
        url: tab.url,
        page_title: tab.title || "",
        target: {},
        value: "",
      });
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
  if (msg.type === "START_RECORDING") {
    startRecording().then(() => sendResponse({ ok: true }));
    return true; // async response
  }

  if (msg.type === "STOP_RECORDING") {
    stopRecording().then((data) => sendResponse(data));
    return true; // async response
  }

  if (msg.type === "RECORDER_EVENT") {
    if (isRecording) {
      recordedEvents.push(msg.event);
      // Broadcast event count to sidepanel
      chrome.runtime.sendMessage({
        type: "RECORDING_EVENT_COUNT",
        count: recordedEvents.length,
      }).catch(() => {});
    }
  }
});
