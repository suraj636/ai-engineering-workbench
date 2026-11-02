/* Telephony Agent console — dashboard, live events and browser audio transport. */
"use strict";

(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const PAGES = {
    model: {
      title: "Model config",
      subtitle: "Pick the speech, language and voice models. Changes apply to new calls.",
    },
    inbound: {
      title: "Inbound call",
      subtitle: "Point a Plivo number at this server and the agent answers every call.",
    },
    outbound: {
      title: "Outbound call",
      subtitle: "Have Plivo dial a destination and stream the conversation to the same agent.",
    },
    live: {
      title: "Live call",
      subtitle: "Conversation and detection log for the active inbound or outbound call.",
    },
    web: {
      title: "Web call",
      subtitle: "Talk to the agent from this browser. Your microphone is the caller.",
    },
    cost: {
      title: "Cost & latency",
      subtitle: "Saved calls with median latency and estimated cost per minute.",
    },
  };

  const REASON_LABELS = {
    caller_hung_up: "Caller hung up",
    browser_hung_up: "Browser hung up",
    user_requested: "Ended by you",
    max_duration: "Max call duration reached",
    idle_timeout: "Idle timeout",
    server_shutdown: "Server shutdown",
    ended: "Completed",
    connection: "Connection lost",
  };

  const STATE_LABELS = {
    INITIALIZING: ["Connecting", "thinking"],
    LISTENING: ["Listening", "listening"],
    THINKING: ["Thinking", "thinking"],
    SPEAKING: ["Speaking", "speaking"],
    INTERRUPTED: ["Interrupted", "interrupted"],
    ENDED: ["Ended", "ended"],
  };

  const PAGE_DEFAULT = "web";

  const state = {
    page: PAGE_DEFAULT,
    health: null,
    config: null,
    configOptions: null,
    pricing: null,
    stats: null,
    warmup: null,
    statsLoading: false,
    statsQueued: false,
    statsTimer: null,
    eventWs: null,
    eventConnected: false,
    eventRetry: 0,
    eventRetryTimer: null,
    callStates: new Map(),
    callDirections: new Map(),
    activeCalls: new Set(),
    webCallId: null,
    apiWarned: false,
    outboundPlacing: false,
    summary: null,
    summaryRequest: 0,
    lastCall: null,
    live: {
      callId: null,
      direction: "",
      startedAt: 0,
      duration: 0,
      fromNumber: "",
      toNumber: "",
      timer: 0,
      turns: new Map(),
      agentBubbles: new Map(),
      userBubbles: new Map(),
      eventCount: 0,
      active: false,
    },
  };

  const call = {
    starting: false,
    active: false,
    ready: false,
    ending: false,
    serverEnded: false,
    callId: null,
    ws: null,
    audioCtx: null,
    mediaStream: null,
    source: null,
    filter: null,
    captureNode: null,
    muteGain: null,
    playGain: null,
    resampler: null,
    pending: new Uint8Array(0),
    playback: new Set(),
    nextPlayTime: 0,
    micLevel: 0,
    meterRaf: 0,
    startedAt: 0,
    timer: 0,
  };

  let lastUserBubble = null;
  let lastUserTurn = null;
  let lastUserFinal = true;
  const agentBubbles = new Map();

  document.addEventListener("DOMContentLoaded", init);

  async function init() {
    buildMicBars();
    bindUi();
    initWarmup();
    const lastSummary = $("#live-last-summary");
    if (lastSummary) {
      lastSummary.addEventListener("click", () => {
        if (state.lastCall) openCallSummary(state.lastCall.callId, state.lastCall.event);
      });
    }
    syncLiveView();
    route();
    syncStartButton();

    await Promise.allSettled([loadHealth(), loadConfig(), loadPricing(), loadStats()]);
    connectEvents();

    setInterval(() => {
      if (document.visibilityState !== "visible") return;
      if (["cost", "inbound", "outbound"].includes(state.page)) loadStats();
    }, 10000);
  }

  // ------------------------------------------------------------------ helpers
  async function api(path, { method = "GET", body = null } = {}) {
    const response = await fetch(path, {
      method,
      cache: "no-store",
      headers: body ? { "Content-Type": "application/json" } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    });
    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }
    if (!response.ok) {
      const detail = data && data.detail;
      throw new Error(typeof detail === "string" ? detail : `Request failed (${response.status})`);
    }
    return data;
  }

  function wsUrl(path) {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    return `${scheme}://${location.host}${path}`;
  }

  function setText(id, value) {
    const node = document.getElementById(id);
    if (node) node.textContent = value;
  }

  function setSaveState(id, text, kind = "") {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = text;
    node.className = `save-state${kind ? ` ${kind}` : ""}`;
  }

  function makeDot(cls = "") {
    const dot = document.createElement("i");
    dot.className = `dot${cls ? ` ${cls}` : ""}`;
    return dot;
  }

  function formatNumber(value) {
    const number = Number(value) || 0;
    return number.toLocaleString("en-US");
  }

  function clock(seconds) {
    const total = Math.max(0, Math.floor(Number(seconds) || 0));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    const pad = (n) => String(n).padStart(2, "0");
    return hours ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${pad(minutes)}:${pad(secs)}`;
  }

  function formatDuration(seconds) {
    const total = Math.max(0, Math.round(Number(seconds) || 0));
    if (total < 60) return `${total}s`;
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours) return `${hours}h ${minutes}m`;
    return `${minutes}m ${secs}s`;
  }

  function formatAudio(seconds) {
    const value = Number(seconds) || 0;
    if (value < 0.05) return "0.0 s";
    if (value < 60) return `${value.toFixed(1)} s`;
    const minutes = Math.floor(value / 60);
    const secs = Math.round(value % 60);
    return `${minutes}m ${secs}s`;
  }

  function formatWhen(iso) {
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return "—";
    const now = new Date();
    const time = date.toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      hour12: false,
    });
    const sameDay =
      date.getFullYear() === now.getFullYear() &&
      date.getMonth() === now.getMonth() &&
      date.getDate() === now.getDate();
    if (sameDay) return time;
    return `${date.toLocaleDateString([], { month: "short", day: "numeric" })} ${time}`;
  }

  function money(value) {
    if (value === null || value === undefined) return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    if (number === 0) return "$0.00";
    if (number < 0.0001) return `$${number.toFixed(6)}`;
    if (number < 0.01) return `$${number.toFixed(4)}`;
    if (number < 1) return `$${number.toFixed(3)}`;
    return `$${number.toFixed(2)}`;
  }

  function moneyExact(value) {
    if (value === null || value === undefined) return "not set";
    return money(value);
  }

  function moneyInr(value) {
    if (value === null || value === undefined) return "—";
    const number = Number(value);
    if (!Number.isFinite(number)) return "—";
    if (number === 0) return "₹0.00";
    if (number < 0.01) return `₹${number.toFixed(4)}`;
    if (number < 1) return `₹${number.toFixed(3)}`;
    return `₹${number.toLocaleString("en-IN", {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2,
    })}`;
  }

  function inrFor(costs) {
    if (!costs) return null;
    if (costs.total_inr !== null && costs.total_inr !== undefined) {
      return Number(costs.total_inr);
    }
    const total = Number(costs.total);
    if (!Number.isFinite(total)) return null;
    const rate = Number(
      costs.usd_inr ||
        (state.pricing && state.pricing.rates && state.pricing.rates.usd_inr) ||
        95
    );
    return total * rate;
  }

  function msText(value) {
    if (value === null || value === undefined) return "—";
    return Math.max(0, Math.round(Number(value))).toString();
  }

  function reasonLabel(reason) {
    if (!reason) return "Completed";
    return REASON_LABELS[reason] || reason.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase());
  }

  function directionLabel(direction) {
    if (direction === "browser") return "Web";
    if (direction === "inbound") return "Inbound";
    if (direction === "outbound") return "Outbound";
    return direction || "—";
  }

  function stateMeta(key) {
    return STATE_LABELS[key] || ["Active", "listening"];
  }

  function toast(message, kind = "info", ttl = 4200) {
    const host = document.getElementById("toasts");
    if (!host) return;
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    host.appendChild(node);
    while (host.children.length > 4) host.firstElementChild.remove();
    setTimeout(() => node.remove(), ttl);
  }

  // -------------------------------------------------------------------- router
  function route() {
    const raw = (location.hash || "").replace(/^#/, "") || PAGE_DEFAULT;
    const [key, detailArg] = raw.split("/");
    const page = PAGES[key] ? key : PAGE_DEFAULT;
    // "#cost/<call_id>" is the per-call analysis page; it still counts as "cost".
    const detailId = page === "cost" && detailArg ? decodeURIComponent(detailArg) : "";
    state.page = page;

    $$(".page").forEach((section) => {
      section.hidden = section.id !== (detailId ? "page-costdetail" : `page-${page}`);
    });
    $$(".nav-item").forEach((item) => {
      item.classList.toggle("active", item.dataset.page === page);
    });

    const meta = PAGES[page];
    setText("page-title", detailId ? "Call analysis" : meta.title);
    setText(
      "page-subtitle",
      detailId ? "Stack used, cost and per-turn latency for one saved call." : meta.subtitle
    );
    document.title = `${detailId ? "Call analysis" : meta.title} · Telephony Agent`;

    if (page === "cost") {
      if (detailId) loadCallDetail(detailId);
      else loadHistory();
    }

    if (["cost", "inbound", "outbound"].includes(page)) loadStats();
    if (page === "live") syncLiveView();
  }

  // Live call page: panels while a call is active, otherwise the "No ongoing call" card.
  function syncLiveView() {
    const active = Boolean(state.live && state.live.active);
    $$("#page-live .live-panel").forEach((node) => {
      node.hidden = !active;
    });
    const idle = $("#live-idle");
    if (idle) idle.hidden = active;
    const last = $("#live-last-summary");
    if (last) last.hidden = !state.lastCall;
  }

  // ------------------------------------------------------------- health/status
  async function loadHealth() {
    try {
      state.health = await api("/api/health");
      state.warmup = state.health.warmup || state.warmup;
      state.apiWarned = false;
      renderHealthUi();
      renderChips();
      syncStartButton();
      syncOutboundAvailability();
    } catch (error) {
      state.health = null;
      renderChips();
      syncStartButton();
      if (!state.apiWarned) {
        state.apiWarned = true;
        toast(
          "Backend API unreachable. Start FastAPI and open this page from its URL.",
          "err",
          7000
        );
      }
    }
  }

  function renderHealthUi() {
    const health = state.health;
    const missing = (health && health.missing_credentials) || [];
    const banner = $("#config-warning");
    if (banner) {
      banner.hidden = missing.length === 0;
      if (missing.length) {
        setText(
          "config-warning-text",
          `Missing ${missing.join(" · ")}. Add them to .env and restart the server.`
        );
      }
    }

    const base = publicBase();
    const placeholder = "Set PUBLIC_BASE_URL in .env";
    setText("inbound-url", base ? `${base}/plivo/inbound` : placeholder);
    setText("status-url", base ? `${base}/plivo/status` : placeholder);
    setText("inbound-number", (health && health.plivo_number) || "not configured");
    setText("outbound-from", (health && health.plivo_number) || "not configured");
    setText(
      "outbound-answer",
      base ? `${base}/plivo/answer?direction=outbound` : placeholder
    );

    const inboundCode = $("#inbound-url");
    if (inboundCode) {
      inboundCode.title = base
        ? "Paste this into your Plivo application's Answer URL."
        : "PUBLIC_BASE_URL is not set, so Plivo cannot reach this server yet.";
    }
    syncSidebarIndicator();
  }

  function publicBase() {
    const value = (state.health && state.health.public_base_url) || "";
    return value.replace(/\/+$/, "");
  }

  function renderChips() {
    const host = $("#status-chips");
    if (!host) return;
    host.replaceChildren();

    host.appendChild(
      statusChip(
        state.eventConnected ? "ok" : "warn",
        state.eventConnected ? "Events live" : "Events offline",
        state.eventConnected ? "green" : "amber"
      )
    );

    const health = state.health;
    if (!health) {
      host.appendChild(statusChip("warn", "API offline", "amber"));
    }

    const active = state.stats
      ? state.stats.totals.active_calls
      : state.activeCalls.size;
    if (active) {
      host.appendChild(
        statusChip("ok", `${active} active call${active === 1 ? "" : "s"}`, "teal")
      );
    }

    const pricingReady = state.pricing
      ? state.pricing.configured
      : Boolean(health && health.pricing_configured);
    host.appendChild(
      statusChip(
        pricingReady ? "ok" : "warn",
        pricingReady ? "Rates set" : "Rates unset",
        pricingReady ? "green" : "amber"
      )
    );

    renderWarmup();
    syncSidebarIndicator();
  }

  // ------------------------------------------------------------ warm-up pill
  const WARM_PROVIDERS = [
    ["Plivo", "plivo"],
    ["AssemblyAI", "assemblyai"],
    ["Sarvam LLM", "sarvam"],
    ["Inworld TTS", "inworld"],
    ["Speechify TTS", "speechify"],
    ["Sarvam TTS", "sarvam_tts"],
  ];

  function warmProviderState(key) {
    const health = state.health;
    const warmup = state.warmup || (health && health.warmup) || null;
    const configured = Boolean(health && health.providers && health.providers[key]);
    const warm = warmup && warmup.providers ? warmup.providers[key] : null;
    let status = configured ? "ready" : "missing";
    let dot = configured ? "green" : "amber";
    if (warm && warmup.enabled) {
      if (warm.status === "warming") { status = "warming"; dot = "teal"; }
      else if (warm.status === "ready") { status = "ready"; dot = "green"; }
      else if (warm.status === "error") { status = "error"; dot = "red"; }
      else if (warm.status === "pending") { status = "queued"; dot = "amber"; }
      else if (warm.status === "skipped" && !configured) { status = "missing"; dot = "amber"; }
    }
    return { status, dot, warm };
  }

  function renderWarmup() {
    const pill = $("#warm-pill");
    const list = $("#warm-list");
    if (!pill || !list) return;
    const warmup = state.warmup || (state.health && state.health.warmup) || null;
    const rows = WARM_PROVIDERS.map(([label, key]) => ({ label, ...warmProviderState(key) }));
    const total = rows.length;
    const ready = rows.filter((r) => r.status === "ready").length;
    const errors = rows.filter((r) => r.status === "error").length;
    const busy = rows.some((r) => r.status === "warming" || r.status === "queued") ||
      Boolean(warmup && (warmup.status === "running" || warmup.status === "pending"));

    const dot = $("#warm-dot");
    let tone = "green";
    if (!state.health) tone = "amber";
    else if (errors) tone = "red";
    else if (busy || ready < total) tone = "amber";
    if (dot) dot.className = `dot ${tone}${busy ? " pulse" : ""}`;
    setText("warm-count", state.health ? `${ready}/${total}` : "");

    let summary = "Waiting for the server…";
    if (state.health) {
      if (warmup && warmup.enabled === false) summary = "Warm-up is disabled (WARMUP_ON_START=false).";
      else if (busy) summary = "Warming providers…";
      else if (errors) summary = `${errors} of ${total} failed${warmup && warmup.finished_at ? " · last run " + new Date(warmup.finished_at).toLocaleTimeString() : ""}`;
      else summary = `All ${total} ready${warmup && warmup.duration_ms ? " in " + (warmup.duration_ms / 1000).toFixed(1) + " s" : ""}`;
    }
    setText("warm-summary", summary);

    list.replaceChildren();
    rows.forEach((row, index) => {
      const item = document.createElement("li");
      item.className = `warm-row ${row.status}`;
      item.style.setProperty("--i", index);
      const dotEl = document.createElement("i");
      dotEl.className = `dot ${row.dot}`;
      const name = document.createElement("span");
      name.className = "warm-name";
      name.textContent = row.label;
      const stateEl = document.createElement("span");
      stateEl.className = "warm-state";
      const ms = row.warm && row.warm.latency_ms ? ` · ${Math.round(row.warm.latency_ms)} ms` : "";
      stateEl.textContent = row.status + (row.status === "ready" || row.status === "error" ? ms : "");
      item.append(dotEl, name, stateEl);
      if (row.warm && row.warm.detail) {
        const detail = document.createElement("span");
        detail.className = "warm-detail";
        detail.textContent = row.warm.detail;
        item.append(detail);
      }
      list.appendChild(item);
    });
    const rerun = $("#warm-rerun");
    if (rerun) rerun.disabled = busy;
  }

  // One popover behaviour for both header pills: click the pill to toggle,
  // click anywhere outside (or Esc) to close; opening one closes the other.
  const popovers = [];

  function setPopover(pop, open) {
    pop.panel.classList.toggle("open", open);
    pop.panel.setAttribute("aria-hidden", open ? "false" : "true");
    pop.pill.setAttribute("aria-expanded", open ? "true" : "false");
  }

  function bindPopover(pillId, panelId, onOpen) {
    const pill = document.getElementById(pillId);
    const panel = document.getElementById(panelId);
    if (!pill || !panel) return;
    const pop = { pill, panel };
    popovers.push(pop);
    pill.addEventListener("click", (event) => {
      event.stopPropagation();
      const open = !panel.classList.contains("open");
      popovers.forEach((other) => setPopover(other, other === pop && open));
      if (open && onOpen) onOpen();
    });
  }

  function initWarmup() {
    renderNetwork();
    bindPopover("warm-pill", "warm-panel");
    bindPopover("net-pill", "net-panel", () => {
      if (!state.network) runNetworkCheck();
    });
    document.addEventListener("click", (event) => {
      popovers.forEach((pop) => {
        if (pop.panel.classList.contains("open") && !pop.panel.contains(event.target)) setPopover(pop, false);
      });
    });
    document.addEventListener("keydown", (event) => {
      if (event.key !== "Escape") return;
      popovers.forEach((pop) => {
        if (pop.panel.classList.contains("open")) {
          setPopover(pop, false);
          pop.pill.focus();
        }
      });
    });
    const rerun = $("#warm-rerun");
    if (rerun) {
      rerun.addEventListener("click", async () => {
        rerun.disabled = true;
        try {
          const data = await api("/api/warmup", { method: "POST" });
          if (data) state.warmup = data;
          renderWarmup();
        } catch (error) {
          toast(`Could not re-run warm-up: ${error.message}`, "warn", 5000);
          rerun.disabled = false;
        }
      });
    }
    const netRun = $("#net-run");
    if (netRun) netRun.addEventListener("click", runNetworkCheck);
    // Show a score without the user having to open the panel first.
    setTimeout(() => {
      if (!state.network) runNetworkCheck();
    }, 1500);
  }

  // ----------------------------------------------------------- network check
  function renderNetwork() {
    const result = state.network;
    const dot = $("#net-dot");
    const rows = $("#net-list");
    const run = $("#net-run");
    if (run) {
      run.disabled = state.networkRunning;
      run.textContent = state.networkRunning ? "Measuring…" : result ? "Re-run" : "Run check";
    }
    if (!dot || !rows) return;
    const tone = !result ? "amber" : result.label === "Good" ? "green" : result.label === "Fair" ? "amber" : "red";
    dot.className = `dot ${tone}${state.networkRunning ? " pulse" : ""}`;
    setText("net-count", result ? `${result.score} · ${result.label}` : state.networkRunning ? "measuring…" : "");

    const advice = $("#net-advice");
    if (!result) {
      setText("net-summary", state.networkRunning ? "Measuring connections to every provider…" : "Not measured yet.");
      rows.replaceChildren();
      if (advice) advice.hidden = true;
      return;
    }
    const when = result.measured_at ? new Date(result.measured_at).toLocaleTimeString() : "";
    setText("net-summary", `Score ${result.score}/100 · ${result.label} · measured ${when}`);
    if (advice) {
      advice.hidden = false;
      advice.textContent = result.advice;
    }

    rows.replaceChildren();
    (result.params || []).forEach((param, index) => {
      const item = document.createElement("li");
      item.className = `warm-row net-row ${param.rating}`;
      item.style.setProperty("--i", index);
      const dotEl = document.createElement("i");
      dotEl.className = `dot ${param.rating === "good" ? "green" : param.rating === "fair" ? "amber" : "red"}`;
      const name = document.createElement("span");
      name.className = "warm-name";
      name.textContent = param.label;
      const value = document.createElement("span");
      value.className = "warm-state";
      value.textContent =
        (param.value === null ? "n/a" : `${param.value}${param.unit === "%" ? "%" : " " + param.unit}`) +
        ` · ${param.rating}`;
      const detail = document.createElement("span");
      detail.className = "warm-detail";
      const what = document.createElement("span");
      what.className = "net-what";
      what.textContent = param.what || "";
      const why = document.createElement("span");
      why.className = "net-why";
      why.textContent = param.why;
      detail.append(what, why);
      item.append(dotEl, name, value, detail);
      rows.appendChild(item);
    });

    const body = $("#net-hosts");
    const wrap = $("#net-hosts-wrap");
    if (body && wrap) {
      body.replaceChildren();
      const fmt = (v) => (v === null || v === undefined ? "–" : `${Math.round(v)} ms`);
      (result.hosts || []).forEach((host) => {
        const tr = document.createElement("tr");
        [host.label, fmt(host.dns_ms), fmt(host.connect_ms), fmt(host.tls_ms), `${host.failures}/${host.attempts}`].forEach(
          (text) => {
            const td = document.createElement("td");
            td.textContent = text;
            tr.appendChild(td);
          }
        );
        body.appendChild(tr);
      });
      wrap.hidden = !(result.hosts || []).length;
    }
  }

  async function runNetworkCheck() {
    if (state.networkRunning) return;
    state.networkRunning = true;
    renderNetwork();
    try {
      const data = await api("/api/network-check", { method: "POST" });
      if (data && data.score !== undefined) state.network = data;
    } catch (error) {
      toast(`Network check failed: ${error.message}`, "warn", 5000);
    } finally {
      state.networkRunning = false;
      renderNetwork();
    }
  }

  function statusChip(kind, label, dotClass) {
    const chip = document.createElement("span");
    chip.className = `chip ${kind}`;
    const dot = document.createElement("i");
    dot.className = `dot ${dotClass}`;
    const strong = document.createElement("strong");
    strong.textContent = label;
    chip.append(dot, strong);
    return chip;
  }

  function syncSidebarIndicator() {
    const dot = $("#sidebar-indicator");
    if (!dot) return;
    dot.className = "pulse-dot";
    if (state.health && state.health.missing_credentials.length) {
      dot.classList.add("warn");
    } else if (!state.eventConnected) {
      dot.classList.add("bad");
    }
  }

  function syncStartButton() {
    const button = $("#start-call");
    if (!button) return;
    const ready = !state.health || state.health.voice_pipeline_ready;
    button.disabled = call.active || call.starting || !ready;

    const hint = $("#web-hint");
    if (!hint) return;
    if (state.health && !ready) {
      const missing = state.health.missing_credentials
        .filter((item) => !item.startsWith("PLIVO"))
        .join(", ");
      hint.textContent = `Voice pipeline not ready — add ${
        missing || "provider credentials"
      } to .env and restart.`;
    } else if (call.starting) {
      hint.textContent = "Requesting microphone and connecting…";
    } else if (call.active) {
      hint.textContent = "Call in progress. Speak naturally — you can interrupt the agent mid-sentence.";
    } else {
      hint.textContent =
        "Headphones recommended. Echo cancellation is enabled, but headphones keep barge-in clean.";
    }
  }

  function syncOutboundAvailability() {
    const button = $("#place-call");
    if (!button) return;
    const health = state.health;
    let reason = "";
    if (health) {
      if (!health.providers.plivo) reason = "Plivo credentials not configured.";
      else if (!health.public_base_url) reason = "PUBLIC_BASE_URL not set — required for outbound.";
    }
    button.disabled = Boolean(reason) || state.outboundPlacing;
    if (reason) {
      setSaveState("outbound-state", reason, "err");
    } else {
      const node = $("#outbound-state");
      if (node && node.classList.contains("err")) setSaveState("outbound-state", "");
    }
  }

  // ---------------------------------------------------------------- config form
  async function loadConfig() {
    try {
      const data = await api("/api/config");
      applyConfigToForm(data);
    } catch {
      // The health request reports the connectivity problem; keep defaults.
    }
  }

  function applyConfigToForm(data) {
    if (!data || !data.config) return;
    state.config = data.config;
    state.configOptions = data.options || {};
    const config = data.config;
    const options = state.configOptions;

    fillSelect($("#cfg-asr"), options.asr_models || [], config.asr_speech_model);
    fillSelect($("#cfg-sarvam"), options.sarvam_models || [], config.sarvam_model);
    fillSelect(
      $("#cfg-tts-provider"),
      options.tts_providers || ["inworld", "speechify", "sarvam"],
      config.tts_provider,
      options.tts_provider_labels || {}
    );
    renderTtsProvider(config.tts_provider, config, options);

    setValue("cfg-temp", config.temperature);
    setValue("cfg-max-tokens", config.max_tokens);
    setValue("cfg-rate", config.speaking_rate);
    setValue("cfg-delivery", config.delivery_mode);
    setValue("cfg-voice", config.voice_id);
  }

  function renderTtsProvider(provider, config, options) {
    const isSpeechify = provider === "speechify";
    const isSarvam = provider === "sarvam";
    const noRateControls = isSpeechify || isSarvam;
    const models = isSarvam
      ? options.sarvam_tts_models || []
      : isSpeechify
        ? options.speechify_models || []
        : options.inworld_models || [];
    const currentModel = isSarvam
      ? config.sarvam_tts_model
      : isSpeechify
        ? config.speechify_model
        : config.inworld_model;
    fillSelect($("#cfg-tts-model"), models, currentModel);

    const voices = isSarvam
      ? options.sarvam_tts_voices || []
      : isSpeechify
        ? options.speechify_voices || []
        : options.inworld_voices || [];
    const voiceLabels = isSarvam
      ? options.sarvam_tts_voice_labels || {}
      : isSpeechify
        ? options.speechify_voice_labels || {}
        : options.inworld_voice_labels || {};
    const currentVoice = isSarvam
      ? config.sarvam_tts_voice_id || config.voice_id
      : isSpeechify
        ? config.speechify_voice_id || config.voice_id
        : config.inworld_voice_id || config.voice_id;
    fillVoices(voices, currentVoice, voiceLabels, voiceMetaFor(provider, options, voiceLabels));
    renderCostEstimate(provider);

    setText(
      "tts-title",
      isSarvam
        ? "Sarvam Bulbul v4 Flash"
        : isSpeechify
          ? "Speechify Simba 3.5"
          : "Inworld TTS 2"
    );
    setText(
      "tts-subtitle",
      isSarvam
        ? "Persistent WebSocket, flushed per sentence, μ-law 8 kHz native."
        : isSpeechify
          ? "Streaming HTTP, one request per sentence, μ-law native."
          : "Persistent WebSocket, sentence-boundary streaming."
    );
    setText(
      "tts-voice-hint",
      isSarvam
        ? "Bulbul v4 Flash English personas (en-IN)."
        : isSpeechify
          ? "Two male en-IN voices and three top Indian female voices, all speaking Indian English."
          : "Ten Indian-accent English voices, plus the standard Inworld voices."
    );
    setText(
      "tts-note",
      isSarvam
        ? "Each sentence is flushed to Sarvam as it arrives. Barge-in reconnects the socket because the API has no cancel message."
        : isSpeechify
          ? "Each sentence is synthesized as it arrives and played in order, so the first words start before the full answer is ready."
          : "The first sentence is streamed while the rest of the answer is still being generated."
    );

    const rateField = $("#tts-rate-field");
    const deliveryField = $("#tts-delivery-field");
    if (rateField) rateField.classList.toggle("is-disabled", noRateControls);
    if (deliveryField) deliveryField.classList.toggle("is-disabled", noRateControls);
    const rateInput = $("#cfg-rate");
    const deliveryInput = $("#cfg-delivery");
    if (rateInput) rateInput.disabled = noRateControls;
    if (deliveryInput) deliveryInput.disabled = noRateControls;
    setText(
      "tts-rate-hint",
      noRateControls ? `Not used by ${isSarvam ? "Bulbul" : "Simba"}` : "0.5–1.5"
    );    const picked = $("#cfg-voice");
    markVoiceTab(picked ? picked.value : currentVoice, state.voiceMeta || {});
  }

  function voiceMetaFor(provider, options, labels) {
    const meta = {};
    const catalog =
      provider === "inworld"
        ? options.inworld_voice_catalog
        : provider === "speechify"
          ? options.speechify_voice_catalog
          : null;
    if (catalog) {
      catalog.forEach((voice) => {
        meta[voice.id] = {
          name: voice.name,
          gender: voice.gender,
          group: voice.group || "",
          sub: [voice.gender, voice.locale].filter(Boolean).join(" · "),
          description: voice.description,
        };
      });
    } else {
      // Sarvam labels look like "Simran — customer service".
      Object.entries(labels || {}).forEach(([id, label]) => {
        const [name, style] = String(label).split(" — ");
        meta[id] = { name, sub: style || "", description: style || "" };
      });
    }
    return meta;
  }

  function setValue(id, value) {
    const node = document.getElementById(id);
    if (node) node.value = value === null || value === undefined ? "" : value;
  }

  function fillSelect(select, values, current, labels) {
    if (!select) return;
    const list = Array.from(values || []);
    if (current && !list.includes(current)) list.unshift(current);
    select.replaceChildren();
    list.forEach((value) => {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = (labels && labels[value]) || value;
      select.appendChild(option);
    });
    if (current) select.value = current;
  }

  function fillVoices(voices, current, labels, meta) {
    const list = Array.from(voices || []);
    if (current && !list.includes(current)) list.unshift(current);

    ["#cfg-voice", "#web-voice"].forEach((selector) => {
      const select = $(selector);
      if (!select) return;
      const previous = select.value;
      fillSelect(select, list, current || previous, labels);
    });
    renderVoiceTabs(list, $("#cfg-voice").value, meta || {});
  }

  // Voice picker: one horizontal row of tabs. #cfg-voice stays as the value
  // holder (saveConfig reads it), the tabs just drive it.
  function renderVoiceTabs(list, selected, meta) {
    const host = $("#voice-tabs");
    if (!host) return;
    host.replaceChildren();
    let group = null;
    list.forEach((id) => {
      const info = meta[id] || { name: id };
      if (info.group && info.group !== group) {
        group = info.group;
        const heading = document.createElement("span");
        heading.className = "voice-group";
        heading.textContent = group;
        host.appendChild(heading);
      }
      const tab = document.createElement("button");
      tab.type = "button";
      tab.className = "voice-tab";
      tab.dataset.voice = id;
      tab.setAttribute("role", "radio");
      tab.title = info.description || "";
      const name = document.createElement("strong");
      name.textContent = info.name || id;
      const sub = document.createElement("small");
      sub.textContent = info.sub || "";
      tab.append(name, sub);
      tab.addEventListener("click", () => selectVoiceTab(id));
      host.appendChild(tab);
    });
    markVoiceTab(selected, meta);
  }

  function selectVoiceTab(id) {
    const select = $("#cfg-voice");
    if (!select) return;
    select.value = id;
    markVoiceTab(id, state.voiceMeta || {});
  }

  function markVoiceTab(selected, meta) {
    state.voiceMeta = meta;
    const host = $("#voice-tabs");
    if (!host) return;
    host.querySelectorAll(".voice-tab").forEach((tab) => {
      const on = tab.dataset.voice === selected;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-checked", on ? "true" : "false");
      if (on && tab.scrollIntoView) tab.scrollIntoView({ block: "nearest", inline: "nearest" });
    });
    const info = (meta && meta[selected]) || null;
    const hint = $("#tts-voice-hint");
    if (hint && info && info.description) {
      hint.textContent = `${info.name}${info.gender ? ` · ${info.gender}` : ""} — ${info.description}`;
    }
  }

  const COST_CHARS_PER_MINUTE = 900;
  function renderCostEstimate(provider) {
    const box = $("#tts-cost-est");
    if (!box) return;
    const rates = (state.pricing && state.pricing.rates) || null;
    const inworldFlash = /flash/i.test(valueOf("cfg-tts-model") || "");
    const ttsRate = rates
      ? provider === "sarvam"
        ? rates.sarvam_tts_per_1m_chars
        : provider === "inworld"
          ? inworldFlash
            ? rates.inworld_tts_2_flash_per_1m_chars
            : rates.inworld_tts_2_per_1m_chars
          : rates.tts_per_1m_chars
      : null;
    const sttRate = rates ? rates.asr_per_hour : null;
    const llmRate = rates ? rates.llm_per_1m_generated_chars : null;
    if ([ttsRate, sttRate, llmRate].some((v) => v === null || v === undefined)) {
      box.hidden = true;
      return;
    }
    const fx = rates.usd_inr || 95;
    const chars = COST_CHARS_PER_MINUTE;
    const rows = [
      ["Speech to text", "AssemblyAI", `$${Number(sttRate).toFixed(2)} / hour`, sttRate / 60],
      ["Language model", "Sarvam 105B", `$${Number(llmRate).toFixed(2)} / 1M chars`, (chars * llmRate) / 1e6],
      ["Text to speech", ttsProviderLabel(provider), `$${Number(ttsRate).toFixed(2)} / 1M chars`, (chars * ttsRate) / 1e6],
    ];
    const total = rows.reduce((sum, row) => sum + row[3], 0);
    rows.push(["Total", "", "", total]);

    // Components across, measures down: compact in height, nothing hidden.
    const head = $("#cost-est-head");
    const body = $("#cost-est-rows");
    head.replaceChildren();
    body.replaceChildren();
    const headRow = document.createElement("tr");
    headRow.appendChild(document.createElement("th"));
    rows.forEach(([label, vendor], index) => {
      const th = document.createElement("th");
      th.textContent = vendor ? `${label} · ${vendor}` : label;
      if (index === rows.length - 1) th.className = "is-total";
      headRow.appendChild(th);
    });
    head.appendChild(headRow);
    [
      ["INR / min", (row) => moneyInr(row[3] * fx), "is-inr"],
      ["USD / min", (row) => `$${row[3].toFixed(4)}`, ""],
      ["List price", (row) => row[2] || "—", "is-list"],
    ].forEach(([title, format, cls]) => {
      const tr = document.createElement("tr");
      if (cls) tr.className = cls;
      const th = document.createElement("th");
      th.scope = "row";
      th.textContent = title;
      tr.appendChild(th);
      rows.forEach((row, index) => {
        const td = document.createElement("td");
        td.textContent = format(row);
        if (index === rows.length - 1) td.className = "is-total";
        tr.appendChild(td);
      });
      body.appendChild(tr);
    });
    setText("cost-est-basis", `${chars} characters spoken per minute`);
    setText(
      "cost-est-note",
      `Per minute = ${chars} spoken characters (about ${chars} generated by the model) plus a full minute of transcription, at ₹${fx} per USD. Estimates only.`
    );
    box.hidden = false;
  }

  async function saveConfig() {
    const button = $("#save-config");
    if (button) button.disabled = true;
    setSaveState("save-state", "Saving…");

    const provider = valueOf("cfg-tts-provider") || "inworld";
    const current = state.config || {};
    const payload = {
      tts_provider: provider,
      asr_speech_model: valueOf("cfg-asr"),
      sarvam_model: valueOf("cfg-sarvam"),
      inworld_model:
        provider === "inworld"
          ? valueOf("cfg-tts-model")
          : current.inworld_model || "inworld-tts-2",
      speechify_model:
        provider === "speechify"
          ? valueOf("cfg-tts-model")
          : current.speechify_model || "simba-3.5",
      sarvam_tts_model:
        provider === "sarvam"
          ? valueOf("cfg-tts-model")
          : current.sarvam_tts_model || "bulbul:v4-flash",
      voice_id: valueOf("cfg-voice").trim(),
      temperature: Number(valueOf("cfg-temp")),
      max_tokens: Number(valueOf("cfg-max-tokens")),
      speaking_rate: Number(valueOf("cfg-rate")),
      delivery_mode: valueOf("cfg-delivery"),
    };

    try {
      if (!Number.isFinite(payload.temperature)) throw new Error("Temperature must be a number.");
      if (!Number.isFinite(payload.max_tokens)) throw new Error("Max tokens must be a number.");
      if (!Number.isFinite(payload.speaking_rate)) throw new Error("Speaking rate must be a number.");
      if (!payload.voice_id) throw new Error("Voice ID is required.");

      const data = await api("/api/config", { method: "POST", body: payload });
      applyConfigToForm({
        config: data.config,
        options: state.configOptions || {},
      });
      setSaveState("save-state", "Saved · applies to new calls", "ok");
      toast("Configuration saved. New calls will use it.", "ok");
    } catch (error) {
      setSaveState("save-state", error.message, "err");
      toast(error.message, "err", 6000);
    } finally {
      if (button) button.disabled = false;
    }
  }

  function valueOf(id) {
    const node = document.getElementById(id);
    return node ? node.value : "";
  }

  // ------------------------------------------------------------- outbound call
  async function placeOutboundCall() {
    const input = $("#outbound-to");
    const button = $("#place-call");
    if (!input || state.outboundPlacing) return;

    const to = input.value.trim().replace(/[\s-]/g, "");
    if (!/^\+[1-9]\d{6,14}$/.test(to)) {
      setSaveState(
        "outbound-state",
        "Enter an E.164 number, for example +14155550123.",
        "err"
      );
      input.focus();
      return;
    }

    state.outboundPlacing = true;
    if (button) button.disabled = true;
    setSaveState("outbound-state", `Dialing ${to}…`);

    try {
      await api("/plivo/outbound", { method: "POST", body: { to } });
      setSaveState("outbound-state", `Dialing ${to} · answering in the agent`, "ok");
      toast(`Placing call to ${to}.`, "ok");
      input.value = "";
      setTimeout(loadStats, 1500);
      setTimeout(loadStats, 5000);
    } catch (error) {
      setSaveState("outbound-state", error.message, "err");
      toast(error.message, "err", 7000);
    } finally {
      state.outboundPlacing = false;
      syncOutboundAvailability();
    }
  }

  // ------------------------------------------------------------------ clipboard
  async function copyValue(selector, button) {
    const node = document.querySelector(selector);
    const text = node ? node.textContent.trim() : "";
    if (!text || text.startsWith("Set ")) {
      toast("Nothing to copy yet — configure it in .env first.", "warn");
      return;
    }
    try {
      await navigator.clipboard.writeText(text);
    } catch {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "readonly");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    if (button) {
      const original = button.textContent;
      button.textContent = "Copied";
      button.classList.add("copied");
      setTimeout(() => {
        button.textContent = original;
        button.classList.remove("copied");
      }, 1300);
    }
    toast("Copied to clipboard.", "ok", 2200);
  }

  // --------------------------------------------------------------------- stats
  async function loadPricing() {
    try {
      state.pricing = await api("/api/pricing");
      renderPricing();
      renderChips();
      renderCostEstimate(valueOf("cfg-tts-provider") || (state.config && state.config.tts_provider));
    } catch {
      state.pricing = null;
    }
  }

  function renderPricing() {
    const pricing = state.pricing;
    const rates = (pricing && pricing.rates) || {};
    const rateText = (value) => {
      if (value === null || value === undefined) return "not set";
      const number = Number(value);
      if (!Number.isFinite(number)) return "not set";
      if (number >= 1) return `$${number.toFixed(2)}`;
      return `$${number.toFixed(6).replace(/0+$/, "").replace(/\.$/, "")}`;
    };
    setText("rate-asr", rateText(rates.asr_per_minute));
    setText("rate-llm", rateText(rates.llm_per_1m_generated_chars));
    setText("rate-tts", rateText(rates.tts_per_1m_chars));

    const badge = $("#rate-badge");
    if (badge) {
      const configured = Boolean(pricing && pricing.configured);
      badge.textContent = configured ? "Static rates" : "Not configured";
      badge.classList.toggle("ok", configured);
    }
  }

  async function loadStats() {
    if (state.statsLoading) {
      state.statsQueued = true;
      return;
    }
    state.statsLoading = true;
    try {
      const data = await api("/api/stats");
      state.stats = data;
      reconcileActiveCalls(data);
      renderStats(data);
    } catch {
      // Keep the last snapshot; the event stream still updates the call panel.
    } finally {
      state.statsLoading = false;
      if (state.statsQueued) {
        state.statsQueued = false;
        loadStats();
      }
    }
  }

  function reconcileActiveCalls(data) {
    (data.calls || []).forEach((record) => {
      state.callDirections.set(record.call_id, record.direction);
      if (record.status === "active") state.activeCalls.add(record.call_id);
      else state.activeCalls.delete(record.call_id);
    });
  }

  function scheduleStats(delay = 1200) {
    if (state.statsTimer) return;
    state.statsTimer = setTimeout(() => {
      state.statsTimer = null;
      loadStats();
    }, delay);
  }

  function renderStats(data) {
    const totals = data.totals || {};
    const costs = data.costs || {};
    const latency = data.latency || {};

    setText("stat-calls", formatNumber(totals.calls || 0));
    setText("stat-calls-sub", `${formatNumber(totals.active_calls || 0)} active`);
    setText("stat-duration", clock(totals.duration_seconds || 0));
    setText("stat-asr", formatAudio(totals.asr_seconds || 0));

    const promptTokens = totals.llm_prompt_tokens || 0;
    const completionTokens = totals.llm_completion_tokens || 0;
    setText("stat-tokens", formatNumber(totals.llm_output_characters || 0));
    setText(
      "stat-tokens-sub",
      `${formatNumber(promptTokens + completionTokens)} tokens · ${formatNumber(
        promptTokens
      )} in`
    );

    setText("stat-tts", formatNumber(totals.tts_characters || 0));
    setText("stat-tts-sub", `${formatAudio(totals.tts_audio_seconds || 0)} audio`);
    const totalInr = inrFor(costs);
    setText("stat-cost", totalInr === null ? money(costs.total) : moneyInr(totalInr));

    const costNode = $("#stat-cost");
    const costSub = costNode ? costNode.parentElement.querySelector("em") : null;
    if (costSub) {
      const inr = inrFor(costs);
      costSub.textContent =
        costs.total === null || costs.total === undefined
          ? costs.rates_configured
            ? "no billable usage yet"
            : "rates not set"
          : `${money(costs.total)} · official rates`;
    }

    setText(
      "stat-latency",
      latency.first_audio_out_ms === null || latency.first_audio_out_ms === undefined
        ? "—"
        : `${Math.round(latency.first_audio_out_ms)} ms`
    );

    renderCostTable(data.calls || []);
    renderActivity("inbound");
    renderActivity("outbound");
    renderChips();
  }

  function makeCallRowInteractive(row, record) {
    if (!row || !record || !record.call_id) return;
    row.classList.add("clickable");
    row.tabIndex = 0;
    row.setAttribute("role", "button");
    row.setAttribute(
      "aria-label",
      `Open summary for ${directionLabel(record.direction)} call`
    );
    const open = () => openCallSummary(record.call_id);
    row.addEventListener("click", open);
    row.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      open();
    });
  }

  // ------------------------------------------------- cost & latency (saved calls)
  state.histType = "all";

  function plainStat(label, value, sub, accent) {
    const card = document.createElement("div");
    card.className = `card stat${accent ? " accent" : ""}`;
    const span = document.createElement("span");
    span.textContent = label;
    const strong = document.createElement("strong");
    strong.textContent = value;
    const em = document.createElement("em");
    em.textContent = sub;
    card.append(span, strong, em);
    return card;
  }

  const msOrDash = (value) =>
    value === null || value === undefined ? "—" : `${Math.round(value)} ms`;
  const perMinute = (value) =>
    value === null || value === undefined ? "—" : `$${Number(value).toFixed(value < 1 ? 4 : 2)}/min`;

  const perMinuteInr = (value) =>
    value === null || value === undefined ? "—" : `${moneyInr(value)}/min`;

  function stackLine(stack) {
    const tts = [ttsProviderLabel(stack.tts_provider), stack.tts_model].filter(Boolean).join(" ");
    return [stack.asr_model, stack.llm_model, tts].filter(Boolean).join(" · ") || "—";
  }

  async function loadHistory() {
    try {
      const data = await api(`/api/history?type=${encodeURIComponent(state.histType)}`);
      renderHistory(data);
    } catch (error) {
      toast(`Could not load call history: ${error.message}`, "warn", 5000);
    }
  }

  function renderHistory(data) {
    $$("#hist-tabs .hist-tab").forEach((tab) => {
      const on = tab.dataset.type === state.histType;
      tab.classList.toggle("is-active", on);
      tab.setAttribute("aria-selected", on ? "true" : "false");
    });

    const summary = data.summary || {};
    const cpm = summary.cost_per_minute || {};
    const fx = summary.usd_inr || 95;
    const ttft = summary.ttft || {};
    const ttfa = summary.ttfa || {};
    const total = summary.total || {};
    const cards = $("#hist-summary");
    cards.replaceChildren(
      plainStat(
        "Cost per minute",
        perMinuteInr(cpm.total_inr),
        cpm.total === null || cpm.total === undefined
          ? "no priced calls yet"
          : `${perMinute(cpm.total)} · STT ${moneyInr((cpm.asr || 0) * fx)} · LLM ${moneyInr((cpm.llm || 0) * fx)} · TTS ${moneyInr((cpm.tts || 0) * fx)} · estimated`,
        true
      ),
      plainStat(
        "TTFT · median",
        msOrDash(ttft.median),
        `p95 ${msOrDash(ttft.p95)} · ${ttft.count || 0} turns`
      ),
      plainStat(
        "TTFA · median",
        msOrDash(ttfa.median),
        `p95 ${msOrDash(ttfa.p95)} · ${ttfa.count || 0} turns`
      ),
      plainStat(
        "Total turn · median",
        msOrDash(total.median),
        `TTFT + TTFA · p95 ${msOrDash(total.p95)} · ${total.count || 0} turns`
      ),
      plainStat(
        "Calls",
        String(summary.calls || 0),
        `${(summary.minutes || 0).toFixed(1)} min of talk time`
      )
    );

    const calls = data.calls || [];
    const body = $("#hist-rows");
    body.replaceChildren();
    calls.forEach((call) => body.appendChild(historyRow(call)));
    const empty = $("#hist-empty");
    empty.hidden = calls.length > 0;
    if (!calls.length) {
      empty.textContent =
        data.enabled === false
          ? "Call history is unavailable: the database could not be opened. Check the server log."
          : "No saved calls yet. Calls appear here after they end.";
    }
    $("#hist-card").querySelector("table").hidden = calls.length === 0;
  }

  function historyRow(call) {
    const row = document.createElement("tr");
    const cell = (text, cls = "") => {
      const td = document.createElement("td");
      td.textContent = text;
      if (cls) td.className = cls;
      return td;
    };
    const latency = (stat) =>
      `${msOrDash(stat && stat.median)} (${stat ? stat.count : 0})`;

    const stack = cell(stackLine(call.stack || {}), "mono muted hist-stack");
    stack.title = [stackLine(call.stack || {}), call.stack && call.stack.voice_id ? `voice ${call.stack.voice_id}` : ""]
      .filter(Boolean)
      .join(" · ");

    const cost = cell(call.cost === null || call.cost === undefined ? "—" : `${moneyInr(call.cost_inr)} · ${money(call.cost)}`, "mono");
    const view = document.createElement("td");
    const link = document.createElement("a");
    link.className = "btn btn-ghost btn-sm";
    link.href = `#cost/${encodeURIComponent(call.call_id)}`;
    link.textContent = "View";
    view.appendChild(link);

    row.append(
      cell(formatWhen(call.started_at), "mono"),
      cell(directionLabel(call.direction)),
      cell(formatDuration(call.duration_seconds), "mono"),
      cell(String(call.turns), "mono"),
      stack,
      cell(latency(call.ttft), "mono"),
      cell(latency(call.ttfa), "mono"),
      cell(latency(call.total), "mono"),
      cost,
      cell(
        call.cost_per_minute === null || call.cost_per_minute === undefined
          ? "—"
          : `${perMinuteInr(call.cost_per_minute_inr)} · ${perMinute(call.cost_per_minute)}`,
        "mono"
      ),
      view
    );
    return row;
  }

  function median(values) {
    if (!values.length) return null;
    const sorted = values.slice().sort((a, b) => a - b);
    const mid = Math.floor(sorted.length / 2);
    return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
  }

  async function loadCallDetail(callId) {
    setText("detail-id", callId);
    state.detailId = callId;
    const error = $("#detail-error");
    const body = $("#detail-body");
    error.hidden = true;
    body.hidden = true;
    try {
      const record = await api(`/api/calls/${encodeURIComponent(callId)}`);
      if (state.detailId !== callId) return;
      renderCallDetail(record);
      body.hidden = false;
    } catch (err) {
      error.textContent =
        err.message && /not found/i.test(err.message)
          ? "This call is no longer available."
          : `Could not load this call: ${err.message}`;
      error.hidden = false;
    }
  }

  function renderCallDetail(record) {
    const costs = record.costs || {};
    const tts = [ttsProviderLabel(record.tts_provider), record.tts_model].filter(Boolean).join(" ");
    const started = new Date(record.started_at);
    const facts = [
      ["Type", directionLabel(record.direction)],
      ["Started", Number.isNaN(started.getTime()) ? "—" : started.toLocaleString()],
      ["Duration", formatDuration(record.duration_seconds)],
      ["Ended", reasonLabel(record.end_reason)],
      ["Speech to text", record.asr_model ? `AssemblyAI ${record.asr_model}` : "—"],
      ["Language model", record.llm_model || "—"],
      ["Text to speech", tts || "—"],
      ["Voice", record.voice_id || "—"],
    ];
    renderLlmUsage("detail", record);
    const grid = $("#detail-stack");
    grid.replaceChildren();
    facts.forEach(([label, value]) => {
      const item = document.createElement("div");
      const span = document.createElement("span");
      span.textContent = label;
      const strong = document.createElement("strong");
      strong.textContent = value;
      item.append(span, strong);
      grid.appendChild(item);
    });

    const turns = (record.turns || []).slice().sort((a, b) => Number(a.index || 0) - Number(b.index || 0));
    const valid = turns.filter((t) => t.kind === "response" && !t.interrupted);
    const ttft = valid.map((t) => t.llm_first_token_ms).filter((v) => v !== null && v !== undefined);
    const ttfa = valid.map((t) => t.tts_net_ttfa_ms).filter((v) => v !== null && v !== undefined);
    const totals = valid
      .filter((t) => t.llm_first_token_ms != null && t.tts_net_ttfa_ms != null)
      .map((t) => t.llm_first_token_ms + t.tts_net_ttfa_ms);
    const minutes = Number(record.duration_seconds) / 60;
    const perMin = costs.total !== null && costs.total !== undefined && minutes > 0 ? costs.total / minutes : null;
    const inr = inrFor(costs);
    const fx = costs.usd_inr || 95;
    $("#detail-stats").replaceChildren(
      plainStat("Total cost", inr === null ? money(costs.total) : moneyInr(inr), `${money(costs.total)} · estimated`, true),
      plainStat(
        "Split",
        [costs.asr, costs.llm, costs.tts].map((v) => moneyInr(v === null || v === undefined ? null : v * fx)).join(" / "),
        `speech to text / language model / text to speech · ${money(costs.asr)} / ${money(costs.llm)} / ${money(costs.tts)}`
      ),
      plainStat("Per minute", perMin === null ? "—" : perMinuteInr(perMin * fx), perMin === null ? "" : perMinute(perMin)),
      plainStat("Median TTFT", msOrDash(median(ttft)), `${ttft.length} turns`),
      plainStat("Median TTFA", msOrDash(median(ttfa)), `${ttfa.length} turns`),
      plainStat("Median total turn", msOrDash(median(totals)), `TTFT + TTFA · ${totals.length} turns`)
    );

    const host = $("#detail-turns");
    host.replaceChildren();
    turns.forEach((turn) => {
      if (turn.user_text) {
        const bubble = createBubble("user", `Caller · turn ${turn.index}`);
        bubble.querySelector("p").textContent = turn.user_text;
        host.appendChild(bubble);
      }
      if (turn.assistant_text) {
        const label =
          turn.kind === "greeting" ? "Agent · greeting" : turn.kind === "fallback" ? "Agent · fallback" : "Agent";
        const bubble = createBubble("agent", turn.interrupted ? `${label} · interrupted` : label);
        bubble.querySelector("p").textContent = turn.assistant_text;
        renderBubbleMetrics(bubble, turn);
        host.appendChild(bubble);
      }
    });
    if (!host.children.length) {
      const note = document.createElement("p");
      note.className = "summary-empty";
      note.textContent = "No transcript was captured for this call.";
      host.appendChild(note);
    }
    setText(
      "detail-turn-note",
      `${turns.length} turn${turns.length === 1 ? "" : "s"}. Interrupted turns are dimmed and not counted in the medians.`
    );
  }

  function initHistory() {
    $$("#hist-tabs .hist-tab").forEach((tab) => {
      tab.addEventListener("click", () => {
        state.histType = tab.dataset.type;
        loadHistory();
      });
    });
    const back = $("#detail-back");
    if (back) back.addEventListener("click", () => { location.hash = "#cost"; });
  }

  function renderCostTable(calls) {
    const tbody = $("#cost-calls");
    if (!tbody) return;
    tbody.replaceChildren();

    calls.slice(0, 50).forEach((record) => {
      const row = document.createElement("tr");
      const status = record.status === "active" ? "Active" : reasonLabel(record.end_reason || "Completed");
      const statusClass =
        record.status === "active"
          ? "teal"
          : (record.errors || []).length
          ? "red"
          : "muted";

      row.append(
        cell(formatWhen(record.started_at), "mono muted"),
        cell(directionLabel(record.direction)),
        cell(formatDuration(record.duration_seconds), "mono"),
        cell(formatAudio(record.asr_billed_seconds || record.asr_seconds), "mono"),
        cell(formatNumber(record.llm_output_characters || 0), "mono"),
        cell(formatNumber(record.tts_characters), "mono"),
        cell(
          record.avg_response_ms === null || record.avg_response_ms === undefined
            ? "—"
            : `${Math.round(record.avg_response_ms)} ms`,
          "mono"
        ),
        cell(
          record.costs && record.costs.total !== null && record.costs.total !== undefined
            ? money(record.costs.total)
            : "not set",
          record.costs && record.costs.total !== null && record.costs.total !== undefined
            ? "mono teal"
            : "mono muted"
        ),
        cell(
          record.costs && record.costs.total !== null && record.costs.total !== undefined
            ? moneyInr(inrFor(record.costs))
            : "—",
          record.costs && record.costs.total !== null && record.costs.total !== undefined
            ? "mono"
            : "mono muted"
        ),
        cell(status, statusClass)
      );

      if ((record.errors || []).length) row.title = record.errors.join("\n");
      makeCallRowInteractive(row, record);
      tbody.appendChild(row);
    });

    const empty = $("#cost-empty");
    if (empty) empty.classList.toggle("hidden", calls.length > 0);
    tbody.closest(".table-wrap").hidden = calls.length === 0;
  }

  function renderActivity(direction) {
    const tbody = document.getElementById(`${direction}-calls`);
    if (!tbody) return;
    const calls = ((state.stats && state.stats.calls) || []).filter(
      (record) => record.direction === direction
    );
    const visible = calls.slice(0, 10);
    tbody.replaceChildren();

    visible.forEach((record) => {
      const row = document.createElement("tr");
      const who =
        direction === "inbound"
          ? record.from_number || "—"
          : record.to_number || "—";
      row.append(
        cell(formatWhen(record.started_at), "mono muted"),
        cell(who),
        cell(formatDuration(record.duration_seconds), "mono"),
        cell(
          record.status === "active" ? "Active" : reasonLabel(record.end_reason || "Completed"),
          record.status === "active" ? "teal" : "muted"
        ),
        cell(
          record.costs && record.costs.total !== null && record.costs.total !== undefined
            ? money(record.costs.total)
            : "not set",
          "mono muted"
        )
      );
      makeCallRowInteractive(row, record);
      tbody.appendChild(row);
    });

    const empty = document.getElementById(`${direction}-empty`);
    if (empty) empty.classList.toggle("hidden", visible.length > 0);
    tbody.closest(".table-wrap").hidden = visible.length === 0;

    const live = document.getElementById(`${direction}-live`);
    if (!live) return;
    live.replaceChildren();
    const active = activeCallFor(direction);
    if (!active) {
      const pill = document.createElement("span");
      pill.className = "status-pill idle";
      pill.append(makeDot(), document.createTextNode(`No active ${direction} call`));
      live.appendChild(pill);
      return;
    }
    const key = state.callStates.get(active.call_id) || "LISTENING";
    const [label, cls] = stateMeta(key);
    const pill = document.createElement("span");
    pill.className = `status-pill ${cls}`;
    pill.append(
      makeDot(),
      document.createTextNode(`${label} · ${String(active.call_id).slice(-6)}`)
    );
    live.appendChild(pill);
  }

  function activeCallFor(direction) {
    const calls = (state.stats && state.stats.calls) || [];
    const fromStats = calls.find(
      (record) => record.direction === direction && record.status === "active"
    );
    if (fromStats) return fromStats;
    for (const callId of state.activeCalls) {
      if (state.callDirections.get(callId) === direction) {
        return { call_id: callId, direction };
      }
    }
    return null;
  }

  function cell(text, className = "", title = "") {
    const node = document.createElement("td");
    node.textContent = text;
    if (className) node.className = className;
    if (title) node.title = title;
    return node;
  }

  // ---------------------------------------------------------------- transcript
  function resetTranscript() {
    const transcript = $("#transcript");
    if (!transcript) return;
    $$(".bubble", transcript).forEach((bubble) => bubble.remove());
    const empty = $("#transcript-empty");
    if (empty) empty.hidden = false;
    transcript.scrollTop = 0;
    lastUserBubble = null;
    lastUserTurn = null;
    lastUserFinal = true;
    agentBubbles.clear();
  }

  function ensureEmptyHidden() {
    const empty = $("#transcript-empty");
    if (empty) empty.hidden = true;
  }

  function isNearBottom() {
    const transcript = $("#transcript");
    if (!transcript) return true;
    return transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 110;
  }

  function scrollTranscript() {
    const transcript = $("#transcript");
    if (transcript) transcript.scrollTop = transcript.scrollHeight;
  }

  function createBubble(role, label) {
    const bubble = document.createElement("div");
    bubble.className = `bubble ${role}`;
    const head = document.createElement("div");
    head.className = "role";
    head.textContent = label;
    const text = document.createElement("p");
    bubble.append(head, text);
    return bubble;
  }

  function addBubble(role, label, turn) {
    const transcript = $("#transcript");
    if (!transcript) return null;
    const bubble = createBubble(role, label);
    if (turn !== null && turn !== undefined) bubble.dataset.turn = String(turn);
    transcript.appendChild(bubble);
    ensureEmptyHidden();
    return bubble;
  }

  function upsertUserBubble(text, final, turn) {
    const stick = isNearBottom();
    const key = Number.isFinite(Number(turn)) ? Number(turn) : null;
    const turnChanged = key !== null && lastUserTurn !== null && key !== lastUserTurn;
    let bubble = lastUserBubble;

    if (!bubble || lastUserFinal || turnChanged) {
      bubble = addBubble("user", "Caller", key);
      lastUserBubble = bubble;
      lastUserTurn = key;
    }
    if (!bubble) return;

    bubble.querySelector("p").textContent = text;
    bubble.classList.toggle("partial", !final);
    lastUserFinal = Boolean(final);
    if (stick) scrollTranscript();
  }

  function appendAssistantDelta(text, turn) {
    if (!text) return;
    const stick = isNearBottom();
    const key = String(turn === null || turn === undefined ? "last" : turn);
    let bubble = agentBubbles.get(key);

    if (!bubble || bubble.dataset.final === "1") {
      bubble = addBubble("agent", "Agent", turn);
      if (!bubble) return;
      agentBubbles.set(key, bubble);
    }

    bubble.querySelector("p").textContent += text;
    bubble.classList.add("partial");
    if (stick) scrollTranscript();
  }

  function finalizeAssistant(text, turn) {
    if (!text) return;
    const stick = isNearBottom();
    const key = String(turn === null || turn === undefined ? "last" : turn);
    let bubble = agentBubbles.get(key);

    if (!bubble) {
      bubble = addBubble("agent", "Agent", turn);
      if (!bubble) return;
      agentBubbles.set(key, bubble);
    }

    bubble.querySelector("p").textContent = text;
    bubble.classList.remove("partial");
    bubble.dataset.final = "1";
    if (stick) scrollTranscript();
  }

  function lastAgentBubble() {
    const bubbles = $$(".bubble.agent");
    return bubbles.length ? bubbles[bubbles.length - 1] : null;
  }

  function markAgentInterrupted(turn) {
    let bubble = null;
    if (turn !== null && turn !== undefined) {
      bubble = agentBubbles.get(String(turn)) || null;
    } else {
      bubble = lastAgentBubble();
      if (bubble && bubble.dataset.final === "1") bubble = null;
    }
    if (!bubble) return;
    bubble.classList.remove("partial");
    bubble.classList.add("interrupted");
    bubble.dataset.final = "1";
  }

  function renderBubbleMetrics(bubble, latency) {
    if (!bubble || !latency) return;
    let footer = bubble.querySelector(".bubble-metrics");
    if (!footer) {
      footer = document.createElement("div");
      footer.className = "bubble-metrics";
      bubble.appendChild(footer);
    }
    footer.replaceChildren();

    const hasNetTtfa =
      latency.tts_net_ttfa_ms !== null && latency.tts_net_ttfa_ms !== undefined;
    const ttsValue = hasNetTtfa
      ? latency.tts_net_ttfa_ms
      : latency.tts_first_audio_ms;

    [
      ["LLM TTFT", latency.llm_first_token_ms],
      [hasNetTtfa ? "TTS net TTFA" : "TTS first audio", ttsValue],
    ].forEach(([label, value]) => {
      const item = document.createElement("span");
      item.append(
        document.createTextNode(label),
        Object.assign(document.createElement("b"), {
          textContent:
            value === null || value === undefined ? "—" : `${msText(value)} ms`,
        })
      );
      footer.appendChild(item);
    });

    const title = [];
    if (latency.tts_first_audio_ms !== null && latency.tts_first_audio_ms !== undefined) {
      title.push(`TTS raw TTFA ${msText(latency.tts_first_audio_ms)} ms (includes LLM TTFT)`);
    }
    if (latency.first_audio_out_ms !== null && latency.first_audio_out_ms !== undefined) {
      title.push(`First audio out ${msText(latency.first_audio_out_ms)} ms`);
    }
    if (latency.asr_final_delay_ms !== null && latency.asr_final_delay_ms !== undefined) {
      title.push(`ASR endpoint ${msText(latency.asr_final_delay_ms)} ms`);
    }
    if (title.length) footer.title = title.join(" · ");
  }

  function attachBubbleMetrics(turn, latency) {
    if (turn === null || turn === undefined) return;
    renderBubbleMetrics(agentBubbles.get(String(turn)) || null, latency);
  }

  // ------------------------------------------------------------ live call panel
  function resetCallPanels() {
    resetTranscript();
    ["lat-asr", "lat-llm", "lat-tts", "lat-total"].forEach((id) => setText(id, "—"));
    setText("use-tokens", "0");
    setText("use-tts", "0");
    setText("use-asr", "0.0 s");
    setText("use-cost", "—");
    const note = $("#use-note");
    if (note) {
      note.textContent = "Estimates use static official rates; see Cost dashboard.";
    }
    setText("call-timer", "00:00");
    updateCallStatus("INITIALIZING");
    setText("mic-hint", "Waiting for microphone");
    const dot = $("#mic-dot");
    if (dot) dot.className = "dot";
    renderMicBars(0);
  }

  function updateCallStatus(key) {
    const pill = $("#call-status");
    if (!pill) return;
    const [label, cls] = stateMeta(key);
    pill.className = `status-pill ${cls}`;
    pill.replaceChildren(makeDot(), document.createTextNode(label));
  }

  function renderLatency(latency) {
    if (!latency) return;
    setText("lat-asr", msText(latency.asr_final_delay_ms));
    setText("lat-llm", msText(latency.llm_first_token_ms));
    setText(
      "lat-tts",
      msText(
        latency.tts_net_ttfa_ms !== null && latency.tts_net_ttfa_ms !== undefined
          ? latency.tts_net_ttfa_ms
          : latency.tts_first_audio_ms
      )
    );
    setText("lat-total", msText(latency.first_audio_out_ms ?? latency.total_ms));
  }

  function renderUsage(usage, costs) {
    const data = usage || {};
    const prompt = Number(data.llm_prompt_tokens) || 0;
    const completion = Number(data.llm_completion_tokens) || 0;
    setText("use-tokens", formatNumber(prompt + completion));
    setText("use-tts", formatNumber(data.tts_characters || 0));
    setText("use-asr", formatAudio(data.asr_seconds || 0));

    const total = costs ? costs.total : null;
    setText("use-cost", total === null || total === undefined ? "—" : money(total));

    const note = $("#use-note");
    if (note) {
      note.textContent =
        total === null || total === undefined
          ? "Cost appears when pricing rates are configured."
          : "Estimate from static official rates and provider-reported usage.";
    }
  }

  // ------------------------------------------------------------- live call page
  function liveIsCurrent(callId) {
    return Boolean(state.live.callId && callId && callId === state.live.callId);
  }

  function liveElapsedSeconds() {
    if (!state.live.startedAt) return 0;
    return Math.max(0, (Date.now() - state.live.startedAt) / 1000);
  }

  function liveTurnEntry(turn) {
    const key = turn === null || turn === undefined ? "0" : String(turn);
    let entry = state.live.turns.get(key);
    if (!entry) {
      entry = { user: "", assistant: "", latency: null, interrupted: false };
      state.live.turns.set(key, entry);
    }
    return entry;
  }

  function liveUpdateTurnCount() {
    const count = state.live.turns.size;
    setText("live-turn-count", `${count} turn${count === 1 ? "" : "s"}`);
  }

  function liveScroll(host) {
    if (!host) return;
    host.scrollTop = host.scrollHeight;
  }

  function liveLog(kind, title, detail = "", badge = "") {
    const host = $("#live-log");
    if (!host) return;
    const stick = host.scrollHeight - host.scrollTop - host.clientHeight < 80;

    const entry = document.createElement("div");
    entry.className = `live-log-entry ${kind}`;

    const time = document.createElement("span");
    time.className = "live-log-time";
    time.textContent = clock(liveElapsedSeconds());

    const dot = document.createElement("span");
    dot.className = "live-log-dot";

    const body = document.createElement("div");
    body.className = "live-log-body";

    const heading = document.createElement("div");
    heading.className = "live-log-title";
    heading.appendChild(document.createTextNode(title));
    if (badge) {
      const tag = document.createElement("b");
      tag.textContent = badge;
      heading.appendChild(tag);
    }

    body.appendChild(heading);
    if (detail) {
      const text = document.createElement("div");
      text.className = "live-log-detail";
      text.textContent = detail;
      body.appendChild(text);
    }

    entry.append(time, dot, body);
    host.appendChild(entry);
    state.live.eventCount += 1;
    while (host.children.length > 400) host.firstElementChild.remove();
    setText(
      "live-log-count",
      `${state.live.eventCount} event${state.live.eventCount === 1 ? "" : "s"}`
    );
    if (stick) liveScroll(host);
  }

  function liveAddBubble(role, label, turn, store) {
    const host = $("#live-transcript");
    if (!host) return null;
    const bubble = createBubble(role, label);
    if (turn !== null && turn !== undefined) bubble.dataset.turn = String(turn);
    host.appendChild(bubble);
    const empty = $("#live-transcript-empty");
    if (empty) empty.hidden = true;
    if (store) store.set(String(turn ?? "last"), bubble);
    return bubble;
  }

  function liveUpsertUser(text, final, turn) {
    if (!text) return;
    const host = $("#live-transcript");
    if (!host) return;
    const stick = host.scrollHeight - host.scrollTop - host.clientHeight < 80;
    const key = String(turn ?? "last");
    let bubble = state.live.userBubbles.get(key);

    if (!bubble || bubble.dataset.final === "1") {
      bubble = liveAddBubble(
        "user",
        turn === null || turn === undefined ? "Caller" : `Caller · turn ${turn}`,
        turn,
        state.live.userBubbles
      );
    }
    if (!bubble) return;

    bubble.querySelector("p").textContent = text;
    bubble.classList.toggle("partial", !final);
    if (final) {
      bubble.dataset.final = "1";
      const entry = liveTurnEntry(turn);
      entry.user = text;
      liveUpdateTurnCount();
    }
    if (stick) liveScroll(host);
  }

  function liveAppendAssistant(text, turn) {
    if (!text) return;
    const host = $("#live-transcript");
    if (!host) return;
    const stick = host.scrollHeight - host.scrollTop - host.clientHeight < 80;
    const key = String(turn ?? "last");
    let bubble = state.live.agentBubbles.get(key);

    if (!bubble || bubble.dataset.final === "1") {
      bubble = liveAddBubble(
        "agent",
        turn === null || turn === undefined ? "Agent" : `Agent · turn ${turn}`,
        turn,
        state.live.agentBubbles
      );
    }
    if (!bubble) return;

    bubble.querySelector("p").textContent += text;
    bubble.classList.add("partial");
    liveTurnEntry(turn).assistant = bubble.querySelector("p").textContent;
    liveUpdateTurnCount();
    if (stick) liveScroll(host);
  }

  function liveFinalizeAssistant(text, turn) {
    if (!text) return;
    const host = $("#live-transcript");
    if (!host) return;
    const stick = host.scrollHeight - host.scrollTop - host.clientHeight < 80;
    const key = String(turn ?? "last");
    let bubble = state.live.agentBubbles.get(key);

    if (!bubble) {
      bubble = liveAddBubble(
        "agent",
        turn === null || turn === undefined ? "Agent" : `Agent · turn ${turn}`,
        turn,
        state.live.agentBubbles
      );
    }
    if (!bubble) return;

    bubble.querySelector("p").textContent = text;
    bubble.classList.remove("partial");
    bubble.dataset.final = "1";
    liveTurnEntry(turn).assistant = text;
    liveUpdateTurnCount();
    if (stick) liveScroll(host);
  }

  function liveMarkInterrupted(turn) {
    let bubble = null;
    if (turn !== null && turn !== undefined) {
      bubble = state.live.agentBubbles.get(String(turn)) || null;
    } else {
      const bubbles = $$("#live-transcript .bubble.agent");
      bubble = bubbles.length ? bubbles[bubbles.length - 1] : null;
    }
    if (!bubble) return;
    bubble.classList.remove("partial");
    bubble.classList.add("interrupted");
    bubble.dataset.final = "1";
    if (turn !== null && turn !== undefined) {
      liveTurnEntry(turn).interrupted = true;
    }
  }

  function liveAttachMetrics(turn, latency) {
    if (turn === null || turn === undefined) return;
    renderBubbleMetrics(state.live.agentBubbles.get(String(turn)) || null, latency);
    liveTurnEntry(turn).latency = latency;
  }

  function liveStatus(key) {
    const pill = $("#live-status");
    if (!pill) return;
    const [label, cls] = stateMeta(key);
    pill.className = `status-pill ${cls}`;
    pill.replaceChildren(makeDot(), document.createTextNode(label));
  }

  function renderLiveUsage(usage, costs) {
    const data = usage || {};
    const prompt = Number(data.llm_prompt_tokens) || 0;
    const completion = Number(data.llm_completion_tokens) || 0;
    const duration = Number(data.duration_seconds);
    if (Number.isFinite(duration) && duration > 0) {
      state.live.duration = Math.max(state.live.duration, duration);
    }
    setText(
      "live-duration",
      clock(
        Number.isFinite(duration) && duration > 0 ? duration : liveElapsedSeconds()
      )
    );
    setText(
      "live-asr",
      formatAudio(data.asr_session_seconds ?? data.asr_seconds ?? 0)
    );
    setText("live-gen-chars", formatNumber(data.llm_output_characters || 0));
    setText("live-tts-chars", formatNumber(data.tts_characters || 0));
    setText("live-tokens", formatNumber(prompt + completion));
    setText("live-usd", costs ? money(costs.total) : "—");
    setText("live-inr", costs ? moneyInr(inrFor(costs)) : "—");
  }

  function liveCaller(event) {
    if (event.direction === "outbound") return event.to_number || "Outbound call";
    if (event.direction === "inbound") return event.from_number || "Inbound caller";
    return event.from_number || "Browser caller";
  }

  function beginLiveCall(event) {
    const live = state.live;
    if (live.active && live.callId === event.call_id) return;
    if (live.timer) {
      clearInterval(live.timer);
      live.timer = 0;
    }

    live.callId = event.call_id || "";
    live.direction = event.direction || "inbound";
    live.startedAt = Date.now();
    live.duration = 0;
    live.fromNumber = event.from_number || "";
    live.toNumber = event.to_number || "";
    live.active = true;
    live.eventCount = 0;
    live.turns.clear();
    live.agentBubbles.clear();
    live.userBubbles.clear();

    const transcript = $("#live-transcript");
    if (transcript) {
      transcript.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "live-empty";
      empty.id = "live-transcript-empty";
      empty.textContent =
        "Caller and agent turns appear here as the call progresses.";
      transcript.appendChild(empty);
    }
    const log = $("#live-log");
    if (log) log.replaceChildren();

    setText("live-turn-count", "0 turns");
    setText("live-log-count", "0 events");
    setText("live-timer", "00:00");
    setText("live-duration", "00:00");
    setText("live-asr", "0.0 s");
    setText("live-gen-chars", "0");
    setText("live-tts-chars", "0");
    setText("live-tokens", "0");
    setText("live-usd", "—");
    setText("live-inr", "—");

    const direction = directionLabel(live.direction);
    setText("live-direction", direction);
    const badge = $("#live-badge");
    if (badge) badge.className = `live-badge${live.direction === "outbound" ? " outbound" : ""}`;
    const caller = liveCaller(event);
    setText("live-caller", caller);

    const asrModel =
      (state.config && state.config.asr_speech_model) ||
      (state.health && state.health.models && state.health.models.asr) ||
      "AssemblyAI";
    const meta = [
      `AssemblyAI ${asrModel}`,
      `Sarvam ${event.sarvam_model || "105B"}`,
      `${ttsProviderLabel(event.tts_provider)} ${event.tts_model || ""}`.trim(),
      event.voice_id ? `voice ${event.voice_id}` : "",
    ]
      .filter(Boolean)
      .join(" · ");
    setText("live-meta", meta);

    liveStatus("INITIALIZING");
    const hangup = $("#live-hangup");
    if (hangup) {
      hangup.disabled = false;
      hangup.textContent = "End call";
    }

    syncLiveView();

    if (location.hash !== "#live") {
      location.hash = "#live";
    } else {
      route();
    }

    liveLog("state", "Call connected", `${direction} · ${caller}`);
    live.timer = setInterval(() => {
      if (!state.live.active) return;
      const elapsed = clock(
        Math.max(liveElapsedSeconds(), state.live.duration || 0)
      );
      setText("live-timer", elapsed);
      setText("live-duration", elapsed);
    }, 1000);
  }

  function endLiveCall(event) {
    const live = state.live;
    if (!live.callId || (event.call_id && event.call_id !== live.callId)) return;
    const wasActive = live.active;
    live.active = false;
    if (live.timer) {
      clearInterval(live.timer);
      live.timer = 0;
    }

    const duration =
      event.duration_seconds !== null && event.duration_seconds !== undefined
        ? Number(event.duration_seconds)
        : liveElapsedSeconds();
    live.duration = Math.max(state.live.duration || 0, Number(duration) || 0);
    setText("live-timer", clock(duration));
    setText("live-duration", clock(duration));
    liveStatus("ENDED");
    if (event.usage) renderLiveUsage(event.usage, event.costs);
    setText(
      "live-meta",
      `Call ended · ${reasonLabel(event.reason)} · ${Number(duration || 0).toFixed(1)} s`
    );

    const hangup = $("#live-hangup");
    if (hangup) {
      hangup.disabled = true;
      hangup.textContent = "End call";
    }
    syncLiveView();

    if (wasActive) {
      liveLog(
        "state",
        "Call ended",
        `${reasonLabel(event.reason)} · ${formatDuration(duration)}`
      );
    }
  }

  async function hangupLiveCall() {
    const live = state.live;
    if (!live.callId || !live.active) return;
    const button = $("#live-hangup");
    if (button) {
      button.disabled = true;
      button.textContent = "Ending…";
    }
    try {
      await api(`/api/calls/${encodeURIComponent(live.callId)}/hangup`, {
        method: "POST",
      });
      toast("Ending call…", "info", 2000);
    } catch (error) {
      toast(`Could not end the call: ${error.message}`, "err", 5000);
      if (button && live.active) {
        button.disabled = false;
        button.textContent = "End call";
      }
    }
  }

  // ------------------------------------------------------------ post-call view
  function directionPage(direction) {
    if (direction === "inbound") return "inbound";
    if (direction === "outbound") return "outbound";
    return "web";
  }

  function ttsProviderLabel(provider) {
    if (provider === "speechify") return "Speechify";
    if (provider === "sarvam") return "Sarvam";
    if (provider === "inworld") return "Inworld";
    return provider ? provider.replace(/^\w/, (c) => c.toUpperCase()) : "TTS";
  }

  function latencyStats(values) {
    const list = (values || [])
      .map((value) => Number(value))
      .filter((value) => Number.isFinite(value))
      .sort((a, b) => a - b);
    if (!list.length) return null;
    const sum = list.reduce((total, value) => total + value, 0);
    const pick = (percent) => {
      const index = Math.min(
        list.length - 1,
        Math.max(0, Math.ceil((percent / 100) * list.length) - 1)
      );
      return list[index];
    };
    return {
      count: list.length,
      avg: sum / list.length,
      p50: pick(50),
      p90: pick(90),
      min: list[0],
      max: list[list.length - 1],
    };
  }

  function renderCostSummary(record) {
    const costs = record.costs || {};
    const total = costs.total;
    const numericTotal = Number(total);
    const hasTotal = total !== null && total !== undefined && Number.isFinite(numericTotal);
    const hasSpend = hasTotal && numericTotal > 0;
    const rows = [
      {
        key: "asr",
        label: "Speech to text",
        detail: `${formatAudio(
          record.asr_billed_seconds || record.asr_seconds || 0
        )} session · AssemblyAI`,
        cost: costs.asr,
      },
      {
        key: "llm",
        label: "Language model",
        detail: `${formatNumber(record.llm_prompt_tokens || 0)} in (${formatNumber(record.llm_cached_tokens || 0)} cached) · ${formatNumber(record.llm_completion_tokens || 0)} out · Sarvam 105B`,
        cost: costs.llm,
      },
      {
        key: "tts",
        label: "Text to speech",
        detail: `${formatNumber(record.tts_characters || 0)} chars · ${ttsProviderLabel(
          record.tts_provider
        )}`,
        cost: costs.tts,
      },
    ];

    const bar = $("#summary-cost-bar");
    const legend = $("#summary-cost-legend");
    const rowsHost = $("#summary-cost-rows");
    if (bar) {
      bar.replaceChildren();
      bar.classList.toggle("empty", !hasSpend);
    }
    if (legend) legend.replaceChildren();

    if (hasSpend) {
      rows.forEach((row) => {
        const cost = Number(row.cost);
        if (!Number.isFinite(cost) || cost <= 0) return;
        const pct = (cost / numericTotal) * 100;
        if (bar) {
          const segment = document.createElement("span");
          segment.className = row.key;
          segment.style.width = `${Math.max(pct, 0.8)}%`;
          segment.title = `${row.label} · ${money(row.cost)} (${pct.toFixed(1)}%)`;
          bar.appendChild(segment);
        }
        if (legend) {
          const item = document.createElement("span");
          item.className = row.key;
          item.append(
            document.createElement("i"),
            document.createTextNode(
              `${row.label} ${pct < 1 ? pct.toFixed(1) : Math.round(pct)}%`
            )
          );
          legend.appendChild(item);
        }
      });
    }

    if (rowsHost) {
      rowsHost.replaceChildren();
      rows.forEach((row) => {
        const cost = row.cost;
        const container = document.createElement("div");
        container.className = "cost-row";

        const swatch = document.createElement("span");
        swatch.className = `cost-swatch ${row.key}`;

        const label = document.createElement("div");
        label.className = "cost-label";
        const strong = document.createElement("strong");
        strong.textContent = row.label;
        const detail = document.createElement("span");
        detail.textContent = row.detail;
        label.append(strong, detail);

        const value = document.createElement("div");
        value.className = "cost-value";
        const amount = document.createElement("strong");
        amount.textContent =
          cost === null || cost === undefined ? "not set" : money(cost);
        const share = document.createElement("span");
        const costMissing = cost === null || cost === undefined;
        share.textContent = costMissing
          ? "rate not set"
          : hasSpend
          ? `${
              Number(cost) / numericTotal < 0.01
                ? ((Number(cost) / numericTotal) * 100).toFixed(1)
                : Math.round((Number(cost) / numericTotal) * 100)
            }% of total`
          : "no billable spend";
        value.append(amount, share);

        container.append(swatch, label, value);
        rowsHost.appendChild(container);
      });
    }

    setText("summary-total", total === null || total === undefined ? "—" : money(total));
    setText(
      "summary-total-note",
      total === null || total === undefined ? "rates not configured" : "estimated total"
    );
    setText(
      "summary-cost-note",
      total === null || total === undefined
        ? "Rates are not configured, so only usage is shown."
        : "Static list rates (30 Oct 2026), excl. GST. ASR per session minute; LLM per token (input, cached, output); TTS per character."
    );
  }

  // Five totals that line up with Sarvam's invoice: duration, input, cached
  // input, output tokens and generated characters. Input includes the cached
  // part (that is how the API reports it); cached tokens are billed cheaper.
  function renderLlmUsage(prefix, record) {
    const grid = $(`#${prefix}-llm-grid`);
    if (!grid) return;
    const input = Number(record.llm_prompt_tokens) || 0;
    const cached = Math.min(Number(record.llm_cached_tokens) || 0, input);
    const output = Number(record.llm_completion_tokens) || 0;
    const chars = Number(record.llm_output_characters) || 0;
    const duration = Number(record.duration_seconds) || 0;
    const cachedShare = input > 0 ? Math.round((cached / input) * 100) : 0;
    const tiles = [
      ["Total duration", `${duration.toFixed(1)} s`, `${(duration / 60).toFixed(2)} min`],
      ["Total input tokens", formatNumber(input), `${formatNumber(input - cached)} uncached + cached`],
      ["Total cached input tokens", formatNumber(cached), `${cachedShare}% of input`],
      ["Total output tokens", formatNumber(output), "completion tokens"],
      ["Total characters", formatNumber(chars), "generated by the model"],
    ];
    grid.replaceChildren(
      ...tiles.map(([label, value, sub]) => {
        const item = document.createElement("div");
        const span = document.createElement("span");
        span.textContent = label;
        const strong = document.createElement("strong");
        strong.textContent = value;
        const em = document.createElement("em");
        em.textContent = sub;
        item.append(span, strong, em);
        return item;
      })
    );
    setText(`${prefix}-llm-pill`, `${formatNumber(input + output)} tokens`);

    // Show the arithmetic so the cost can be checked against the invoice.
    const rates = (state.pricing && state.pricing.rates) || {};
    const fx = Number((record.costs && record.costs.usd_inr) || rates.usd_inr || 95);
    const inr = (key) => (rates[key] === undefined || rates[key] === null ? null : rates[key] * fx);
    const rIn = inr("llm_input_per_1m_tokens");
    const rCached = inr("llm_cached_per_1m_tokens");
    const rOut = inr("llm_output_per_1m_tokens");
    const note = $(`#${prefix}-llm-note`);
    if (!note) return;
    if (rIn === null || rCached === null || rOut === null) {
      note.textContent = "";
    } else if (output > 0) {
      note.textContent =
        `LLM cost = ${formatNumber(input - cached)} uncached × ₹${rIn.toFixed(0)} + ` +
        `${formatNumber(cached)} cached × ₹${rCached.toFixed(0)} + ` +
        `${formatNumber(output)} output × ₹${rOut.toFixed(0)}  (per 1M tokens, excl. GST)`;
    } else {
      note.textContent = "No token counts were reported for this call; the LLM cost is estimated from characters.";
    }
  }

  function renderSummaryUsage(record) {
    renderLlmUsage("summary", record);
    const costs = record.costs || {};
    const prompt = Number(record.llm_prompt_tokens) || 0;
    const completion = Number(record.llm_completion_tokens) || 0;
    const generated = Number(record.llm_output_characters) || 0;
    const ttsChars = Number(record.tts_characters) || 0;
    const duration = Number(record.duration_seconds) || 0;
    const asr =
      Number(
        record.asr_billed_seconds !== undefined && record.asr_billed_seconds !== null
          ? record.asr_billed_seconds
          : record.asr_session_seconds || record.asr_seconds
      ) || 0;

    setText("summary-duration", `${duration.toFixed(1)} s`);
    setText("summary-asr", formatAudio(asr));
    setText("summary-gen-chars", formatNumber(generated));
    setText("summary-tts-chars", formatNumber(ttsChars));
    setText("summary-total-chars", formatNumber(generated + ttsChars));
    setText("summary-tokens", formatNumber(prompt + completion));
    setText(
      "summary-tokens-sub",
      `${formatNumber(prompt)} in · ${formatNumber(completion)} out`
    );
    setText("summary-usd", money(costs.total));
    setText("summary-inr", moneyInr(inrFor(costs)));
    const rate = Number(
      costs.usd_inr ||
        (state.pricing && state.pricing.rates && state.pricing.rates.usd_inr) ||
        95
    );
    setText("summary-inr-note", `at ₹${rate.toFixed(2)}/USD`);
    setText(
      "summary-usage-pill",
      `${formatNumber(generated + ttsChars)} chars · ${duration.toFixed(0)} s`
    );
  }

  function renderLatencySummary(record) {
    const turns = (record.turns || []).filter((turn) => turn.kind === "response");
    const metrics = [
      { label: "LLM TTFT", key: "llm_first_token_ms" },
      { label: "TTS net TTFA", key: "tts_net_ttfa_ms" },
      { label: "TTS raw TTFA", key: "tts_first_audio_ms" },
      { label: "First audio out", key: "first_audio_out_ms" },
      { label: "ASR endpoint", key: "asr_final_delay_ms" },
    ];
    const stats = {};
    metrics.forEach((metric) => {
      stats[metric.key] = latencyStats(turns.map((turn) => turn[metric.key]));
    });

    const rowsHost = $("#summary-latency-rows");
    if (rowsHost) {
      rowsHost.replaceChildren();
      metrics.forEach((metric) => {
        const data = stats[metric.key];
        const row = document.createElement("tr");
        const label = document.createElement("td");
        label.textContent = metric.label;
        row.appendChild(label);
        ["avg", "p50", "p90", "min", "max"].forEach((key) => {
          const cell = document.createElement("td");
          cell.textContent = data ? msText(data[key]) : "—";
          if (!data) cell.className = "muted";
          row.appendChild(cell);
        });
        rowsHost.appendChild(row);
      });
    }

    const endToEnd = stats.first_audio_out_ms;
    const llm = stats.llm_first_token_ms;
    const ttsNet = stats.tts_net_ttfa_ms;
    const ttsRaw = stats.tts_first_audio_ms;
    const interrupted = turns.filter((turn) => turn.interrupted).length;
    let description = "No completed response turns were recorded for this call.";
    if (turns.length) {
      const parts = [
        `Across ${turns.length} response turn${turns.length === 1 ? "" : "s"}, first audio reached the caller in ${msText(
          endToEnd && endToEnd.avg
        )} ms on average${
          endToEnd ? ` (p90 ${msText(endToEnd.p90)} ms)` : ""
        }.`,
      ];
      if (llm) parts.push(`LLM TTFT averaged ${msText(llm.avg)} ms.`);
      if (ttsNet) {
        parts.push(
          `TTS net TTFA averaged ${msText(ttsNet.avg)} ms after the first token` +
            (ttsRaw ? ` (raw ${msText(ttsRaw.avg)} ms from turn start).` : ".")
        );
      }
      if (interrupted) {
        parts.push(
          `${interrupted} turn${interrupted === 1 ? " was" : "s were"} interrupted by the caller.`
        );
      }
      description = parts.join(" ");
    }
    setText("summary-latency-desc", description);
    setText(
      "summary-turn-count",
      `${turns.length} turn${turns.length === 1 ? "" : "s"}`
    );
  }

  function renderSummaryTranscript(record) {
    const host = $("#summary-transcript");
    if (!host) return;
    host.replaceChildren();

    const turns = (record.turns || [])
      .slice()
      .sort((a, b) => Number(a.index || 0) - Number(b.index || 0));

    turns.forEach((turn) => {
      if (turn.user_text) {
        const bubble = createBubble("user", "Caller");
        bubble.querySelector("p").textContent = turn.user_text;
        if (turn.index !== null && turn.index !== undefined) {
          bubble.dataset.turn = String(turn.index);
        }
        host.appendChild(bubble);
      }
      if (turn.assistant_text) {
        const label =
          turn.kind === "greeting"
            ? "Agent · greeting"
            : turn.kind === "fallback"
            ? "Agent · fallback"
            : "Agent";
        const bubble = createBubble("agent", label);
        bubble.querySelector("p").textContent = turn.assistant_text;
        if (turn.interrupted) bubble.classList.add("interrupted");
        if (turn.index !== null && turn.index !== undefined) {
          bubble.dataset.turn = String(turn.index);
        }
        renderBubbleMetrics(bubble, turn);
        host.appendChild(bubble);
      }
    });

    if (!host.children.length) {
      const empty = document.createElement("p");
      empty.className = "summary-empty";
      empty.textContent =
        "No transcript was captured for this call. Turn text is available when the session closes normally.";
      host.appendChild(empty);
    }

    setText(
      "summary-transcript-count",
      `${turns.length} turn${turns.length === 1 ? "" : "s"}`
    );
  }

  function renderCallSummary(record) {
    const direction = record.direction || "browser";
    const dirLabel =
      direction === "inbound"
        ? "Inbound call"
        : direction === "outbound"
        ? "Outbound call"
        : "Web call";
    const who =
      direction === "inbound"
        ? record.from_number || "unknown caller"
        : direction === "outbound"
        ? record.to_number || "unknown destination"
        : "browser microphone";
    const status =
      record.status === "active"
        ? "In progress"
        : reasonLabel(record.end_reason || "Completed");
    setText("summary-title", record.status === "active" ? "Call in progress" : "Call complete");
    setText(
      "summary-subtitle",
      `${dirLabel} · ${who} · ${formatDuration(record.duration_seconds || 0)} · ${status}`
    );

    renderCostSummary(record);
    renderSummaryUsage(record);
    renderLatencySummary(record);
    renderSummaryTranscript(record);

    const hasTurns = Boolean((record.turns || []).length);
    const toggle = $("#summary-toggle");
    if (toggle) toggle.hidden = !hasTurns;
  }

  async function openCallSummary(callId, event = null) {
    if (!callId) return;
    const token = (state.summaryRequest || 0) + 1;
    state.summaryRequest = token;

    let record = null;
    try {
      record = await api(`/api/calls/${encodeURIComponent(callId)}`);
    } catch {
      record = null;
    }
    if (token !== state.summaryRequest) return;

    if (!record) {
      const usage = (event && event.usage) || {};
      const live = callId === state.live.callId ? state.live : null;
      record = {
        call_id: callId,
        direction:
          (event && event.direction) || state.callDirections.get(callId) || "browser",
        status: "completed",
        duration_seconds: (event && event.duration_seconds) || 0,
        end_reason: (event && event.reason) || "ended",
        from_number: (event && event.from_number) || (live && live.fromNumber) || "",
        to_number: (event && event.to_number) || (live && live.toNumber) || "",
        tts_provider: (state.config && state.config.tts_provider) || "",
        asr_seconds: usage.asr_seconds || 0,
        asr_session_seconds: usage.asr_session_seconds || 0,
        asr_billed_seconds: usage.asr_session_seconds || usage.asr_seconds || 0,
        llm_prompt_tokens: usage.llm_prompt_tokens || 0,
        llm_completion_tokens: usage.llm_completion_tokens || 0,
        llm_output_characters: usage.llm_output_characters || 0,
        tts_characters: usage.tts_characters || 0,
        tts_audio_seconds: usage.tts_audio_seconds || 0,
        costs: (event && event.costs) || {},
        turns: live
          ? Array.from(live.turns.entries()).map(([key, entry]) => ({
              ...(entry.latency || {}),
              index: Number(key),
              turn_id: `t${key}`,
              kind: "response",
              user_text: entry.user,
              assistant_text: entry.assistant,
              interrupted: Boolean(entry.interrupted),
            }))
          : [],
        errors: [],
      };
    }

    state.summary = { record };
    renderCallSummary(record);
    showSummaryModal();
  }

  function showSummaryModal() {
    const modal = $("#summary-modal");
    if (!modal) return;
    modal.hidden = false;
    document.body.classList.add("modal-open");
    setSummaryView("overview");
    const back = $("#summary-back");
    if (back) back.focus();
  }

  function setSummaryView(view) {
    const overview = $("#summary-overview");
    const transcript = $("#summary-transcript-view");
    const isTranscript = view === "transcript";
    if (overview) overview.hidden = isTranscript;
    if (transcript) transcript.hidden = !isTranscript;
    const toggle = $("#summary-toggle");
    if (toggle) toggle.textContent = isTranscript ? "Back to summary" : "View transcript";
    if (isTranscript) {
      const host = $("#summary-transcript");
      if (host) host.scrollTop = 0;
    }
  }

  function closeCallSummary({ navigate = true } = {}) {
    const summary = state.summary;
    const modal = $("#summary-modal");
    if (modal) modal.hidden = true;
    document.body.classList.remove("modal-open");
    state.summary = null;
    if (!navigate || !summary) return;
    const page = directionPage(summary.record.direction);
    if (location.hash !== `#${page}`) {
      location.hash = `#${page}`;
    } else {
      route();
    }
  }

  // --------------------------------------------------------------- event stream
  const noticeCooldowns = new Map();

  function notifyOnce(key, message, kind = "warn", cooldown = 8000) {
    const now = Date.now();
    if (now - (noticeCooldowns.get(key) || 0) < cooldown) return;
    noticeCooldowns.set(key, now);
    toast(message, kind, 3600);
  }

  function connectEvents() {
    if (
      state.eventWs &&
      (state.eventWs.readyState === WebSocket.OPEN ||
        state.eventWs.readyState === WebSocket.CONNECTING)
    ) {
      return;
    }

    let socket;
    try {
      socket = new WebSocket(wsUrl("/ws/events"));
    } catch {
      scheduleEventReconnect();
      return;
    }
    state.eventWs = socket;

    socket.onopen = () => {
      state.eventConnected = true;
      state.eventRetry = 0;
      renderChips();
      loadStats();
    };

    socket.onmessage = (message) => {
      let event = null;
      try {
        event = JSON.parse(message.data);
      } catch {
        return;
      }
      if (!event || !event.type) return;
      if (event.type === "hello") {
        state.eventConnected = true;
        if (event.warmup) state.warmup = event.warmup;
        renderChips();
        return;
      }
      if (event.type === "ping") return;
      handleEvent(event);
    };

    socket.onclose = () => {
      if (state.eventWs === socket) state.eventWs = null;
      state.eventConnected = false;
      renderChips();
      scheduleEventReconnect();
    };

    socket.onerror = () => {
      try {
        socket.close();
      } catch {
        // onclose schedules the retry.
      }
    };
  }

  function scheduleEventReconnect() {
    if (state.eventRetryTimer) return;
    const delay = Math.min(15000, 1000 * 2 ** Math.min(state.eventRetry, 4));
    state.eventRetry += 1;
    state.eventRetryTimer = setTimeout(() => {
      state.eventRetryTimer = null;
      connectEvents();
    }, delay);
  }

  function handleEvent(event) {
    const type = event.type;
    const callId = event.call_id || "";
    const isWeb = Boolean(call.callId && callId === call.callId);
    if (callId && event.direction) state.callDirections.set(callId, event.direction);

    switch (type) {
      case "call_started": {
        if (callId) {
          state.activeCalls.add(callId);
          state.callStates.set(callId, "INITIALIZING");
        }
        if (event.direction === "inbound" || event.direction === "outbound") {
          beginLiveCall(event);
        }
        renderActivity("inbound");
        renderActivity("outbound");
        scheduleStats();
        return;
      }

      case "call_state": {
        if (callId) state.callStates.set(callId, event.state);
        if (isWeb) {
          updateCallStatus(event.state);
          if (event.state === "LISTENING") setText("mic-hint", "Streaming 8 kHz μ-law");
        } else if (liveIsCurrent(callId)) {
          liveStatus(event.state);
          liveLog(
            "state",
            `State · ${stateMeta(event.state)[0]}`,
            event.state === "SPEAKING"
              ? "Agent audio is playing."
              : event.state === "THINKING"
              ? "Waiting for the language model."
              : ""
          );
        }
        renderActivity("inbound");
        renderActivity("outbound");
        return;
      }

      case "transcript": {
        if (!isWeb && !liveIsCurrent(callId)) return;
        const text = (event.text || "").trim();
        if (!text) return;
        if (event.role === "user") {
          if (isWeb) {
            upsertUserBubble(text, Boolean(event.final), event.turn);
          } else {
            liveUpsertUser(text, Boolean(event.final), event.turn);
          }
        } else if (event.role === "assistant" && event.final) {
          if (isWeb) {
            finalizeAssistant(text, event.turn);
          } else {
            liveFinalizeAssistant(text, event.turn);
          }
        }
        return;
      }

      case "assistant_delta": {
        if (isWeb) appendAssistantDelta(event.text || "", event.turn);
        else if (liveIsCurrent(callId)) liveAppendAssistant(event.text || "", event.turn);
        return;
      }

      case "turn_complete": {
        if (isWeb) {
          if (event.kind === "response") renderLatency(event.latency);
          attachBubbleMetrics(event.turn, event.latency);
          if (event.interrupted) markAgentInterrupted(event.turn);
        } else if (liveIsCurrent(callId)) {
          liveAttachMetrics(event.turn, event.latency);
          if (event.interrupted) liveMarkInterrupted(event.turn);
          if (event.kind === "response" && event.latency) {
            const latency = event.latency;
            liveLog(
              "turn",
              `Turn ${event.turn} complete${event.interrupted ? " · interrupted" : ""}`,
              `LLM TTFT ${msText(latency.llm_first_token_ms)} ms · TTS net TTFA ${msText(
                latency.tts_net_ttfa_ms ?? latency.tts_first_audio_ms
              )} ms`,
              event.interrupted ? "barge-in" : ""
            );
          }
        }
        return;
      }

      case "usage": {
        if (isWeb) renderUsage(event.usage, event.costs);
        else if (liveIsCurrent(callId)) renderLiveUsage(event.usage, event.costs);
        scheduleStats();
        return;
      }

      case "barge_in": {
        if (isWeb) {
          clearPlayback();
          markAgentInterrupted();
          toast("Barge-in · playback cleared.", "info", 2000);
        } else if (liveIsCurrent(callId)) {
          liveMarkInterrupted(event.turn);
          liveLog(
            "barge",
            "Barge-in · caller interrupted",
            `Cleared ${event.cleared ? "Plivo audio" : "audio (clear failed)"} · ${Math.round(
              Number(event.playout_remaining_ms) || 0
            )} ms queued at detection`,
            "clearAudio"
          );
        }
        return;
      }

      case "speech_started": {
        if (liveIsCurrent(callId)) {
          const confidence =
            event.confidence === null || event.confidence === undefined
              ? ""
              : ` · confidence ${Number(event.confidence).toFixed(2)}`;
          liveLog(
            event.interrupted ? "speech barge" : "speech",
            event.interrupted ? "Speech detected · interrupting agent" : "Speech detected",
            `state ${event.state}${confidence}${
              event.playout_remaining_ms
                ? ` · ${Math.round(Number(event.playout_remaining_ms))} ms playout left`
                : ""
            }`
          );
        }
        return;
      }

      case "turn_started": {
        if (liveIsCurrent(callId)) {
          liveTurnEntry(event.turn);
          liveUpdateTurnCount();
          liveLog(
            "turn",
            `Turn ${event.turn} · caller finished`,
            `${event.user_text || ""}${
              event.asr_final_delay_ms !== null && event.asr_final_delay_ms !== undefined
                ? ` · ASR endpoint ${msText(event.asr_final_delay_ms)} ms`
                : ""
            }`
          );
        }
        return;
      }

      case "llm_first_token": {
        if (liveIsCurrent(callId)) {
          liveLog(
            "llm",
            `LLM first token · turn ${event.turn}`,
            `${msText(event.llm_first_token_ms)} ms after the caller stopped`
          );
        }
        return;
      }

      case "tts_first_audio": {
        if (liveIsCurrent(callId)) {
          liveLog(
            "tts",
            `TTS first audio · turn ${event.turn}`,
            `net TTFA ${msText(event.tts_net_ttfa_ms)} ms · raw from turn start ${msText(
              event.tts_first_audio_ms
            )} ms`
          );
        }
        return;
      }

      case "playout_complete": {
        if (liveIsCurrent(callId)) {
          liveLog(
            "playout",
            `Playout complete · ${event.name || "audio"}`,
            `${Number(event.playout_seconds || 0).toFixed(2)} s of audio`
          );
        }
        return;
      }

      case "call_ended": {
        // The call is written to SQLite in the background; refresh shortly after.
        if (state.page === "cost" && !location.hash.includes("/")) setTimeout(loadHistory, 1500);
        const summarize = Boolean(callId);
        if (callId) state.lastCall = { callId, event };
        if (callId) {
          state.activeCalls.delete(callId);
          state.callStates.set(callId, "ENDED");
        }
        renderActivity("inbound");
        renderActivity("outbound");
        if (isWeb) {
          if (event.usage) renderUsage(event.usage, event.costs);
          if (event.duration_seconds !== null && event.duration_seconds !== undefined) {
            setText("call-timer", clock(event.duration_seconds));
          }
          finishWebCall("event", event);
        } else {
          endLiveCall(event);
          scheduleStats(400);
        }
        if (summarize) openCallSummary(callId, event);
        return;
      }

      case "error": {
        const stage = event.stage ? `[${event.stage}] ` : "";
        if (liveIsCurrent(callId)) {
          liveLog("error", `Error${event.stage ? ` · ${event.stage}` : ""}`, event.message || "Provider error");
        }
        toast(`${stage}${event.message || "Provider error"}`, "err", 6000);
        return;
      }

      case "provider_status": {
        if (liveIsCurrent(callId)) {
          liveLog(
            event.status === "reconnecting" ? "error" : "state",
            `${(event.stage || "provider").toUpperCase()} · ${event.status || "status"}`,
            event.message || ""
          );
        }
        if (event.status === "reconnecting") {
          notifyOnce(
            `provider-${event.stage || "unknown"}`,
            event.message || `${(event.stage || "provider").toUpperCase()} reconnecting…`,
            "warn"
          );
        }
        return;
      }

      case "warmup": {
        if (event.snapshot) state.warmup = event.snapshot;
        renderChips();
        if (!event.provider && event.status === "complete") {
          const failed = Object.entries((state.warmup && state.warmup.providers) || {})
            .filter(([, info]) => info && info.status === "error")
            .map(([name]) => name);
          if (failed.length) {
            notifyOnce(
              "warmup-failed",
              `Warm-up: ${failed.join(", ")} failed. Check server logs.`,
              "warn",
              60000
            );
          }
        }
        return;
      }

      case "plivo_status": {
        const status = String(event.call_status || "").toLowerCase();
        if (["failed", "busy", "no-answer", "canceled", "cancelled"].includes(status)) {
          toast(`Plivo call ${status}.`, "err", 5000);
        }
        scheduleStats(700);
        return;
      }

      case "idle_reprompt": {
        if (isWeb) toast("The agent is checking in — still listening.", "info", 3200);
        else if (liveIsCurrent(callId)) {
          liveLog("state", "Idle check-in", "No caller speech within the idle window.");
        }
        return;
      }

      case "dtmf": {
        if (liveIsCurrent(callId)) liveLog("turn", `Keypad · ${event.digit}`, "DTMF digit received.");
        if (event.digit) toast(`Keypad: ${event.digit}`, "info", 2000);
        return;
      }

      default:
        return;
    }
  }

  // ------------------------------------------------------- browser audio codec
  const MIC_WORKLET = `
class MicCaptureProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.frameSize = opts.frameSize || 2048;
    this.buffer = new Float32Array(this.frameSize);
    this.offset = 0;
  }
  process(inputs) {
    const input = inputs[0];
    const channel = input && input[0];
    if (!channel) return true;
    for (let i = 0; i < channel.length; i += 1) {
      this.buffer[this.offset] = channel[i];
      this.offset += 1;
      if (this.offset === this.frameSize) {
        const frame = this.buffer;
        this.port.postMessage({ type: "audio", data: frame }, [frame.buffer]);
        this.buffer = new Float32Array(this.frameSize);
        this.offset = 0;
      }
    }
    return true;
  }
}
registerProcessor("mic-capture", MicCaptureProcessor);
`;

  const MIC_SHAPE = [
    0.35, 0.55, 0.75, 0.95, 0.65, 0.45, 0.7, 1, 0.8, 0.5, 0.35, 0.6, 0.9, 1,
    0.75, 0.5, 0.4, 0.65, 0.95, 0.7, 0.45, 0.8, 1, 0.6, 0.35, 0.55, 0.85, 0.5,
  ];
  const MIC_FRAME_BYTES = 320; // 40 ms of 8 kHz μ-law

  function linearToMulaw(sample) {
    const BIAS = 0x84;
    const CLIP = 32635;
    let value = sample | 0;
    let sign = (value >> 8) & 0x80;
    if (sign) value = -value;
    if (value > CLIP) value = CLIP;
    value += BIAS;

    let exponent = 7;
    for (let mask = 0x4000; (value & mask) === 0 && exponent > 0; exponent -= 1, mask >>= 1) {
      // Find the segment.
    }
    const mantissa = (value >> (exponent + 3)) & 0x0f;
    return (~(sign | (exponent << 4) | mantissa)) & 0xff;
  }

  function floatToMulawBytes(samples) {
    const out = new Uint8Array(samples.length);
    for (let i = 0; i < samples.length; i += 1) {
      const sample = Math.max(-1, Math.min(1, samples[i]));
      const pcm = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
      out[i] = linearToMulaw(pcm);
    }
    return out;
  }

  function mulawToFloat32(bytes) {
    const out = new Float32Array(bytes.length);
    const BIAS = 0x84;
    for (let i = 0; i < bytes.length; i += 1) {
      const value = ~bytes[i] & 0xff;
      const sign = value & 0x80;
      const exponent = (value >> 4) & 0x07;
      const mantissa = value & 0x0f;
      let sample = ((mantissa << 3) + BIAS) << exponent;
      sample -= BIAS;
      out[i] = (sign ? -sample : sample) / 32768;
    }
    return out;
  }

  function bytesToBase64(bytes) {
    let binary = "";
    const step = 0x8000;
    for (let i = 0; i < bytes.length; i += step) {
      binary += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
    }
    return btoa(binary);
  }

  function base64ToBytes(text) {
    const binary = atob(text);
    const out = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) out[i] = binary.charCodeAt(i);
    return out;
  }

  class LinearResampler {
    constructor(inRate, outRate) {
      this.inRate = inRate;
      this.outRate = outRate;
      this.ratio = inRate / outRate;
      this.buffer = new Float32Array(0);
      this.position = 0;
    }

    process(input) {
      if (!input || !input.length) return new Float32Array(0);
      const combined = new Float32Array(this.buffer.length + input.length);
      combined.set(this.buffer);
      combined.set(input, this.buffer.length);
      if (combined.length < 2) {
        this.buffer = combined;
        return new Float32Array(0);
      }

      let position = this.position;
      const count = Math.max(
        0,
        Math.floor((combined.length - 1 - position) / this.ratio) + 1
      );
      const out = new Float32Array(count);
      for (let i = 0; i < count; i += 1) {
        const index = Math.floor(position);
        const fraction = position - index;
        out[i] =
          combined[index] * (1 - fraction) + combined[index + 1] * fraction;
        position += this.ratio;
      }

      const keep = Math.min(Math.floor(position), combined.length - 1);
      this.buffer = combined.slice(keep);
      this.position = position - keep;
      return out;
    }
  }

  // ------------------------------------------------------------ microphone path
  function buildMicBars() {
    const host = $("#mic-bars");
    if (!host) return;
    host.replaceChildren();
    for (let i = 0; i < MIC_SHAPE.length; i += 1) {
      const bar = document.createElement("span");
      bar.style.height = "3px";
      host.appendChild(bar);
    }
  }

  function renderMicBars(level) {
    const bars = $$("#mic-bars span");
    const value = Math.max(0, Math.min(1, Number(level) || 0));
    bars.forEach((bar, index) => {
      const shape = MIC_SHAPE[index % MIC_SHAPE.length];
      bar.style.height = `${(3 + value * 18 * shape).toFixed(1)}px`;
      bar.style.background =
        value > 0.025
          ? "linear-gradient(180deg, #2dd4bf, #0d9488)"
          : "#1d2d28";
    });
  }

  function startMicMeter() {
    stopMicMeter();
    let display = 0;
    const tick = () => {
      display = Math.max(call.micLevel, display * 0.86);
      renderMicBars(display);
      call.meterRaf = requestAnimationFrame(tick);
    };
    call.meterRaf = requestAnimationFrame(tick);
    const dot = $("#mic-dot");
    if (dot) dot.className = "dot teal";
  }

  function stopMicMeter() {
    if (call.meterRaf) cancelAnimationFrame(call.meterRaf);
    call.meterRaf = 0;
    call.micLevel = 0;
    renderMicBars(0);
    const dot = $("#mic-dot");
    if (dot) dot.className = "dot";
  }

  async function setupCapture(stream, ctx) {
    call.mediaStream = stream;
    call.audioCtx = ctx;

    const source = ctx.createMediaStreamSource(stream);
    const filter = ctx.createBiquadFilter();
    filter.type = "lowpass";
    filter.frequency.value = 3400;
    filter.Q.value = 0.707;

    const mute = ctx.createGain();
    mute.gain.value = 0;
    mute.connect(ctx.destination);

    const playGain = ctx.createGain();
    playGain.gain.value = 1;
    playGain.connect(ctx.destination);

    call.source = source;
    call.filter = filter;
    call.muteGain = mute;
    call.playGain = playGain;

    let node = null;
    if (ctx.audioWorklet && typeof AudioWorkletNode !== "undefined") {
      try {
        const blob = new Blob([MIC_WORKLET], { type: "application/javascript" });
        const url = URL.createObjectURL(blob);
        try {
          await ctx.audioWorklet.addModule(url);
        } finally {
          URL.revokeObjectURL(url);
        }
        node = new AudioWorkletNode(ctx, "mic-capture", {
          numberOfInputs: 1,
          numberOfOutputs: 1,
          outputChannelCount: [1],
          processorOptions: { frameSize: 2048 },
        });
        node.port.onmessage = (event) => {
          const data = event.data;
          if (data && data.type === "audio" && data.data) onMicChunk(data.data);
        };
      } catch (error) {
        console.warn("AudioWorklet unavailable, using ScriptProcessor.", error);
        node = null;
      }
    }

    if (!node) node = createScriptCapture(ctx);
    source.connect(filter);
    filter.connect(node);
    node.connect(mute);
    call.captureNode = node;
    startMicMeter();
  }

  function createScriptCapture(ctx) {
    const node = ctx.createScriptProcessor(2048, 1, 1);
    node.onaudioprocess = (event) => {
      const channel = event.inputBuffer.getChannelData(0);
      onMicChunk(new Float32Array(channel));
    };
    return node;
  }

  function onMicChunk(samples) {
    if (!samples || !samples.length) return;

    let sum = 0;
    for (let i = 0; i < samples.length; i += 1) sum += samples[i] * samples[i];
    call.micLevel = Math.min(1, Math.sqrt(sum / samples.length) * 7.5);

    if (!call.audioCtx) return;
    if (!call.resampler) {
      call.resampler = new LinearResampler(call.audioCtx.sampleRate, 8000);
    }

    const resampled = call.resampler.process(samples);
    if (!resampled.length) return;
    const ulaw = floatToMulawBytes(resampled);

    if (!call.ready || !call.ws || call.ws.readyState !== WebSocket.OPEN) return;

    const merged = new Uint8Array(call.pending.length + ulaw.length);
    merged.set(call.pending);
    merged.set(ulaw, call.pending.length);
    call.pending = merged;

    let offset = 0;
    while (call.pending.length - offset >= MIC_FRAME_BYTES) {
      const frame = call.pending.subarray(offset, offset + MIC_FRAME_BYTES);
      try {
        call.ws.send(
          JSON.stringify({ type: "audio", payload: bytesToBase64(frame) })
        );
      } catch {
        break;
      }
      offset += MIC_FRAME_BYTES;
    }
    if (offset) call.pending = call.pending.slice(offset);
  }

  // ------------------------------------------------------------- playback path
  function playMulaw(payload) {
    if (!payload || !call.audioCtx) return;
    const ctx = call.audioCtx;
    if (ctx.state === "suspended") ctx.resume().catch(() => {});

    let bytes;
    try {
      bytes = base64ToBytes(payload);
    } catch {
      return;
    }
    if (!bytes.length) return;

    const floats = mulawToFloat32(bytes);
    const buffer = ctx.createBuffer(1, floats.length, 8000);
    buffer.copyToChannel(floats, 0);

    const source = ctx.createBufferSource();
    source.buffer = buffer;
    source.connect(call.playGain || ctx.destination);

    const now = ctx.currentTime;
    if (call.nextPlayTime < now + 0.02) call.nextPlayTime = now + 0.05;
    source.start(call.nextPlayTime);
    call.nextPlayTime += buffer.duration;

    call.playback.add(source);
    source.onended = () => {
      call.playback.delete(source);
      try {
        source.disconnect();
      } catch {
        // Already disconnected.
      }
    };
  }

  function clearPlayback() {
    for (const source of Array.from(call.playback)) {
      try {
        source.stop();
      } catch {
        // Already stopped.
      }
      try {
        source.disconnect();
      } catch {
        // Already disconnected.
      }
    }
    call.playback.clear();
    call.nextPlayTime = 0;
  }

  function handleServerMessage(message) {
    switch (message.type) {
      case "audio": {
        if (message.encoding && message.encoding !== "mulaw") return;
        playMulaw(message.payload);
        return;
      }
      case "clear": {
        clearPlayback();
        return;
      }
      case "ended": {
        call.serverEnded = true;
        if (call.active || call.starting) finishWebCall("server", null);
        else closeBrowserSocket();
        return;
      }
      case "error": {
        toast(message.message || "Voice agent error.", "err", 6000);
        return;
      }
      default:
        return;
    }
  }

  // --------------------------------------------------------------- call control
  async function startWebCall() {
    if (call.active || call.starting) return;
    if (state.summary) closeCallSummary({ navigate: false });
    if (location.protocol === "file:") {
      toast("Open the console through FastAPI (http://localhost:8000), not as a file.", "err", 7000);
      return;
    }

    call.starting = true;
    call.serverEnded = false;
    resetCallPanels();
    syncStartButton();

    let ctx = null;
    try {
      if (!window.isSecureContext) {
        throw new Error("Microphone access needs https:// or http://localhost.");
      }
      if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        throw new Error("This browser cannot capture microphone audio.");
      }
      const AudioContextClass = window.AudioContext || window.webkitAudioContext;
      if (!AudioContextClass) throw new Error("Web Audio is not supported in this browser.");

      ctx = new AudioContextClass();
      ctx.resume().catch(() => {});
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
        },
      });

      await setupCapture(stream, ctx);
      if (ctx.state === "suspended") await ctx.resume().catch(() => {});
      await maybeApplyVoice();

      const ready = await openBrowserSocket();
      call.callId = ready.call_id;
      call.ready = true;
      call.active = true;
      call.starting = false;
      state.webCallId = ready.call_id;
      state.activeCalls.add(ready.call_id);
      state.callDirections.set(ready.call_id, "browser");
      state.callStates.set(ready.call_id, "LISTENING");

      showLiveCall();
      startCallTimer();
      updateCallStatus("LISTENING");
      setText("mic-hint", "Streaming 8 kHz μ-law");
      syncStartButton();
      toast("Connected · speak naturally, you can interrupt the agent.", "ok", 3500);
    } catch (error) {
      console.error(error);
      cleanupAudioCall();
      closeBrowserSocket();
      call.starting = false;
      call.active = false;
      call.ready = false;
      call.callId = null;
      state.webCallId = null;
      hideLiveCall();
      updateCallStatus("ENDED");
      syncStartButton();
      toast(error.message || "Could not start the call.", "err", 7000);
    }
  }

  function openBrowserSocket() {
    return new Promise((resolve, reject) => {
      const dialSelect = $("#dial-select");
      const dial = dialSelect ? dialSelect.value : "browser";
      const socket = new WebSocket(wsUrl("/ws/browser"));
      call.ws = socket;
      let settled = false;

      const timeout = setTimeout(() => {
        if (settled) return;
        settled = true;
        try {
          socket.close();
        } catch {
          // Already closed.
        }
        reject(new Error("The voice agent did not respond in time. Check the server logs."));
      }, 20000);

      socket.onopen = () => {
        socket.send(JSON.stringify({ type: "start", dial: dial || "browser" }));
      };

      socket.onmessage = (message) => {
        let payload = null;
        try {
          payload = JSON.parse(message.data);
        } catch {
          return;
        }
        if (!payload || !payload.type) return;

        if (payload.type === "ready") {
          clearTimeout(timeout);
          call.callId = payload.call_id;
          call.ready = true;
          if (!settled) {
            settled = true;
            resolve(payload);
          }
          return;
        }

        if (payload.type === "error" && !call.ready) {
          clearTimeout(timeout);
          if (!settled) {
            settled = true;
            reject(new Error(payload.message || "The voice agent rejected the call."));
          }
          return;
        }

        handleServerMessage(payload);
      };

      socket.onclose = () => {
        clearTimeout(timeout);
        if (call.ws === socket) call.ws = null;
        if (!settled) {
          settled = true;
          reject(new Error("Voice agent connection closed before the call started."));
          return;
        }
        if (call.ready && !call.ending) finishWebCall("connection", null);
      };
    });
  }

  async function maybeApplyVoice() {
    const select = $("#web-voice");
    const chosen = select ? select.value.trim() : "";
    if (!chosen) return;
    if (state.config && chosen === state.config.voice_id) return;
    try {
      const data = await api("/api/config", { method: "POST", body: { voice_id: chosen } });
      state.config = data.config;
      const options = state.configOptions || {};
      const labels =
        state.config.tts_provider === "sarvam"
          ? options.sarvam_tts_voice_labels || {}
          : options.speechify_voice_labels || {};
      toast(`Voice set to ${labels[chosen] || chosen}.`, "info", 3000);
    } catch (error) {
      toast(`Could not switch voice: ${error.message}`, "warn", 5000);
    }
  }

  function showLiveCall() {
    const idle = $("#idle-visual");
    const live = $("#live-call");
    if (idle) idle.hidden = true;
    if (live) live.hidden = false;
  }

  function hideLiveCall() {
    const idle = $("#idle-visual");
    const live = $("#live-call");
    if (idle) idle.hidden = false;
    if (live) live.hidden = true;
  }

  function endWebCall() {
    if (!call.active && !call.starting) return;
    if (call.ws && call.ws.readyState === WebSocket.OPEN) {
      try {
        call.ws.send(JSON.stringify({ type: "stop" }));
      } catch {
        // The socket may already be closing.
      }
    }
    finishWebCall("user", null);
  }

  function finishWebCall(source = "event", event = null) {
    if (call.ending) return;
    const wasLive = call.active || call.starting || call.ready;
    call.ending = true;
    call.active = false;
    call.starting = false;
    call.ready = false;

    stopCallTimer();
    if (event && event.duration_seconds !== null && event.duration_seconds !== undefined) {
      setText("call-timer", clock(event.duration_seconds));
    }
    updateCallStatus("ENDED");
    cleanupAudioCall();
    closeBrowserSocket();

    state.webCallId = null;
    call.callId = null;
    call.ending = false;
    setText("mic-hint", "Call ended");
    syncStartButton();

    if (!wasLive) return;
    if (source === "event") {
      toast(`Call ended · ${reasonLabel(event && event.reason)}`, "info", 4500);
    } else if (source === "server") {
      toast("Call ended.", "info", 3000);
    } else if (source === "connection") {
      toast("Connection to the voice agent was lost.", "err", 5000);
    } else if (source === "user") {
      toast("Call ended by you.", "info", 3000);
    }
  }

  function cleanupAudioCall() {
    stopMicMeter();
    if (call.mediaStream) {
      call.mediaStream.getTracks().forEach((track) => {
        try {
          track.stop();
        } catch {
          // Already stopped.
        }
      });
    }

    clearPlayback();
    if (call.captureNode && call.captureNode.port) call.captureNode.port.onmessage = null;
    [call.source, call.captureNode, call.filter, call.muteGain, call.playGain].forEach(
      (node) => {
        if (!node) return;
        try {
          node.disconnect();
        } catch {
          // Already disconnected.
        }
      }
    );

    if (call.audioCtx && call.audioCtx.state !== "closed") {
      call.audioCtx.close().catch(() => {});
    }

    call.mediaStream = null;
    call.source = null;
    call.filter = null;
    call.captureNode = null;
    call.muteGain = null;
    call.playGain = null;
    call.audioCtx = null;
    call.resampler = null;
    call.pending = new Uint8Array(0);
    call.nextPlayTime = 0;
  }

  function closeBrowserSocket() {
    const socket = call.ws;
    call.ws = null;
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onerror = null;
    socket.onclose = null;
    try {
      if (
        socket.readyState === WebSocket.OPEN ||
        socket.readyState === WebSocket.CONNECTING
      ) {
        socket.close(1000, "call ended");
      }
    } catch {
      // Already closed.
    }
  }

  function startCallTimer() {
    stopCallTimer();
    call.startedAt = Date.now();
    setText("call-timer", "00:00");
    call.timer = setInterval(() => {
      setText("call-timer", clock((Date.now() - call.startedAt) / 1000));
    }, 500);
  }

  function stopCallTimer() {
    if (call.timer) clearInterval(call.timer);
    call.timer = 0;
  }

  // ------------------------------------------------------------------- bindings
  function bindUi() {
    initHistory();
    window.addEventListener("hashchange", route);

    const saveButton = $("#save-config");
    if (saveButton) saveButton.addEventListener("click", saveConfig);

    const modelSelect = $("#cfg-tts-model");
    if (modelSelect) {
      modelSelect.addEventListener("change", () => {
        renderCostEstimate(valueOf("cfg-tts-provider") || "");
      });
    }

    const providerSelect = $("#cfg-tts-provider");
    if (providerSelect) {
      providerSelect.addEventListener("change", () => {
        if (!state.config) return;
        renderTtsProvider(
          providerSelect.value,
          state.config,
          state.configOptions || {}
        );
        setSaveState("save-state", "Unsaved provider change");
      });
    }

    const placeButton = $("#place-call");
    if (placeButton) placeButton.addEventListener("click", placeOutboundCall);

    const startButton = $("#start-call");
    if (startButton) startButton.addEventListener("click", startWebCall);

    const endButton = $("#end-call");
    if (endButton) endButton.addEventListener("click", endWebCall);

    const liveHangup = $("#live-hangup");
    if (liveHangup) liveHangup.addEventListener("click", hangupLiveCall);

    const summaryToggle = $("#summary-toggle");
    if (summaryToggle) {
      summaryToggle.addEventListener("click", () => {
        const transcript = $("#summary-transcript-view");
        setSummaryView(transcript && transcript.hidden ? "transcript" : "overview");
      });
    }

    const summaryBack = $("#summary-back");
    if (summaryBack) {
      summaryBack.addEventListener("click", () => closeCallSummary({ navigate: true }));
    }

    const summaryClose = $("#summary-close");
    if (summaryClose) {
      summaryClose.addEventListener("click", () => closeCallSummary({ navigate: false }));
    }

    $$("[data-summary-close]").forEach((node) => {
      node.addEventListener("click", () => closeCallSummary({ navigate: false }));
    });

    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.summary) {
        closeCallSummary({ navigate: false });
      }
    });

    $$("[data-copy]").forEach((button) => {
      button.addEventListener("click", () => copyValue(button.dataset.copy, button));
    });

    const outboundInput = $("#outbound-to");
    if (outboundInput) {
      outboundInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter") {
          event.preventDefault();
          placeOutboundCall();
        }
      });
    }

    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "visible" && ["cost", "inbound", "outbound"].includes(state.page)) {
        loadStats();
      }
    });

    window.addEventListener("beforeunload", () => {
      if (call.ws && call.ws.readyState === WebSocket.OPEN) {
        try {
          call.ws.send(JSON.stringify({ type: "stop" }));
        } catch {
          // Page is unloading.
        }
      }
      closeBrowserSocket();
    });
  }
})();
