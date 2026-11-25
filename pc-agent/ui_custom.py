"""User-editable theme and plugins for the Jarvis web interface.

Everything here lives next to config.json in ``<agent>/ui/`` so a user can edit it with Notepad
while the agent is running and just refresh the browser. No rebuild, no redeploy, no Python needed:

    ui/theme.css          CSS overrides, loaded after the built-in stylesheet
    ui/plugins/*.js       plain scripts that call ``Jarvis.plugin({...})``

A shipped example plugin draws a blob that breathes on tool calls and pulses with the spoken audio.
Delete it, rename it, or add your own files – the interface discovers them on every page load.
"""
import os
import re

import config

PLUGIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.js$")

DEFAULT_THEME = """/* Jarvis theme - edit freely, then refresh the browser.
   These variables override the built-in palette; add any CSS you like below. */
:root {
  --accent: #d97757;
  --accent-soft: #9f7aea;
  --bg: #171614;
  --sidebar: #1d1c19;
  --surface: #24231f;
  --surface-2: #2b2a26;
  --line: #38362f;
  --text: #f4f1ea;
  --muted: #989287;
}

/* Your own rules go here. Example: a soft glow behind the conversation.
.thread { background: radial-gradient(circle at 50% 0%, #2a201a, transparent 60%); }
*/
"""

DEFAULT_BLOB = r"""/* Jarvis plugin: a blob that breathes while thinking and pulses with spoken audio. */
Jarvis.plugin({
  name: 'blob',
  init: function () {
    var canvas = document.createElement('canvas');
    canvas.width = 160; canvas.height = 160;
    canvas.style.cssText = 'position:fixed;right:28px;bottom:118px;width:120px;height:120px;' +
      'pointer-events:none;z-index:5;opacity:.85;';
    document.body.appendChild(canvas);
    var ctx = canvas.getContext('2d');
    var energy = 0.12, phase = 0, analyser = null, samples = null;

    function bump(amount) { energy = Math.min(1.6, energy + amount); }
    Jarvis.on('thinking', function () { bump(0.3); });
    Jarvis.on('tool', function () { bump(0.25); });
    Jarvis.on('stream', function () { bump(0.04); });
    Jarvis.on('confirm', function () { energy = 0.6; });
    Jarvis.on('reply', function () { bump(0.9); });
    Jarvis.on('cancelled', function () { energy = 0.1; });
    Jarvis.on('audio', function (audio) {
      if (analyser) return;
      try {
        var Ctx = window.AudioContext || window.webkitAudioContext;
        var ac = new Ctx();
        var source = ac.createMediaElementSource(audio);
        analyser = ac.createAnalyser();
        analyser.fftSize = 256;
        source.connect(analyser);
        analyser.connect(ac.destination);
        samples = new Uint8Array(analyser.frequencyBinCount);
      } catch (e) { /* autoplay rules can block AudioContext; the blob still reacts to text */ }
    });

    function draw() {
      phase += 0.02 + energy * 0.04;
      if (analyser && samples) {
        analyser.getByteTimeDomainData(samples);
        var sum = 0;
        for (var i = 0; i < samples.length; i++) {
          var v = (samples[i] - 128) / 128;
          sum += v * v;
        }
        energy = Math.max(energy * 0.94, Math.min(1.6, Math.sqrt(sum / samples.length) * 3));
      } else {
        energy *= 0.965;
      }
      ctx.clearRect(0, 0, 160, 160);
      var r = 40 + energy * 18 + Math.sin(phase) * 4 + Math.sin(phase * 1.7) * 3;
      var cx = 80 + Math.sin(phase * 0.7) * 6;
      var cy = 80 + Math.cos(phase * 0.9) * 6;
      var style = getComputedStyle(document.documentElement);
      var a = style.getPropertyValue('--accent').trim() || '#d97757';
      var b = style.getPropertyValue('--accent-soft').trim() || '#9f7aea';
      var gradient = ctx.createLinearGradient(cx - r, cy - r, cx + r, cy + r);
      gradient.addColorStop(0, a);
      gradient.addColorStop(1, b);
      ctx.globalAlpha = 0.55 + Math.min(0.4, energy * 0.4);
      ctx.beginPath();
      for (var t = 0; t <= Math.PI * 2 + 0.01; t += 0.08) {
        var wobble = Math.sin(t * 3 + phase) * 5 + Math.sin(t * 5 - phase * 1.3) * 3;
        var rr = r + wobble;
        var x = cx + Math.cos(t) * rr;
        var y = cy + Math.sin(t) * rr;
        if (t === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
      }
      ctx.closePath();
      ctx.fillStyle = gradient;
      ctx.fill();
      ctx.globalAlpha = 1;
      requestAnimationFrame(draw);
    }
    draw();
  }
});
"""

PLUGIN_README = """Jarvis plugins
==============

Drop a .js file in this folder and refresh the browser. No restart, no build step.

A plugin registers itself like this:

    Jarvis.plugin({
      name: 'my-plugin',
      init: function (ui) { ... },       // called once after the page loads
      onPrompt: function (data) {},
      onThinking: function () {},
      onTool: function (name) {},
      onStream: function (chunk, wholeText) {},
      onConfirm: function (data) {},
      onReply: function (data) {},
      onAudio: function (audioElement) {},
      onStatus: function (status) {},
      onError: function (data) {},
      onCancelled: function () {},
      onFallback: function (data) {},
      onVerified: function (data) {},
      onPause: function (data) {},
      onToolError: function (data) {},
      onSpeak: function (text) {}
    });

Every `onX` hook is wired to the corresponding `Jarvis.on('x', ...)` event automatically, or you
can call `Jarvis.on(name, fn)` directly for the same event names. Audio events fire once per
sentence clip, so visualisations keep reacting while a reply is still being spoken.

Useful helpers on the `ui` argument:
  ui.hero()    -> the greeting element, or null after the first message
  ui.thread()  -> the conversation container
  ui.body()    -> document.body, for fixed overlays

`ui/theme.css` is loaded after the built-in stylesheet, so any CSS rule or :root variable there
wins. The blob.js file in this folder is just an example - delete it to remove the blob.

Server-side plugins are optional Python files in <agent>/plugins/ (or assistant_plugin_dir).
Each file defines `register(api)` and may call `api.on(name, fn)` for the same event names or
`api.register_tool(name, fn, description, parameters)` for an additional read-only tool.
"""


def folder() -> str:
    return config.data_path("ui")


def plugins_folder() -> str:
    return config.data_path("ui", "plugins")


def ensure() -> None:
    """Create editable defaults once. Never overwrite a user's changes."""
    os.makedirs(plugins_folder(), exist_ok=True)
    for path, content in (
        (os.path.join(folder(), "theme.css"), DEFAULT_THEME),
        (os.path.join(plugins_folder(), "blob.js"), DEFAULT_BLOB),
        (os.path.join(plugins_folder(), "README.txt"), PLUGIN_README),
    ):
        if not os.path.exists(path):
            try:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(content)
            except OSError:
                pass


def plugin_names() -> list[str]:
    """Safe, sorted plugin filenames. A bad name is ignored rather than executed."""
    names = []
    try:
        entries = os.listdir(plugins_folder())
    except OSError:
        return names
    for name in entries:
        if PLUGIN_RE.match(name) and os.path.isfile(os.path.join(plugins_folder(), name)):
            names.append(name)
    return sorted(names)


def read_theme() -> str:
    ensure()
    try:
        with open(os.path.join(folder(), "theme.css"), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def read_plugin(name: str) -> str | None:
    if not PLUGIN_RE.match(str(name)):
        return None
    try:
        with open(os.path.join(plugins_folder(), str(name)), encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return None
