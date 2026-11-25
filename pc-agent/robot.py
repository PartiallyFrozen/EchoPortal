"""Robot vacuum adapter for the Jarvis ``robot`` tool.

The ZOOZEE PWA in this workspace already knows the Slamtec command IDs (STOP=24, GO_HOME=25,
SWEEP=26, FIND_ME=56). This module is the transport scaffold: it publishes a JSON command to a
configurable MQTT topic. Fill in ``robot_broker`` / ``robot_topic`` / ``robot_device_id`` in
config.json once the device-specific topic and payload shape are known; until then the tool reports
that it is not configured instead of pretending to act.
"""
import json
import time

import config

DEFAULT_ACTIONS = {
    "start": 26,
    "sweep": 26,
    "stop": 24,
    "home": 25,
    "dock": 25,
    "find": 56,
}


class RobotClient:
    """Small MQTT publisher with a lazy connection; safe to construct even when unconfigured."""

    def __init__(self):
        self._client = None
        self._connected_at = 0.0

    def configured(self) -> bool:
        return bool(config.get("robot_enabled", False) and config.get("robot_broker", "") and config.get("robot_topic", ""))

    def _connect(self):
        if self._client is not None and time.time() - self._connected_at < 300:
            return self._client
        try:
            import paho.mqtt.client as mqtt  # noqa: PLC0415 - optional dependency
        except ImportError:
            raise RuntimeError("paho-mqtt is not installed") from None
        broker = str(config.get("robot_broker"))
        port = int(config.get("robot_port", 8883) or 8883)
        client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        user = config.get("robot_user", "")
        password = config.get("robot_password", "")
        if user:
            client.username_pw_set(str(user), str(password or ""))
        if port == 8883:
            client.tls_set()
        client.connect(broker, port, keepalive=60)
        client.loop_start()
        self._client = client
        self._connected_at = time.time()
        return client

    def send(self, action: str, **kwargs) -> dict:
        action = str(action).strip().lower()
        if action not in DEFAULT_ACTIONS:
            return {"ok": False, "error": "unknown action %s" % action}
        if not self.configured():
            return {"ok": False, "error": "robot is not configured (set robot_enabled, robot_broker and robot_topic)"}
        device_id = str(config.get("robot_device_id", ""))
        topic = str(config.get("robot_topic")).format(device_id=device_id, action=action)
        payload = {
            "cmd": DEFAULT_ACTIONS[action],
            "action": action,
            "device_id": device_id,
            "at": int(time.time()),
            **{k: v for k, v in kwargs.items() if k in ("x", "y", "region")},
        }
        try:
            client = self._connect()
            info = client.publish(topic, json.dumps(payload), qos=0)
            info.wait_for_publish(timeout=8)
            return {"ok": info.is_published(), "action": action, "topic": topic}
        except Exception as e:  # noqa: BLE001
            self._client = None
            return {"ok": False, "error": str(e)}

    def close(self):
        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:  # noqa: BLE001
                pass
            self._client = None
