"""Advertise the agent over mDNS so the Spot can find it without a hardcoded IP.

The shell looks for `_echoportal._tcp` with Android's NsdManager. zeroconf is optional: if it is
not installed the agent still runs, it just cannot be auto-discovered.
"""
import socket

SERVICE_TYPE = "_echoportal._tcp.local."


def service_name(host: str | None = None) -> str:
    host = host or socket.gethostname()
    # Dots in an instance name are legal but confusing in some resolvers; keep it simple.
    return ("EchoPortal on %s" % host).replace(".", "-")[:63]


def _lan_ip() -> str | None:
    """The address the default route would use. Opens no connection and sends no packets."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                return ip
    except OSError:
        pass
    return None


def register(port: int = 8765, media_port: int = 8766, address: str | None = None, instance: str | None = None):
    """Register the service. Returns an opaque handle for unregister(), or None if unavailable."""
    try:
        from zeroconf import ServiceInfo, Zeroconf
    except ImportError:
        print("discovery: zeroconf is not installed; the Spot must use a typed address")
        return None
    ip = address or _lan_ip()
    if not ip:
        print("discovery: no LAN address to advertise")
        return None
    name = (instance or service_name()) + "." + SERVICE_TYPE
    info = ServiceInfo(
        SERVICE_TYPE,
        name,
        addresses=[socket.inet_aton(ip)],
        port=int(port),
        properties={"path": "/", "media": str(media_port), "version": "0.1"},
        server=(socket.gethostname().replace(".", "-") + ".local."),
    )
    # Probing can take a few seconds and machines with VPNs / link-local adapters sometimes make
    # zeroconf bind to the wrong interface. Try the LAN address first, then whatever it picks.
    last = None
    for kwargs in ({"interfaces": [ip]}, {}):
        zc = None
        try:
            zc = Zeroconf(**kwargs)
            # allow_name_change: if a stale record from a previous run still holds the name,
            # advertise as "name (2)" instead of failing with a message-less NonUniqueNameException.
            zc.register_service(info, allow_name_change=True)
            print("discovery: advertised %s at %s:%d" % (name, ip, port))
            return {"zeroconf": zc, "info": info}
        except Exception as e:  # noqa: BLE001
            last = e
            if zc is not None:
                try:
                    zc.close()
                except Exception:  # noqa: BLE001
                    pass
    print("discovery: could not advertise:", repr(last))
    return None


def unregister(handle) -> None:
    if not handle:
        return
    try:
        handle["zeroconf"].unregister_service(handle["info"])
        handle["zeroconf"].close()
    except Exception as e:  # noqa: BLE001
        print("discovery: unregister failed:", e)
