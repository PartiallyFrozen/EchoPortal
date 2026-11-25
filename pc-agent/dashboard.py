"""Jarvis: the local web interface for the PC agent.

A single self-contained document – no CDN, no build step – styled after the major AI assistants:
a quiet conversation sidebar, a centered reading column, full-width assistant messages with a
typing caret, tool-call traces, inline approval cards, and a floating composer.

It talks to the same hub the Spots use, over WebSocket on the hub port, so an Echo Spot is just one
client of this brain. Every network value is written with ``textContent``; nothing untrusted is
ever interpreted as markup.
"""
import json

import ui_custom

REFRESH_MS = 5000

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis · PC agent</title>
<style>
:root {
  color-scheme: dark;
  --bg: #171614;
  --sidebar: #1d1c19;
  --surface: #24231f;
  --surface-2: #2b2a26;
  --line: #38362f;
  --line-soft: #2d2b26;
  --text: #f4f1ea;
  --text-dim: #c9c4b8;
  --muted: #989287;
  --accent: #d97757;
  --accent-soft: #9f7aea;
  --ok: #78c2a4;
  --warn: #e0aa63;
  --bad: #e07a72;
  --radius: 14px;
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--text);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
  -webkit-font-smoothing: antialiased;
}
button, input, textarea { font: inherit; color: inherit; }
button { cursor: pointer; }
button:disabled { cursor: default; opacity: .45; }
.app { display: flex; height: 100vh; overflow: hidden; }

/* ---------- conversation sidebar ---------- */
.sidebar {
  width: 268px; flex: 0 0 268px; display: flex; flex-direction: column;
  background: var(--sidebar); border-right: 1px solid var(--line-soft); padding: 16px 12px 12px;
}
.brand { display: flex; align-items: center; gap: 10px; padding: 2px 8px 18px; }
.mark {
  width: 30px; height: 30px; border-radius: 9px; display: grid; place-items: center;
  background: linear-gradient(140deg, var(--accent), var(--accent-soft));
  color: #171614; font-size: 16px; font-weight: 700;
}
.brand-name { font-weight: 650; font-size: 15px; letter-spacing: -.01em; }
.brand-sub { font-size: 10px; color: var(--muted); letter-spacing: .1em; text-transform: uppercase; }
.new-chat {
  display: flex; align-items: center; gap: 8px; width: 100%; padding: 9px 12px; margin-bottom: 18px;
  background: var(--surface); border: 1px solid var(--line); border-radius: 11px;
  color: var(--text-dim); text-align: left; font-size: 13px;
}
.new-chat:hover { border-color: var(--accent); color: var(--text); }
.nav-title { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); padding: 0 10px 7px; }
.nav-group { margin-bottom: 18px; min-height: 0; }
.recents { max-height: 34vh; overflow-y: auto; }
.nav-item {
  display: block; width: 100%; padding: 7px 10px; border: 0; border-radius: 9px;
  background: transparent; color: var(--text-dim); text-align: left; font-size: 12.5px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.nav-item:hover { background: var(--surface); color: var(--text); }
.nav-item.active { background: var(--surface-2); color: var(--text); }
.recent-row { display: flex; align-items: center; gap: 4px; }
.recent-row .nav-item { flex: 1; min-width: 0; padding-right: 8px; }
.row-del {
  flex: 0 0 22px; width: 22px; height: 22px; border: 0; border-radius: 6px;
  background: transparent; color: var(--muted); font-size: 15px; line-height: 1;
}
.row-del:hover { background: var(--surface-2); color: var(--bad); }
.vote {
  flex: 0 0 22px; width: 22px; height: 22px; border: 0; border-radius: 6px;
  background: transparent; color: var(--muted); font-size: 11px; line-height: 1;
}
.vote:hover { background: var(--surface-2); color: var(--ok); }
.vote.down:hover { color: var(--bad); }
.card-row { position: relative; }
.card-row .card-item { padding-right: 30px; margin-bottom: 7px; }
.card-row .row-del { position: absolute; top: 7px; right: 7px; }
.fact-row .card-item { padding-right: 60px; }
.fact-row .vote { position: absolute; top: 7px; right: 7px; }
.fact-row .vote.down { right: 30px; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; background: var(--muted); margin-right: 8px; }
.dot.ok { background: var(--ok); }
.sidebar-foot { margin-top: auto; border-top: 1px solid var(--line-soft); padding: 12px 10px 2px; }
.foot-line { font-size: 11.5px; color: var(--muted); display: flex; align-items: center; gap: 8px; }

/* ---------- main ---------- */
.main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
.topbar {
  height: 54px; flex: 0 0 54px; display: flex; align-items: center; justify-content: space-between;
  gap: 16px; padding: 0 22px; border-bottom: 1px solid var(--line-soft);
}
.crumb { font-size: 13px; color: var(--muted); }
.crumb b { color: var(--text); font-weight: 600; }
.top-actions { display: flex; align-items: center; gap: 8px; }
.pill {
  font-size: 11px; padding: 5px 10px; border-radius: 999px;
  border: 1px solid var(--line); background: var(--surface); color: var(--muted);
}
.pill.ok { color: var(--ok); border-color: #2f5647; }
.pill.warn { color: var(--warn); border-color: #584629; }
.icon-btn {
  width: 30px; height: 30px; border-radius: 9px; border: 1px solid var(--line);
  background: var(--surface); color: var(--muted); font-size: 15px; line-height: 1;
}
.icon-btn:hover { color: var(--text); border-color: var(--accent); }
.model-select {
  max-width: 210px; padding: 6px 10px; border-radius: 9px; border: 1px solid var(--line);
  background: var(--surface); color: var(--text-dim); font-size: 12px; outline: 0;
}
.model-select:focus { border-color: var(--accent); color: var(--text); }
.model-select:disabled { opacity: .5; }
.workspace { flex: 1; display: flex; min-height: 0; }
.canvas { flex: 1; min-width: 0; display: flex; flex-direction: column; }

/* ---------- thread ---------- */
.thread { flex: 1; overflow-y: auto; padding: 34px 0 10px; }
.thread-inner { max-width: 760px; margin: 0 auto; padding: 0 26px; }
.hero { padding: 8vh 0 6vh; }
.hero-mark {
  width: 44px; height: 44px; border-radius: 13px; display: grid; place-items: center;
  background: linear-gradient(140deg, var(--accent), var(--accent-soft)); color: #171614;
  font-size: 22px; font-weight: 700; margin-bottom: 20px;
}
.hero h1 { font-size: 30px; line-height: 1.2; letter-spacing: -.03em; font-weight: 600; margin: 0 0 10px; }
.hero p { color: var(--muted); max-width: 560px; margin: 0; font-size: 14px; }
.hero .caps { font-size: 11px; letter-spacing: .12em; text-transform: uppercase; color: var(--accent); margin-bottom: 10px; }
.msg { margin: 0 0 30px; }
.msg.user { display: flex; justify-content: flex-end; }
.user-bubble {
  max-width: 78%; background: var(--surface); border: 1px solid var(--line);
  border-radius: 16px 16px 5px 16px; padding: 11px 16px; white-space: pre-wrap; word-break: break-word;
}
.assistant { display: flex; gap: 14px; }
.assistant .mark-sm {
  flex: 0 0 28px; width: 28px; height: 28px; border-radius: 8px; display: grid; place-items: center;
  background: linear-gradient(140deg, var(--accent), var(--accent-soft)); color: #171614;
  font-size: 13px; font-weight: 700; margin-top: 2px;
}
.assistant-body { flex: 1; min-width: 0; }
.assistant-text { white-space: pre-wrap; word-break: break-word; font-size: 15px; line-height: 1.75; color: var(--text); }
.caret {
  display: inline-block; width: 7px; height: 15px; margin-left: 2px; vertical-align: -2px;
  background: var(--accent); animation: blink 1s steps(2, start) infinite;
}
@keyframes blink { to { visibility: hidden; } }
.thinking { display: flex; gap: 5px; align-items: center; height: 26px; }
.thinking i { width: 5px; height: 5px; border-radius: 50%; background: var(--muted); animation: pulse 1.2s infinite; }
.thinking i:nth-child(2) { animation-delay: .18s; }
.thinking i:nth-child(3) { animation-delay: .36s; }
@keyframes pulse { 0%, 60%, 100% { opacity: .25; } 30% { opacity: 1; } }
.trace { margin: 10px 0 12px; display: flex; flex-wrap: wrap; gap: 6px; }
.trace-chip {
  display: inline-flex; align-items: center; gap: 7px; font-size: 11.5px; color: var(--muted);
  background: var(--surface); border: 1px solid var(--line-soft); border-radius: 999px; padding: 4px 11px;
}
.trace-chip b { color: var(--text-dim); font-weight: 550; }
.approval {
  border: 1px solid #57456f; background: linear-gradient(150deg, #2a2334, #22202a);
  border-radius: var(--radius); padding: 16px 18px; margin: 14px 0 4px;
}
.approval h4 { margin: 0 0 6px; font-size: 12px; letter-spacing: .1em; text-transform: uppercase; color: var(--accent-soft); }
.approval p { margin: 0; font-size: 14px; color: var(--text-dim); }
.approval .actions { display: flex; gap: 8px; margin-top: 14px; flex-wrap: wrap; }
.btn {
  border-radius: 9px; border: 1px solid var(--line); background: var(--surface-2);
  padding: 7px 16px; font-size: 13px; color: var(--text-dim);
}
.btn:hover { border-color: var(--accent); color: var(--text); }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #1a1613; font-weight: 600; }
.btn.primary:hover { background: #e58a6d; color: #1a1613; }
.approval .meta { font-size: 11px; color: var(--muted); margin-top: 10px; }

/* ---------- composer ---------- */
.composer-zone { flex: 0 0 auto; padding: 8px 26px 20px; background: linear-gradient(0deg, var(--bg) 70%, rgba(23,22,20,0)); }
.composer-shell { max-width: 760px; margin: 0 auto; }
.suggestions { display: flex; gap: 7px; flex-wrap: wrap; margin-bottom: 10px; }
.suggestion {
  font-size: 12px; color: var(--muted); background: transparent; border: 1px solid var(--line-soft);
  border-radius: 999px; padding: 5px 12px;
}
.suggestion:hover { color: var(--text); border-color: var(--line); background: var(--surface); }
.composer {
  background: var(--surface); border: 1px solid var(--line); border-radius: 16px;
  padding: 12px 14px 10px; box-shadow: 0 12px 40px rgba(0, 0, 0, .22);
}
.composer:focus-within { border-color: #6b5a4d; }
.composer textarea {
  width: 100%; border: 0; outline: 0; resize: none; background: transparent;
  min-height: 44px; max-height: 180px; line-height: 1.5; font-size: 15px;
}
.composer textarea::placeholder { color: var(--muted); }
.composer-bar { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.hint { font-size: 11px; color: var(--muted); }
.send {
  border: 0; border-radius: 9px; background: var(--accent); color: #1a1613;
  font-weight: 650; font-size: 13px; padding: 8px 15px;
}
.send:hover:not(:disabled) { background: #e58a6d; }
.disclaimer { text-align: center; font-size: 10.5px; color: var(--muted); margin-top: 9px; }

/* ---------- inspector ---------- */
.inspector {
  width: 300px; flex: 0 0 300px; border-left: 1px solid var(--line-soft); background: var(--sidebar);
  overflow-y: auto; padding: 20px 18px;
}
.inspector[hidden] { display: none; }
.inspector h2 { font-size: 15px; margin: 0 0 4px; font-weight: 600; }
.inspector .sub { font-size: 11.5px; color: var(--muted); margin: 0 0 20px; }
.panel { border-top: 1px solid var(--line-soft); padding-top: 14px; margin-top: 16px; }
.panel h3 { font-size: 10px; letter-spacing: .12em; text-transform: uppercase; color: var(--muted); margin: 0 0 10px; }
.kv { display: grid; grid-template-columns: auto 1fr; gap: 4px 12px; font-size: 12.5px; }
.kv .k { color: var(--muted); }
.kv .v { text-align: right; word-break: break-word; }
.card-item { border: 1px solid var(--line-soft); background: var(--surface); border-radius: 10px; padding: 9px 11px; margin-bottom: 7px; }
.card-item .title { font-size: 12.5px; }
.card-item .meta { font-size: 10.5px; color: var(--muted); margin-top: 2px; }
.empty-mini { font-size: 12px; color: var(--muted); font-style: italic; }
.field { margin-bottom: 11px; }
.field label { display: block; font-size: 11px; color: var(--muted); margin-bottom: 4px; }
.field input, .field select {
  width: 100%; border: 1px solid var(--line); border-radius: 8px; background: var(--bg);
  color: var(--text); padding: 7px 9px; font-size: 12.5px; outline: 0;
}
.field input:focus, .field select:focus { border-color: var(--accent); }
.field-row { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
.bar { height: 5px; border-radius: 999px; background: var(--surface-2); overflow: hidden; margin-top: 5px; }
.bar > i { display: block; height: 100%; background: var(--accent); }

/* ---------- token modal ---------- */
.modal { position: fixed; inset: 0; display: grid; place-items: center; background: rgba(10, 9, 8, .78); z-index: 20; }
.modal[hidden] { display: none; }
.modal-card {
  width: min(420px, 90vw); background: var(--surface); border: 1px solid var(--line);
  border-radius: 18px; padding: 24px; box-shadow: 0 30px 90px rgba(0, 0, 0, .5);
}
.modal-card h2 { margin: 0 0 6px; font-size: 18px; font-weight: 600; }
.modal-card p { color: var(--muted); font-size: 13px; margin: 0 0 16px; }
.modal-card input {
  width: 100%; border: 1px solid var(--line); border-radius: 10px; background: var(--bg);
  padding: 11px 12px; outline: 0; margin-bottom: 12px;
}
.modal-card input:focus { border-color: var(--accent); }
.toast { position: fixed; bottom: 22px; left: 50%; transform: translateX(-50%); background: var(--surface-2);
  border: 1px solid var(--line); border-radius: 10px; padding: 9px 14px; font-size: 12px; z-index: 30; }
.toast[hidden] { display: none; }
@media (max-width: 1100px) { .inspector { display: none; } }
@media (max-width: 780px) { .sidebar { display: none; } .thread-inner, .composer-zone { padding-left: 16px; padding-right: 16px; } }
</style>
<link id="user-theme" rel="stylesheet">
</head>
<body>
<div class="app">
  <aside class="sidebar">
    <div class="brand">
      <div class="mark">&#10022;</div>
      <div><div class="brand-name">Jarvis</div><div class="brand-sub">PC agent</div></div>
    </div>
    <button class="new-chat" id="new-chat"><span>&#43;</span> New conversation</button>
    <div class="nav-group">
      <div class="nav-title">Recents</div>
      <div class="recents" id="recents"></div>
    </div>
    <div class="nav-group">
      <div class="nav-title">Workspace</div>
      <button class="nav-item" data-panel="devices">&#9679; Devices</button>
      <button class="nav-item" data-panel="settings">&#9679; Settings</button>
      <button class="nav-item" data-panel="memory">&#9679; Memory</button>
      <button class="nav-item" data-panel="observability">&#9679; Observability</button>
    </div>
    <div class="sidebar-foot">
      <div class="foot-line"><span class="dot" id="foot-dot"></span><span id="foot-status">connecting…</span></div>
      <div class="foot-line" style="margin-top:4px">Local · private by default</div>
    </div>
  </aside>

  <main class="main">
    <header class="topbar">
      <div class="crumb">Workspace / <b>Jarvis</b></div>
      <div class="top-actions">
        <select class="model-select" id="model-select" title="Choose assistant model" aria-label="Assistant model"></select>
        <span class="pill" id="pill-backend">…</span>
        <span class="pill" id="pill-conn">offline</span>
        <button class="icon-btn" id="clear" title="Delete all saved chats" aria-label="Delete all saved chats">&#128465;</button>
      </div>
    </header>
    <div class="workspace">
      <section class="canvas">
        <div class="thread" id="thread">
          <div class="thread-inner" id="thread-inner">
            <div class="hero" id="hero">
              <div class="hero-mark">&#10022;</div>
              <div class="caps">Jarvis · PC agent</div>
              <h1>What can I help you with?</h1>
              <p>I can see your screen, work with files, notes and tasks, control windows and media,
              search the web, manage your calendar and smart home, and speak through any connected
              Echo Spot. Actions that change something always ask first.</p>
            </div>
          </div>
        </div>
        <div class="composer-zone">
          <div class="composer-shell">
            <div class="suggestions" id="suggestions"></div>
            <form class="composer" id="composer">
              <textarea id="input" rows="1" placeholder="Message Jarvis…"></textarea>
              <div class="composer-bar">
                <span class="hint">Enter to send · Shift+Enter for a new line</span>
                <button class="send" id="send" type="submit">Send</button>
                <button class="send" id="stop" type="button" hidden style="background:var(--surface-2);color:var(--text)">Stop</button>
              </div>
            </form>
            <div class="disclaimer">Jarvis can act on this PC. Approve carefully — “Always” remembers only the exact action.</div>
          </div>
        </div>
      </section>
      <aside class="inspector" id="inspector" hidden>
        <h2 id="inspector-title">Context</h2>
        <p class="sub" id="inspector-sub">Live state from the agent</p>
        <div id="inspector-body"></div>
      </aside>
    </div>
  </main>
</div>

<div class="modal" id="modal" hidden>
  <div class="modal-card">
    <h2>Connect to the agent</h2>
    <p>Enter the agent's <code>api_token</code>. It stays in this browser session only.</p>
    <input id="token-input" type="password" autocomplete="off" placeholder="Hub token">
    <button class="send" id="token-go" type="button" style="width:100%">Connect</button>
  </div>
</div>
<div class="toast" id="toast" hidden></div>

<script>
(function () {
  "use strict";
  var plugins = [];
  var Jarvis = {
    hooks: {},
    on: function (name, fn) {
      if (typeof fn === 'function') (this.hooks[name] = this.hooks[name] || []).push(fn);
    },
    emit: function (name, a, b) {
      (this.hooks[name] || []).forEach(function (fn) { try { fn(a, b); } catch (e) {} });
    },
    plugin: function (definition) {
      if (!definition || typeof definition !== 'object') return;
      plugins.push(definition);
      var hooks = {
        onPrompt: 'prompt', onThinking: 'thinking', onTool: 'tool', onStream: 'stream',
        onConfirm: 'confirm', onReply: 'reply', onAudio: 'audio', onStatus: 'status',
        onError: 'error', onCancelled: 'cancelled', onFallback: 'fallback',
        onVerified: 'verified', onPause: 'pause', onToolError: 'tool_error',
        onSpeak: 'speak'
      };
      Object.keys(hooks).forEach(function (key) {
        if (typeof definition[key] === 'function') Jarvis.on(hooks[key], definition[key]);
      });
    },
    ui: {
      hero: function () { return $('hero'); },
      thread: function () { return $('thread-inner'); },
      body: function () { return document.body; }
    }
  };
  window.Jarvis = Jarvis;

  var INITIAL = __INITIAL_STATUS__ || {};
  var params = new URLSearchParams(location.search);
  var token = params.get("token") || sessionStorage.getItem("jarvis_token") || "";
  // The token must not survive in browser history or screenshots. The page response sets a
  // same-origin HttpOnly cookie for media/theme/plugin requests; keep a session copy for WS/API.
  if (token) {
    sessionStorage.setItem("jarvis_token", token);
    if (params.has("token")) history.replaceState({}, "", location.pathname);
  }
  var device = sessionStorage.getItem("jarvis_device") || "";
  if (!device) {
    device = "browser-" + ((window.crypto && crypto.randomUUID) ? crypto.randomUUID().slice(0, 8) : Math.random().toString(36).slice(2, 10));
    sessionStorage.setItem("jarvis_device", device);
  }
  var hubPort = __HUB_PORT__, mediaPort = __MEDIA_PORT__, REFRESH = __REFRESH_MS__;
  var ws = null, retry = 0, currentToken = "", streamEl = null, traceEl = null, busy = false;
  var scrollQueued = false;
  var hero = null, panel = "", lastStatus = INITIAL;
  var $ = function (id) { return document.getElementById(id); };

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }
  function empty(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }
  function toast(text) {
    var node = $("toast");
    node.textContent = text;
    node.hidden = false;
    setTimeout(function () { node.hidden = true; }, 2600);
  }
  function scroll() {
    if (scrollQueued) return;
    scrollQueued = true;
    requestAnimationFrame(function () {
      var t = $("thread");
      if (t) t.scrollTop = t.scrollHeight;
      scrollQueued = false;
    });
  }
  function dropHero() { if (hero && hero.parentNode) hero.parentNode.removeChild(hero); hero = null; }

  function addUser(text) {
    dropHero();
    var row = el("div", "msg user");
    row.appendChild(el("div", "user-bubble", text));
    $("thread-inner").appendChild(row);
    scroll();
  }
  function newAssistant() {
    dropHero();
    var row = el("div", "msg assistant");
    row.appendChild(el("div", "mark-sm", "✦"));
    var body = el("div", "assistant-body");
    var thinking = el("div", "thinking");
    thinking.appendChild(el("i")); thinking.appendChild(el("i")); thinking.appendChild(el("i"));
    body.appendChild(thinking);
    row.appendChild(el("div", "trace"));
    row.appendChild(body);
    $("thread-inner").appendChild(row);
    scroll();
    return { row: row, body: body, thinking: thinking, text: null, trace: row.querySelector(".trace") };
  }
  function setText(entry, text) {
    if (!entry.text) {
      if (entry.thinking && entry.thinking.parentNode) entry.thinking.parentNode.removeChild(entry.thinking);
      entry.thinking = null;
      entry.text = el("div", "assistant-text");
      entry.body.appendChild(entry.text);
    }
    var full = String(text || "").replace(/\[pause\]/g, " ");
    var prev = entry.text.textContent || "";
    if (full.length > prev.length && full.indexOf(prev) === 0) {
      entry.text.appendChild(document.createTextNode(full.slice(prev.length)));
    } else if (full !== prev) {
      entry.text.textContent = full;
    }
    scroll();
  }
  function addTrace(entry, name) {
    var chip = el("span", "trace-chip");
    chip.appendChild(el("b", "", name));
    entry.trace.appendChild(chip);
    scroll();
  }
  function finish(entry, text) {
    setText(entry, text);
    if (entry.text) entry.text.appendChild(el("span", "caret"));
    setTimeout(function () { var c = entry.text && entry.text.querySelector(".caret"); if (c) c.parentNode.removeChild(c); }, 1500);
    entry.trace = null;
  }
  function addStandalone(text) {
    var entry = newAssistant();
    finish(entry, text);
    return entry;
  }
  var audioQueue = [], audioPlaying = false, audioActive = null;
  function pumpAudio() {
    if (audioPlaying || !audioQueue.length) return;
    var rel = audioQueue.shift();
    var base = (location.protocol === "https:" ? "https://" : "http://") + location.hostname + ":" + mediaPort + "/media/";
    // The dashboard's HttpOnly cookie authorizes subresources; no token in the URL.
    var audio = new Audio(base + rel);
    audioActive = audio;
    audioPlaying = true;
    Jarvis.emit('audio', audio);
    function next() {
      if (audioActive === audio) audioActive = null;
      audioPlaying = false;
      pumpAudio();
    }
    audio.onended = next;
    audio.onerror = next;
    audio.play().catch(next);
  }
  function playAudio(rel, replace) {
    if (!rel) return;
    if (replace) { audioQueue.length = 0; if (audioActive) { audioActive.pause(); audioActive = null; } }
    audioQueue.push(rel);
    pumpAudio();
  }

  function approval(data, entry) {
    dropHero();
    var card = el("div", "approval");
    card.appendChild(el("h4", "", "Approval needed"));
    card.appendChild(el("p", "", data.text || "Run this action?"));
    var actions = el("div", "actions");
    var no = el("button", "btn", "No");
    var yes = el("button", "btn primary", "Yes");
    var always = el("button", "btn", "Always");
    function answer(ok, remember) {
      send({ op: "confirm", token: data.token || currentToken, yes: ok, always: !!remember });
      if (card.parentNode) card.parentNode.removeChild(card);
    }
    no.onclick = function () { answer(false, false); };
    yes.onclick = function () { answer(true, false); };
    always.onclick = function () { answer(true, true); };
    actions.appendChild(no); actions.appendChild(yes); actions.appendChild(always);
    card.appendChild(actions);
    if (data.expires_s) {
      var left = parseInt(data.expires_s, 10);
      var meta = el("div", "meta", "Expires in " + left + "s");
      card.appendChild(meta);
      var timer = setInterval(function () {
        left -= 1;
        if (left <= 0 || !card.parentNode) { clearInterval(timer); return; }
        meta.textContent = "Expires in " + left + "s";
      }, 1000);
    }
    if (entry && entry.body) entry.body.appendChild(card);
    else $("thread-inner").appendChild(card);
    scroll();
  }

  function handle(data) {
    var dev = data.device || "";
    if (dev && dev !== device) return;
    if (data.token) currentToken = data.token;
    switch (data.op) {
      case "status":
        renderStatus(data);
        Jarvis.emit('status', data);
        break;
      case "prompt":
        Jarvis.emit('prompt', data.text || '');
        break;
      case "thinking":
        Jarvis.emit('thinking');
        if (!busy) break;
        var e = newAssistant();
        traceEl = e;
        break;
      case "tool":
        if (traceEl) addTrace(traceEl, data.text || "tool");
        Jarvis.emit('tool', data.text || "tool");
        break;
      case "stream":
        if (!traceEl) traceEl = newAssistant();
        setText(traceEl, data.text || "");
        Jarvis.emit('stream', data.text || '', traceEl.text ? traceEl.text.textContent : '');
        break;
      case "confirm":
        approval(data, traceEl);
        Jarvis.emit('confirm', data);
        break;
      case "reply":
        if (traceEl) finish(traceEl, data.text || "");
        else addStandalone(data.text || "");
        Jarvis.emit('reply', data);
        if (!data.audio_segments) playAudio(data.audio, true);
        endTurn();
        break;
      case "audio":
        playAudio(data.audio || data.path, data.segment !== true);
        Jarvis.emit('audio', data.audio || data.path);
        break;
      case "speak":
        Jarvis.emit('speak', data.text || '');
        break;
      case "pause":
        if (traceEl && traceEl.text) traceEl.text.appendChild(document.createTextNode("  "));
        Jarvis.emit('pause', data);
        break;
      case "fallback":
        if (traceEl) addTrace(traceEl, "local fallback");
        Jarvis.emit('fallback', data);
        break;
      case "verified":
        if (traceEl) addTrace(traceEl, "verified");
        Jarvis.emit('verified', data);
        break;
      case "tool_error":
        if (traceEl) addTrace(traceEl, "invalid tool call");
        Jarvis.emit('tool_error', data);
        break;
      case "error":
        if (traceEl) finish(traceEl, "⚠ " + (data.text || "assistant error"));
        else addStandalone("⚠ " + (data.text || "assistant error"));
        Jarvis.emit('error', data.text || "assistant error");
        endTurn();
        break;
      case "cancelled":
        if (traceEl) finish(traceEl, "Cancelled.");
        else addStandalone("Cancelled.");
        Jarvis.emit('cancelled');
        endTurn();
        break;
      default: break;
    }
  }

  function startTurn() {
    busy = true; currentToken = ""; traceEl = null;
    $("send").disabled = true; $("send").hidden = true; $("stop").hidden = false;
  }
  function endTurn() {
    busy = false; traceEl = null;
    $("send").disabled = false; $("send").hidden = false; $("stop").hidden = true;
    scroll();
  }
  function ask(text) {
    text = (text || "").trim();
    if (!text || busy) return;
    addUser(text);
    startTurn();
    send({ op: "ask", text: text });
  }
  function send(data) {
    if (ws && ws.readyState === 1) {
      data.device = device;
      ws.send(JSON.stringify({ ch: "assistant", data: data }));
    } else { toast("Agent is offline — reconnecting…"); }
  }
  function newChat() {
    empty($("thread-inner"));
    hero = el("div", "hero");
    hero.appendChild(el("div", "hero-mark", "✦"));
    hero.appendChild(el("div", "caps", "Jarvis · PC agent"));
    hero.appendChild(el("h1", "", "What can I help you with?"));
    hero.appendChild(el("p", "", "I can see your screen, work with files, notes and tasks, " +
      "control windows and media, search the web, manage your calendar and smart home, " +
      "and speak through any connected Echo Spot. Actions that change something always ask first."));
    $("thread-inner").appendChild(hero);
    traceEl = null; currentToken = "";
  }

  function deleteChat(id) {
    if (!id || !window.confirm("Delete this saved chat?")) return;
    api("/api/assistant/history/" + encodeURIComponent(id), { method: "DELETE" })
      .then(function (r) { if (!r.ok) throw new Error("delete failed"); return r.json(); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.error || "delete failed");
        toast("Chat deleted");
        refreshSide();
      })
      .catch(function () { toast("Could not delete that chat"); });
  }
  function clearChats() {
    if (!window.confirm("Delete all saved chats? Remembered facts will be kept.")) return;
    api("/api/assistant/history", { method: "DELETE" })
      .then(function (r) { if (!r.ok) throw new Error("delete failed"); return r.json(); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.error || "delete failed");
        newChat();
        refreshSide();
        toast("Saved chats deleted");
      })
      .catch(function () { toast("Could not delete chats"); });
  }
  function rateFact(id, vote) {
    api("/api/assistant/feedback", {
      method: "POST", body: JSON.stringify({ fact_id: id, vote: vote })
    })
      .then(function (r) { if (!r.ok) throw new Error("vote failed"); return r.json(); })
      .then(function (res) {
        if (!res.ok) throw new Error(res.error || "vote failed");
        toast("Feedback saved");
        renderFacts();
      })
      .catch(function () { toast("Could not save feedback"); });
  }
  function renderFacts() {
    var host = $("fact-list");
    if (!host) return;
    empty(host);
    api("/api/assistant/facts?limit=40").then(function (r) { return r.json(); }).then(function (rows) {
      empty(host);
      if (!Array.isArray(rows) || !rows.length) {
        host.appendChild(el("div", "empty-mini", "No remembered facts yet.")); return;
      }
      rows.forEach(function (f) {
        var row = el("div", "card-row fact-row");
        var weight = f.feedback ? " · weight " + f.feedback : "";
        row.appendChild(cardItem(String(f.text || "").slice(0, 80), String(f.category || "fact") + weight));
        var down = el("button", "vote down", "▼");
        down.title = "Recall this less";
        down.onclick = function () { rateFact(f.id, "down"); };
        var up = el("button", "vote", "▲");
        up.title = "Recall this more";
        up.onclick = function () { rateFact(f.id, "up"); };
        row.appendChild(down); row.appendChild(up);
        host.appendChild(row);
      });
    }).catch(function () {});
  }
  function loadModels() {
    var select = $("model-select");
    return api("/api/assistant/models").then(function (r) { return r.json(); }).then(function (data) {
      empty(select);
      var choices = Array.isArray(data.choices) ? data.choices : [];
      choices.forEach(function (choice) {
        var option = el("option", "", choice.label || choice.id);
        option.value = choice.id;
        select.appendChild(option);
      });
      select.value = data.current || "hybrid";
      select.disabled = choices.length === 0;
      if (!select.value && choices.length) select.value = choices[0].id;
    }).catch(function () { select.disabled = true; });
  }
  function changeModel() {
    var id = $("model-select").value;
    api("/api/assistant/model", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ id: id })
    }).then(function (r) { return r.json(); }).then(function (res) {
      if (!res.ok) throw new Error(res.error || "model change failed");
      toast("Using " + (res.label || id));
      send({ op: "status" });
      loadModels();
      refreshSide();
    }).catch(function () { toast("Could not change model"); loadModels(); });
  }

  function connect() {
    if (!token) { $("modal").hidden = false; return; }
    var url = (location.protocol === "https:" ? "wss://" : "ws://") + location.hostname + ":" + hubPort +
      "/?token=" + encodeURIComponent(token) + "&device=" + encodeURIComponent(device);
    try { ws = new WebSocket(url); } catch (e) { return; }
    ws.onopen = function () {
      retry = 0;
      var conn = $("pill-conn");
      conn.textContent = "connected"; conn.className = "pill ok";
      $("foot-dot").className = "dot ok";
      $("foot-status").textContent = "connected to agent";
      send({ op: "status" });
      refreshSide();
    };
    ws.onmessage = function (event) {
      var msg; try { msg = JSON.parse(event.data); } catch (e) { return; }
      if (msg && msg.ch === "assistant") handle(msg.data || {});
    };
    ws.onclose = function () {
      var conn = $("pill-conn");
      conn.textContent = "reconnecting"; conn.className = "pill warn";
      $("foot-dot").className = "dot";
      $("foot-status").textContent = "reconnecting…";
      setTimeout(connect, Math.min(8000, 600 * Math.pow(2, retry++)));
    };
    ws.onerror = function () {};
  }

  function api(path, options) {
    options = options || {};
    options.headers = Object.assign({ "X-EchoPortal-Token": token }, options.headers || {});
    options.cache = "no-store";
    return fetch(path, options);
  }
  function renderStatus(s) {
    lastStatus = s || lastStatus;
    var s2 = lastStatus;
    var backend = $("pill-backend");
    if (s2.backend) { backend.textContent = s2.backend; backend.className = "pill ok"; }
    if (s2.ready !== undefined) {
      $("foot-status").textContent = "connected · " + (s2.ready ? "ready" : "not ready");
    }
    if (panel === "devices") renderDevices(s2);
    if (panel === "activity") renderActivity(s2);
    if (panel === "observability") renderObservability(s2);
    if (panel === "memory") renderMemory(s2);
  }
  function kv(node, pairs) {
    empty(node);
    pairs.forEach(function (pair) {
      if (pair[1] === undefined || pair[1] === null || pair[1] === "") return;
      node.appendChild(el("span", "k", pair[0]));
      node.appendChild(el("span", "v", pair[1]));
    });
  }
  function cardItem(title, meta) {
    var card = el("div", "card-item");
    card.appendChild(el("div", "title", title));
    card.appendChild(el("div", "meta", meta || ""));
    return card;
  }
  function block(title, body) {
    var section = el("div", "panel");
    section.appendChild(el("h3", "", title));
    section.appendChild(body);
    return section;
  }
  function showPanel(name) {
    panel = name;
    var inspector = $("inspector");
    inspector.hidden = false;
    document.querySelectorAll(".nav-item").forEach(function (b) { b.classList.toggle("active", b.dataset.panel === name); });
    if (name === "devices") {
      $("inspector-title").textContent = "Devices";
      $("inspector-sub").textContent = "Clients connected to this brain";
      renderDevices(lastStatus);
    } else if (name === "memory") {
      $("inspector-title").textContent = "Memory";
      $("inspector-sub").textContent = "Persistent facts and conversation";
      renderMemory(lastStatus);
    } else if (name === "settings") {
      $("inspector-title").textContent = "Settings";
      $("inspector-sub").textContent = "This client's model and voice";
      if (!$("device-key")) renderSettings();
    } else if (name === "observability") {
      $("inspector-title").textContent = "Observability";
      $("inspector-sub").textContent = "Model, tool and fallback health";
      renderObservability(lastStatus);
    } else {
      $("inspector-title").textContent = "Activity";
      $("inspector-sub").textContent = "Live agent state";
      renderActivity(lastStatus);
    }
  }
  function renderDevices(s) {
    var host = empty($("inspector-body"));
    var status = el("div", "kv");
    kv(status, [["Connected clients", s.clients], ["Known devices", s.devices], ["Hub port", s.hub_port], ["Media port", s.media_port]]);
    host.appendChild(block("Status", status));
    var list = el("div");
    api("/api/assistant/devices").then(function (r) { return r.json(); }).then(function (devices) {
      empty(list);
      var keys = devices && typeof devices === "object" ? Object.keys(devices) : [];
      if (!keys.length) { list.appendChild(el("div", "empty-mini", "No devices seen yet.")); return; }
      keys.forEach(function (key) {
        var info = devices[key] || {};
        var when = info.last_seen ? new Date(info.last_seen * 1000).toLocaleString() : "never";
        list.appendChild(cardItem(info.name || key, key + " · last seen " + when));
      });
    }).catch(function () {});
    host.appendChild(block("Spots & clients", list));
  }
  function renderMemory(s) {
    var host = empty($("inspector-body"));
    var m = (s.assistant && s.assistant.memory) || {};
    var stats = el("div", "kv");
    kv(stats, [["Facts", m.facts], ["Turns", m.turns], ["Tools", s.assistant && s.assistant.tools ? s.assistant.tools.length : undefined]]);
    host.appendChild(block("Store", stats));
    var list = el("div");
    api("/api/assistant/history?limit=30").then(function (r) { return r.json(); }).then(function (rows) {
      empty(list);
      if (!Array.isArray(rows) || !rows.length) { list.appendChild(el("div", "empty-mini", "No conversation saved yet.")); return; }
      rows.slice().reverse().forEach(function (t) {
        var row = el("div", "card-row");
        row.appendChild(cardItem(String(t.user || "").slice(0, 70), String(t.assistant || "").slice(0, 90)));
        if (t.id) {
          var del = el("button", "row-del", "×");
          del.title = "Delete chat";
          del.onclick = function () { deleteChat(t.id); };
          row.appendChild(del);
        }
        list.appendChild(row);
      });
    }).catch(function () {});
    host.appendChild(block("Recent turns", list));
    var facts = el("div");
    facts.id = "fact-list";
    host.appendChild(block("Remembered facts · feedback", facts));
    renderFacts();
  }
  function renderActivity(s) {
    var host = empty($("inspector-body"));
    var info = el("div", "kv");
    kv(info, [["Host", s.host], ["Backend", s.backend], ["Voice", s.voice], ["Version", s.version], ["Clients", s.clients]]);
    host.appendChild(block("Agent", info));
    var list = el("div");
    api("/api/assistant/metrics?limit=20").then(function (r) { return r.json(); }).then(function (rows) {
      empty(list);
      if (!Array.isArray(rows) || !rows.length) { list.appendChild(el("div", "empty-mini", "No model calls yet.")); return; }
      rows.slice().reverse().forEach(function (m) {
        list.appendChild(cardItem((m.backend || "model") + " · " + (m.ms || 0) + " ms",
          (m.ts ? new Date(m.ts * 1000).toLocaleTimeString() : "") + " · " + (m.chars || 0) + " chars · " + (m.tools || 0) + " tools"));
      });
    }).catch(function () {});
    host.appendChild(block("Model calls", list));
  }
  function field(label, input) {
    var wrap = el("div", "field");
    wrap.appendChild(el("label", "", label));
    wrap.appendChild(input);
    return wrap;
  }
  function numberField(label, id, value, min, max, step) {
    var input = el("input");
    input.type = "number"; input.id = id; input.min = min; input.max = max; input.step = step || 1;
    input.value = value === undefined || value === null ? "" : value;
    return field(label, input);
  }
  function renderSettings() {
    var host = empty($("inspector-body"));
    var key = el("input"); key.type = "text"; key.id = "device-key"; key.value = device;
    host.appendChild(field("Device id", key));
    var model = el("select"); model.id = "device-model";
    host.appendChild(field("Model for this device", model));
    var style = el("select"); style.id = "device-style";
    [["jarvis", "Jarvis"], ["natural", "Natural"]].forEach(function (pair) {
      var option = el("option", "", pair[1]); option.value = pair[0]; style.appendChild(option);
    });
    host.appendChild(field("Voice style", style));
    host.appendChild(numberField("Speech pace (0.5–2.0)", "device-scale", 0.95, 0.5, 2, 0.05));
    var voiceInput = el("input"); voiceInput.type = "text"; voiceInput.id = "device-voice";
    host.appendChild(field("SAPI voice name", voiceInput));
    var row = el("div", "field-row");
    row.appendChild(numberField("Rate", "device-rate", 0, -10, 10));
    row.appendChild(numberField("Pitch", "device-pitch", 0, -8, 8));
    host.appendChild(row);
    var save = el("button", "btn primary", "Save device settings");
    save.id = "device-save";
    save.style.width = "100%";
    host.appendChild(save);

    function load() {
      var id = $("device-key").value.trim() || device;
      api("/api/assistant/device?device=" + encodeURIComponent(id)).then(function (r) { return r.json(); })
        .then(function (prefs) {
          var choices = [];
          api("/api/assistant/models").then(function (r) { return r.json(); }).then(function (data) {
            choices = Array.isArray(data.choices) ? data.choices : [];
            empty(model);
            var blank = el("option", "", "Follow global picker"); blank.value = ""; model.appendChild(blank);
            choices.forEach(function (choice) {
              var option = el("option", "", choice.label || choice.id);
              option.value = choice.id; model.appendChild(option);
            });
            model.value = prefs.model_choice || "";
          }).catch(function () {});
          style.value = prefs.tts_style || "jarvis";
          $("device-scale").value = prefs.tts_length_scale === undefined ? 0.95 : prefs.tts_length_scale;
          $("device-voice").value = prefs.tts_voice || "";
          $("device-rate").value = prefs.tts_rate === undefined ? 0 : prefs.tts_rate;
          $("device-pitch").value = prefs.tts_pitch === undefined ? 0 : prefs.tts_pitch;
        }).catch(function () {});
    }
    $("device-key").addEventListener("change", load);
    save.addEventListener("click", function () {
      var id = $("device-key").value.trim() || device;
      var values = {
        model_choice: model.value, tts_style: style.value,
        tts_length_scale: parseFloat($("device-scale").value),
        tts_voice: $("device-voice").value.trim(),
        tts_rate: parseInt($("device-rate").value, 10),
        tts_pitch: parseInt($("device-pitch").value, 10)
      };
      api("/api/assistant/device", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ device: id, values: values })
      }).then(function (r) { return r.json(); }).then(function (res) {
        if (!res.ok) throw new Error(res.error || "save failed");
        toast("Settings saved for " + id);
      }).catch(function (e) { toast(e.message || "Could not save settings"); });
    });
    load();
  }
  function renderObservability(s) {
    var host = empty($("inspector-body"));
    var a = s.assistant || {};
    var stats = el("div", "kv");
    kv(stats, [["Backend", s.backend], ["Model", s.model], ["Ready", s.ready],
               ["Fallbacks", a.fallbacks], ["Tools ok", a.tool_success],
               ["Tools failed", a.tool_failures], ["Pending", a.pending]]);
    host.appendChild(block("Health", stats));
    var list = el("div");
    api("/api/assistant/metrics?limit=20").then(function (r) { return r.json(); }).then(function (rows) {
      empty(list);
      if (!Array.isArray(rows) || !rows.length) { list.appendChild(el("div", "empty-mini", "No model calls yet.")); return; }
      rows.slice().reverse().forEach(function (m) {
        var title = (m.model || m.backend || "model") + " · " + (m.ms || 0) + " ms";
        var meta = (m.ts ? new Date(m.ts * 1000).toLocaleTimeString() : "") +
          " · " + (m.chars || 0) + " chars · tools " + (m.tools_ok || 0) + "/" + (m.tools || 0);
        if (m.fallback) meta += " · fallback";
        var card = cardItem(title, meta);
        if (m.tools) {
          var bar = el("div", "bar");
          var fill = el("i");
          fill.style.width = Math.round(100 * (m.tools_ok || 0) / m.tools) + "%";
          bar.appendChild(fill); card.appendChild(bar);
        }
        list.appendChild(card);
      });
    }).catch(function () {});
    host.appendChild(block("Model calls", list));
  }
  function renderRecents(rows) {
    var host = empty($("recents"));
    if (!Array.isArray(rows) || !rows.length) { host.appendChild(el("div", "nav-item", "No conversations yet")); return; }
    rows.slice(-12).reverse().forEach(function (t) {
      var row = el("div", "recent-row");
      var b = el("button", "nav-item", String(t.user || "Untitled").slice(0, 42));
      b.onclick = function () { $("input").value = String(t.user || ""); $("input").focus(); };
      row.appendChild(b);
      if (t.id) {
        var del = el("button", "row-del", "×");
        del.title = "Delete chat";
        del.onclick = function () { deleteChat(t.id); };
        row.appendChild(del);
      }
      host.appendChild(row);
    });
  }
  async function refreshSide() {
    try {
      var status = await (await api("/api/status")).json();
      renderStatus(status);
      var history = await (await api("/api/assistant/history?limit=12")).json();
      renderRecents(history);
      // renderStatus() already refreshes the active panel; showPanel() would duplicate
      // the render and its HTTP requests every REFRESH_MS.
    } catch (e) { /* WebSocket status keeps the header current */ }
  }

  var SUGGESTIONS = [
    "What's on my screen?", "PC stats", "What's on my calendar?",
    "Show my open windows", "Set a 5 minute timer", "Remember: my favourite colour is blue"
  ];
  SUGGESTIONS.forEach(function (label) {
    var b = el("button", "suggestion", label);
    b.onclick = function () { ask(label); };
    $("suggestions").appendChild(b);
  });
  $("composer").addEventListener("submit", function (event) {
    event.preventDefault();
    var input = $("input");
    ask(input.value);
    input.value = "";
    input.style.height = "auto";
  });
  $("input").addEventListener("keydown", function (event) {
    if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); $("composer").requestSubmit(); }
  });
  $("input").addEventListener("input", function () {
    this.style.height = "auto";
    this.style.height = Math.min(180, this.scrollHeight) + "px";
  });
  $("stop").addEventListener("click", function () { send({ op: "cancel", token: currentToken }); });
  $("new-chat").addEventListener("click", newChat);
  $("clear").addEventListener("click", clearChats);
  $("model-select").addEventListener("change", changeModel);
  document.querySelectorAll(".nav-item[data-panel]").forEach(function (b) {
    b.addEventListener("click", function () { showPanel(b.dataset.panel); });
  });
  $("token-go").addEventListener("click", function () {
    var value = $("token-input").value.trim();
    if (!value) return;
    token = value;
    sessionStorage.setItem("jarvis_token", value);
    // Let same-origin subresources authenticate immediately. A later page load replaces this
    // with the server's HttpOnly cookie.
    document.cookie = "echoportal_token=" + encodeURIComponent(value) + "; path=/; SameSite=Strict";
    history.replaceState({}, "", location.pathname);
    $("modal").hidden = true;
    connect();
    refreshSide();
  });

  function loadPlugins(names) {
    var list = Array.isArray(names) ? names : [];
    var index = 0;
    function next() {
      if (index >= list.length) {
        plugins.forEach(function (definition) {
          try { if (definition.init) definition.init(Jarvis.ui); } catch (e) {}
        });
        return;
      }
      var script = document.createElement('script');
      script.src = '/ui/plugins/' + encodeURIComponent(list[index++]);
      script.onload = next;
      script.onerror = next;
      document.head.appendChild(script);
    }
    next();
  }

  hero = $("hero");
  var theme = $("user-theme");
  if (theme) theme.href = "/ui/theme.css";
  loadPlugins(__PLUGINS__);
  renderStatus(INITIAL);
  loadModels();
  connect();
  refreshSide();
  setInterval(refreshSide, REFRESH);
})();
</script>
</body>
</html>
"""


def render(status: dict, hub_port: int = 8765, media_port: int = 8766,
           plugins: list[str] | None = None) -> str:
    """Return the complete Jarvis interface as one standalone document."""
    status = status if isinstance(status, dict) else {}
    names = plugins if plugins is not None else ui_custom.plugin_names()
    def safe_json(value) -> str:
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")

    return (
        PAGE.replace("__INITIAL_STATUS__", safe_json(status))
        .replace("__HUB_PORT__", str(int(hub_port)))
        .replace("__MEDIA_PORT__", str(int(media_port)))
        .replace("__PLUGINS__", safe_json(names))
        .replace("__REFRESH_MS__", str(REFRESH_MS))
    )
