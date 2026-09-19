"""Install (or remove) the EchoPortal hooks in Claude Code's user settings so the Spot's Claude face shows real activity.

    python scripts/install_claude_hooks.py             # add the hooks from pc-agent/claude_hooks.json
    python scripts/install_claude_hooks.py --remove    # take them out again

Edits ~/.claude/settings.json in place (a backup settings.json.bak is written first). Restart Claude Code
(or start a new session) afterwards - hooks are read when a session starts.
"""
import json
import os
import shutil
import sys

SETTINGS = os.path.expanduser("~/.claude/settings.json")
HOOKS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "pc-agent", "claude_hooks.json")

cfg = json.load(open(SETTINGS, encoding="utf-8")) if os.path.exists(SETTINGS) else {}
if os.path.exists(SETTINGS):
    shutil.copyfile(SETTINGS, SETTINGS + ".bak")
hooks = cfg.setdefault("hooks", {})
mine = json.load(open(HOOKS, encoding="utf-8"))["hooks"]

if "--remove" in sys.argv:
    for ev in list(hooks):
        hooks[ev] = [e for e in hooks[ev] if not any("claude_hook.py" in h.get("command", "") for h in e.get("hooks", []))]
        if not hooks[ev]:
            del hooks[ev]
    if not hooks:
        cfg.pop("hooks", None)
    print("EchoPortal hooks removed")
else:
    added = []
    for ev, entries in mine.items():
        cur = hooks.setdefault(ev, [])
        if not any("claude_hook.py" in h.get("command", "") for e in cur for h in e.get("hooks", [])):
            cur.extend(entries)
            added.append(ev)
    print("hooks added:", ", ".join(added) if added else "none (already installed)")

json.dump(cfg, open(SETTINGS, "w", encoding="utf-8"), indent=2)
print("wrote", SETTINGS, "(backup: settings.json.bak)")
print("Now restart Claude Code / start a new session so it picks the hooks up.")
