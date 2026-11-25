"""Assistant memory lives in a SQLite file under tmp_path; nothing touches the install."""
import json
import sqlite3

import config
import memory


def test_remember_recall_scores_and_bumps_usage(tmp_path):
    store = memory.MemoryStore(str(tmp_path / "memory.sqlite3"))
    saved = store.remember("Paris is in France", category="place")
    store.remember("The sky is blue")
    assert saved["text"] == "Paris is in France"
    assert saved["category"] == "place"
    assert saved["uses"] == 0

    hits = store.recall("paris")
    assert len(hits) == 1
    assert hits[0]["text"] == "Paris is in France"
    assert hits[0]["score"] > 0
    assert hits[0]["uses"] == 1
    assert hits[0]["last_used_at"] is not None
    assert store.recall("PARIS")[0]["uses"] == 2

    category_hits = store.recall("place")
    assert category_hits[0]["text"] == "Paris is in France"
    assert store.recall("nothing here") == []
    json.dumps(hits)  # every returned value must be JSON-safe


def test_feedback_adjusts_recall_weight_and_survives_old_databases(tmp_path):
    path = str(tmp_path / "memory.sqlite3")
    # Simulate an install created before the feedback column existed.
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, "
                     "category TEXT NOT NULL DEFAULT 'fact', created_at REAL NOT NULL, "
                     "last_used_at REAL, uses INTEGER NOT NULL DEFAULT 0)")
        conn.execute("INSERT INTO facts (text, created_at) VALUES (?, ?)", ("Legacy fact", 1.0))
    store = memory.MemoryStore(path)
    assert store.all_facts()[0]["feedback"] == 0
    assert store.set_feedback(1, "up")["feedback"] == 1
    assert store.set_feedback(1, "down")["feedback"] == 0
    assert store.set_feedback(999, "up")["ok"] is False

    first = store.remember("Milk is oat in the fridge")
    second = store.remember("Cereal is oat in the cupboard")
    ranked = store.recall("oat")
    assert {item["id"] for item in ranked} == {first["id"], second["id"]}
    top, other = ranked[0]["id"], ranked[-1]["id"]
    for _ in range(5):
        store.set_feedback(other, "up")
        store.set_feedback(top, "down")
    assert store.recall("oat")[0]["id"] == other


def test_forget_deletes_facts_matching_text_or_category(tmp_path):
    store = memory.MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.remember("Paris is in France", category="place")
    store.remember("The sky is blue", category="fact")

    assert store.forget("paris")["deleted"] == 1
    assert [f["text"] for f in store.all_facts()] == ["The sky is blue"]
    assert store.forget("fact")["deleted"] == 1
    assert store.all_facts() == []


def test_recent_turns_are_chronological_and_limited(tmp_path):
    store = memory.MemoryStore(str(tmp_path / "memory.sqlite3"))
    for i in range(5):
        store.append_turn("q%d" % i, "a%d" % i)

    turns = store.recent_turns(3)
    assert [t["user"] for t in turns] == ["q2", "q3", "q4"]
    assert [t["assistant"] for t in turns] == ["a2", "a3", "a4"]
    assert store.stats()["turns"] == 5
    json.dumps(turns)


def test_delete_and_clear_chat_turns_keep_facts(tmp_path):
    store = memory.MemoryStore(str(tmp_path / "memory.sqlite3"))
    store.remember("The sky is blue")
    first = store.append_turn("first", "reply one")
    store.append_turn("second", "reply two")

    assert store.delete_turn(first["id"])["deleted"] == 1
    assert [t["user"] for t in store.recent_turns()] == ["second"]
    assert store.delete_turn("bad")["ok"] is False

    assert store.clear_turns()["deleted"] == 1
    assert store.recent_turns() == []
    assert [f["text"] for f in store.all_facts()] == ["The sky is blue"]


def test_supersession_decay_and_turn_compaction(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_memory_half_life_days": 30})
    store = memory.MemoryStore(str(tmp_path / "memory.sqlite3"))

    first = store.remember("My parking spot is B2")
    second = store.remember("My parking spot is C4")

    assert second["superseded"] == [first["id"]]
    assert [f["text"] for f in store.all_facts()] == ["My parking spot is C4"]
    assert "parking" in store.latest_summary() or store.latest_summary() == ""

    for i in range(6):
        store.append_turn("question %d" % i, "answer %d" % i)
    result = store.compact_turns(keep=2)

    assert result["compacted"] == 4
    assert [t["user"] for t in store.recent_turns()] == ["question 4", "question 5"]
    assert "question 0" in store.latest_summary()
    assert store.stats()["summaries"] == 1


def test_semantic_scores_fail_closed_when_ollama_is_offline(monkeypatch):
    monkeypatch.setattr(memory, "_EMBED_DISABLED_UNTIL", 0.0)
    monkeypatch.setattr(config, "VALUES", {**config.VALUES, "assistant_embed_model": "nomic-embed-text"})

    def offline(*args, **kwargs):
        raise OSError("offline")

    monkeypatch.setattr(memory.urllib.request, "urlopen", offline)
    scores = memory.semantic_scores("parking", ["parking notes", "unrelated"])

    assert scores == [0.0, 0.0]
    assert memory._EMBED_DISABLED_UNTIL > 0


def test_stats_and_wal_mode(tmp_path):
    path = tmp_path / "memory.sqlite3"
    store = memory.MemoryStore(str(path))
    store.remember("A fact")
    store.append_turn("question", "answer")

    stats = store.stats()
    assert stats["facts"] == 1
    assert stats["turns"] == 1
    assert stats["path"] == str(path)
    assert stats["size_bytes"] > 0

    con = sqlite3.connect(str(path))
    try:
        assert con.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        con.close()


def test_default_store_is_a_singleton_under_the_data_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(memory, "_DEFAULT_STORE", None)
    monkeypatch.setattr(config, "data_path", lambda *parts: str(tmp_path.joinpath(*parts)))

    first = memory.default_store()
    assert memory.default_store() is first
    assert (tmp_path / "assistant_memory.sqlite3").exists()
