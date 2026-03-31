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

// ── Screenshot capture ──

async function captureScreenshot() {
  try {
    return await chrome.tabs.captureVisibleTab(null, { format: "jpeg", quality: 50 });
  } catch {
    return null;
  }
}

function broadcastEventCount() {
  chrome.runtime.sendMessage({
    type: "RECORDING_EVENT_COUNT",
    count: recordedEvents.length,
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
