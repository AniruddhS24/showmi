// Content script injected into tabs during recording.
// Captures DOM interactions and sends them to the background script.
// Idempotent — safe to inject multiple times (guard flag).

if (!window.__showmi_recorder) {
  window.__showmi_recorder = true;

  // ── Helpers ──

  function buildSelector(el) {
    if (el.id) return `#${el.id}`;
    let s = el.tagName.toLowerCase();
    if (el.className && typeof el.className === "string") {
      const classes = el.className.trim().split(/\s+/).slice(0, 2).join(".");
      if (classes) s += `.${classes}`;
    }
    // Add one parent for disambiguation
    if (el.parentElement && el.parentElement !== document.body) {
      const parent = el.parentElement;
      let ps = parent.tagName.toLowerCase();
      if (parent.id) ps = `#${parent.id}`;
      s = `${ps} > ${s}`;
    }
    return s;
  }

  function describeElement(el) {
    return {
      tag: el.tagName.toLowerCase(),
      text: (el.innerText || "").slice(0, 120).trim(),
      aria_label: el.getAttribute("aria-label") || "",
      placeholder: el.getAttribute("placeholder") || "",
      name: el.getAttribute("name") || "",
      id: el.id || "",
      type: el.getAttribute("type") || "",
      role: el.getAttribute("role") || "",
      href: el.getAttribute("href") || "",
      selector: buildSelector(el),
    };
  }

  function describeElementCompact(el) {
    if (!el || !el.tagName) return "";
    const tag = el.tagName.toLowerCase();
    const attrs = [];
    if (el.id) attrs.push(`id="${el.id}"`);
    if (el.className && typeof el.className === "string") {
      const cls = el.className.trim();
      if (cls) attrs.push(`class="${cls.split(/\s+/).slice(0, 3).join(" ")}"`);
    }
    for (const a of ["aria-label", "role", "placeholder", "type", "name", "href"]) {
      const v = el.getAttribute(a);
      if (v) attrs.push(`${a}="${v.slice(0, 60)}"`);
    }
    const text = (el.innerText || "").slice(0, 80).trim().replace(/\n+/g, " ");
    const attrStr = attrs.length ? " " + attrs.join(" ") : "";
    return text ? `<${tag}${attrStr}>${text}</${tag}>` : `<${tag}${attrStr}>`;
  }

  function captureLocalDOM(el, maxElements = 40) {
    const seen = new Set();
    const collected = [];

    function addEl(node, priority) {
      if (!node || !node.tagName || seen.has(node) || node === document.documentElement || node === document.body) return;
      seen.add(node);
      const desc = describeElementCompact(node);
      if (desc) collected.push({ desc, priority });
    }

    // The interacted element (highest priority)
    addEl(el, 0);

    // Parent chain (up to 3 levels)
    let parent = el?.parentElement;
    for (let i = 1; i <= 3 && parent && parent !== document.body; i++) {
      addEl(parent, i);
      // Siblings at this level
      if (parent.parentElement) {
        for (const sibling of parent.parentElement.children) {
          addEl(sibling, i + 1);
        }
      }
      parent = parent.parentElement;
    }

    // Siblings of the target
    if (el?.parentElement) {
      for (const sibling of el.parentElement.children) {
        addEl(sibling, 1);
      }
    }

    // Nearby landmarks
    const landmarks = document.querySelectorAll(
      "[role], [aria-label], header, nav, main, footer, form, h1, h2, h3"
    );
    for (const lm of landmarks) {
      addEl(lm, 5);
    }

    // Sort by priority (closer = lower number = first), cap at max
    collected.sort((a, b) => a.priority - b.priority);
    return collected.slice(0, maxElements).map((c) => "  " + c.desc).join("\n");
  }

  function sendEvent(evt) {
    try {
      chrome.runtime.sendMessage({ type: "RECORDER_EVENT", event: evt });
    } catch {
      // Extension context invalidated — clean up
      cleanup();
    }
  }

  // ── Event handlers ──

  const listeners = [];
  function listen(target, event, handler, options) {
    target.addEventListener(event, handler, options);
    listeners.push({ target, event, handler, options });
  }

  // Click
  listen(document, "click", (e) => {
    const el = e.target.closest("[role], button, a, input, select, textarea, [onclick]") || e.target;
    sendEvent({
      type: "click",
      timestamp: new Date().toISOString(),
      url: location.href,
      page_title: document.title,
      target: describeElement(el),
      value: "",
      dom_context: captureLocalDOM(el),
    });
  }, true);

  // Input (debounced per element)
  const inputTimers = new WeakMap();
  const pendingInputs = new WeakMap();

  function flushInput(el) {
    if (pendingInputs.has(el)) {
      sendEvent(pendingInputs.get(el));
      pendingInputs.delete(el);
    }
    if (inputTimers.has(el)) {
      clearTimeout(inputTimers.get(el));
      inputTimers.delete(el);
    }
  }

  listen(document, "input", (e) => {
    const el = e.target;
    if (!el || !("value" in el)) return;

    const evt = {
      type: "input",
      timestamp: new Date().toISOString(),
      url: location.href,
      page_title: document.title,
      target: describeElement(el),
      value: el.value,
      dom_context: captureLocalDOM(el),
    };

    pendingInputs.set(el, evt);

    if (inputTimers.has(el)) clearTimeout(inputTimers.get(el));
    inputTimers.set(el, setTimeout(() => {
      flushInput(el);
    }, 500));
  }, true);

  // Change (for select dropdowns, checkboxes, radios)
  listen(document, "change", (e) => {
    const el = e.target;
    const tag = el.tagName.toLowerCase();
    if (tag === "select" || el.type === "checkbox" || el.type === "radio") {
      sendEvent({
        type: "select",
        timestamp: new Date().toISOString(),
        url: location.href,
        page_title: document.title,
        target: describeElement(el),
        value: el.type === "checkbox" ? String(el.checked) : el.value,
        dom_context: captureLocalDOM(el),
      });
    }
  }, true);

  // Keydown (Enter and Escape only)
  listen(document, "keydown", (e) => {
    if (e.key !== "Enter" && e.key !== "Escape") return;
    sendEvent({
      type: "keypress",
      timestamp: new Date().toISOString(),
      url: location.href,
      page_title: document.title,
      target: describeElement(e.target),
      value: e.key,
      dom_context: captureLocalDOM(e.target),
    });
  }, true);

  // Submit
  listen(document, "submit", (e) => {
    const form = e.target;
    sendEvent({
      type: "submit",
      timestamp: new Date().toISOString(),
      url: location.href,
      page_title: document.title,
      target: {
        tag: "form",
        text: "",
        aria_label: "",
        placeholder: "",
        name: form.getAttribute("name") || "",
        id: form.id || "",
        type: "",
        role: "",
        href: form.action || "",
        selector: buildSelector(form),
      },
      value: "",
      dom_context: captureLocalDOM(form),
    });
  }, true);

  // ── Visual indicator ──

  const indicator = document.createElement("div");
  indicator.id = "__showmi_rec_indicator";
  indicator.style.cssText =
    "position:fixed;top:8px;right:8px;width:10px;height:10px;border-radius:50%;" +
    "background:#f85149;z-index:2147483647;pointer-events:none;box-shadow:0 0 0 2px rgba(248,81,73,0.3);" +
    "animation:__showmi_pulse 1.2s infinite;";
  const style = document.createElement("style");
  style.textContent =
    "@keyframes __showmi_pulse{0%,100%{opacity:1}50%{opacity:0.3}}";
  document.documentElement.appendChild(style);
  document.documentElement.appendChild(indicator);

  // ── Cleanup ──

  function cleanup() {
    for (const { target, event, handler, options } of listeners) {
      target.removeEventListener(event, handler, options);
    }
    listeners.length = 0;

    // Flush any pending debounced inputs
    document.querySelectorAll("input, textarea").forEach((el) => flushInput(el));

    // Remove visual indicator
    indicator.remove();
    style.remove();

    window.__showmi_recorder = false;
  }

  // Listen for stop signal
  chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type === "RECORDER_STOP") {
      cleanup();
    }
  });
}
