"""Config-driven alerts: turn the dashboard into something that tells you when to look.

Rules live in config.json under "alerts". A metric rule fires a banner when a value stays above or
below a threshold for a while; a service rule fires when a TCP port stops answering.

  {"name": "GPU hot", "metric": "gpu_temp", "above": 85, "for_s": 30, "cooldown_s": 600}
  {"name": "Disk low", "metric": "disk_free_pct", "below": 10, "for_s": 60}
  {"name": "Home Assistant", "host": "127.0.0.1", "port": 8123, "for_s": 60}

Metric names: cpu_pct, mem_pct, gpu_temp, gpu_util, gpu_power, disk_free_pct, disk_used_pct,
net_down_mbps, net_up_mbps, proc_count. "text" may use {name} and {value}.
"""
import asyncio
import time

import config


def _f(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _metric(stats: dict, name: str):
    """Pull one number out of a stats payload. None means 'not available on this machine'."""
    if not isinstance(stats, dict):
        return None
    cpu = stats.get("cpu") or {}
    mem = stats.get("mem") or {}
    gpu = stats.get("gpu") or {}
    disks = stats.get("disks") or []
    nets = stats.get("nets") or []
    if name == "cpu_pct":
        return cpu.get("total")
    if name == "mem_pct":
        return mem.get("pct")
    if name == "gpu_temp":
        return gpu.get("temp_c")
    if name == "gpu_util":
        return gpu.get("util_3d")
    if name == "gpu_power":
        return gpu.get("power_w")
    if name == "disk_free_pct":
        return min((100 - _f(d.get("used_pct")) for d in disks if isinstance(d, dict)), default=None)
    if name == "disk_used_pct":
        return max((_f(d.get("used_pct")) for d in disks if isinstance(d, dict)), default=None)
    if name == "net_down_mbps":
        return max((_f(n.get("down_mbps")) for n in nets if isinstance(n, dict)), default=None)
    if name == "net_up_mbps":
        return max((_f(n.get("up_mbps")) for n in nets if isinstance(n, dict)), default=None)
    if name == "proc_count":
        return stats.get("proc_count")
    return None


def _fmt_value(v) -> str:
    if isinstance(v, float):
        return ("%.1f" % v).rstrip("0").rstrip(".")
    return str(v)


class AlertEngine:
    def __init__(self, notify, rules=None):
        self.notify = notify
        raw = rules if rules is not None else config.get("alerts", [])
        if not isinstance(raw, list):
            raw = []
        self.rules = []
        for r in raw:
            if not isinstance(r, dict):
                continue
            if "metric" not in r and r.get("port") is not None:
                port = _f(r.get("port"), None)
                if port is None or not (1 <= port <= 65535):
                    print("alerts: ignoring a service rule with a bad port:", r.get("name") or r)
                    continue
                r = {**r, "port": int(port)}
            self.rules.append(r)
        self.state: dict[int, dict] = {}

    def _st(self, i: int) -> dict:
        return self.state.setdefault(i, {"since": None, "last": None, "down_since": None, "down_last": None, "up": True})

    def _text(self, rule: dict, value) -> str:
        name = rule.get("name") or rule.get("metric") or rule.get("service") or "Alert"
        template = rule.get("text") or "{name}: {value}"
        try:
            return template.format(name=name, value=_fmt_value(value))
        except (KeyError, IndexError, ValueError):
            return "%s: %s" % (name, _fmt_value(value))

    async def _fire(self, rule: dict, value, title: str = "EchoPortal alert"):
        text = self._text(rule, value)
        print("alert:", text)
        if self.notify:
            await self.notify(title, text, rule.get("face", "pcmon"), True)

    async def check(self, stats: dict, now: float | None = None) -> list[dict]:
        """Evaluate metric rules against one stats sample. Returns the rules that fired."""
        now = time.time() if now is None else now
        fired = []
        for i, rule in enumerate(self.rules):
            if "metric" not in rule:
                continue
            value = _metric(stats, rule["metric"])
            if value is None:
                continue
            try:
                value = float(value)
                above = None if rule.get("above") is None else float(rule["above"])
                below = None if rule.get("below") is None else float(rule["below"])
            except (TypeError, ValueError):
                continue
            hit = (above is not None and value > above) or (below is not None and value < below)
            st = self._st(i)
            if not hit:
                st["since"] = None
                continue
            if st["since"] is None:
                st["since"] = now
            # A negative window would fire on the first sample; a negative cooldown would fire on
            # every sample. Treat the window as zero and the cooldown as its default.
            for_s = max(0.0, _f(rule.get("for_s", 0)))
            cooldown = _f(rule.get("cooldown_s", 600))
            if cooldown < 0:
                cooldown = 600.0
            if now - st["since"] >= for_s and (st["last"] is None or now - st["last"] >= cooldown):
                st["last"] = now
                fired.append(rule)
                await self._fire(rule, value)
        return fired

    async def check_service(self, rule: dict, now: float | None = None, index: int | None = None) -> bool:
        """True when the TCP port answered. Updates the rule's state and fires on a sustained outage."""
        now = time.time() if now is None else now
        host = rule.get("host") or "127.0.0.1"
        port = int(_f(rule.get("port"), 0))
        up = False
        if port:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=3)
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:  # noqa: BLE001
                    pass
                up = True
            except (OSError, asyncio.TimeoutError):
                up = False
        st = self._st(self.rules.index(rule) if index is None else index)
        if up:
            st["down_since"] = None
            st["up"] = True
            return True
        st["up"] = False
        if st["down_since"] is None:
            st["down_since"] = now
        for_s = max(0.0, _f(rule.get("for_s", 60)))
        cooldown = _f(rule.get("cooldown_s", 600))
        if cooldown < 0:
            cooldown = 600.0
        if now - st["down_since"] >= for_s and (st["down_last"] is None or now - st["down_last"] >= cooldown):
            st["down_last"] = now
            await self._fire(rule, "%s:%s down" % (host, port), "Service down")
        return False

    async def run(self):
        """Poll service rules. Metric rules are evaluated by the stats broadcaster."""
        while True:
            services = [(i, r) for i, r in enumerate(self.rules) if r.get("port") and "metric" not in r]
            if services:
                await asyncio.gather(*(self.check_service(r, index=i) for i, r in services), return_exceptions=True)
            await asyncio.sleep(15)
