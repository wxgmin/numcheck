/* NumCheck UI. Vanilla JS, no build step. All DOM is built with textContent (never innerHTML). */
(function () {
  "use strict";

  // ------------------------------------------------------------------ constants
  var STAGES = [
    ["validate", "Validating files"], ["deck", "Reading deck"], ["models", "Reading models"],
    ["match", "Matching"], ["checks", "Checks"], ["reports", "Writing reports"]
  ];
  var TYPE_LABELS = {
    mismatch: "Model mismatch", inconsistent_across_slides: "Inconsistent across slides", sum_error: "Sum error",
    pct_reconcile: "Percent reconcile", growth_reconcile: "Growth reconcile", ratio_reconcile: "Ratio reconcile",
    unit_period_mismatch: "Unit / period", stale: "Stale", unmatched: "Unmatched", ambiguous: "Ambiguous",
    period_ambiguous: "Period ambiguous", sign_error: "Sign error", scale_error: "Scale error",
    rounding: "Rounding", source_error: "Source error", footnote: "Footnote"
  };
  var STATUS_LABELS = {
    matched: "Verified", matched_value_only: "Value only", mismatch: "Mismatch",
    stale: "Stale", ambiguous: "Ambiguous", unmatched: "Unmatched", skipped: "Skipped"
  };
  var SEV_ORDER = { high: 0, medium: 1, low: 2 };
  var SEV_KEYS = ["high", "medium", "low"];
  var REVIEW_LABELS = { resolved: "Resolved", false_positive: "False positive" };
  var COLUMNS = [
    ["sev", "Severity", ""], ["slide", "Slide", "r"], ["type", "Finding", ""], ["metric", "Metric", ""],
    ["deck", "In the deck", "r"], ["expected", "Expected", "r"],
    ["", "Evidence", "issue-inspect"], ["source", "Excel source", ""]
  ];
  var COV_PAGE = 500;
  var POLL_MS = 700;

  // ---------------------------------------------------------------------- state
  var S = {
    meta: null,
    chatGPTConnected: false,
    files: { deck: [], models: [], old: [] },
    jobId: null, polling: null, running: false,
    data: null, issues: [], matches: [], matchByUid: new Map(),
    sourceBusy: false,
    sort: { key: "sev", dir: 1 },
    sev: {}, type: {}, search: "", slide: "", reviewFilter: "",
    visible: [], selectedId: null, drawerId: null, lastFocus: null,
    reviews: {}, tab: "issues",
    covStatus: {}, covSearch: "", covLimit: COV_PAGE
  };

  // -------------------------------------------------------------------- helpers
  function $(id) { return document.getElementById(id); }

  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    if (attrs) {
      Object.keys(attrs).forEach(function (k) {
        var v = attrs[k];
        if (v === null || v === undefined || v === false) return;
        if (k === "class") n.className = v;
        else if (k === "text") n.textContent = v;
        else if (k === "on") Object.keys(v).forEach(function (ev) { n.addEventListener(ev, v[ev]); });
        else n.setAttribute(k, v === true ? "" : v);
      });
    }
    (kids || []).forEach(function (c) {
      if (c === null || c === undefined || c === false) return;
      n.appendChild(typeof c === "string" || typeof c === "number" ? document.createTextNode(String(c)) : c);
    });
    return n;
  }

  function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }

  function icon(tplId) { return $(tplId).content.firstElementChild.cloneNode(true); }

  function store(op, key, val) {
    try {
      if (op === "get") return window.localStorage.getItem(key);
      if (op === "set") window.localStorage.setItem(key, val);
    } catch (e) { /* storage unavailable: degrade silently */ }
    return null;
  }

  function api(method, url, body) {
    var opts = { method: method, headers: { "X-Requested-With": "numcheck" } };
    if (body) opts.body = body;
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) {
          var err = new Error(j.error || (j.error && j.error.message) || ("Request failed (" + r.status + ")"));
          err.hint = j.hint || ""; err.file = j.file || ""; err.status = r.status;
          throw err;
        }
        return j;
      });
    });
  }

  function typeLabel(t) { return TYPE_LABELS[t] || (t || "").replace(/_/g, " "); }
  function pct(n) { return (n === null || n === undefined || isNaN(n)) ? "n/a" : (Math.round(n * 10) / 10).toFixed(1) + "%"; }
  function fmtBytes(n) {
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(0) + " KB";
    return (n / 1048576).toFixed(1) + " MB";
  }
  function fmtDelta(d) {
    if (d === null || d === undefined || isNaN(d)) return "";
    var a = Math.abs(d), s;
    if (a === 0) s = "0";
    else if (a >= 1000) s = a.toLocaleString("en-US", { maximumFractionDigits: 0 });
    else s = String(parseFloat(a.toPrecision(3)));
    return (d < 0 ? "−" : "+") + s;
  }
  function plural(n, one, many) { return n + " " + (n === 1 ? one : (many || one + "s")); }

  function sevBadge(sev) { return el("span", { class: "sev sev-" + sev, text: sev.charAt(0).toUpperCase() + sev.slice(1) }); }
  function statusPill(st) { return el("span", { class: "pill pill-" + st, text: STATUS_LABELS[st] || st }); }

  // -------------------------------------------------------------------- theme
  function effectiveTheme() {
    var t = document.documentElement.getAttribute("data-theme");
    if (t) return t;
    return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
  }
  function paintThemeButton() {
    var dark = effectiveTheme() === "dark";
    $("themeLabel").textContent = dark ? "Light" : "Dark";
    $("themeBtn").setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
  }
  function initTheme() {
    var saved = store("get", "numcheck.theme");
    if (saved === "light" || saved === "dark") document.documentElement.setAttribute("data-theme", saved);
    paintThemeButton();
    $("themeBtn").addEventListener("click", function () {
      var next = effectiveTheme() === "dark" ? "light" : "dark";
      document.documentElement.setAttribute("data-theme", next);
      store("set", "numcheck.theme", next);
      paintThemeButton();
    });
  }

  // ------------------------------------------------------------- run status bar
  function setStatus(state, text) {
    $("runStatus").setAttribute("data-state", state);
    $("runStatusText").textContent = text;
  }

  // -------------------------------------------------------------------- meta
  function loadMeta() {
    return fetch("/api/meta").then(function (r) { return r.json(); }).then(function (m) {
      S.meta = m;
      var sel = clear($("set-llm"));
      (m.providers || []).forEach(function (p) {
        sel.appendChild(el("option", { value: p.id, text: p.label }));
      });
      var d = m.defaults || {};
      sel.value = d.llm || "off";
      $("set-rounding").value = d.rounding_mode || "round";
      $("set-base").value = d.base_period ? String(d.base_period).toUpperCase() : "";
      $("set-tol").value = d.tolerance ? d.tolerance : "";
      $("set-require-label").checked = d.require_label_evidence !== false;
      $("set-annotate").checked = d.annotate !== false;
      var ocr = m.ocr && m.ocr.available;
      $("set-ocr").disabled = !ocr;
      $("set-ocr").checked = !!(ocr && d.ocr);
      $("ocr-note").textContent = (m.ocr && m.ocr.detail) || "Local OCR is unavailable.";
      paintLlmNote();
      if (sel.value !== "chatgpt") refreshChatGPT(false);
    }).catch(function () { /* meta is optional; defaults in markup still work */ });
  }
  function paintLlmNote() {
    var id = $("set-llm").value;
    var provider = ((S.meta && S.meta.providers) || []).find(function (p) { return p.id === id; });
    var needsKey = ["openai", "anthropic", "openrouter", "openai-compatible", "jev"].indexOf(id) >= 0;
    $("provider-fields").hidden = id === "off";
    $("chatgpt-connect").hidden = id !== "chatgpt";
    $("llm-model").hidden = id === "chatgpt";
    $("llm-model-label").hidden = id === "chatgpt";
    $("llm-key-field").hidden = !needsKey;
    $("llm-key").value = "";
    $("llm-model").value = provider ? provider.model : "";
    $("llm-model").placeholder = id === "codex" ? "Use Codex default" : "Enter model ID";
    $("provider-status").textContent = id === "off" ? "" : "Connection not checked.";
    $("llm-note").textContent = provider && provider.external
      ? "Sends labels, period names, and source identifiers for ambiguous figures. Files and cell values stay local."
      : "Checks slide numbers against Excel locally.";
    var setup = $("provider-setup");
    setup.textContent = "";
    var links = {
      openai: ["OpenAI API keys", "https://platform.openai.com/api-keys"],
      anthropic: ["Anthropic API keys", "https://console.anthropic.com/settings/keys"],
      openrouter: ["OpenRouter API keys", "https://openrouter.ai/settings/keys"],
      jev: ["Vercel AI Gateway keys", "https://vercel.com/docs/ai-gateway/authentication"],
      ollama: ["Get Ollama", "https://ollama.com/download"],
      codex: ["Codex sign-in instructions", "https://developers.openai.com/codex/auth"]
    };
    if (id === "jev") setup.textContent = "Uses typed choices. Low-probability answers remain unresolved. ";
    if (id === "codex") setup.textContent = "Uses the Codex CLI sign-in on this computer. Run codex login if needed. ";
    if (id === "ollama") setup.textContent = "Start Ollama and pull the model listed above. ";
    if (provider && !provider.available && !needsKey && id !== "codex" && id !== "chatgpt") setup.textContent = provider.reason + " ";
    if (provider && provider.available && needsKey) setup.textContent += "A server credential is configured where required. An entered key overrides it. ";
    if (id === "openai-compatible" && provider && !provider.available) setup.textContent = provider.reason;
    if (id === "chatgpt") setup.textContent = "Uses your eligible ChatGPT plan for label matching. NumCheck saves your connection on this computer and renews it when needed.";
    if (links[id]) setup.appendChild(el("a", { href: links[id][1], target: "_blank", rel: "noopener", text: links[id][0] }));
    paintAIReviewStatus();
    if (id === "chatgpt") return refreshChatGPT(true);
  }

  function paintAIReviewStatus() {
    var picker = $("set-llm"), status = $("ai-review-status");
    var provider = picker.value || "off";
    var automatic = $("llm-auto").checked;
    $("source-search-auto").disabled = automatic;
    $("source-search-note").textContent = automatic ? "Local source search runs first whenever AI is enabled." :
      "Searches the current Excel files locally for every unresolved figure.";
    if (!automatic) {
      status.textContent = S.chatGPTConnected && (provider === "off" || provider === "chatgpt")
        ? "Off for this review. ChatGPT is connected." : "Off for this review.";
    } else if (provider === "off") {
      status.textContent = "Choose an AI provider to use in this review.";
    } else if (provider === "chatgpt" && !$("chatgpt-model").value) {
      status.textContent = S.chatGPTConnected
        ? "Choose a ChatGPT model when the list is ready." : "Connect ChatGPT to use it in this review.";
    } else {
      status.textContent = "On with " + picker.selectedOptions[0].text + ". NumCheck searches Excel before asking AI.";
    }
  }

  function appendProviderSettings(fd) {
    var id = $("set-llm").value;
    fd.append("llm", id);
    if (id === "off") return;
    fd.append("llm_model", id === "chatgpt" ? $("chatgpt-model").value : $("llm-model").value.trim());
    if (!$("llm-key-field").hidden) fd.append("llm_api_key", $("llm-key").value.trim());
    fd.append("llm_redact", $("llm-redact").value.trim());
  }

  async function refreshChatGPT(loadModels, polling) {
    var status = $("chatgpt-status"), picker = $("chatgpt-model"), button = $("chatgpt-signin");
    try {
      var state = await api("GET", "/api/chatgpt/status");
      S.chatGPTConnected = state.connected;
      paintAIReviewStatus();
      $("chatgpt-shortcut").textContent = state.connected ? "Manage ChatGPT connection" : "Connect ChatGPT";
      $("chatgpt-disconnect").hidden = !state.connected;
      $("chatgpt-disconnect").disabled = state.pending;
      button.disabled = state.pending;
      button.textContent = state.connected ? "Connect a different account" : "Continue with ChatGPT";
      status.textContent = state.pending ? "Finish authorization in your browser."
        : (state.message || (state.connected ? "Connected as " + state.account + "." : "ChatGPT is disconnected."));
      if (state.pending) {
        if (polling) setTimeout(function () { refreshChatGPT(true, true); }, 1000);
        return;
      }
      if (polling) clear($("chatgpt-recovery"));
      if (loadModels && state.connected) {
        var previous = picker.value;
        var result = await api("GET", "/api/chatgpt/models");
        clear(picker);
        result.models.forEach(function (model) { picker.appendChild(el("option", { value: model.id, text: model.label })); });
        if (result.models.some(function (model) { return model.id === previous; })) picker.value = previous;
        picker.disabled = !result.models.length;
        if (picker.disabled) picker.appendChild(el("option", { value: "", text: "No eligible models found" }));
      } else if (!state.connected) {
        clear(picker).appendChild(el("option", { value: "", text: "Connect to load models" }));
        picker.disabled = true;
      }
    } catch (err) { status.textContent = err.message; }
    finally { paintAIReviewStatus(); }
  }

  async function connectChatGPT() {
    var popup = window.open("about:blank", "_blank");
    if (popup) popup.opener = null;
    $("chatgpt-signin").disabled = true;
    try {
      var result = await api("POST", "/api/chatgpt/connect");
      if (popup && !popup.closed) popup.location.replace(result.url);
      else clear($("chatgpt-recovery")).appendChild(el("a", { href: result.url, target: "_blank",
        rel: "noopener noreferrer", text: "Open ChatGPT authorization" }));
      await refreshChatGPT(true, true);
    } catch (err) {
      if (popup) popup.close();
      $("chatgpt-status").textContent = err.message;
      $("chatgpt-signin").disabled = false;
    }
  }

  async function testProvider() {
    var button = $("test-provider"), status = $("provider-status"), fd = new FormData();
    appendProviderSettings(fd);
    button.disabled = true;
    status.textContent = "Testing with a sample label…";
    try {
      await api("POST", "/api/providers/test", fd);
      if (status.textContent === "Testing with a sample label…") status.textContent = "Connected. The provider selected the correct sample label.";
    } catch (err) {
      if (status.textContent === "Testing with a sample label…") status.textContent = err.message;
    } finally {
      button.disabled = false;
    }
  }

  // --------------------------------------------------------------------- files
  var SLOT = {
    deck: { exts: [".pptx", ".pptm"], multi: false, input: "in-deck", list: "files-deck" },
    models: { exts: [".xlsx", ".xlsm"], multi: true, input: "in-models", list: "files-models" },
    old: { exts: [".xlsx", ".xlsm"], multi: false, input: "in-old", list: "files-old" }
  };
  function maxBytes() { return ((S.meta && S.meta.limits && S.meta.limits.max_upload_mb) || 250) * 1048576; }

  function validateFile(slot, f) {
    var name = f.name.toLowerCase();
    if (!SLOT[slot].exts.some(function (e) { return name.slice(-e.length) === e; })) return "Expected " + SLOT[slot].exts.join(" or ");
    if (f.size === 0) return "File is empty";
    if (f.size > maxBytes()) return "Larger than " + Math.round(maxBytes() / 1048576) + " MB";
    return "";
  }
  function addFiles(slot, list) {
    var arr = Array.prototype.slice.call(list);
    if (!arr.length) return;
    var cfg = SLOT[slot];
    var entries = arr.map(function (f) { return { file: f, error: validateFile(slot, f) }; });
    S.files[slot] = cfg.multi ? S.files[slot].concat(entries) : [entries[0]];
    renderFiles(slot);
    clearFormError();
  }
  function renderFiles(slot) {
    var ul = clear($(SLOT[slot].list));
    S.files[slot].forEach(function (e, idx) {
      var li = el("li", { class: "file" + (e.error ? " is-invalid" : "") }, [
        el("span", { class: "file-name", text: e.file.name, title: e.file.name }),
        el("span", { class: "file-size num", text: fmtBytes(e.file.size) }),
        el("button", {
          type: "button", class: "x-btn", "aria-label": "Remove " + e.file.name,
          on: { click: function () { S.files[slot].splice(idx, 1); renderFiles(slot); } }
        }, [icon("tpl-icon-x")]),
        el("span", { class: "file-state", text: e.error ? "Invalid" : "Ready" }),
        e.error ? el("span", { class: "file-msg", text: e.error }) : null
      ]);
      ul.appendChild(li);
    });
  }
  function initDropzones() {
    Object.keys(SLOT).forEach(function (slot) {
      var zone = document.querySelector('.dz[data-upload-slot="' + slot + '"]');
      var input = $(SLOT[slot].input);
      input.addEventListener("change", function () { addFiles(slot, input.files); input.value = ""; });
      ["dragenter", "dragover"].forEach(function (ev) {
        zone.addEventListener(ev, function (e) { e.preventDefault(); zone.classList.add("is-over"); });
      });
      zone.addEventListener("dragleave", function (e) {
        if (!zone.contains(e.relatedTarget)) zone.classList.remove("is-over");
      });
      zone.addEventListener("drop", function (e) {
        e.preventDefault(); zone.classList.remove("is-over");
        if (e.dataTransfer && e.dataTransfer.files) addFiles(slot, e.dataTransfer.files);
      });
    });
    // A drop outside a zone must not navigate the page to the file.
    ["dragover", "drop"].forEach(function (ev) { window.addEventListener(ev, function (e) { e.preventDefault(); }); });
  }

  function showFormError(title, hint) {
    var box = clear($("formError"));
    box.appendChild(el("strong", { text: title }));
    if (hint) { box.appendChild(document.createElement("br")); box.appendChild(document.createTextNode(hint)); }
    box.hidden = false;
  }
  function clearFormError() { $("formError").hidden = true; }

  // ------------------------------------------------------------------- submit
  function buildReviewOptions() {
    var fd = new FormData();
    if ($("llm-auto").checked) appendProviderSettings(fd);
    else fd.append("llm", "off");
    fd.append("rounding_mode", $("set-rounding").value);
    fd.append("base_period", $("set-base").value.trim());
    var tol = $("set-tol").value.trim();
    if (tol) fd.append("tolerance", tol);
    fd.append("require_label_evidence", String($("set-require-label").checked));
    fd.append("annotate", $("set-annotate").checked ? "true" : "false");
    fd.append("ocr", $("set-ocr").checked && !$("set-ocr").disabled ? "true" : "false");
    fd.append("source_search", String($("llm-auto").checked || $("source-search-auto").checked));
    return fd;
  }
  function preflight(demo) {
    if (!demo) {
      if (!S.files.deck.length) return ["A deck is required.", "Add a .pptx file to the Deck slot."];
      if (!S.files.models.length) return ["At least one model is required.", "Add an .xlsx file to the Models slot."];
      var bad = [].concat(S.files.deck, S.files.models, S.files.old).find(function (e) { return e.error; });
      if (bad) return [bad.file.name + ": " + bad.error + ".", "Remove the file or replace it."];
    }
    if ($("llm-auto").checked && (!$("set-llm").value || $("set-llm").value === "off")) {
      $("ai-settings").open = true;
      return ["Choose an AI provider.", "Open AI provider settings or turn off AI for this review."];
    }
    if ($("llm-auto").checked && $("set-llm").value === "chatgpt" && !$("chatgpt-model").value) {
      $("ai-settings").open = true;
      return ["Choose a ChatGPT model.", "Connect your account and select a model in AI provider settings."];
    }
    var tol = $("set-tol").value.trim();
    if (tol && (isNaN(Number(tol)) || Number(tol) < 0 || Number(tol) > 0.1)) return ["Tolerance must be between 0 and 0.1.", "0.1 equals 10 percent."];
    return null;
  }
  function onSubmit(ev) {
    ev.preventDefault();
    submitReview(false);
  }
  function onDemo() {
    submitReview(true);
  }
  function submitReview(demo) {
    if (S.running) return;
    var bad = preflight(demo);
    if (bad) { showFormError(bad[0], bad[1]); $("formError").focus(); return; }
    var form = buildReviewOptions();
    if (!demo) {
      form.append("deck", S.files.deck[0].file, S.files.deck[0].file.name);
      S.files.models.forEach(function (e) { form.append("models", e.file, e.file.name); });
      if (S.files.old.length) form.append("model_old", S.files.old[0].file, S.files.old[0].file.name);
    }
    startJob(function () { return api("POST", demo ? "/api/demo" : "/api/jobs", form); }, demo);
  }

  function startJob(post, demo) {
    clearFormError();
    S.running = true;
    $("runBtn").disabled = true; $("demoBtn").disabled = true;
    showView("progress");
    $("progressTitle").textContent = demo ? "Reviewing Project Keystone" : "Reviewing your files";
    $("view").focus();
    paintProgress("validate", 0.02, demo ? "Preparing demo files" : "Uploading files");
    setStatus("running", "Running");
    post().then(function (j) {
      S.jobId = j.job_id;
      poll();
    }).catch(function (err) { failRun(err); });
  }

  function failRun(err) {
    S.running = false;
    $("runBtn").disabled = false; $("demoBtn").disabled = false;
    setStatus("error", "Failed");
    showView("empty");
    showFormError(err.message || "The review failed.", err.hint || (err.file ? "File: " + err.file : ""));
    $("formError").focus();
  }

  function poll() {
    clearTimeout(S.polling);
    api("GET", "/api/jobs/" + S.jobId).then(function (st) {
      if (st.status === "error") {
        var e = st.error || {};
        failRun(Object.assign(new Error(e.message || st.message || "The review failed."), { hint: e.hint, file: e.file }));
        return;
      }
      if (st.status === "done") { loadResult(); return; }
      paintProgress(st.stage, st.progress, st.message);
      S.polling = setTimeout(poll, POLL_MS);
    }).catch(function (err) {
      if (err.status === 404) failRun(new Error("The job expired or was removed.", ""));
      else S.polling = setTimeout(poll, POLL_MS * 2);
    });
  }

  function paintProgress(stage, fraction, message) {
    $("progressBar").style.setProperty("--p", Math.round((fraction || 0) * 100) + "%");
    var idx = -1;
    STAGES.forEach(function (s, i) { if (s[0] === stage) idx = i; });
    var ol = clear($("stageList"));
    STAGES.forEach(function (s, i) {
      var cls = i < idx ? "is-done" : (i === idx ? "is-active" : "");
      ol.appendChild(el("li", { class: cls, "aria-current": i === idx ? "step" : null }, [
        el("span", { class: "mark", "aria-hidden": "true" }),
        el("span", { text: s[1] }),
        i === idx && message ? el("span", { class: "msg", text: message }) : null
      ]));
    });
    setStatus("running", (idx >= 0 ? STAGES[idx][1] : "Running") + " · " + Math.round((fraction || 0) * 100) + "%");
  }

  function loadResult() {
    api("GET", "/api/jobs/" + S.jobId + "/result").then(function (data) {
      S.running = false;
      $("runBtn").disabled = false; $("demoBtn").disabled = false;
      setResult(data);
    }).catch(function (err) { failRun(err); });
  }

  // ------------------------------------------------------------------- views
  function showView(name) {
    $("layout").dataset.view = name;
    $("setupIntro").hidden = name !== "empty";
    $("setupPanel").hidden = name !== "empty";
    $("emptyView").hidden = name !== "empty";
    $("progressView").hidden = name !== "progress";
    $("resultsView").hidden = name !== "results";
    $("fatalView").hidden = true;
  }

  // ----------------------------------------------------------------- results
  function reviewKey() { return "numcheck.review." + S.jobId; }
  function loadReviews() {
    S.reviews = {};
    var raw = store("get", reviewKey());
    if (raw) { try { S.reviews = JSON.parse(raw) || {}; } catch (e) { S.reviews = {}; } }
  }
  function saveReviews() { store("set", reviewKey(), JSON.stringify(S.reviews)); }
  function reviewOf(id) { return S.reviews[id] || { status: "open", note: "" }; }

  function setResult(data, preserve) {
    S.data = data;
    S.issues = (data.issues || []).slice();
    S.matches = data.matches || [];
    S.matchByUid = new Map(S.matches.map(function (match) { return [match.deck_uid, match]; }));
    if (!preserve) {
      S.sev = {}; S.type = {}; S.search = ""; S.slide = ""; S.reviewFilter = "";
      S.covStatus = {}; S.covSearch = ""; S.covLimit = COV_PAGE;
      S.selectedId = null; S.tab = "issues";
      $("issueSearch").value = ""; $("covSearch").value = ""; $("statusFilter").value = "";
      loadReviews();
    }
    showView("results");
    renderSummary();
    renderWarnings();
    buildIssueControls();
    renderIssueHead();
    renderIssues();
    renderSlides();
    buildCoverageControls();
    renderCoverage();
    selectTab(S.tab, false);
    setStatus("done", "Review complete");
    renderSourceReview();
    if (preserve) {
      if (S.drawerId !== null) {
        if (issueById(S.drawerId)) fillDrawer(S.drawerId);
        else closeDrawer();
      }
      return;
    }
    $("view").focus();
    var job = S.jobId, first = S.visible[0];
    if (first && S.meta && S.meta.slide_preview) {
      setTimeout(function () {
        if (S.jobId === job && !$("resultsView").hidden && !$("evidenceDialog").open) {
          api("GET", "/api/jobs/" + job + "/evidence/slide?issue=" + first.id).catch(function () {});
        }
      }, 150);
    }
  }

  function renderSourceReview(message) {
    var cov = S.data.coverage || {}, summary = S.data.source_review || {};
    var remaining = (cov.unmatched || 0) + (cov.ambiguous || 0) + (cov.matched_value_only || 0);
    $("sourceReview").hidden = !remaining && !summary.searched;
    $("findAllSources").disabled = S.sourceBusy || !remaining || !!summary.searched;
    $("findAllSources").textContent = summary.searched ? "Local source search complete" : "Find possible sources for all";
    $("askAllSources").disabled = S.sourceBusy || !remaining;
    $("runAISourceReview").disabled = S.sourceBusy || !remaining;
    $("newReviewBtn").disabled = S.sourceBusy;
    var counts = summary.searched === undefined ? "" :
      "Searched " + summary.searched + " · " + summary.with_candidates + " with possible sources · " +
      summary.verified + " newly verified" +
      (summary.ai_requested ? (summary.ai_reviewed ? " · AI checked " + summary.ai_reviewed :
        " · No remaining cells eligible for AI") : "") +
      (summary.suggested ? " · " + plural(summary.suggested, "AI suggestion") : "") + ". ";
    $("sourceReviewStatus").textContent = message || (counts + plural(remaining, "unresolved figure") + ".");
  }

  function showAIProvider(error) {
    var dialog = $("aiSourceDialog"), settings = $("ai-settings");
    $("aiSourceError").textContent = error || "";
    if (!dialog.open) {
      $("aiProviderMount").appendChild(settings);
      settings.open = true;
      dialog.showModal();
      $("set-llm").focus();
    }
  }

  async function askAllSources() {
    if (S.sourceBusy) return;
    if ($("set-llm").value === "off" && S.chatGPTConnected) {
      S.sourceBusy = true;
      renderSourceReview("Loading models from your connected ChatGPT account…");
      $("set-llm").value = "chatgpt";
      await paintLlmNote();
      S.sourceBusy = false;
      renderSourceReview();
    }
    var provider = $("set-llm").value;
    if (!provider || provider === "off" || (provider === "chatgpt" && !$("chatgpt-model").value)) {
      showAIProvider($("aiSourceDialog").open ? "Choose a provider and model before asking AI." : "");
      return;
    }
    runSourceReview(true);
  }

  async function runSourceReview(useAI) {
    if (S.sourceBusy) return;
    var base = "/api/jobs/" + S.jobId, route = base + "/evidence/source-review";
    var form = new FormData();
    if (useAI) appendProviderSettings(form);
    var dialog = $("aiSourceDialog");
    S.sourceBusy = true;
    renderSourceReview(useAI ? "Searching the workbooks and asking AI to compare candidate labels…" :
      "Searching the current Excel files for every unresolved figure…");
    var message = "";
    try {
      try {
        await api("POST", route, form);
      } catch (err) {
        if (err.status !== 409) throw err;
      }
      if (dialog.open) dialog.close();
      while (true) {
        var progress = await api("GET", route);
        if (progress.status === "done") {
          setResult(await api("GET", base + "/result"), true);
          if (progress.warning) message = $("sourceReviewStatus").textContent + " " + progress.warning;
          break;
        }
        if (progress.status === "error" || !progress.status) {
          throw new Error(progress.message || "The source review could not start.");
        }
        await new Promise(function (resolve) { setTimeout(resolve, POLL_MS); });
      }
    } catch (err) {
      message = err.message;
      if (useAI && err.status === 422) showAIProvider(err.message);
    } finally {
      S.sourceBusy = false;
      renderSourceReview(message);
    }
  }

  function renderSummary() {
    var d = S.data, issues = S.issues, cov = d.coverage || {};
    var counts = { high: 0, medium: 0, low: 0 };
    issues.forEach(function (i) { counts[i.severity] = (counts[i.severity] || 0) + 1; });
    var v = $("verdict");
    if (!issues.length) {
      v.textContent = "No issues found";
      v.className = "verdict is-clean";
    } else {
      v.textContent = plural(issues.length, "finding");
      v.className = "verdict";
    }
    var inp = d.inputs || {};
    var meta = [inp.deck || "Deck"];
    if ((inp.models || []).length) meta.push("Current: " + inp.models.join(", "));
    if (inp.model_old) meta.push("Prior: " + inp.model_old);
    var metaHost = clear($("runMeta"));
    meta.forEach(function (name) { metaHost.appendChild(el("span", { text: name })); });
    renderDownloads("downloads", d.downloads || []);
    $("originalsGroup").hidden = !(d.originals || []).length;
    renderDownloads("originals", d.originals || []);

    var k = clear($("kpis"));
    var split = el("div", { class: "sev-split" }, SEV_KEYS.map(function (s) {
      return el("span", null, [el("b", { text: counts[s] }), s.charAt(0).toUpperCase() + s.slice(1)]);
    }));
    k.appendChild(el("div", { class: "kpi" }, [el("div", { class: "kpi-label", text: "Severity" }), split]));

    var vp = typeof cov.verified_pct === "number" ? cov.verified_pct : null;
    var bar = el("div", { class: "bar", role: "img", "aria-label": vp === null ? "Coverage unknown" : "Coverage " + pct(vp) });
    var fill = el("span");
    fill.style.setProperty("--p", (vp === null ? 0 : Math.min(vp, 100)) + "%");
    bar.appendChild(fill);
    k.appendChild(el("div", { class: "kpi" }, [el("div", { class: "kpi-label", text: "Verified" }),
      el("div", { class: "kpi-value", text: pct(vp) }), bar,
      el("div", { class: "kpi-sub", text: (cov.verified || 0) + " of " + (cov.checkable || 0) + " numbers" + (cov.matched_value_only ? " · " + cov.matched_value_only + " value only" : "") })]));

    k.appendChild(el("div", { class: "kpi" }, [el("div", { class: "kpi-label", text: "Figures reviewed" }),
      el("div", { class: "kpi-value", text: cov.checkable !== undefined ? cov.checkable : (d.deck_numbers || 0) }),
      el("div", { class: "kpi-sub", text: (d.model_values || 0).toLocaleString("en-US") + " model values read" })]));

    var el_s = typeof d.elapsed === "number" ? d.elapsed : 0;
    k.appendChild(el("div", { class: "kpi" }, [el("div", { class: "kpi-label", text: "Run time" }),
      el("div", { class: "kpi-value", text: el_s < 10 ? el_s.toFixed(1) + " s" : Math.round(el_s) + " s" }),
      el("div", { class: "kpi-sub", text: plural((d.slides || []).length, "slide") })]));
  }

  function renderDownloads(id, files) {
    var host = clear($(id));
    files.forEach(function (file) {
      host.appendChild(el("a", { class: "btn small", href: file.url, download: "", title: file.name },
        [icon("tpl-icon-dl"), file.label]));
    });
  }

  function warningNode(w) {
    var where = [];
    if (w.file) where.push(w.file);
    if (w.sheet) where.push(w.sheet);
    if (w.slide_number) where.push("slide " + w.slide_number);
    if (w.count) where.push(w.count + " affected");
    return el("div", { class: "warn" }, [icon("tpl-icon-warn"), el("div", null, [
      el("div", null, [w.message || ""]),
      w.hint ? el("div", { class: "muted", text: w.hint }) : null,
      el("div", { class: "warn-code", text: [w.code].concat(where).filter(Boolean).join(" · ") })
    ])]);
  }
  function renderWarnings() {
    var ws = S.data.warnings || [];
    var top = clear($("warnList"));
    ws.slice(0, 3).forEach(function (w) { top.appendChild(warningNode(w)); });
    if (ws.length > 3) {
      top.appendChild(el("button", { type: "button", class: "linkish", text: "Show all " + ws.length + " warnings",
        on: { click: function () { selectTab("warnings", true); } } }));
    }
    var full = clear($("warnFull"));
    if (!ws.length) full.appendChild(el("p", { class: "table-empty", text: "No warnings. All inputs were read in full." }));
    ws.forEach(function (w) { full.appendChild(warningNode(w)); });
    $("cnt-warnings").textContent = ws.length ? String(ws.length) : "";
  }

  // -------------------------------------------------------------------- tabs
  function selectTab(name, focus) {
    S.tab = name;
    ["issues", "slides", "coverage", "warnings"].forEach(function (t) {
      var on = t === name;
      var tab = $("tab-" + t);
      tab.setAttribute("aria-selected", on ? "true" : "false");
      tab.tabIndex = on ? 0 : -1;
      $("pane-" + t).hidden = !on;
      if (on && focus) tab.focus();
    });
  }
  function initTabs() {
    var names = ["issues", "slides", "coverage", "warnings"];
    names.forEach(function (n, idx) {
      var tab = $("tab-" + n);
      tab.addEventListener("click", function () { selectTab(n, false); });
      tab.addEventListener("keydown", function (e) {
        var to = null;
        if (e.key === "ArrowRight") to = names[(idx + 1) % names.length];
        else if (e.key === "ArrowLeft") to = names[(idx + names.length - 1) % names.length];
        else if (e.key === "Home") to = names[0];
        else if (e.key === "End") to = names[names.length - 1];
        if (to) { e.preventDefault(); selectTab(to, true); }
      });
    });
  }

  // ------------------------------------------------------------ issue controls
  function chip(label, count, pressed, onClick, kbd) {
    return el("button", { type: "button", class: "chip", "aria-pressed": pressed ? "true" : "false", on: { click: onClick } },
      [label, el("span", { class: "n", text: count }), kbd ? el("kbd", { text: kbd }) : null]);
  }
  function buildIssueControls() {
    var counts = {}, types = {};
    S.issues.forEach(function (i) {
      counts[i.severity] = (counts[i.severity] || 0) + 1;
      types[i.issue_type] = (types[i.issue_type] || 0) + 1;
    });
    var sc = clear($("sevChips"));
    SEV_KEYS.forEach(function (s, idx) {
      sc.appendChild(chip(s.charAt(0).toUpperCase() + s.slice(1), counts[s] || 0, !!S.sev[s], function () { toggleSev(s); }, String(idx + 1)));
    });
    var tc = clear($("typeChips"));
    Object.keys(S.type).forEach(function (type) { if (!types[type]) delete S.type[type]; });
    Object.keys(types).sort(function (a, b) { return types[b] - types[a]; }).forEach(function (t) {
      tc.appendChild(chip(typeLabel(t), types[t], !!S.type[t], function () {
        if (S.type[t]) delete S.type[t]; else S.type[t] = true;
        buildIssueControls(); renderIssues();
      }));
    });
    var sf = $("slideFilter"), cur = S.slide;
    clear(sf).appendChild(el("option", { value: "", text: "All slides" }));
    var slides = {};
    S.issues.forEach(function (i) { slides[i.slide_number] = true; });
    var titles = {};
    (S.data.slides || []).forEach(function (s) { titles[s.slide_number] = s.title; });
    Object.keys(slides).map(Number).sort(function (a, b) { return a - b; }).forEach(function (n) {
      sf.appendChild(el("option", { value: String(n), text: "Slide " + n + (titles[n] ? " · " + titles[n] : "") }));
    });
    if (cur && !slides[cur]) S.slide = cur = "";
    sf.value = cur;
  }
  function toggleSev(s) {
    if (S.sev[s]) delete S.sev[s]; else S.sev[s] = true;
    buildIssueControls(); renderIssues();
  }

  function issueValue(i, key) {
    switch (key) {
      case "sev": return SEV_ORDER[i.severity];
      case "slide": return i.slide_number;
      case "type": return typeLabel(i.issue_type).toLowerCase();
      case "metric": return (i.deck_label || i.metric_key || "").toLowerCase();
      case "deck": return i.deck_value || "";
      case "expected": return i.expected_value || "";
      case "delta": return i.difference === null || i.difference === undefined ? -1 : Math.abs(i.difference);
      case "source": return (i.source_cell || "").toLowerCase();
      case "conf": return i.confidence === null || i.confidence === undefined ? -1 : i.confidence;
    }
    return 0;
  }
  function filteredIssues() {
    var q = S.search.trim().toLowerCase();
    var sevOn = Object.keys(S.sev).length, typeOn = Object.keys(S.type).length;
    var list = S.issues.filter(function (i) {
      if (sevOn && !S.sev[i.severity]) return false;
      if (typeOn && !S.type[i.issue_type]) return false;
      if (S.slide && String(i.slide_number) !== S.slide) return false;
      if (S.reviewFilter && reviewOf(i.id).status !== S.reviewFilter) return false;
      if (q) {
        var hay = [i.message, i.deck_label, i.model_label, i.metric_key, i.deck_value, i.expected_value, i.source_cell,
          typeLabel(i.issue_type), i.suggestion].join(" ").toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });
    var k = S.sort.key, dir = S.sort.dir;
    list.sort(function (a, b) {
      var x = issueValue(a, k), y = issueValue(b, k);
      var c = x < y ? -1 : (x > y ? 1 : 0);
      if (c === 0) c = a.slide_number - b.slide_number || a.id - b.id;
      return c * dir;
    });
    return list;
  }

  function renderIssueHead() {
    var tr = clear($("issueHead"));
    COLUMNS.forEach(function (c) {
      if (!c[0]) {
        tr.appendChild(el("th", { scope: "col", class: c[2], text: c[1] }));
        return;
      }
      var active = S.sort.key === c[0];
      var th = el("th", { role: "columnheader", scope: "col", class: c[2], "aria-sort": active ? (S.sort.dir === 1 ? "ascending" : "descending") : "none" }, [
        el("button", { type: "button", class: "th-btn", on: { click: function () {
          if (S.sort.key === c[0]) S.sort.dir = -S.sort.dir; else S.sort = { key: c[0], dir: 1 };
          renderIssueHead(); renderIssues();
        } } }, [c[1], icon("tpl-icon-arrow")])
      ]);
      tr.appendChild(th);
    });
  }

  function renderIssues() {
    var list = S.visible = filteredIssues();
    var body = clear($("issueBody"));
    $("cnt-issues").textContent = S.issues.length ? String(S.issues.length) : "";
    $("issueCount").textContent = list.length === S.issues.length ? plural(list.length, "issue")
      : list.length + " of " + plural(S.issues.length, "issue");
    if (!list.length) {
      var msg = S.issues.length ? "No issues match the current filters." : "No findings were flagged. See Coverage for verification status.";
      body.appendChild(el("tr", { role: "row" }, [el("td", { role: "cell", colspan: COLUMNS.length, class: "table-empty", text: msg })]));
      return;
    }
    list.forEach(function (i) {
      var rv = reviewOf(i.id);
      var reviewed = rv.status !== "open";
      var match = S.matchByUid.get(i.deck_uid);
      var tr = el("tr", { "data-id": i.id, role: "row", class: (i.id === S.selectedId ? "is-selected " : "") + (reviewed ? "is-reviewed" : ""),
        on: { click: function () { S.selectedId = i.id; openDrawer(i.id); } } }, [
        el("td", { role: "cell" }, [sevBadge(i.severity)]),
        el("td", { role: "cell", class: "r num issue-slide", text: i.slide_number }),
        el("td", { role: "cell", class: "issue-type", text: typeLabel(i.issue_type) }),
        el("td", { role: "cell", class: "metric clip" }, [
          el("button", { type: "button", class: "linkish", text: i.deck_label || i.metric_key || "(unlabelled)",
            "aria-label": "Open issue " + i.id + ": " + (i.deck_label || i.metric_key || typeLabel(i.issue_type)),
            on: { click: function (e) { e.stopPropagation(); S.selectedId = i.id; openDrawer(i.id); } } }),
          reviewed ? el("span", { class: "review-tag", text: REVIEW_LABELS[rv.status] }) : null
        ]),
        el("td", { role: "cell", class: "r mono issue-deck", "data-caption": "In the deck" },
          [i.deck_value ? evidenceButton(i.deck_value, i.deck_value + " on slide " + i.slide_number, { issue: i.id }) : "—"]),
        el("td", { role: "cell", class: "r mono issue-expected", "data-caption": "Expected" },
          [i.expected_value && i.source_cell ? evidenceButton(i.expected_value, i.source_cell, { issue: i.id }) : (i.expected_value || "—")]),
        el("td", { role: "cell", class: "issue-inspect" },
          [evidenceButton("Inspect", "issue " + i.id + " sources", { issue: i.id }, true)]),
        el("td", { role: "cell", class: "source-cell", title: i.source_cell }, [
          i.source_cell ? el("span", { class: "mono", text: i.source_cell }) : match && match.best_source ? null : "—",
          match && sourceDiscoveryLabel(match)
        ])
      ]);
      body.appendChild(tr);
    });
  }

  function moveSelection(delta) {
    if (!S.visible.length) return;
    var idx = -1;
    S.visible.forEach(function (i, n) { if (i.id === S.selectedId) idx = n; });
    idx = idx < 0 ? (delta > 0 ? 0 : S.visible.length - 1) : Math.max(0, Math.min(S.visible.length - 1, idx + delta));
    S.selectedId = S.visible[idx].id;
    if (S.drawerId !== null) { fillDrawer(S.selectedId); }
    renderIssues();
    var row = document.querySelector('#issueBody tr[data-id="' + S.selectedId + '"]');
    if (row && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
  }

  // ---------------------------------------------------------------- by slide
  function renderSlides() {
    var host = clear($("slideList"));
    var per = {};
    ((S.data.coverage || {}).per_slide || []).forEach(function (p) { per[p.slide_number] = p; });
    var by = {};
    S.issues.forEach(function (i) { (by[i.slide_number] = by[i.slide_number] || []).push(i); });
    var slides = (S.data.slides || []).slice();
    var known = {};
    slides.forEach(function (s) { known[s.slide_number] = true; });
    Object.keys(by).forEach(function (n) { if (!known[n]) slides.push({ slide_number: Number(n), title: "" }); });
    slides.sort(function (a, b) { return a.slide_number - b.slide_number; });
    $("cnt-slides").textContent = slides.length ? String(slides.length) : "";
    if (!slides.length) { host.appendChild(el("p", { class: "table-empty", text: "No slide information in this result." })); return; }
    slides.forEach(function (s) {
      var items = (by[s.slide_number] || []).slice().sort(function (a, b) { return SEV_ORDER[a.severity] - SEV_ORDER[b.severity] || a.id - b.id; });
      var p = per[s.slide_number] || {};
      var stats = [];
      if (p.checkable !== undefined) stats.push(p.verified + " of " + p.checkable + " verified");
      stats.push(items.length ? plural(items.length, "issue") : "clean");
      var block = el("section", { class: "slide-block" + (items.length ? "" : " is-clean") }, [
        el("div", { class: "slide-head" }, [
          el("h3", { text: "Slide " + s.slide_number }),
          el("span", { class: s.title ? "" : "muted", text: s.title || "Untitled" }),
          s.hidden ? el("span", { class: "muted", text: "(hidden)" }) : null,
          el("span", { class: "stats", text: stats.join(" · ") })
        ]),
        items.length ? el("ul", { class: "slide-issues" }, items.map(function (i) {
          return el("li", null, [
            sevBadge(i.severity),
            el("span", { class: "muted", text: typeLabel(i.issue_type) }),
            el("button", { type: "button", class: "linkish", text: (i.deck_label ? i.deck_label + ": " : "") + i.message,
              on: { click: function () { S.selectedId = i.id; openDrawer(i.id); } } })
          ]);
        })) : null
      ]);
      host.appendChild(block);
    });
  }

  // ---------------------------------------------------------------- coverage
  function buildCoverageControls() {
    var counts = {};
    S.matches.forEach(function (m) { counts[m.status] = (counts[m.status] || 0) + 1; });
    $("cnt-coverage").textContent = S.matches.length ? String(S.matches.length) : "";
    var host = clear($("statusChips"));
    Object.keys(S.covStatus).forEach(function (status) { if (!counts[status]) delete S.covStatus[status]; });
    ["matched", "matched_value_only", "mismatch", "stale", "ambiguous", "unmatched"].forEach(function (st) {
      if (!counts[st]) return;
      host.appendChild(chip(STATUS_LABELS[st], counts[st], !!S.covStatus[st], function () {
        if (S.covStatus[st]) delete S.covStatus[st]; else S.covStatus[st] = true;
        S.covLimit = COV_PAGE; buildCoverageControls(); renderCoverage();
      }));
    });
  }
  function renderCoverage() {
    var on = Object.keys(S.covStatus).length, q = S.covSearch.trim().toLowerCase();
    var rows = S.matches.filter(function (m) {
      if (on && !S.covStatus[m.status]) return false;
      if (q && [m.raw_text, m.label, m.source_cell, m.best_source, m.model_value].join(" ").toLowerCase().indexOf(q) < 0) return false;
      return m.status !== "skipped";
    });
    var body = clear($("covBody"));
    rows.slice(0, S.covLimit).forEach(function (m) {
      body.appendChild(el("tr", null, [
        el("td", { class: "r num", text: m.slide_number }),
        el("td", { class: "r mono" }, [m.deck_uid ? evidenceButton(m.raw_text,
          m.raw_text + " on slide " + m.slide_number, { figure: m.deck_uid }) : m.raw_text]),
        el("td", null, [m.deck_uid ? evidenceButton("Inspect", m.raw_text + " on slide " + m.slide_number,
          { figure: m.deck_uid }, true) : "—"]),
        el("td", { class: "clip", text: m.label, title: m.label }),
        el("td", null, [statusPill(m.status)]),
        el("td", { class: "clip", title: m.source_cell }, [
          el("span", { class: "mono", text: m.source_cell }), sourceDiscoveryLabel(m)
        ]),
        el("td", { class: "r mono", text: m.model_value }),
        el("td", { text: (m.method || "").replace(/_/g, " ") }),
        el("td", { class: "r mono", text: m.confidence === null || m.confidence === undefined ? "" : Math.round(m.confidence * 100) + "%" })
      ]));
    });
    if (!rows.length) body.appendChild(el("tr", null, [el("td", { colspan: 9, class: "table-empty", text: "No numbers match." })]));
    $("covCount").textContent = rows.length === S.matches.length ? plural(rows.length, "number") : rows.length + " of " + plural(S.matches.length, "number");
    var more = $("covMore");
    more.hidden = rows.length <= S.covLimit;
    more.textContent = "Show " + Math.min(COV_PAGE, rows.length - S.covLimit) + " more";
    $("covNote").textContent = S.data.matches_truncated
      ? "Showing the first " + S.matches.length + " of " + S.data.matches_total + " numbers. The Excel report lists all of them." : "";
  }

  function sourceDiscoveryLabel(match) {
    if (["unmatched", "ambiguous", "matched_value_only"].indexOf(match.status) < 0) return null;
    if (!match.best_source) return null;
    var alternatives = Math.max(0, match.possible_source_count - 1);
    return el("span", { class: "source-candidate", title: match.best_source }, [
      el("span", { class: "mono", text: match.best_source.replace(/^\[[^\]]*\]\s*/, "") }),
      el("span", { class: "note source-candidate", text: (match.source_suggestion ? "AI-selected source" : "Best candidate") +
        (alternatives ? " · " + plural(alternatives, "alternative") : "") })
    ]);
  }

  // -------------------------------------------------------------- source preview
  var previewSequence = 0;

  function evidenceButton(text, label, params, prominent) {
    return el("button", { type: "button", class: prominent ? "btn small inspect-btn" : "linkish", text: text,
      "aria-label": (prominent ? "Inspect " : "Preview ") + label, on: { click: function (event) {
        event.stopPropagation();
        openEvidence(params, label);
      } } });
  }

  function originalLink(path, text) {
    return path ? el("a", { class: "btn small", href: path, text: text }) : null;
  }

  function paintWorkbook(host, data, loadCell, issueId) {
    clear(host);
    var selected = (data.sources || []).find(function (source) { return source.selected; });
    if (data.sources && (data.sources.length > 1 || (data.sources.length && !selected))) {
      var label = el("label", { class: "evidence-picker", text: "Source cell" });
      var select = el("select", { on: { change: function () { if (select.value) loadCell(select.value); } } });
      select.appendChild(el("option", { value: "", text: "Choose a source", disabled: true }));
      var statusLabels = { reconciles: "Value reconciles", inferred: "Matches with inferred units",
        differs: "Value differs", unknown: "Value unconfirmed" };
      data.sources.forEach(function (source) {
        select.appendChild(el("option", { value: source.ref,
          text: (source.prior ? "Prior model · " : source.ref === data.ai_source ? "AI-selected · " :
            source.ref === data.source_suggestion ? "AI suggestion · " :
            (source.comparison ? statusLabels[source.comparison] + " · " : "")) +
            (source.label || source.ref) + (source.display_value ? " · " + source.display_value : "") +
            (source.possible && source.metric ? " · " + source.metric : "") }));
      });
      select.value = selected ? selected.ref : "";
      label.appendChild(select);
      host.appendChild(label);
    }
    if (data.rows) {
      host.appendChild(el("p", { class: "evidence-reference" }, [
        data.file + " · ", el("strong", { text: data.sheet + "!" + data.address })
      ]));
      var context = [data.row_label, data.col_label].filter(Boolean).join(" · ");
      if (context) host.appendChild(el("p", { class: "note", text: context }));
      var w = data.window, currentRef = selected && selected.ref;
      function moveButton(text, action, row, col, disabled) {
        return el("button", { type: "button", class: "btn small", "data-move": action, text: text,
          disabled: disabled, on: { click: function () { loadCell(currentRef, row, col); } } });
      }
      host.appendChild(el("nav", { class: "sheet-navigation", "aria-label": "Worksheet navigation" }, [
        moveButton("Up", "up", Math.max(1, w.top - 12), w.left, w.top === 1),
        moveButton("Down", "down", Math.min(w.top + 12, w.max_row - (w.bottom - w.top)), w.left, w.bottom >= w.max_row),
        moveButton("Left", "left", w.top, Math.max(1, w.left - 7), w.left === 1),
        moveButton("Right", "right", w.top, Math.min(w.left + 7, w.max_col - (w.right - w.left)), w.right >= w.max_col),
        moveButton("Back to " + data.address, "source", undefined, undefined, false)
      ]));
      host.appendChild(el("p", { class: "note sheet-coordinates",
        text: "Rows " + w.top + "–" + w.bottom + " · Columns " + w.first + "–" + w.last }));

      function gridRow(row, header) {
        return el("tr", null, [el("th", { scope: "row", class: "sheet-rownum", text: row.row })].concat(row.cells.map(function (cell, index) {
          var current = cell.address === data.address, pinned = index === 0 && data.label_column === data.columns[0];
          return el(header ? "th" : "td", { scope: header ? "col" : null,
            class: (cell.numeric ? "is-number " : "") + (cell.bold ? "is-bold " : "") +
              (pinned ? "sheet-label " : "") + (current ? "is-source" : ""),
            "aria-label": current ? "Highlighted source " + cell.address + ": " + (cell.text || "empty") : null,
            text: cell.text, title: cell.address + (cell.text ? " · " + cell.text : "") });
        })));
      }
      var grid = el("table", { class: "evidence-grid", "aria-label": "Cells around " + data.address });
      var headings = el("thead", null, [el("tr", null, [el("th", { class: "sheet-rownum", text: "#" })].concat(data.columns.map(function (column, index) {
        return el("th", { scope: "col", class: index === 0 && column === data.label_column ? "sheet-label" : "", text: column });
      })))]);
      if (data.header) headings.appendChild(gridRow(data.header, true));
      grid.appendChild(headings);
      grid.appendChild(el("tbody", null, data.rows.filter(function (row) {
        return !data.header || row.row !== data.header.row;
      }).map(function (row) { return gridRow(row, false); })));
      var gridHost = el("div", { class: "evidence-scroll", tabindex: "0", role: "region", "aria-label": "Workbook grid" }, [grid]);
      host.appendChild(gridHost);
      if (data.candidate_check) host.appendChild(el("p", { class: "note", text: data.candidate_check +
        " Confirm the metric and period before recording this candidate." }));
      if (data.formula) {
        host.appendChild(el("p", { class: "evidence-formula" }, [el("strong", { text: "Formula" }),
          el("code", { text: data.formula })]));
        if (!data.saved_value) host.appendChild(el("p", { class: "note", text: "This formula has no saved result in the original file." }));
      }
      if (data.hidden_sheet) host.appendChild(el("p", { class: "note", text: "This sheet is hidden in the original workbook." }));
    }
    var actions = el("div", { class: "evidence-actions source-confirmation" });
    if (data.rows && issueId !== undefined && data.slot !== "prior") {
      var confirmed = reviewOf(issueId).confirmedSource;
      var active = confirmed && confirmed.slot === data.slot && confirmed.sheet === data.sheet && confirmed.address === data.address;
      var confirm = el("button", { type: "button", class: "btn small", text: active ? "Remove source confirmation" : "Confirm source for handoff",
        on: { click: function () {
          var source = active ? null : { slot: data.slot, file: data.file, sheet: data.sheet, address: data.address };
          setReview(issueId, { confirmedSource: source }, true);
          paintWorkbook(host, data, loadCell, issueId);
          host.querySelector(".source-confirmation button").focus();
        } } });
      actions.appendChild(confirm);
      if (active) actions.appendChild(el("span", { class: "note", role: "status", text: "Source confirmed for this review." }));
    }
    if (data.download) actions.appendChild(originalLink(data.download, "Download original Excel"));
    if (data.message) host.appendChild(el("p", { class: "note", text: data.message }));
    if (actions.childElementCount) host.appendChild(actions);
    var highlighted = host.querySelector(".is-source");
    if (highlighted) {
      var gridHost = host.querySelector(".evidence-scroll");
      var gridBox = gridHost.getBoundingClientRect(), targetBox = highlighted.getBoundingClientRect();
      var pinnedLabel = gridHost.querySelector(".sheet-label");
      var frozenWidth = (pinnedLabel ? pinnedLabel.offsetWidth : 0) + gridHost.querySelector(".sheet-rownum").offsetWidth;
      if (targetBox.right > gridBox.right) gridHost.scrollLeft = targetBox.right - gridBox.right + 12;
      if (targetBox.top > gridBox.top + gridHost.clientHeight - highlighted.offsetHeight) {
        gridHost.scrollTop = Math.max(0, targetBox.top - gridBox.top - gridHost.clientHeight / 2);
      }
      if (targetBox.left < gridBox.left + frozenWidth && !highlighted.classList.contains("sheet-label")) gridHost.scrollLeft = 0;
    }

  }

  function openEvidence(params, label) {
    if (!S.jobId) return;
    var dialog = $("evidenceDialog"), run = ++previewSequence, requestIndex = 0;
    var base = "/api/jobs/" + S.jobId + "/evidence/";
    var query = new URLSearchParams(params);
    var tools = $("sourceTools"), find = $("findSources"), ask = $("askSourceAI"), discovery = $("sourceDiscoveryStatus");
    var issueId = params.issue;
    if (issueId === undefined && params.figure) {
      var finding = S.issues.find(function (issue) { return issue.deck_uid === params.figure; });
      if (finding) issueId = finding.id;
    }
    var aiConfigured = $("set-llm").value !== "off";
    tools.hidden = true;
    find.hidden = false;
    find.disabled = ask.disabled = false;
    ask.hidden = true;
    discovery.textContent = "";
    async function discover(useAI) {
      var form = new FormData();
      if (useAI) appendProviderSettings(form);
      find.disabled = ask.disabled = true;
      discovery.textContent = useAI ? "Identifying the best workbook source…" : "Searching workbook labels locally…";
      try {
        var result = await api("POST", base + "sources?" + query, form);
        if (run !== previewSequence) return;
        ask.hidden = !aiConfigured || !result.ai_eligible;
        var source = result.recorded_source;
        if (source && issueId !== undefined) {
          setReview(issueId, { aiSource: source }, S.drawerId !== issueId);
        }
        discovery.textContent = result.message || (source ?
          "AI recorded " + source.file + " · " + source.sheet + "!" + source.address + "." :
          (result.count ? "Found " + plural(result.count, "possible source") + ". Check the value and label." :
            "No plausible workbook cells were found."));
        loadCell(result.recommended);
      } catch (err) {
        if (run === previewSequence) discovery.textContent = err.message;
      } finally {
        if (run === previewSequence) find.disabled = ask.disabled = false;
      }
    }
    find.onclick = function () { discover(false); };
    ask.onclick = function () { discover(true); };
    var slide = clear($("slidePreview")), workbook = clear($("workbookPreview"));
    $("evidenceSubtitle").textContent = label;
    slide.textContent = "Rendering original slide…";
    workbook.textContent = "Reading original workbook…";
    if (!dialog.open) dialog.showModal();

    api("GET", base + "slide?" + query).then(function (data) {
      if (run !== previewSequence) return;
      clear(slide);
      if (data.image) {
        var description = "Slide " + data.slide + (data.highlight ? " · " + data.highlight + " highlighted" : " · Location unavailable");
        slide.appendChild(el("img", { class: "original-slide", src: data.image, alt: description }));
        slide.appendChild(el("p", { class: "note", text: description + " · Rendered by " + data.renderer }));
      } else {
        slide.appendChild(el("p", { class: "note", text: data.message }));
      }
      if (data.download) slide.appendChild(el("div", { class: "evidence-actions" },
        [originalLink(data.download, "Download original PowerPoint")]));
    }).catch(function (err) { if (run === previewSequence) slide.textContent = err.message; });

    function loadCell(ref, row, col) {
      var index = ++requestIndex, sourceQuery = new URLSearchParams(query);
      if (ref) sourceQuery.set("cell", ref);
      if (row !== undefined) sourceQuery.set("row", row);
      if (col !== undefined) sourceQuery.set("col", col);
      api("GET", base + "workbook?" + sourceQuery).then(function (data) {
        if (run === previewSequence && index === requestIndex) {
          var focused = workbook.contains(document.activeElement), move = document.activeElement.dataset.move;
          tools.hidden = !data.can_discover;
          if (data.can_discover && !data.source_search_done && !discovery.textContent) ask.hidden = !aiConfigured;
          if (data.can_discover && data.source_search_done && !discovery.textContent) {
            find.hidden = true;
            ask.hidden = !aiConfigured || !data.ai_eligible;
            var recorded = data.sources.find(function (source) { return source.ref === data.ai_source; });
            discovery.textContent = recorded ? "AI recorded " + recorded.label + "."
              : data.source_suggestion ? "Showing the AI-selected source."
              : data.best_source ? "Showing the best workbook candidate."
                : "No plausible workbook cells were found.";
          }
          var findingMatch = S.matchByUid.get(params.figure || (issueById(issueId) || {}).deck_uid);
          if (findingMatch && data.best_source && (findingMatch.best_source !== data.best_source ||
              findingMatch.possible_source_count !== data.possible_count ||
              (data.ai_source && findingMatch.source_suggestion !== data.ai_source))) {
            findingMatch.best_source = data.best_source;
            findingMatch.possible_source_count = data.possible_count;
            if (data.ai_source) findingMatch.source_suggestion = data.ai_source;
            renderIssues();
            renderCoverage();
          }
          paintWorkbook(workbook, data, loadCell, issueId);
          if (focused) {
            var next = move ? workbook.querySelector('[data-move="' + move + '"]:not(:disabled)') ||
              workbook.querySelector('[data-move="source"]') : workbook.querySelector("select");
            if (next) next.focus({ preventScroll: true });
          }
        }
      }).catch(function (err) {
        if (run === previewSequence && index === requestIndex) workbook.textContent = err.message;
      });
    }
    loadCell();
  }

  // ------------------------------------------------------------------ drawer
  function issueById(id) { for (var n = 0; n < S.issues.length; n++) if (S.issues[n].id === id) return S.issues[n]; return null; }

  function parseDeck(text) {
    var m = /(-?\d[\d,]*)(?:\.(\d+))?/.exec(text || "");
    if (!m) return null;
    var dec = m[2] ? m[2].length : 0;
    var v = parseFloat(m[1].replace(/,/g, "") + (m[2] ? "." + m[2] : ""));
    if (isNaN(v)) return null;
    return { value: v, decimals: dec, prefix: text.slice(0, m.index), suffix: text.slice(m.index + m[0].length) };
  }
  function precisionSentence(i) {
    var p = parseDeck(i.deck_value);
    if (!p || !i.expected_value) return "";
    var half = 0.5 * Math.pow(10, -p.decimals), d = p.decimals + 1;
    var num = function (x) { return x.toFixed(d).replace("-", "−"); };
    return "Displayed " + i.deck_value.trim() + " covers " + p.prefix.replace("-", "") + num(p.value - half) + p.suffix +
      " to " + num(p.value + half) + p.suffix + "; the model has " + i.expected_value + ".";
  }

  function kvRow(k, v, mono) {
    if (!v) return [];
    return [el("dt", { text: k }), el("dd", { class: mono ? "mono" : "", text: v })];
  }

  function fillDrawer(id) {
    var i = issueById(id);
    if (!i) return;
    S.drawerId = id;
    var rv = reviewOf(id);
    var sevHost = clear($("drawerSev"));
    sevHost.appendChild(sevBadge(i.severity));
    sevHost.appendChild(el("span", { class: "muted", text: "  #" + i.id + " · Slide " + i.slide_number + " · " + typeLabel(i.issue_type) }));
    $("drawerTitle").textContent = i.deck_label || i.metric_key || typeLabel(i.issue_type);

    var body = clear($("drawerBody"));
    var hasExpected = !!i.expected_value;
    var det = i.details || {};
    var slide = (S.data.slides || []).find(function (entry) { return entry.slide_number === i.slide_number; });
    var slideLocation = "Slide " + i.slide_number + (slide && slide.title ? " · " + slide.title : "");
    if (Number.isInteger(i.table_row) && Number.isInteger(i.table_col) && i.table_row >= 0 && i.table_col >= 0) {
      slideLocation += " · Table row " + (i.table_row + 1) + ", column " + (i.table_col + 1);
    } else if (det.deck_source === "chart") {
      slideLocation += " · Chart";
    }
    body.appendChild(el("section", null, [
      el("div", { class: "drawer-inspect" }, [
        evidenceButton("Inspect PowerPoint & Excel", "original sources for issue " + i.id, { issue: i.id }, true)
      ]),
      el("div", { class: "compare" }, [
        el("div", null, [el("div", { class: "detail-label", text: "In the deck" }), el("div", { class: "big is-deck", text: i.deck_value || "—" }),
          el("p", { class: "evidence-location", text: slideLocation })]),
        el("div", null, [el("div", { class: "detail-label", text: i.source_cell ? "Expected from Excel" : "Expected" }),
          el("div", { class: "big" + (hasExpected ? "" : " is-empty"), text: i.expected_value || "—" }),
          el("p", { class: "evidence-location" + (i.source_cell ? " mono" : ""), text: i.source_cell || "No workbook cell linked" })])
      ]),
      el("p", { class: "explain", text: i.message }),
      precisionSentence(i) ? el("p", { class: "explain muted", text: precisionSentence(i) }) : null,
      (i.difference !== null && i.difference !== undefined) ? el("p", { class: "explain muted num", text:
        "Difference " + (i.difference_text || fmtDelta(i.difference)) + (i.tolerance_text ? " against a tolerance of " + i.tolerance_text : (i.tolerance !== null && i.tolerance !== undefined ? " against a tolerance of ±" + parseFloat(Number(i.tolerance).toPrecision(3)) : "")) }) : null
    ]));

    if (i.suggestion) {
      var copy = el("button", { type: "button", class: "btn small", text: "Copy", on: { click: function () { copyText(i.suggestion, copy); } } });
      body.appendChild(el("div", { class: "suggest" }, [
        el("div", { class: "what" }, [el("div", { class: "detail-label", text: "Suggestion" }), "Change to ", el("span", { class: "mono", text: i.suggestion })]),
        copy
      ]));
    }

    var kv = [].concat(
      kvRow("Previous model cell", det.old_cell, true), kvRow("Model label", i.model_label),
      kvRow("Check", i.check ? i.check.replace(/_/g, " ") : ""), kvRow("Confidence", i.confidence === null || i.confidence === undefined ? "" : Math.round(i.confidence * 100) + "%", true),
      kvRow("Shape", i.shape_id, true)
    );
    var sourceMatch = S.matchByUid.get(i.deck_uid);
    if (!rv.aiSource && sourceMatch && sourceMatch.best_source) kv.push.apply(kv,
      kvRow(sourceMatch.source_suggestion ? "AI-selected source" : "Best candidate", sourceMatch.best_source, true));
    if (rv.aiSource) kv.push.apply(kv, kvRow("AI-selected source",
      rv.aiSource.file + " · " + rv.aiSource.sheet + "!" + rv.aiSource.address, true));
    if (rv.confirmedSource) kv.push.apply(kv, kvRow("Confirmed for handoff",
      rv.confirmedSource.file + " · " + rv.confirmedSource.sheet + "!" + rv.confirmedSource.address, true));
    if (kv.length) body.appendChild(el("section", null, [el("div", { class: "detail-label", text: "Source" }), el("dl", { class: "kv" }, kv)]));

    var ctx = det.context || det.deck_context;
    if (ctx) body.appendChild(el("section", null, [el("div", { class: "detail-label", text: "Deck context" }), el("p", { text: String(ctx) })]));

    var cands = det.candidates || [];
    if (cands.length) {
      body.appendChild(el("section", null, [el("div", { class: "detail-label", text: "Candidates" }),
        el("ul", { class: "rel" }, cands.map(function (c) { return el("li", null, [describeObj(c)]); }))]));
    }
    if ((i.related || []).length) {
      body.appendChild(el("section", null, [el("div", { class: "detail-label", text: "Related findings" }),
        el("ul", { class: "rel" }, i.related.map(function (r) { return el("li", null, [describeObj(r)]); }))]));
    }

    var ta = el("textarea", { id: "reviewNote", "aria-label": "Review note", placeholder: "Add note", rows: 3 });
    ta.value = rv.note || "";
    ta.addEventListener("input", function () { setReview(id, { note: ta.value }, true); });
    var btns = el("div", { class: "review-btns" }, [
      reviewButton(id, "resolved", "Mark resolved", rv.status),
      reviewButton(id, "false_positive", "False positive", rv.status)
    ]);
    body.appendChild(el("section", { class: "review" }, [
      el("div", { class: "detail-label", text: "Review" }),
      el("p", { class: "note", text: rv.status === "open" ? "Open" : "Status: " + REVIEW_LABELS[rv.status] }),
      btns, el("label", { class: "visually-hidden", for: "reviewNote", text: "Note" }), ta
    ]));

    var foot = clear($("drawerFoot"));
    foot.appendChild(el("button", { type: "button", class: "btn small", text: "Export review log (CSV)", on: { click: exportLog } }));
  }

  function describeObj(o) {
    if (typeof o !== "object" || o === null) return String(o);
    var parts = [];
    var slide = o.slide_number || o.slide;
    if (slide) parts.push("Slide " + slide);
    ["label", "value", "deck_value", "raw", "source_cell", "cell", "ref", "model_value", "note", "message"].forEach(function (k) {
      if (o[k] !== undefined && o[k] !== null && o[k] !== "") parts.push(String(o[k]));
    });
    if (!parts.length) parts.push(JSON.stringify(o));
    return parts.join(" · ");
  }

  function reviewButton(id, status, label, current) {
    var on = current === status;
    return el("button", { type: "button", class: "btn small", "aria-pressed": on ? "true" : "false", text: label,
      on: { click: function () { setReview(id, { status: on ? "open" : status }, false); } } });
  }
  function setReview(id, patch, keepDrawer) {
    var cur = reviewOf(id);
    S.reviews[id] = Object.assign({}, cur, patch);
    saveReviews();
    renderIssues();
    if (!keepDrawer) fillDrawer(id);
  }

  function copyText(text, btn) {
    var done = function (ok) {
      btn.textContent = ok ? "Copied" : "Copy failed";
      setTimeout(function () { btn.textContent = "Copy"; }, 1500);
    };
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(false); });
    } else {
      var t = el("textarea", { class: "visually-hidden" }); t.value = text; document.body.appendChild(t);
      t.select(); var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(t); done(ok);
    }
  }

  function openDrawer(id) {
    if (S.drawerId === null) S.lastFocus = document.activeElement;
    fillDrawer(id);
    var dr = $("drawer"), sc = $("scrim");
    sc.hidden = false; dr.hidden = false;
    window.requestAnimationFrame(function () { sc.classList.add("is-open"); dr.classList.add("is-open"); });
    renderIssues();
    $("drawerClose").focus();
  }
  function closeDrawer() {
    if (S.drawerId === null) return;
    S.drawerId = null;
    var dr = $("drawer"), sc = $("scrim");
    dr.classList.remove("is-open"); sc.classList.remove("is-open");
    setTimeout(function () { if (S.drawerId === null) { dr.hidden = true; sc.hidden = true; } }, 160);
    if (S.lastFocus && document.contains(S.lastFocus)) S.lastFocus.focus();
    else {
      var row = document.querySelector('#issueBody tr.is-selected button');
      if (row) row.focus();
    }
  }

  function trapFocus(container, e) {
    var f = Array.prototype.slice.call(container.querySelectorAll('button, [href], input, select, textarea, [tabindex]:not([tabindex="-1"])'))
      .filter(function (n) { return !n.disabled && !n.hidden && n.offsetParent !== null; });
    if (!f.length) return;
    var first = f[0], last = f[f.length - 1];
    if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
    else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    else if (!container.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  }

  // --------------------------------------------------------------- CSV export
  function csvCell(v) {
    var s = v === null || v === undefined ? "" : String(v);
    if (/^[=+@\t\r]/.test(s) || (/^-/.test(s) && !/^-[\d.,]+%?$/.test(s))) s = "'" + s;
    return '"' + s.replace(/"/g, '""') + '"';
  }
  async function exportHandoff() {
    var button = $("exportHandoff"), job = S.jobId, result = S.data;
    button.disabled = true;
    button.textContent = "Preparing handoff…";
    try {
      var originals = await api("GET", "/api/jobs/" + job + "/inputs");
      if (S.jobId !== job) return;
      var payload = {
        format: "numcheck-review-handoff", version: 1, numcheck_version: originals.version,
        exported_at: new Date().toISOString(), inputs: originals.files, settings: result.settings,
        coverage: result.coverage, warnings: result.warnings,
        findings: S.issues.map(function (issue) { return { finding: issue, review: reviewOf(issue.id) }; })
      };
      var url = URL.createObjectURL(new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" }));
      var anchor = el("a", { href: url, download: "numcheck-review-handoff.json" });
      document.body.appendChild(anchor); anchor.click(); anchor.remove();
      setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    } catch (err) {
      button.textContent = "Handoff unavailable";
      button.title = err.message;
      setTimeout(function () { button.textContent = "Export review handoff"; }, 2500);
      return;
    } finally { button.disabled = false; }
    button.textContent = "Export review handoff";
  }

  function exportLog() {
    var head = ["Id", "Slide", "Severity", "Type", "Metric", "Deck", "Expected", "Suggestion", "Source cell", "Status", "Note", "Message"];
    var lines = [head.map(csvCell).join(",")];
    S.issues.slice().sort(function (a, b) { return a.slide_number - b.slide_number || a.id - b.id; }).forEach(function (i) {
      var rv = reviewOf(i.id);
      lines.push([i.id, i.slide_number, i.severity, typeLabel(i.issue_type), i.deck_label || i.metric_key, i.deck_value,
        i.expected_value, i.suggestion, i.source_cell, rv.status === "open" ? "Open" : REVIEW_LABELS[rv.status], rv.note, i.message]
        .map(csvCell).join(","));
    });
    var blob = new Blob(["﻿" + lines.join("\r\n") + "\r\n"], { type: "text/csv;charset=utf-8" });
    var url = URL.createObjectURL(blob);
    var a = el("a", { href: url, download: "numcheck-review-log.csv" });
    document.body.appendChild(a); a.click(); document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 2000);
  }

  // -------------------------------------------------------------------- help
  function openHelp() { S.helpFocus = document.activeElement; $("helpModal").hidden = false; $("helpClose").focus(); }
  function closeHelp() {
    $("helpModal").hidden = true;
    if (S.helpFocus && document.contains(S.helpFocus)) S.helpFocus.focus();
  }

  // ---------------------------------------------------------------- keyboard
  function typing(t) { return t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA" || t.tagName === "SELECT" || t.isContentEditable); }
  function onKey(e) {
    if ($("evidenceDialog").open) return;
    if (e.key === "Tab") {
      if (!$("helpModal").hidden) trapFocus($("helpModal"), e);
      else if (S.drawerId !== null) trapFocus($("drawer"), e);
      return;
    }
    if (e.key === "Escape") {
      if (!$("helpModal").hidden) { closeHelp(); e.preventDefault(); }
      else if (S.drawerId !== null) { closeDrawer(); e.preventDefault(); }
      else if (typing(e.target) || e.target.classList.contains("tip-trigger")) e.target.blur();
      return;
    }
    if (e.ctrlKey || e.metaKey || e.altKey) return;
    if (typing(e.target)) return;
    var resultsOn = !$("resultsView").hidden && S.tab === "issues";
    switch (e.key) {
      case "?": e.preventDefault(); openHelp(); break;
      case "/": if (!$("resultsView").hidden) { e.preventDefault(); selectTab("issues", false); $("issueSearch").focus(); } break;
      case "j": if (resultsOn) { e.preventDefault(); moveSelection(1); } break;
      case "k": if (resultsOn) { e.preventDefault(); moveSelection(-1); } break;
      case "Enter":
        if (resultsOn && S.drawerId === null && S.selectedId !== null && e.target === document.body) { e.preventDefault(); openDrawer(S.selectedId); }
        break;
      case "1": case "2": case "3": if (resultsOn) { e.preventDefault(); toggleSev(SEV_KEYS[Number(e.key) - 1]); } break;
    }
  }

  // -------------------------------------------------------------------- init
  function init() {
    $("evidenceDialog").addEventListener("close", function () { previewSequence++; });
    initTheme();
    initTabs();
    initDropzones();
    loadMeta();
    $("reviewForm").addEventListener("submit", onSubmit);
    $("demoBtn").addEventListener("click", onDemo);
    $("newReviewBtn").addEventListener("click", function () {
      showView("empty");
      setStatus("idle", "Ready");
      $("in-deck").focus();
    });
    $("findAllSources").addEventListener("click", function () { runSourceReview(false); });
    $("askAllSources").addEventListener("click", askAllSources);
    $("runAISourceReview").addEventListener("click", askAllSources);
    $("aiSourceDialog").addEventListener("close", function () {
      $("setupAiProvider").appendChild($("ai-settings"));
    });
    $("llm-auto").addEventListener("change", function () {
      if ($("llm-auto").checked && $("set-llm").value === "off") {
        if (S.chatGPTConnected) {
          $("set-llm").value = "chatgpt";
          paintLlmNote();
        } else {
          $("ai-settings").open = true;
        }
      }
      paintAIReviewStatus();
    });
    $("set-llm").addEventListener("change", function () {
      if ($("set-llm").value === "off") $("llm-auto").checked = false;
      paintLlmNote();
    });
    $("chatgpt-shortcut").addEventListener("click", function () {
      $("set-llm").value = "chatgpt";
      paintLlmNote();
      $("chatgpt-signin").focus();
    });
    $("chatgpt-signin").addEventListener("click", connectChatGPT);
    $("chatgpt-disconnect").addEventListener("click", async function () {
      try {
        await api("POST", "/api/chatgpt/disconnect");
        $("provider-status").textContent = "Connection not checked.";
        await refreshChatGPT(false);
      } catch (err) { $("chatgpt-status").textContent = err.message; }
    });
    $("chatgpt-model").addEventListener("change", function () {
      $("provider-status").textContent = "Connection not checked.";
      paintAIReviewStatus();
    });
    $("test-provider").addEventListener("click", testProvider);
    $("provider-fields").addEventListener("input", function () { $("provider-status").textContent = "Connection not checked."; });
    $("issueSearch").addEventListener("input", function (e) { S.search = e.target.value; renderIssues(); });
    $("slideFilter").addEventListener("change", function (e) { S.slide = e.target.value; renderIssues(); });
    $("statusFilter").addEventListener("change", function (e) { S.reviewFilter = e.target.value; renderIssues(); });
    $("covSearch").addEventListener("input", function (e) { S.covSearch = e.target.value; S.covLimit = COV_PAGE; renderCoverage(); });
    $("covMore").addEventListener("click", function () { S.covLimit += COV_PAGE; renderCoverage(); });
    $("exportLog").addEventListener("click", exportLog);
    $("exportHandoff").addEventListener("click", exportHandoff);
    $("drawerClose").addEventListener("click", closeDrawer);
    $("scrim").addEventListener("click", closeDrawer);
    $("drawerNext").addEventListener("click", function () { moveSelection(1); });
    $("drawerPrev").addEventListener("click", function () { moveSelection(-1); });
    $("helpBtn").addEventListener("click", openHelp);
    $("helpClose").addEventListener("click", closeHelp);
    document.addEventListener("keydown", onKey);
    showView("empty");
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
  else init();
})();
