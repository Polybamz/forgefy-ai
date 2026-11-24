/* Admin console. Plain JS, no build step. All server data is inserted as text
   (never as HTML), so nothing a key name or a model's answer contains can run. */
"use strict";

// ── Helpers ─────────────────────────────────────────────────────────────────
const $ = (sel, root = document) => root.querySelector(sel);

function h(tag, props = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    // Through the CSSOM: the console's CSP blocks style *attributes*, not this.
    else if (k === "style") el.style.cssText = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "text") el.textContent = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat()) {
    if (c == null || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const nf = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const fmt = (n) => (n == null ? "—" : nf.format(n));
const pct = (a, b) => (b ? `${((a / b) * 100).toFixed(a / b < 0.1 ? 1 : 0)}%` : "0%");

function ago(iso) {
  if (!iso) return "never";
  const s = (Date.now() - new Date(iso).getTime()) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

function toast(text) {
  const t = h("div", { class: "toast", role: "status" }, text);
  document.body.append(t);
  setTimeout(() => t.remove(), 2400);
}

async function copy(text) {
  try { await navigator.clipboard.writeText(text); toast("Copied"); }
  catch { toast("Copy failed — select the text and copy it"); }
}

// ── Session ─────────────────────────────────────────────────────────────────
const TOKEN = "admin-token";
const session = {
  get() {
    try { return sessionStorage.getItem(TOKEN) || localStorage.getItem(TOKEN); } catch { return null; }
  },
  set(token, remember) {
    try { (remember ? localStorage : sessionStorage).setItem(TOKEN, token); } catch { /* private mode */ }
    memoryToken = token;
  },
  clear() {
    try { sessionStorage.removeItem(TOKEN); localStorage.removeItem(TOKEN); } catch { /* ignore */ }
    memoryToken = null;
  },
};
let memoryToken = null;
const token = () => memoryToken || session.get();

class AuthError extends Error {}

async function api(path, { method = "GET", body, signal, raw = false } = {}) {
  const res = await fetch(`/admin/api${path}`, {
    method, signal,
    headers: { Authorization: `Bearer ${token()}`, ...(body ? { "Content-Type": "application/json" } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (res.status === 401) throw new AuthError("Wrong admin token.");
  if (raw) return res;
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail ?? data.error;
    const msg = typeof d === "string" ? d
      : Array.isArray(d) ? d.map((e) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`).join("; ")
      : (d && d.message) || `HTTP ${res.status}`;
    throw new Error(msg);
  }
  return data;
}

// ── Page scaffolding ────────────────────────────────────────────────────────
const content = () => $("#content");

function pageHead(title, subtitle, ...actions) {
  return h("div", { class: "page-head" },
    h("div", {}, h("h1", {}, title), subtitle && h("p", {}, subtitle)),
    actions.length ? h("div", {}, ...actions) : null);
}

function panel(title, body, ...actions) {
  return h("section", { class: "panel" },
    h("div", { class: "panel-head" }, h("h2", {}, title), actions.length ? h("div", {}, ...actions) : null),
    body);
}

function table(headers, rows, emptyText = "Nothing yet.") {
  if (!rows.length) return h("div", { class: "empty" }, emptyText);
  return h("div", { class: "table-wrap" },
    h("table", {},
      h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
      h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => (c instanceof Node && c.tagName === "TD" ? c : h("td", {}, c))))))));
}

function setRuntime(ok) {
  $("#runtime-dot").className = `dot ${ok ? "ok" : "bad"}`;
}

// ── Overview ────────────────────────────────────────────────────────────────
async function overview(c) {
  const d = await api("/overview");
  setRuntime(d.runtime === "ok");
  const day = d.last_24h;
  c.append(pageHead("Overview", "The server right now, and the last 24 hours."));

  const notices = [];
  if (d.runtime !== "ok") {
    notices.push(h("div", { class: "notice bad" }, h("strong", {}, "The model runtime is not responding."),
      h("p", {}, d.runtime), h("p", {}, "On the server: ", h("code", {}, "docker compose ps"), " and ",
        h("code", {}, "docker compose logs ollama"))));
  }
  if (d.models_missing.length) {
    notices.push(h("div", { class: "notice warn" },
      h("strong", {}, `${d.models_missing.length} offered model(s) not downloaded yet: `),
      d.models_missing.map((m) => h("span", { class: "chip" }, m)),
      h("p", {}, "Requests for them fail until you run ", h("code", {}, "./scripts/pull-models.sh"), " on the server.")));
  }

  const stats = h("div", { class: "stats" },
    stat("Requests · 24 h", fmt(day.requests), day.apps.length ? `${day.apps.length} app(s)` : "no traffic yet"),
    stat("Errors · 24 h", fmt(day.errors), `${pct(day.errors, day.requests)} of requests`),
    stat("Tokens · 24 h", fmt(day.prompt_tokens + day.completion_tokens),
      `${fmt(day.prompt_tokens)} in · ${fmt(day.completion_tokens)} out`),
    stat("Active keys", fmt(d.keys_active), "manage in API keys"));

  const loaded = d.loaded.length
    ? h("div", { class: "mem" }, d.loaded.map((m) => h("div", { class: "mem-row" },
        h("span", { class: "tag" }, m.name),
        h("span", { class: "muted num" }, `${m.size_gb ?? "?"} GB${m.on_gpu_pct ? ` · ${m.on_gpu_pct}% GPU` : ""}`),
        h("div", { class: "bar" }, h("span", { style: `width:${Math.min(100, ((m.size_gb || 0) / 8) * 100)}%` })))))
    : h("p", { class: "muted" }, "No model loaded. The next request loads one (10–60 s on a CPU).");

  const roles = Object.entries(d.roles);
  const roleTable = table(["Role", "Runs on"],
    [["default", h("span", { class: "tag" }, d.default_model)],
     ...roles.map(([r, m]) => [h("span", { class: "chip role" }, r), h("span", { class: "tag" }, m)])],
    "No roles set — add MODEL_ALIASES in .env.");

  const sp = d.speech || {};
  const speech = h("div", { class: "mem-row", style: "margin-top:14px;padding-top:12px;border-top:1px solid var(--line)" },
    h("span", {}, "Speech-to-text ", h("span", { class: "tag" }, sp.model || "")),
    sp.status !== "ok" ? h("span", { class: "status bad" }, "unreachable")
      : sp.busy ? h("span", { class: "status warn" }, "transcribing")
      : sp.loaded ? h("span", { class: "status ok" }, "loaded")
      : h("span", { class: "status" }, "loads on first request"));

  c.append(h("div", { class: "stack" }, ...notices, stats,
    h("div", { class: "grid-2" },
      panel("In memory now", h("div", { class: "panel-body" }, loaded, speech)),
      panel("Roles", roleTable))));
}

function stat(label, value, sub) {
  return h("div", { class: "stat" }, h("div", { class: "label" }, label),
    h("div", { class: "value" }, value), h("div", { class: "sub" }, sub));
}

// ── Models ──────────────────────────────────────────────────────────────────
async function models(c) {
  const d = await api("/models");
  setRuntime(d.runtime === "ok");
  c.append(pageHead("Models", "What's on offer, what each model can do, and what's in memory."));
  const rows = d.models.map((m) => [
    h("td", {}, h("span", { class: "tag" }, m.name), m.default ? h("span", { class: "chip role", style: "margin-left:8px" }, "default") : null),
    m.kind === "embed" ? "embeddings" : "chat",
    h("td", { class: "wrap" }, m.capabilities.length ? m.capabilities.filter((x) => x !== "completion").map((x) => h("span", { class: "chip" }, x)) : h("span", { class: "faint" }, "—")),
    h("td", { class: "wrap" }, m.roles.map((r) => h("span", { class: "chip role" }, r))),
    h("td", { class: "num muted" }, m.size_gb ? `${m.size_gb} GB` : "—"),
    !m.installed ? h("span", { class: "status bad" }, "Not downloaded")
      : m.loaded ? h("span", { class: "status ok" }, "In memory")
      : h("span", { class: "status" }, "Ready"),
  ]);
  c.append(h("div", { class: "stack" },
    panel("On offer", table(["Model", "Kind", "Can", "Roles", "Size", "Status"], rows, "No models configured.")),
    d.installed_not_offered.length ? h("div", { class: "notice" },
      h("strong", {}, "Downloaded but not offered: "),
      d.installed_not_offered.map((m) => h("span", { class: "chip" }, m)),
      h("p", { class: "muted" }, "Add them to CHAT_MODELS or EMBEDDING_MODELS in .env to offer them, or remove them with ",
        h("code", {}, "docker compose exec ollama ollama rm <name>"), " to free disk.")) : null,
    h("p", { class: "muted" }, "To add a model: check its license, add it to MODELS and CHAT_MODELS in .env, run ",
      h("code", {}, "./scripts/pull-models.sh"), ", then ", h("code", {}, "docker compose up -d"),
      ". Roles are set in MODEL_ALIASES.")));
}

// ── Keys ────────────────────────────────────────────────────────────────────
async function keys(c) {
  c.append(pageHead("API keys", "One key per app or customer. A key is shown once, when it is created."));
  const revealSlot = h("div");
  const err = h("p", { class: "error", role: "alert", hidden: true });

  const form = h("form", { class: "panel-body", autocomplete: "off" },
    h("div", { class: "form-row" },
      field("App or customer", h("input", { name: "app", required: true, placeholder: "forgefy", pattern: "[a-z][a-z0-9\\-]{1,31}", title: "lowercase letters, digits and -" })),
      field("Environment", h("select", { name: "environment" }, ["production", "staging", "development"].map((e) => h("option", {}, e)))),
      field("Requests / min", h("input", { name: "rate", type: "number", min: "1", max: "10000", placeholder: "default" })),
      field("Label (optional)", h("input", { name: "name", maxlength: "80", placeholder: "e.g. Vndor web backend" })),
      h("button", { class: "btn btn-primary", type: "submit" }, "Create key")),
    err);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    err.hidden = true;
    const f = new FormData(form);
    try {
      const k = await api("/keys", { method: "POST", body: {
        app: f.get("app").trim(), environment: f.get("environment"),
        name: f.get("name").trim() || null, rate_limit_per_minute: f.get("rate") ? Number(f.get("rate")) : null,
      } });
      form.reset();
      revealSlot.replaceChildren(h("div", { class: "notice ok reveal" },
        h("strong", {}, `New key for ${k.app} (${k.environment})`),
        h("div", { class: "secret" }, h("code", {}, k.key),
          h("button", { class: "btn btn-sm", type: "button", onclick: () => copy(k.key) }, "Copy")),
        h("p", {}, "Copy it now and put it in that app's secrets — it is not stored and can't be shown again."),
        h("button", { class: "link", type: "button", onclick: () => revealSlot.replaceChildren() }, "I've saved it — hide")));
      await renderList();
    } catch (ex) {
      if (ex instanceof AuthError) throw ex;
      err.textContent = ex.message; err.hidden = false;
    }
  });

  const listSlot = h("div");
  async function renderList() {
    const d = await api("/keys");
    listSlot.replaceChildren(panel(`Keys (${d.keys.filter((k) => !k.revoked).length} active)`, table(
      ["Key", "App", "Env", "Label", "Rate/min", "Created", "Last used", ""],
      d.keys.map((k) => [
        h("span", { class: "tag" }, `${k.prefix}…`),
        h("strong", {}, k.app), k.environment, h("td", { class: "wrap muted" }, k.name),
        h("td", { class: "num" }, k.rate_limit_per_minute),
        h("span", { class: "muted", title: k.created_at }, ago(k.created_at)),
        h("span", { class: "muted", title: k.last_used_at || "" }, ago(k.last_used_at)),
        k.revoked ? h("span", { class: "status bad" }, "Revoked")
          : h("button", { class: "btn btn-sm btn-danger", type: "button", onclick: async () => {
              if (!confirm(`Revoke ${k.prefix}… (${k.app})? Apps using it will get 401 immediately.`)) return;
              try { await api(`/keys/${encodeURIComponent(k.prefix)}/revoke`, { method: "POST" }); toast("Key revoked"); await renderList(); }
              catch (ex) { if (ex instanceof AuthError) return signOut(ex.message); toast(ex.message); }
            } }, "Revoke"),
      ]), "No keys yet — create one above.")));
  }

  c.append(h("div", { class: "stack" }, panel("Create a key", form), revealSlot, listSlot));
  await renderList();
}

function field(label, input) {
  const id = `f-${Math.random().toString(36).slice(2, 8)}`;
  input.id = id;
  return h("div", { class: "field" }, h("label", { for: id }, label), input);
}

// ── Usage ───────────────────────────────────────────────────────────────────
let usageDays = 7;

async function usage(c) {
  const d = await api(`/usage?days=${usageDays}`);
  const seg = h("div", { class: "segmented", role: "group", "aria-label": "Period" },
    [1, 7, 30, 90].map((n) => h("button", { type: "button", class: n === usageDays ? "on" : "",
      onclick: () => { usageDays = n; render(); } }, n === 1 ? "24 h" : `${n} days`)));
  c.append(pageHead("Usage", "Requests, errors and tokens per app. Only metadata is recorded — never prompts or answers.", seg));

  const total = d.summary.reduce((a, r) => a + r.requests, 0);
  const errors = d.summary.filter((r) => r.status !== "ok").reduce((a, r) => a + r.requests, 0);
  const tokensIn = d.summary.reduce((a, r) => a + r.prompt_tokens, 0);
  const tokensOut = d.summary.reduce((a, r) => a + r.completion_tokens, 0);

  c.append(h("div", { class: "stack" },
    h("div", { class: "stats" },
      stat("Requests", fmt(total), `${usageDays === 1 ? "24 hours" : `${usageDays} days`}`),
      stat("Errors", fmt(errors), `${pct(errors, total)} of requests`),
      stat("Tokens in", fmt(tokensIn), "prompts sent to models"),
      stat("Tokens out", fmt(tokensOut), "generated by models")),
    panel("Requests per day", h("div", { class: "panel-body" }, chart(d.daily, usageDays))),
    panel("By app and API", table(["App", "API", "Status", "Requests", "Avg", "p95", "Tokens in", "Tokens out", "Audio"],
      d.summary.map((r) => [
        h("strong", {}, r.app), h("span", { class: "tag" }, r.task),
        h("span", { class: `status ${r.status === "ok" ? "ok" : r.status === "busy" ? "warn" : "bad"}` }, r.status),
        h("td", { class: "num" }, fmt(r.requests)), h("td", { class: "num muted" }, secs(r.avg_latency_ms)),
        h("td", { class: "num muted" }, secs(r.p95_latency_ms)), h("td", { class: "num" }, fmt(r.prompt_tokens)),
        h("td", { class: "num" }, fmt(r.completion_tokens)),
        h("td", { class: "num" }, r.audio_seconds ? minutes(r.audio_seconds) : "—"),
      ]), "No requests in this period."))));
}

const minutes = (s) => (s < 60 ? `${Math.round(s)} s` : s < 3600 ? `${(s / 60).toFixed(1)} min` : `${(s / 3600).toFixed(1)} h`);

const secs = (ms) => (ms == null ? "—" : ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);

function chart(daily, days) {
  const NS = "http://www.w3.org/2000/svg";
  const s = (tag, attrs = {}, text) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v);
    if (text != null) el.textContent = text;
    return el;
  };
  const dayKeys = [];
  for (let i = Math.max(days, 1) - 1; i >= 0; i--) {
    dayKeys.push(new Date(Date.now() - i * 86400000).toISOString().slice(0, 10));
  }
  const apps = [...new Set(daily.map((r) => r.app))].sort();
  if (!apps.length) return h("div", { class: "empty" }, "No requests in this period.");
  const by = {};
  for (const r of daily) (by[r.day] ||= {})[r.app] = r.requests;
  const totals = dayKeys.map((d) => apps.reduce((a, app) => a + ((by[d] || {})[app] || 0), 0));
  const max = Math.max(1, ...totals);

  const W = 900, H = 220, L = 36, B = 22, T = 8;
  const svg = s("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none", role: "img",
    "aria-label": `Requests per day for the last ${days} days` });
  for (const f of [0, 0.5, 1]) {
    const y = T + (H - B - T) * (1 - f);
    svg.append(s("line", { class: "grid", x1: L, x2: W, y1: y, y2: y }));
    svg.append(s("text", { x: L - 6, y: y + 4, "text-anchor": "end" }, fmt(Math.round(max * f))));
  }
  const slot = (W - L) / dayKeys.length;
  const bw = Math.max(2, Math.min(28, slot * 0.7));
  dayKeys.forEach((d, i) => {
    let y = H - B;
    apps.forEach((app, j) => {
      const v = (by[d] || {})[app] || 0;
      if (!v) return;
      const hgt = ((H - B - T) * v) / max;
      y -= hgt;
      const rect = s("rect", { x: L + i * slot + (slot - bw) / 2, y, width: bw, height: hgt, rx: 2 });
      rect.style.fill = `var(--s${(j % 6) + 1})`;
      rect.append(s("title", {}, `${d} · ${app}: ${v} request(s)`));
      svg.append(rect);
    });
    const every = Math.ceil(dayKeys.length / 10);
    if (i % every === 0 || i === dayKeys.length - 1) {
      svg.append(s("text", { x: L + i * slot + slot / 2, y: H - 6, "text-anchor": "middle" }, d.slice(5)));
    }
  });
  return h("div", {}, svg, h("div", { class: "legend" },
    apps.map((a, j) => h("span", {}, h("i", { style: `background:var(--s${(j % 6) + 1})` }), a))));
}

// ── Playground ──────────────────────────────────────────────────────────────
const play = { messages: [], controller: null };

async function playground(c) {
  const d = await api("/models");
  const chatModels = d.models.filter((m) => m.kind === "chat" && m.installed);
  const caps = Object.fromEntries(d.models.map((m) => [m.name, m.capabilities]));
  const roleOf = {};
  for (const m of d.models) for (const r of m.roles) roleOf[r] = m.name;

  c.append(pageHead("Playground", "Try any model or role. Requests are logged as app “admin-console”."));
  if (!chatModels.length) {
    c.append(h("div", { class: "notice warn" }, "No chat model is downloaded yet — run ",
      h("code", {}, "./scripts/pull-models.sh"), " on the server."));
    return;
  }

  const select = h("select", {},
    h("optgroup", { label: "Roles" }, Object.keys(roleOf).filter((r) => d.models.find((m) => m.name === roleOf[r] && m.kind === "chat"))
      .map((r) => h("option", { value: r }, `${r} → ${roleOf[r]}`))),
    h("optgroup", { label: "Models" }, chatModels.map((m) => h("option", { value: m.name }, m.name))));
  const think = h("input", { type: "checkbox" });
  const thinkLabel = h("label", { class: "check" }, think, "Think before answering");
  const capLine = h("p", { class: "muted", style: "margin:0;font-size:12px" });
  const system = h("textarea", { rows: "5", placeholder: "System instructions (optional), e.g. You write short product descriptions in French." });

  function syncCaps() {
    const model = roleOf[select.value] || select.value;
    const cs = caps[model] || [];
    think.disabled = !cs.includes("thinking");
    thinkLabel.style.opacity = think.disabled ? "0.5" : "1";
    if (think.disabled) think.checked = false;
    capLine.textContent = `${model} · ${cs.filter((x) => x !== "completion").join(", ") || "text only"}`;
  }
  select.addEventListener("change", syncCaps);
  syncCaps();

  const transcript = h("div", { class: "transcript", "aria-live": "polite" });
  const input = h("textarea", { rows: "2", placeholder: "Message — Enter to send, Shift+Enter for a new line" });
  const send = h("button", { class: "btn btn-primary", type: "submit" }, "Send");
  const composer = h("form", { class: "composer" }, input, send);

  function drawHistory() {
    transcript.replaceChildren(...(play.messages.length ? play.messages.map((m) => bubble(m.role, m.content, m.thinking, m.meta))
      : [h("div", { class: "empty" }, "Send a message to start. The first request to a model loads it into memory, which can take a minute on a CPU.")]));
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); composer.requestSubmit(); }
  });

  composer.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (play.controller) { play.controller.abort(); return; }
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    play.messages.push({ role: "user", content: text });
    drawHistory();

    const reply = { role: "assistant", content: "", thinking: "", meta: "" };
    const body = h("div", { class: "body cursor" });
    const thinkBox = h("div");
    const meta = h("div", { class: "meta" }, "waiting for the model…");
    transcript.append(h("div", { class: "msg" }, h("div", { class: "who" }, select.value), h("details", { hidden: true }, h("summary", {}, "Thinking"), thinkBox), body, meta));
    transcript.scrollTop = transcript.scrollHeight;

    const msgs = [];
    if (system.value.trim()) msgs.push({ role: "system", content: system.value.trim() });
    msgs.push(...play.messages.map(({ role, content }) => ({ role, content })));
    const payload = { model: select.value, messages: msgs, stream: true };
    if (!think.disabled) payload.think = think.checked;

    play.controller = new AbortController();
    send.textContent = "Stop";
    const started = performance.now();
    let last = null;
    try {
      const res = await api("/playground", { method: "POST", body: payload, signal: play.controller.signal, raw: true });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.error || err.detail || `HTTP ${res.status}`);
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buf.indexOf("\n")) >= 0) {
          const line = buf.slice(0, nl).trim();
          buf = buf.slice(nl + 1);
          if (!line) continue;
          const chunk = JSON.parse(line);
          if (chunk.error) throw new Error(chunk.error);
          const m = chunk.message || {};
          if (m.thinking) { reply.thinking += m.thinking; thinkBox.textContent = reply.thinking; thinkBox.parentElement.hidden = false; }
          if (m.content) { reply.content += m.content; body.textContent = reply.content; meta.textContent = "generating…"; }
          if (chunk.done) last = chunk;
          transcript.scrollTop = transcript.scrollHeight;
        }
      }
      const secsTaken = ((performance.now() - started) / 1000).toFixed(1);
      const tps = last && last.eval_duration ? (last.eval_count / (last.eval_duration / 1e9)).toFixed(1) : null;
      reply.meta = `${last?.model || select.value} · ${secsTaken} s${last ? ` · ${last.eval_count} tokens` : ""}${tps ? ` · ${tps} tok/s` : ""}`;
    } catch (ex) {
      if (ex instanceof AuthError) return signOut(ex.message);
      reply.meta = ex.name === "AbortError" ? "stopped" : `error: ${ex.message}`;
    } finally {
      play.controller = null;
      send.textContent = "Send";
      body.classList.remove("cursor");
    }
    if (reply.content || reply.thinking) play.messages.push(reply);
    else play.messages.pop();  // nothing came back: let the user retry the same message
    drawHistory();
    if (!reply.content && !reply.thinking) transcript.append(h("div", { class: "notice bad" }, reply.meta));
    transcript.scrollTop = transcript.scrollHeight;
  });

  const controls = h("div", { class: "play-controls" },
    panel("Settings", h("div", { class: "panel-body stack", style: "gap:12px" },
      field("Model or role", select), capLine, thinkLabel, field("System instructions", system),
      h("button", { class: "btn", type: "button", onclick: () => { play.messages = []; drawHistory(); } }, "Clear conversation"))));

  c.append(h("div", { class: "play" }, controls, h("section", { class: "panel" }, transcript, composer)));
  drawHistory();
}
function docs(c) {
  c.append(pageHead("Docs", "The API is fully documented in OpenAPI format."));
  c.append(h("div", { class: "stack" },
    panel("OpenAPI spec", h("div", { class: "panel-body" }, h("p", {}, "The API is fully described in OpenAPI format, which can be used to generate client code or documentation."), h("a", { href: "/admin/api/openapi.json", target: "_blank" }, "/admin/api/openapi.json"))),
    panel("Swagger UI", h("div", { class: "panel-body" }, h("p", {}, "Interactive API documentation and testing via Swagger UI."), h("a", { href: "/admin/api/docs", target: "_blank" }, "/admin/api/docs")))));
}
function bubble(role, text, thinking, meta) {
  return h("div", { class: `msg ${role}` },
    h("div", { class: "who" }, role === "user" ? "You" : "Model"),
    thinking ? h("details", {}, h("summary", {}, "Thinking"), h("div", {}, thinking)) : null,
    h("div", { class: "body" }, text),
    meta ? h("div", { class: "meta" }, meta) : null);
}

// ── Router ──────────────────────────────────────────────────────────────────
const PAGES = { overview, models, keys, usage, playground, docs };
let refreshTimer = null;

async function render() {
  const page = (location.hash.slice(1) || "overview").split("?")[0];
  const fn = PAGES[page] || overview;
  for (const a of document.querySelectorAll("nav a")) a.classList.toggle("active", a.dataset.page === page);
  clearTimeout(refreshTimer);
  const c = content();
  c.replaceChildren(h("p", { class: "muted" }, "Loading…"));
  const fresh = h("div");
  try {
    await fn(fresh);
    c.replaceChildren(...fresh.childNodes);
  } catch (ex) {
    if (ex instanceof AuthError) return signOut(ex.message);
    c.replaceChildren(h("div", { class: "notice bad" }, h("strong", {}, "Couldn't load this page. "), ex.message));
  }
  // Keep the overview live; other pages refresh when you open them.
  if (page === "overview") {
    refreshTimer = setTimeout(() => { if ((location.hash.slice(1) || "overview") === "overview") render(); }, 15000);
  }
}


// ── Sign-in / out ───────────────────────────────────────────────────────────
function showLogin(message) {
  $("#app").hidden = true;
  $("#login").hidden = false;
  const err = $("#login-error");
  err.textContent = message || "";
  err.hidden = !message;
  $("#token").focus();
}

function signOut(message) {
  session.clear();
  clearTimeout(refreshTimer);
  showLogin(message);
}

async function start() {
  $("#login").hidden = true;
  $("#app").hidden = false;
  $("#host").textContent = location.host;
  await render();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const value = $("#token").value.trim();
    memoryToken = value;
    try {
      await api("/overview");
      session.set(value, $("#remember").checked);
      $("#token").value = "";
      await start();
    } catch (ex) {
      memoryToken = null;
      showLogin(ex.message);
    }
  });
  $("#signout").addEventListener("click", () => signOut());
  window.addEventListener("hashchange", () => { if (token()) render(); });
  if (token()) start(); else showLogin();
});
