"""The PC stats tool must work even when the broadcast sampler is idle."""
import agent


def test_pc_stats_samples_on_demand(monkeypatch):
    sample = {"cpu": {"total": 12.5}, "mem": {"pct": 40.0},
              "gpu": {"name": "RX 7700", "ded_total_gb": 16.0, "temp_c": 52.0},
              "uptime_s": 900}
    calls = []

    monkeypatch.setattr(agent, "LAST_STATS", {})
    monkeypatch.setattr(agent, "LAST_SAMPLE_TS", 0.0)
    monkeypatch.setattr(agent, "sample", lambda: calls.append(True) or sample)

    result = agent._tool_pc_stats()

    assert calls == [True]
    assert result["vram_gb"] == 16.0
    assert result["gpu"] == "RX 7700"
    assert agent.LAST_SAMPLE_TS > 0

    # The cached sample is reused on the next turn.
    assert agent._tool_pc_stats() == result
    assert calls == [True]
