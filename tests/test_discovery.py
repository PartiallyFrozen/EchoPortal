"""mDNS advertisement helpers."""
import discovery


def test_service_name_is_short_and_has_no_dots():
    name = discovery.service_name("my-pc.local")
    assert name == "EchoPortal on my-pc-local"
    assert len(name) <= 63


def test_the_service_type_matches_what_the_shell_looks_for():
    assert discovery.SERVICE_TYPE == "_echoportal._tcp.local."
