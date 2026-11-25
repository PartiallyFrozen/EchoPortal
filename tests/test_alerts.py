"""Alert rules: thresholds, sustained conditions, cooldowns and service checks."""
import asyncio

import alerts


def stats(cpu=10, mem=20, gpu_temp=50, disk_used=50):
    return {
        "cpu": {"total": cpu},
        "mem": {"pct": mem},
        "gpu": {"temp_c": gpu_temp, "util_3d": 5, "power_w": 40},
        "disks": [{"used_pct": disk_used}],
        "nets": [{"down_mbps": 1, "up_mbps": 0.5}],
        "proc_count": 100,
    }


def engine(rules):
    fired = []

    async def notify(*args):
        fired.append(args)

    return alerts.AlertEngine(notify, rules=rules), fired


def test_a_metric_must_stay_over_the_threshold_for_the_hold_time():
    eng, fired = engine([{"metric": "gpu_temp", "above": 85, "for_s": 30, "cooldown_s": 600}])
    asyncio.run(eng.check(stats(gpu_temp=90), now=0))
    assert not fired
    asyncio.run(eng.check(stats(gpu_temp=90), now=29))
    assert not fired
    asyncio.run(eng.check(stats(gpu_temp=90), now=31))
    assert len(fired) == 1
    assert "gpu_temp" in fired[0][1] or "90" in fired[0][1]


def test_a_condition_that_clears_restarts_the_hold_time():
    eng, fired = engine([{"metric": "cpu_pct", "above": 95, "for_s": 10, "cooldown_s": 0}])
    asyncio.run(eng.check(stats(cpu=99), now=0))
    asyncio.run(eng.check(stats(cpu=10), now=5))
    asyncio.run(eng.check(stats(cpu=99), now=6))
    assert not fired, "the hold time restarted when the CPU dropped"
    asyncio.run(eng.check(stats(cpu=99), now=17))
    assert len(fired) == 1


def test_cooldown_suppresses_repeats_until_it_expires():
    eng, fired = engine([{"metric": "mem_pct", "above": 90, "for_s": 0, "cooldown_s": 60}])
    asyncio.run(eng.check(stats(mem=95), now=0))
    asyncio.run(eng.check(stats(mem=95), now=10))
    assert len(fired) == 1
    asyncio.run(eng.check(stats(mem=95), now=61))
    assert len(fired) == 2


def test_below_rules_work_on_the_smallest_free_disk():
    eng, fired = engine([{"metric": "disk_free_pct", "below": 10, "for_s": 0, "cooldown_s": 0}])
    asyncio.run(eng.check(stats(disk_used=95), now=0))
    assert len(fired) == 1


def test_an_unavailable_metric_is_skipped():
    eng, fired = engine([{"metric": "gpu_temp", "above": 1, "for_s": 0, "cooldown_s": 0}])
    s = stats()
    s["gpu"] = {}
    asyncio.run(eng.check(s, now=0))
    assert not fired


def test_the_text_template_gets_the_value():
    eng, fired = engine([{"name": "GPU hot", "metric": "gpu_temp", "above": 80, "text": "{name}: {value}C"}])
    asyncio.run(eng.check(stats(gpu_temp=88.5), now=0))
    assert fired[0][0] == "EchoPortal alert"
    assert fired[0][1] == "GPU hot: 88.5C"


def test_a_service_that_stays_down_fires_once(monkeypatch):
    async def refuse(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(alerts.asyncio, "open_connection", refuse)
    eng, fired = engine([{"name": "HA", "host": "127.0.0.1", "port": 8123, "for_s": 30, "cooldown_s": 600}])
    rule = eng.rules[0]
    assert asyncio.run(eng.check_service(rule, now=0, index=0)) is False
    asyncio.run(eng.check_service(rule, now=29, index=0))
    assert not fired
    asyncio.run(eng.check_service(rule, now=31, index=0))
    assert len(fired) == 1
    assert fired[0][0] == "Service down"


def test_a_recovered_service_rearms_the_alert(monkeypatch):
    state = {"up": False}

    class Writer:
        def close(self):
            pass

        async def wait_closed(self):
            pass

    async def connect(*_a, **_k):
        if not state["up"]:
            raise OSError("down")
        return object(), Writer()

    monkeypatch.setattr(alerts.asyncio, "open_connection", connect)
    eng, fired = engine([{"name": "HA", "host": "127.0.0.1", "port": 8123, "for_s": 0, "cooldown_s": 0}])
    rule = eng.rules[0]
    asyncio.run(eng.check_service(rule, now=0, index=0))
    assert len(fired) == 1
    state["up"] = True
    assert asyncio.run(eng.check_service(rule, now=10, index=0)) is True
    state["up"] = False
    asyncio.run(eng.check_service(rule, now=20, index=0))
    assert len(fired) == 2


def test_a_service_rule_with_a_bad_port_is_ignored():
    eng, _fired = engine([{"name": "bad", "port": "http"}, {"name": "ok", "port": 8123}])
    assert [r["name"] for r in eng.rules] == ["ok"]


def test_a_bad_threshold_is_skipped_not_treated_as_zero():
    eng, fired = engine([{"metric": "gpu_temp", "above": "hot", "for_s": 0}])
    asyncio.run(eng.check(stats(gpu_temp=90), now=0))
    assert not fired


def test_a_negative_cooldown_does_not_fire_on_every_sample():
    eng, fired = engine([{"metric": "cpu_pct", "above": 90, "for_s": 0, "cooldown_s": -60}])
    asyncio.run(eng.check(stats(cpu=99), now=0))
    asyncio.run(eng.check(stats(cpu=99), now=15))
    assert len(fired) == 1


def test_junk_in_a_stats_payload_does_not_raise():
    eng, fired = engine([{"metric": "disk_free_pct", "below": 10, "for_s": 0}])
    s = stats()
    s["disks"] = [{"used_pct": "n/a"}, None, {"used_pct": 95}]
    asyncio.run(eng.check(s, now=0))
    assert len(fired) == 1
