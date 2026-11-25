# Jarvis Conversation for Home Assistant

A small custom integration that makes Jarvis available as a Home Assistant
**conversation agent**. HA voice assistants, dashboards and automations can then
send speech to the same brain used by the PC dashboard and the Echo Spot.

## Install

1. Copy `custom_components/jarvis_conversation/` into your Home Assistant
   config directory as `custom_components/jarvis_conversation/`.
2. Restart Home Assistant.
3. Settings → Devices & services → Add integration → **Jarvis Conversation**.
4. Enter the PC agent URL (for example `http://192.168.1.25:8766`) and the
   `api_token` from the agent's `config.json`. The token is sent as
   `X-EchoPortal-Token`; no other agent settings are changed.
5. Settings → Voice assistants → your assistant → Conversation agent → **Jarvis**.

The integration verifies the connection with `GET /api/status` before it saves
the entry. It talks to `/v1/chat/completions` with `model: "jarvis"` and
`stream: false`, so it uses the same model picker, tools, memory and routing as
every other Jarvis client.

## Notes

- This is a **source file for Home Assistant**, not part of the Windows agent.
  It is not installed by the agent installer and needs no rebuild of the EXE.
- Tools that change the PC state still go through Jarvis's on-screen confirm
  flow. Home Assistant only receives the final text reply.
- To avoid a loop, do not enable both this integration and an automation that
  re-sends Jarvis replies back into a conversation agent.
