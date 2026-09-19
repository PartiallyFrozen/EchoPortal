"""EchoPortal broadcast: make ComfyUI send job progress to EVERY websocket client.

Stock ComfyUI sends execution_start / executing / progress / executed / execution_* only to the
client that queued the prompt (the browser). This tiny extension re-targets those events to
"everyone", so passive observers like the EchoPortal agent (Echo Spot dashboard) can follow along.
It registers no nodes. Drop the folder into custom_nodes/ and restart ComfyUI.
"""
from server import PromptServer

_BROADCAST = {
    "execution_start", "execution_cached", "executing", "progress", "progress_state",
    "executed", "execution_success", "execution_error", "execution_interrupted",
}

_orig_send_sync = PromptServer.send_sync


def _send_sync(self, event, data, sid=None):
    if event in _BROADCAST:
        sid = None
    return _orig_send_sync(self, event, data, sid)


PromptServer.send_sync = _send_sync
print("[rook_broadcast] job progress events will be broadcast to all clients")

NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
