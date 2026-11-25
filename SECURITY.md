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
network you do not control. The **config template** still ships an empty `api_token`, but the agent
never starts the LAN ports with it:

- **Authentication.** A non-empty `api_token` is required on `0.0.0.0:8765` (WebSocket),
  `0.0.0.0:8766` (HTTP media/API), and `/media/`. A fresh installer generates a random per-install
  token; if a config has an empty value, the agent generates one at startup and refuses to start if
  the config cannot be written. Anyone with the token can read telemetry and ComfyUI output, send
  banners, and drive the typing path.
- **Voice transcription is not local by default here.** The source can fall back to local Whisper
  when it is installed, but this packaged build does not bundle it. Gemini transcription sends the
  recorded audio to Google; without a Gemini key this package has no working transcription.
- **The voice face types into your focused window.** A transcript from the Spot is injected as
  keystrokes on the PC. Anyone who can authenticate to the agent can drive that.
- **Wake word validation uses the network once.** Picovoice Porcupine listens on the Spot, but the
  AccessKey is validated online the first time it starts; after that the engine runs offline.
- **Claude Code hooks are optional and local.** They send one UDP datagram per event to
  `127.0.0.1:8767`, carrying tool names and prompt text. Do not enable them on a shared machine if
  your prompts are sensitive.
- **The device is rooted with ADB enabled.** That is how the shell is installed. Treat the Spot as
  an untrusted device on a trusted network, not the reverse.

Recommended: run the agent on a home LAN or a VLAN you own, keep the ports off any port-forward, use
the Windows Firewall to scope them to the subnet, and never clear the token or expose the Gemini key
or Google ICS addresses.

## Out of scope

- Physical attacks on the Echo Spot, including the bootrom exploit used to unlock it.
- Vulnerabilities in LineageOS, ComfyUI, ffmpeg, or other third-party software this project talks
  to. Report those upstream.
