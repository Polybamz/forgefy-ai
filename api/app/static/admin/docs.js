/* Docs page of the admin console: how to use this server from your apps.
   Built from live data — this server's address, its models, roles and speech
   model — so every example works as written once a key is pasted in.
   Uses helpers from admin.js (h, api, pageHead, panel, table, copy). */
"use strict";

// What each model family is for, by Ollama tag prefix.
const MODEL_NOTES = [
  ["qwen3-coder", "Coding agent: writes, fixes and explains code; multi-step code edits."],
  ["qwen2.5-coder", "Code: writing, fixing and explaining code."],
  ["devstral", "Agentic coding: multi-step edits across files, tool use."],
  ["qwen3", "All-round assistant: chat, writing, summaries, tool calling. Strong in English and French."],
  ["deepseek-r1", "Step-by-step reasoning: maths, planning, logic. Thinks at length before answering — slower."],
  ["phi4", "Compact model with strong reasoning for its size."],
  ["gpt-oss", "General assistant with tool calling and adjustable reasoning effort."],
  ["gemma3", "Understands images as well as text: photos, screenshots, scanned documents."],
  ["llama", "General assistant, very widely supported by tools."],
  ["mistral", "Fast general model; good French."],
  ["bge-m3", "Embeddings (text → vectors) for search and similarity, multilingual including French."],
  ["nomic-embed", "Embeddings (text → vectors) for search and similarity, English."],
];
const noteFor = (name) => (MODEL_NOTES.find(([p]) => name.startsWith(p)) || [null, "Open-weight model."])[1];

// ── Syntax highlighting ─────────────────────────────────────────────────────
// A small tokenizer per language: enough to colour keywords, strings,
// functions, numbers, keys and comments. Tokens become <span>s with
// textContent, so code is never parsed as HTML. (No library: the console's CSP
// allows scripts from this server only.)
const KW = {
  python: "import|from|as|def|return|for|in|if|else|elif|with|while|lambda|and|or|not|is|True|False|None|async|await|class|try|except",
  js: "import|from|const|let|var|await|async|export|function|return|new|for|of|in|if|else|true|false|null|undefined|typeof|class",
  php: "echo|function|return|if|else|true|false|null|array|new|foreach|as",
  bash: "curl|export|claude|docker|pip|npm|cd",
};
const BUILTINS = {
  python: "print|open|max|min|range|len|sum|zip|json|math|base64",
  js: "console|process|JSON|Response|ReadableStream|TextEncoder|fs",
  php: "curl_init|curl_setopt_array|curl_exec|json_encode|json_decode|getenv",
};

function rules(lang) {
  const kw = (list) => new RegExp(`\\b(?:${list})\\b`, "y");
  const str = /(?:[frb]{1,2}(?=["']))?(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:\\.|[^"\\\n])*"|'(?:\\.|[^'\\\n])*'|`(?:\\.|[^`\\])*`)/y;
  const num = /\b\d[\d_]*(?:\.\d+)?(?:e\d+)?\b/y;
  const fn = /[A-Za-z_]\w*(?=\()/y;
  switch (lang) {
    case "python":
      return [["comment", /#[^\n]*/y], ["string", str], ["number", num], ["keyword", kw(KW.python)],
              ["builtin", kw(BUILTINS.python)], ["fn", fn], ["key", /[A-Za-z_]\w*(?==[^=])/y]];
    case "javascript": case "typescript":
      return [["comment", /\/\/[^\n]*/y], ["string", str], ["number", num], ["keyword", kw(KW.js)],
              ["builtin", kw(BUILTINS.js)], ["fn", fn], ["key", /[A-Za-z_]\w*(?=\s*:(?!:))/y],
              ["type", /\b(?:Request|string|number)\b/y]];
    case "php":
      return [["comment", /(?:\/\/|#)[^\n]*/y], ["meta", /<\?php/y], ["string", str], ["number", num],
              ["var", /\$\w+/y], ["keyword", kw(KW.php)], ["builtin", kw(BUILTINS.php)],
              ["const", /\b[A-Z][A-Z_]{2,}\b/y], ["fn", fn]];
    case "bash":
      return [["comment", /#[^\n]*/y], ["string", str], ["keyword", kw(KW.bash)], ["var", /\$\w+/y],
              ["flag", /(?<=\s)--?[A-Za-z][\w-]*/y], ["key", /\b[A-Za-z_]+(?==)/y]];
    case "env":
      return [["comment", /#[^\n]*/y], ["key", /^[A-Z][A-Z0-9_]*(?==)/my], ["punct", /=/y]];
    case "markdown":
      return [["punct", /^---$/my], ["key", /^[a-z][\w-]*(?=:)/my], ["keyword", /^#{1,6} [^\n]*/my],
              ["number", /^\d+\.(?= )/my], ["punct", /^\s*[-*](?= )/my]];
    case "tree":
      return [["comment", /#[^\n]*/y], ["punct", /[├└│─]+/y], ["fn", /[\w.-]+\/(?=\s|$)/my]];
    case "text":
      return [["key", /^[^:\n]+:/my]];
    default:
      return [];
  }
}

function highlight(code, lang) {
  const rs = rules(lang);
  const frag = document.createDocumentFragment();
  let plain = "";
  const flush = () => { if (plain) { frag.append(plain); plain = ""; } };
  for (let i = 0; i < code.length;) {
    let hit = null;
    for (const [kind, re] of rs) {
      re.lastIndex = i;
      const m = re.exec(code);
      if (m && m[0].length) { hit = [kind, m[0]]; break; }
    }
    if (hit) {
      flush();
      frag.append(h("span", { class: `tk-${hit[0]}` }, hit[1]));
      i += hit[1].length;
    } else {
      // Consume a whole word at once, so keywords aren't matched inside identifiers.
      const w = /[A-Za-z_]\w*|\s+|./y;
      w.lastIndex = i;
      const m = w.exec(code);
      plain += m[0];
      i += m[0].length;
    }
  }
  flush();
  return frag;
}

const LANG_LABELS = { python: "Python", javascript: "JavaScript", typescript: "TypeScript", php: "PHP",
                      bash: "Shell", env: ".env", text: "Settings", markdown: "Markdown", tree: "Folders" };

function codeBlock(code, lang) {
  const src = code.trim();
  const pre = h("pre", { class: "code" }, h("code", {}, highlight(src, lang)));
  return h("div", { class: `code-wrap lang-${lang}` },
    h("div", { class: "code-bar" }, h("span", { class: "lang" }, LANG_LABELS[lang] || lang),
      h("button", { class: "btn btn-sm", type: "button", onclick: () => copy(src) }, "Copy")),
    pre);
}

function tabs(variants) {
  // variants: [[label, code, lang], ...]
  const bodies = variants.map(([, code, lang]) => codeBlock(code, lang));
  const buttons = variants.map(([label], i) => h("button", {
    type: "button", class: i === 0 ? "on" : "",
    onclick: () => {
      buttons.forEach((b, j) => b.classList.toggle("on", j === i));
      bodies.forEach((b, j) => { b.hidden = j !== i; });
    },
  }, label));
  bodies.forEach((b, j) => { b.hidden = j !== 0; });
  return h("div", { class: "tabs" },
    variants.length > 1 ? h("div", { class: "segmented", role: "tablist" }, buttons) : null, ...bodies);
}

// The colour of each API family — used for the hero key, the API table, the
// examples' rails and capability chips, so a colour always means the same API.
const FAMILY_LABEL = { chat: "Chat", embed: "Embeddings", speech: "Speech", connect: "Connect a tool" };

function example(title, why, variants, note, family = "chat") {
  return h("section", { class: `example fam-${family}` },
    h("div", { class: "example-head" },
      h("h3", {}, title), h("span", { class: `fam-tag fam-${family}` }, FAMILY_LABEL[family])),
    h("p", { class: "muted" }, why), tabs(variants),
    note ? h("p", { class: "faint small" }, note) : null);
}

const capChip = (cap) => h("span", { class: `chip cap cap-${cap}` }, cap);

function hero(base, hasEmbed, hasSpeech) {
  const url = new URL(base);
  const endpoints = [
    ["chat", "POST", "/v1/chat/completions"],
    ["chat", "POST", "/v1/messages"],
    ["chat", "POST", "/api/chat"],
    hasEmbed && ["embed", "POST", "/v1/embeddings"],
    hasSpeech && ["speech", "POST", "/v1/audio/transcriptions"],
  ].filter(Boolean);
  return h("section", { class: "docs-hero" },
    h("p", { class: "hero-lead" }, "Your models answer at"),
    h("div", { class: "hero-url" },
      h("span", { class: "u-scheme" }, `${url.protocol}//`), h("span", { class: "u-host" }, url.host)),
    h("div", { class: "hero-endpoints" }, endpoints.map(([fam, method, path]) =>
      h("span", { class: `ep fam-${fam}` }, h("span", { class: "ep-method" }, method), path))),
    h("div", { class: "hero-key" }, Object.entries(FAMILY_LABEL)
      .filter(([fam]) => (fam !== "embed" || hasEmbed) && (fam !== "speech" || hasSpeech))
      .map(([fam, label]) => h("span", { class: `key-item fam-${fam}` }, h("i"), label))));
}

async function docs(c) {
  const [ov, md] = await Promise.all([api("/overview"), api("/models")]);
  const BASE = location.origin;
  const chat = md.models.filter((m) => m.kind === "chat");
  const embed = md.models.filter((m) => m.kind === "embed");
  const roles = ov.roles || {};
  const roleFor = (target) => Object.keys(roles).filter((r) => roles[r] === target && !r.includes("*"));
  const generalName = Object.keys(roles).find((r) => r === "general") || ov.default_model;
  const embedName = Object.keys(roles).find((r) => r === "embed") || (embed[0] && embed[0].name) || "embed";
  const visionModel = chat.find((m) => m.capabilities.includes("vision"));
  const toolModel = chat.find((m) => m.capabilities.includes("tools"));
  const speech = ov.speech || {};
  const KEY = "YOUR_API_KEY";

  c.append(pageHead("Docs", "How to use the models on this server from your apps — with examples you can copy."));
  c.append(hero(BASE, embed.length > 0, Boolean(speech.model)));

  // ── Contents ──
  const sections = [["quick", "Quick start"], ["models", "Models"], ["key", "Get a key"],
    ["modes", "APIs & modes"], ["skills", "Skills & plugins"], ["examples", "Examples"], ["limits", "Errors & limits"]];
  const toc = h("nav", { class: "toc", "aria-label": "On this page" },
    sections.map(([id, label]) => h("a", { href: `#docs`, onclick: (e) => {
      e.preventDefault(); document.getElementById(`d-${id}`).scrollIntoView({ behavior: "smooth" });
    } }, label)));

  // ── Quick start ──
  const quick = panel("Quick start", h("div", { class: "panel-body prose" },
    h("ol", {},
      h("li", {}, "Create a key for your app under ", h("a", { href: "#keys" }, "API keys"), ". Copy it — it's shown once."),
      h("li", {}, "Store it in your app's server-side settings, e.g. ", h("code", {}, "AI_API_KEY"), " in its .env. Never put it in browser or mobile code."),
      h("li", {}, "Point any OpenAI-compatible library at this server and send a request:")),
    codeBlock(`curl ${BASE}/v1/chat/completions \\
  -H "Authorization: Bearer ${KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{"model": "${generalName}", "messages": [{"role": "user", "content": "Hello!"}]}'`, "bash"),
    h("p", {}, "Base URL for OpenAI-style libraries: ", h("code", {}, `${BASE}/v1`), " · Interactive API reference: ",
      h("a", { href: "/docs", target: "_blank", rel: "noopener" }, "/docs ↗"))));
  quick.id = "d-quick";

  // ── Models ──
  const modelRows = md.models.map((m) => [
    h("td", {}, h("span", { class: "tag" }, m.name),
      m.default ? h("span", { class: "chip role", style: "margin-left:8px" }, "default") : null),
    h("td", { class: "wrap" }, roleFor(m.name).map((r) => h("span", { class: "chip role" }, r))),
    h("td", { class: "wrap" }, noteFor(m.name)),
    h("td", { class: "wrap" }, m.capabilities.filter((x) => x !== "completion").map(capChip)),
    !m.installed ? h("span", { class: "status bad" }, "not downloaded") : h("span", { class: "status ok" }, "ready"),
  ]);
  if (speech.model) {
    modelRows.push([h("span", { class: "tag" }, `whisper ${speech.model}`), h("td", {}),
      h("td", { class: "wrap" }, "Speech → text in ~100 languages (incl. English and French), or translation into English."),
      h("td", {}, capChip("speech")),
      speech.status === "ok" ? h("span", { class: "status ok" }, "ready") : h("span", { class: "status bad" }, "unavailable")]);
  }
  const models = panel("Models", h("div", {},
    h("div", { class: "panel-body prose" },
      h("p", {}, "Ask for a model by its name (", h("code", {}, `"model": "${ov.default_model}"`), ") or by a ",
        h("strong", {}, "role"), " (", h("code", {}, `"model": "${generalName}"`),
        "). Prefer roles: when a better model takes over a role, your app follows without a code change. ",
        h("code", {}, '"default"'), " always means the default model."),
      h("p", { class: "muted" }, "What models can do: ", capChip("tools"), " call functions you define, ",
        capChip("thinking"), " reason before answering, ", capChip("vision"), " read images, ",
        capChip("embedding"), " turn text into vectors, ", capChip("speech"), " turn audio into text.")),
    table(["Model", "Roles", "Good for", "Can", "Status"], modelRows, "No models configured.")));
  models.id = "d-models";

  // ── Keys ──
  const key = panel("Get a key", h("div", { class: "panel-body prose" },
    h("ol", {},
      h("li", {}, "Open ", h("a", { href: "#keys" }, "API keys"), " in this console."),
      h("li", {}, "Enter who it's for — an app (", h("code", {}, "forgefy"), ", ", h("code", {}, "vndor"),
        ") or a customer — pick the environment, optionally a rate limit, and click ", h("strong", {}, "Create key"), "."),
      h("li", {}, "Copy the key immediately: only a fingerprint of it is stored, so it can't be shown again."),
      h("li", {}, "Send it with every request as ", h("code", {}, "Authorization: Bearer <key>"),
        " (OpenAI style) or ", h("code", {}, "x-api-key: <key>"), " (Anthropic style).")),
    h("p", {}, h("strong", {}, "One key per app, per environment"),
      " — usage is reported per key's app, and revoking one key cuts off only that app. Lost or leaked? Revoke it and create a new one."),
    h("p", { class: "muted" }, "From the server's command line instead: ",
      h("code", {}, "docker compose exec api python -m app.cli keys create --app myapp --env production"))));
  key.id = "d-key";

  // ── APIs & modes ──
  const modes = panel("APIs & modes", h("div", {},
    h("div", { class: "panel-body prose" },
      h("p", {}, "The same models, in the formats AI tools already speak. Pick the one your library uses — they behave the same.")),
    table(["API", "Endpoint", "Use it from"], [
      ["chat", "Chat in OpenAI format", "POST /v1/chat/completions", "OpenAI SDKs (any language), LangChain, LlamaIndex, n8n, Open WebUI, most tools"],
      ["chat", "Chat in Anthropic format", "POST /v1/messages", "Claude Code, Claude Agent SDK, Anthropic SDKs"],
      ["chat", "Chat in Ollama format", "POST /api/chat", "Ollama clients, and Forgefy's backend"],
      ["embed", "Embeddings", "POST /v1/embeddings, /api/embed", "Search, recommendations, duplicate detection"],
      ["speech", "Speech to text", "POST /v1/audio/transcriptions", "Meeting recordings, voice notes, subtitles"],
      ["speech", "Speech to English", "POST /v1/audio/translations", "Recordings in any language, as English text"],
      ["connect", "List models", "GET /v1/models", "What's on offer, with capabilities"],
    ].map(([fam, name, path, use]) => [
      h("td", {}, h("span", { class: `api-name fam-${fam}` }, h("i"), name)),
      h("code", { class: `ep-path fam-${fam}` }, path),
      h("td", { class: "wrap" }, use),
    ])),
    h("div", { class: "panel-body", style: "padding-bottom:0" }, h("h3", { style: "font-size:14px;margin:6px 0 0" }, "Modes and options")),
    h("div", {},
      table(["Mode", "How", "Notes"], [
        ["System instructions", h("td", { class: "wrap" }, "A message with role ", h("code", {}, "system"), " (Anthropic: ", h("code", {}, "system"), " field)"), h("td", { class: "wrap" }, "Sets tone, language, rules for the whole conversation.")],
        ["Streaming", h("code", {}, '"stream": true'), h("td", { class: "wrap" }, "Text arrives word by word — show it as it comes, so users aren't staring at a spinner.")],
        ["JSON output", h("code", {}, '"response_format": {"type": "json_object"}'), h("td", { class: "wrap" }, "Guarantees valid JSON — ask for the fields you want in the prompt.")],
        ["Tool calling", h("code", {}, '"tools": [...]'), h("td", { class: "wrap" }, `The model asks your code to run a function and uses the result. Needs a model with “tools”${toolModel ? ` (e.g. ${toolModel.name})` : ""}.`)],
        ["Thinking on/off", h("td", { class: "wrap" }, h("code", {}, '"reasoning_effort": "none"'), " (OpenAI) · ", h("code", {}, '"think": false'), " (Ollama) · ", h("code", {}, '"thinking": {"type": "disabled"}'), " (Anthropic)"), h("td", { class: "wrap" }, "Thinking models answer better but ~2× slower. Turn it off for quick, simple tasks.")],
        ["Images", h("td", { class: "wrap" }, "Image parts in the message content"), h("td", { class: "wrap" }, visionModel ? `Use ${visionModel.name}${roleFor(visionModel.name).length ? ` (role ${roleFor(visionModel.name)[0]})` : ""}.` : "Needs a vision model (e.g. gemma3) — none is offered on this server yet.")],
        ["Conversation", h("td", { class: "wrap" }, "Send the earlier messages again each time"), h("td", { class: "wrap" }, "The server keeps no history: your app owns the conversation.")],
      ]))));
  modes.id = "d-modes";

  // ── Skills & plugins ──
  const skillMd = `
---
name: product-listing
description: Write an online shop listing for a product. Use when asked to describe or list a product for sale.
---

# Product listing

Write the listing in this order:
1. A title under 60 characters
2. Three short bullet points on what the buyer gets
3. Materials and size
4. Price in FCFA, if given

Keep it friendly and plain. Answer in the customer's language (English or French).`;

  const skills = panel("Skills & plugins", h("div", { class: "panel-body stack" },
    h("div", { class: "prose" },
      h("p", {}, "This server only runs the models. ", h("strong", {}, "Skills and plugins live in the tool or app that calls it"),
        ", so each app can use the ones it wants, and you add or change them without touching the server."),
      h("ul", {},
        h("li", {}, h("strong", {}, "A skill"), " is a folder with a ", h("code", {}, "SKILL.md"),
          " file: a name, a one-line description of when to use it, and instructions. The model sees only the descriptions, and reads a skill's instructions when a task matches."),
        h("li", {}, h("strong", {}, "A plugin"), " bundles skills with tools: MCP servers, subagents and hooks, or in your own app, functions the model can call.")),
      h("p", {}, "Pick the way that matches where you use the models. All three need a model with the ",
        capChip("tools"), " capability; ", h("code", {}, generalName), " has it.")),

    example("1. In Claude Code", "Claude Code loads skills, plugins and MCP servers itself. Point it at this server and use them as usual.", [
      ["Claude Code", `
# 1. Point Claude Code at this server (add these to your shell profile)
export ANTHROPIC_BASE_URL=${BASE}
export ANTHROPIC_API_KEY=${KEY}

# 2. Add a skill: one folder per skill, with a SKILL.md inside
mkdir -p .claude/skills/product-listing      # this project only
mkdir -p ~/.claude/skills/product-listing    # every project

# 3. Start it. Plugins and MCP servers are added the usual way, inside Claude Code:
claude
#   /plugin install <plugin>@<marketplace>
#   /mcp`, "bash"],
      ["SKILL.md", skillMd, "markdown"],
      ["Plugin folder", `
my-plugin/
├── .claude-plugin/
│   └── plugin.json        # name, version, description (optional)
├── skills/
│   └── product-listing/
│       └── SKILL.md
├── agents/                # subagents (optional)
├── hooks/hooks.json       # hooks (optional)
└── .mcp.json              # MCP servers, i.e. extra tools (optional)`, "tree"],
    ], "Claude Code sends long instructions with every request: raise NUM_CTX to 32768 in the server's .env, and expect slower answers than from Claude itself.", "connect"),

    example("2. In your app, with the Claude Agent SDK", "The Agent SDK is Claude Code as a library: your app gets the same skills, plugins, MCP servers and tools, running on this server's models.", [
      ["Python", `
# pip install claude-agent-sdk
import asyncio
import os

from claude_agent_sdk import ClaudeAgentOptions, ResultMessage, query

options = ClaudeAgentOptions(
    cwd="/srv/my-app",                    # skills in /srv/my-app/.claude/skills/
    setting_sources=["project"],          # load the project's skills
    skills="all",                         # or a list: ["product-listing"]
    plugins=[{"type": "local", "path": "./plugins/my-plugin"}],
    allowed_tools=["Read", "Grep", "Glob"],
    env={"ANTHROPIC_BASE_URL": "${BASE}", "ANTHROPIC_API_KEY": os.environ["AI_API_KEY"]},
)

async def main():
    async for message in query(prompt="Write a listing for a raffia tote bag", options=options):
        if isinstance(message, ResultMessage) and message.subtype == "success":
            print(message.result)

asyncio.run(main())`, "python"],
      ["TypeScript", `
// npm install @anthropic-ai/claude-agent-sdk
import { query } from "@anthropic-ai/claude-agent-sdk";

for await (const message of query({
  prompt: "Write a listing for a raffia tote bag",
  options: {
    cwd: "/srv/my-app",                   // skills in /srv/my-app/.claude/skills/
    settingSources: ["project"],
    skills: "all",
    plugins: [{ type: "local", path: "./plugins/my-plugin" }],
    allowedTools: ["Read", "Grep", "Glob"],
    env: { ...process.env, ANTHROPIC_BASE_URL: "${BASE}", ANTHROPIC_API_KEY: process.env.AI_API_KEY },
  },
})) {
  if (message.type === "result" && message.subtype === "success") console.log(message.result);
}`, "typescript"],
    ], "Plugin skills are named plugin-name:skill-name. To run one directly, send it as the prompt, e.g. \"/my-plugin:product-listing\".", "connect"),

    example("3. In any app, with the OpenAI SDK", "No agent framework needed: keep skills as SKILL.md folders and plugins as functions, and give the model one tool to open a skill.", [
      ["Python", `
# Skills: ./skills/<name>/SKILL.md  ·  Plugins: tools plus the code that runs them
import json
import os
import pathlib

from openai import OpenAI

ai = OpenAI(base_url="${BASE}/v1", api_key=os.environ["AI_API_KEY"])

def load_skill(path):
    text = path.read_text(encoding="utf-8")
    header = text.split("---")[1]
    meta = dict(line.split(":", 1) for line in header.strip().splitlines())
    return meta["name"].strip(), {"description": meta["description"].strip(), "text": text}

SKILLS = dict(load_skill(p) for p in pathlib.Path("skills").glob("*/SKILL.md"))

# A plugin: what the model sees, and the function your app runs.
PLUGINS = {
    "get_order_status": ({
        "type": "function",
        "function": {
            "name": "get_order_status",
            "description": "Status of a customer's order",
            "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
        },
    }, lambda args: {"order_id": args["order_id"], "status": "shipped"}),
}

use_skill = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": "Read a skill's instructions before doing a task it covers.",
        "parameters": {"type": "object", "properties": {"name": {"type": "string", "enum": list(SKILLS)}}, "required": ["name"]},
    },
}
tools = [use_skill] + [spec for spec, _ in PLUGINS.values()]
skill_list = "".join(f"\\n- {name}: {s['description']}" for name, s in SKILLS.items())
messages = [
    {"role": "system", "content": "When a task matches one of these skills, call use_skill first:" + skill_list},
    {"role": "user", "content": "Write a listing for a raffia tote bag, 8500 FCFA"},
]

for _ in range(8):                        # stop runaway loops
    reply = ai.chat.completions.create(model="${generalName}", messages=messages, tools=tools).choices[0].message
    messages.append(reply)
    if not reply.tool_calls:
        break
    for call in reply.tool_calls:
        args = json.loads(call.function.arguments)
        if call.function.name == "use_skill":
            result = SKILLS[args["name"]]["text"]
        else:
            result = json.dumps(PLUGINS[call.function.name][1](args))
        messages.append({"role": "tool", "tool_call_id": call.id, "content": result})

print(reply.content)`, "python"],
      ["SKILL.md", skillMd, "markdown"],
    ], "Only the skill descriptions go in every request; a skill's full text is sent only when the model asks for it, which keeps requests short and fast on a CPU.", "connect")));
  skills.id = "d-skills";

  // ── Examples ──
  const ex = [];
  ex.push(example("Chat — ask a question", "The basic request. Works the same for any model or role.", [
    ["Python", `
# pip install openai
from openai import OpenAI

ai = OpenAI(base_url="${BASE}/v1", api_key="${KEY}")

reply = ai.chat.completions.create(
    model="${generalName}",
    messages=[
        {"role": "system", "content": "You are a helpful assistant for a small online shop. Answer briefly."},
        {"role": "user", "content": "Write a welcome message for new customers."},
    ],
)
print(reply.choices[0].message.content)`, "python"],
    ["JavaScript", `
// npm install openai   (server-side: Node, Next.js route, Express…)
import OpenAI from "openai";

const ai = new OpenAI({ baseURL: "${BASE}/v1", apiKey: process.env.AI_API_KEY });

const reply = await ai.chat.completions.create({
  model: "${generalName}",
  messages: [
    { role: "system", content: "You are a helpful assistant for a small online shop. Answer briefly." },
    { role: "user", content: "Write a welcome message for new customers." },
  ],
});
console.log(reply.choices[0].message.content);`, "javascript"],
    ["PHP", `
<?php
// Plain PHP — works in Laravel, WordPress, any PHP app.
$ch = curl_init("${BASE}/v1/chat/completions");
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 300,
    CURLOPT_HTTPHEADER => [
        "Authorization: Bearer " . getenv("AI_API_KEY"),
        "Content-Type: application/json",
    ],
    CURLOPT_POSTFIELDS => json_encode([
        "model" => "${generalName}",
        "messages" => [
            ["role" => "user", "content" => "Write a welcome message for new customers."],
        ],
    ]),
]);
$reply = json_decode(curl_exec($ch), true);
echo $reply["choices"][0]["message"]["content"];`, "php"],
    ["curl", `
curl ${BASE}/v1/chat/completions \\
  -H "Authorization: Bearer ${KEY}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${generalName}",
    "messages": [{"role": "user", "content": "Write a welcome message for new customers."}]
  }'`, "bash"],
  ], "Responses take seconds to minutes on this CPU server: call from your backend, show a loading state, and set a generous timeout (2–5 minutes)."));

  ex.push(example("Streaming — show the answer as it's written", "Best for chat screens: users see text immediately instead of waiting for the whole answer.", [
    ["Python", `
stream = ai.chat.completions.create(
    model="${generalName}",
    messages=[{"role": "user", "content": "Explain what an API is, in three sentences."}],
    stream=True,
)
for chunk in stream:
    if chunk.choices and chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="", flush=True)`, "python"],
    ["JavaScript", `
const stream = await ai.chat.completions.create({
  model: "${generalName}",
  messages: [{ role: "user", content: "Explain what an API is, in three sentences." }],
  stream: true,
});
for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}`, "javascript"],
    ["Next.js route", `
// app/api/chat/route.ts — your browser code calls /api/chat; the key stays on the server.
import OpenAI from "openai";

const ai = new OpenAI({ baseURL: "${BASE}/v1", apiKey: process.env.AI_API_KEY });

export async function POST(req: Request) {
  const { messages } = await req.json();
  const stream = await ai.chat.completions.create({ model: "${generalName}", messages, stream: true });
  const encoder = new TextEncoder();
  return new Response(new ReadableStream({
    async start(controller) {
      for await (const chunk of stream) {
        controller.enqueue(encoder.encode(chunk.choices[0]?.delta?.content ?? ""));
      }
      controller.close();
    },
  }), { headers: { "Content-Type": "text/plain; charset=utf-8" } });
}`, "typescript"],
  ]));

  ex.push(example("JSON output — structured data your code can use", "E.g. turn a merchant's notes into product fields, or a meeting into tasks.", [
    ["Python", `
import json

reply = ai.chat.completions.create(
    model="${generalName}",
    response_format={"type": "json_object"},
    messages=[
        {"role": "system", "content": "Extract product details. Reply with JSON: "
                                      '{"name": str, "price_fcfa": int|null, "colors": [str], "category": str}'},
        {"role": "user", "content": "Sac en raphia tissé main, 12 000 FCFA, dispo en naturel et noir."},
    ],
)
product = json.loads(reply.choices[0].message.content)
print(product["name"], product["price_fcfa"])`, "python"],
    ["JavaScript", `
const reply = await ai.chat.completions.create({
  model: "${generalName}",
  response_format: { type: "json_object" },
  messages: [
    { role: "system", content: 'Extract tasks. Reply with JSON: {"tasks": [{"title": str, "owner": str|null}]}' },
    { role: "user", content: meetingNotes },
  ],
});
const { tasks } = JSON.parse(reply.choices[0].message.content);`, "javascript"],
  ], "Always validate the parsed JSON before saving it — models can leave out fields."));

  ex.push(example("Tool calling — let the model use your data", `The model decides to call your function; your code runs it and sends the result back.${toolModel ? "" : " Needs a model with the “tools” capability."}`, [
    ["Python", `
import json

tools = [{
    "type": "function",
    "function": {
        "name": "get_order_status",
        "description": "Status of a customer's order",
        "parameters": {"type": "object", "properties": {"order_id": {"type": "string"}}, "required": ["order_id"]},
    },
}]
messages = [{"role": "user", "content": "Where is my order VN-1042?"}]

reply = ai.chat.completions.create(model="${toolModel ? toolModel.name : generalName}", messages=messages, tools=tools)
call = reply.choices[0].message.tool_calls[0]           # the model asks: get_order_status("VN-1042")
args = json.loads(call.function.arguments)

status = look_up_order(args["order_id"])                # your own code / database
messages += [reply.choices[0].message,
             {"role": "tool", "tool_call_id": call.id, "content": json.dumps(status)}]

final = ai.chat.completions.create(model="${toolModel ? toolModel.name : generalName}", messages=messages, tools=tools)
print(final.choices[0].message.content)                 # "Your order VN-1042 was shipped today…"`, "python"],
  ]));

  if (embed.length) {
    ex.push(example("Embeddings — search by meaning", "Find products, documents or past questions that mean the same thing, even with different words or languages.", [
      ["Python", `
import math

products = ["Handwoven raffia tote bag", "Shea butter soap 150 g", "Leather sandals"]
vectors = [d.embedding for d in ai.embeddings.create(model="${embedName}", input=products).data]
# Store the vectors with your products (e.g. in Postgres with pgvector). Then, for a search:

query = ai.embeddings.create(model="${embedName}", input="sac en raphia").data[0].embedding

def cosine(a, b):
    return sum(x * y for x, y in zip(a, b)) / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)))

best = max(range(len(products)), key=lambda i: cosine(query, vectors[i]))
print(products[best])   # → Handwoven raffia tote bag`, "python"],
      ["JavaScript", `
const { data } = await ai.embeddings.create({ model: "${embedName}", input: ["raffia bag", "sac en raphia"] });
const [a, b] = data.map((d) => d.embedding);
// Compare with cosine similarity; store vectors in pgvector, Qdrant, etc.`, "javascript"],
    ], null, "embed"));
  }

  if (visionModel) {
    ex.push(example("Images — describe a product photo", `Uses ${visionModel.name}, which can read images.`, [
      ["Python", `
import base64

with open("product.jpg", "rb") as f:
    image = base64.b64encode(f.read()).decode()

reply = ai.chat.completions.create(
    model="${roleFor(visionModel.name)[0] || visionModel.name}",
    messages=[{"role": "user", "content": [
        {"type": "text", "text": "Describe this product for an online shop listing."},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
    ]}],
)
print(reply.choices[0].message.content)`, "python"],
    ]));
  }

  if (speech.model) {
    ex.push(example("Speech to text — transcribe a recording", "Meeting recordings, voice notes from WhatsApp, interviews. Add language to skip detection; prompt helps with names.", [
      ["Python", `
ai = OpenAI(base_url="${BASE}/v1", api_key="${KEY}", timeout=1800)

with open("meeting.mp3", "rb") as f:
    result = ai.audio.transcriptions.create(
        model="whisper-1",                # any name works; the server uses ${speech.model}
        file=f,
        language="fr",                    # optional
        prompt="Forgefy, Vndor, Mobile Money",
    )
print(result.text)

# Subtitles: response_format="srt" or "vtt" · Timed segments: response_format="verbose_json"
# Any language → English: ai.audio.translations.create(model="whisper-1", file=f)`, "python"],
      ["JavaScript", `
import fs from "node:fs";

const result = await ai.audio.transcriptions.create({
  model: "whisper-1",
  file: fs.createReadStream("voice-note.ogg"),
});
console.log(result.text);`, "javascript"],
      ["curl", `
curl ${BASE}/v1/audio/transcriptions \\
  -H "Authorization: Bearer ${KEY}" \\
  -F file=@meeting.mp3 \\
  -F language=fr \\
  -F response_format=verbose_json`, "bash"],
    ], "On a CPU, transcription runs at roughly real time (10 minutes of audio ≈ 10 minutes), one recording at a time. Up to 100 MB per file; split recordings longer than ~20 minutes.", "speech"));
  }

  ex.push(example("Forgefy — use these models for blueprints and builds", "Forgefy's backend already speaks the Ollama format: only its settings change.", [
    ["forgefy-backend/.env", `
OLLAMA_URL=${BASE}
OLLAMA_API_KEY=${KEY}          # a key created with app "forgefy"
OLLAMA_MODEL=${generalName}
BP_MODEL=Qwen3                 # blueprints on your models
BUILD_MODEL=Qwen3              # builds on your models (slow on CPU)`, "env"],
  ], null, "connect"));

  ex.push(example("Claude Code and the Anthropic SDK", "Claude Code keeps its own skills, plugins and MCP servers; only the model runs here.", [
    ["Claude Code", `
export ANTHROPIC_BASE_URL=${BASE}
export ANTHROPIC_API_KEY=${KEY}
claude`, "bash"],
    ["Python (Anthropic SDK)", `
# pip install anthropic
import anthropic

client = anthropic.Anthropic(base_url="${BASE}", api_key="${KEY}")
reply = client.messages.create(
    model="${generalName}", max_tokens=1024,
    system="You are a concise assistant.",
    messages=[{"role": "user", "content": "Summarise this in one line: …"}],
)
print(reply.content[0].text)`, "python"],
  ], "Claude Code sends very long instructions; it works best on a server with NUM_CTX raised to 32768 and plenty of RAM.", "connect"));

  ex.push(example("No-code tools — n8n, Open WebUI, Continue, Flowise…", "Any tool with an “OpenAI-compatible” connection works with these settings.", [
    ["Settings", `
Provider / type:  OpenAI-compatible (or "OpenAI" with a custom base URL)
Base URL:         ${BASE}/v1
API key:          ${KEY}
Model:            ${generalName}   (or any model name from the Models section)`, "text"],
  ], null, "connect"));

  const examples = panel("Examples", h("div", { class: "panel-body stack" }, ...ex));
  examples.id = "d-examples";

  // ── Errors & limits ──
  const limits = panel("Errors & limits", h("div", {},
    table(["Status", "Meaning", "What your app should do"], [
      [h("code", {}, "401"), "Missing, wrong or revoked key", "Check the key and how it's sent"],
      [h("code", {}, "403"), "The key isn't allowed to use this API", "Use a key with the right permission"],
      [h("code", {}, "422"), "Bad request, or a model that isn't offered (the message lists what is)", "Fix the request"],
      [h("code", {}, "429"), "Rate limit for this key reached", h("td", { class: "wrap" }, "Wait the ", h("code", {}, "Retry-After"), " seconds, then retry")],
      [h("code", {}, "503"), "Busy (requests queue one at a time) or a model is unavailable", h("td", { class: "wrap" }, "Retry after ", h("code", {}, "Retry-After"), " seconds")],
    ]),
    h("div", { class: "panel-body prose" },
      h("ul", {},
        h("li", {}, h("strong", {}, "Speed: "), "this server runs on CPUs — a short answer takes seconds, a long one a minute or more. The first request to a model that isn't in memory adds 10–60 s."),
        h("li", {}, h("strong", {}, "One at a time: "), "requests are processed in turn; others wait in a queue (up to 2 minutes) before getting a 503."),
        h("li", {}, h("strong", {}, "Privacy: "), "only who, when, which model and how many tokens are recorded — never prompts, answers or audio."),
        h("li", {}, h("strong", {}, "Keys: "), "keep them on your servers. A key in a website or mobile app can be copied by anyone.")))));
  limits.id = "d-limits";

  c.append(h("div", { class: "docs stack" }, toc, quick, models, key, modes, skills, examples, limits));
}
