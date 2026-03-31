(async () => {
  try {
    const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    stream.getTracks().forEach((t) => t.stop());
    chrome.runtime.sendMessage({ type: "MIC_PERMISSION_RESULT", granted: true });
  } catch (err) {
    chrome.runtime.sendMessage({
      type: "MIC_PERMISSION_RESULT",
      granted: false,
      error: err?.name || "Unknown",
    });
  }
})();
