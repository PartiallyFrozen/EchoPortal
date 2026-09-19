# Security policy

## Reporting a vulnerability

Please report security issues privately through
[GitHub Security Advisories](https://github.com/PartiallyFrozen/EchoPortal/security/advisories/new)
rather than opening a public issue.

Include what you found, how to reproduce it, and what an attacker could do with it. Expect an
acknowledgement within a week. If a fix is warranted, it lands in a patch release and the advisory
is published with credit to you unless you prefer otherwise.

Supported: the latest release on the default branch. Older tags do not get fixes.

## What this software does by design

These are not vulnerabilities, they are the threat model. Read them before putting EchoPortal on a
network you do not control.

- **The agent has no authentication.** It listens on `0.0.0.0:8765` (WebSocket) and `0.0.0.0:8766`
  (HTTP media) so the Spot can reach it. Anyone who can route to those ports can read your system
  telemetry, see ComfyUI output, and send banners to the display.
- **The voice face types into your focused window.** Audio captured on the Spot is transcribed
  locally and injected as keystrokes on the PC. Anyone who can reach the agent can drive that.
- **Claude Code hooks are optional and local.** They send one UDP datagram per event to
  `127.0.0.1:8767`, carrying tool names and prompt text. Do not enable them on a shared machine if
  your prompts are sensitive.
- **The device is rooted with ADB enabled.** That is how the shell is installed. Treat the Spot as
  an untrusted device on a trusted network, not the reverse.

Recommended: run the agent on a home LAN or a VLAN you own, keep the ports off any port-forward,
and use the Windows Firewall to scope them to the subnet.

## Out of scope

- Physical attacks on the Echo Spot, including the bootrom exploit used to unlock it.
- Vulnerabilities in LineageOS, ComfyUI, ffmpeg, or other third-party software this project talks
  to. Report those upstream.
