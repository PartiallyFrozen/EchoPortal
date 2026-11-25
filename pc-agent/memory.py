"""Persistent memory for the assistant: facts, semantic recall and conversation turns.

One SQLite file under ``config.data_path("assistant_memory.sqlite3")`` holds facts with usage
counters, optional Ollama embeddings, supersession links, and recent conversation turns. Older
turns can be compacted into a short summary. Every public method returns plain dicts/lists so
results can go straight over the hub as JSON.
"""
import json
import math
import os
import re
import sqlite3
import struct
import time
import urllib.request
from contextlib import contextmanager

import config

_DEFAULT_STORE = None
_EMBED_DISABLED_UNTIL = 0.0

# Words that say nothing about what a fact is about; used to find the subject token for supersession.
_STOPWORDS = {
    "remember", "please", "that", "this", "the", "and", "for", "with", "about", "currently",
    "favourite", "favorite", "like", "love", "prefer", "want", "need", "have", "has", "had",
    "with", "from", "into", "just", "only", "mine", "ours", "your", "their", "what", "when",
    "where", "which", "does", "did", "done", "will", "would", "should", "could", "there",
}


def _limit(value, default: int, cap: int = 1000) -> int:
    try:
        return max(0, min(cap, int(value)))
    except (TypeError, ValueError):
        return default


def _subject(text: str) -> str:
    """First meaningful word of a fact; two facts with the same subject supersede each other."""
    for word in re.findall(r"\w+", str(text).lower()):
        if len(word) > 2 and word not in _STOPWORDS:
            return word
    return ""


def _ollama_base() -> str:
    base = str(config.get("assistant_local_url") or "http://127.0.0.1:11434").rstrip("/")
    if base.endswith("/api/chat"):
        base = base[:-len("/api/chat")]
    return base


def _embed(text: str) -> list[float] | None:
    """Get an embedding from Ollama. Offline or slow servers silently disable this for a minute."""
    global _EMBED_DISABLED_UNTIL
    if time.time() < _EMBED_DISABLED_UNTIL:
        return None
    model = str(config.get("assistant_embed_model", "nomic-embed-text") or "").strip()
    if not model:
        return None
    body = json.dumps({"model": model, "input": str(text)[:4000]}).encode("utf-8")
    req = urllib.request.Request(
        _ollama_base() + "/api/embed", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=1.5) as resp:
            payload = json.load(resp)
        vectors = payload.get("embeddings") or []
        if vectors:
            return [float(x) for x in vectors[0]]
    except Exception:  # noqa: BLE001 - semantic recall is an optional enhancement
        pass
    _EMBED_DISABLED_UNTIL = time.time() + 60.0
    return None


def semantic_scores(query: str, texts: list[str]) -> list[float]:
    """Cosine scores for note chunks. Offline/Ollama failure returns zeros, never raises."""
    global _EMBED_DISABLED_UNTIL
    if not texts or time.time() < _EMBED_DISABLED_UNTIL:
        return [0.0] * len(texts)
    model = str(config.get("assistant_embed_model", "nomic-embed-text") or "").strip()
    if not model:
        return [0.0] * len(texts)
    body = json.dumps({"model": model, "input": [str(query)[:4000], *(str(t)[:4000] for t in texts)]}).encode("utf-8")
    req = urllib.request.Request(
        _ollama_base() + "/api/embed", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=3.0) as resp:
            vectors = (json.load(resp).get("embeddings") or [])
        if len(vectors) == len(texts) + 1:
            query_vector = [float(x) for x in vectors[0]]
            return [_cosine(query_vector, [float(x) for x in vec]) for vec in vectors[1:]]
    except Exception:  # noqa: BLE001
        pass
    _EMBED_DISABLED_UNTIL = time.time() + 60.0
    return [0.0] * len(texts)


def _pack(vec: list[float]) -> bytes:
    return struct.pack("<%df" % len(vec), *vec)


def _unpack(raw) -> list[float] | None:
    if not raw:
        return None
    try:
        return list(struct.unpack("<%df" % (len(raw) // 4), raw[:len(raw) // 4 * 4]))
    except (TypeError, struct.error):
        return None


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return float(dot / (na * nb)) if na and nb else 0.0


class MemoryStore:
    """A small SQLite-backed fact and conversation store, safe to construct at any time."""

    def __init__(self, path: str):
        self.path = str(path)
        parent = os.path.dirname(os.path.abspath(self.path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._db() as conn:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS facts ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "text TEXT NOT NULL, "
                "category TEXT NOT NULL DEFAULT 'fact', "
                "created_at REAL NOT NULL, "
                "last_used_at REAL, "
                "uses INTEGER NOT NULL DEFAULT 0)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS turns ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "user TEXT NOT NULL, "
                "assistant TEXT NOT NULL, "
                "created_at REAL NOT NULL)"
            )
            conn.execute(
                "CREATE TABLE IF NOT EXISTS summaries ("
                "id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "text TEXT NOT NULL, "
                "up_to_turn INTEGER NOT NULL, "
                "created_at REAL NOT NULL)"
            )
            # Guarded migration: older installs have none of these columns.
            for statement in (
                "ALTER TABLE facts ADD COLUMN active INTEGER NOT NULL DEFAULT 1",
                "ALTER TABLE facts ADD COLUMN superseded_by INTEGER",
                "ALTER TABLE facts ADD COLUMN embedding BLOB",
                "ALTER TABLE facts ADD COLUMN feedback INTEGER NOT NULL DEFAULT 0",
            ):
                try:
                    conn.execute(statement)
                except sqlite3.OperationalError:
                    pass
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_category ON facts(category)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_facts_active ON facts(active)")

    @contextmanager
    def _db(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _pattern(value: str) -> str:
        """A LIKE pattern that treats % and _ in the needle as literal characters."""
        escaped = str(value).lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        return "%" + escaped + "%"

    @staticmethod
    def _fact(row, score=None) -> dict:
        result = {
            "id": int(row["id"]),
            "text": str(row["text"]),
            "category": str(row["category"]),
            "created_at": float(row["created_at"]),
            "last_used_at": None if row["last_used_at"] is None else float(row["last_used_at"]),
            "uses": int(row["uses"]),
        }
        if "active" in row.keys():
            result["active"] = int(row["active"])
            result["superseded_by"] = (None if row["superseded_by"] is None
                                       else int(row["superseded_by"]))
            result["semantic"] = bool(row["embedding"])
        if "feedback" in row.keys():
            result["feedback"] = int(row["feedback"])
        if score is not None:
            result["score"] = int(score)
        return result

    def remember(self, text: str, category: str = "fact") -> dict:
        """Store a fact, retiring any earlier active fact about the same subject."""
        text = str(text).strip()
        if not text:
            return {"ok": False, "error": "empty fact"}
        category = str(category or "fact").strip() or "fact"
        now = time.time()
        subject = _subject(text)
        vector = _embed(text)
        with self._db() as conn:
            retired = []
            if subject:
                for row in conn.execute(
                    "SELECT id, text FROM facts WHERE active = 1 AND category = ?", (category,)
                ).fetchall():
                    if _subject(row["text"]) == subject:
                        retired.append(int(row["id"]))
            cur = conn.execute(
                "INSERT INTO facts (text, category, created_at, uses, embedding) VALUES (?, ?, ?, 0, ?)",
                (text, category, now, _pack(vector) if vector else None),
            )
            new_id = int(cur.lastrowid)
            for old_id in retired:
                conn.execute(
                    "UPDATE facts SET active = 0, superseded_by = ? WHERE id = ?", (new_id, old_id)
                )
            row = conn.execute("SELECT * FROM facts WHERE id = ?", (new_id,)).fetchone()
        result = {"ok": True, **self._fact(row)}
        if retired:
            result["superseded"] = retired
        return result

    def recall(self, query: str, limit: int = 5) -> list[dict]:
        """Keyword + semantic scoring with recency decay; falls back to keywords when offline."""
        words = [w for w in re.findall(r"\w+", str(query).lower()) if w]
        if not words:
            return []
        count = _limit(limit, 5)
        if not count:
            return []
        query_vector = _embed(query)
        half_life = max(1.0, float(config.get("assistant_memory_half_life_days", 30) or 30))
        now = time.time()
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM facts WHERE active = 1").fetchall()
        ranked = []
        for row in rows:
            low_text, low_category = str(row["text"]).lower(), str(row["category"]).lower()
            keyword = sum(3 if w in low_text else (1 if w in low_category else 0) for w in words)
            similarity = (_cosine(query_vector, _unpack(row["embedding"]))
                          if query_vector and row["embedding"] else 0.0)
            if keyword <= 0 and similarity < 0.55:
                continue
            age_days = max(0.0, (now - float(row["created_at"])) / 86400.0)
            decay = 0.5 + 0.5 * math.exp(-age_days / half_life)
            # Explicit thumbs up/down is the strongest signal a fact can carry; clamp it so one
            # enthusiastic session cannot bury everything else permanently.
            feedback = max(-5, min(5, int(row["feedback"] or 0))) if "feedback" in row.keys() else 0
            rank = (keyword * 3.0 + similarity * 4.0 + min(int(row["uses"]), 20) * 0.05
                    + decay * 0.5 + feedback * 0.4)
            ranked.append((rank, keyword or 1, int(row["id"]), row))
        ranked.sort(key=lambda item: (item[0], item[2]), reverse=True)
        selected = ranked[:count]
        with self._db() as conn:
            if selected:
                conn.executemany(
                    "UPDATE facts SET uses = uses + 1, last_used_at = ? WHERE id = ?",
                    [(now, item[2]) for item in selected],
                )
        results = []
        for rank, keyword, _id, row in selected:
            item = self._fact(row, score=keyword)
            item["uses"] += 1
            item["last_used_at"] = now
            item["rank"] = round(rank, 4)
            results.append(item)
        return results

    def forget(self, needle: str) -> dict:
        needle = str(needle).strip()
        if not needle:
            return {"ok": True, "deleted": 0}
        pattern = self._pattern(needle)
        with self._db() as conn:
            cur = conn.execute(
                "DELETE FROM facts WHERE lower(text) LIKE ? ESCAPE '\\' OR lower(category) LIKE ? ESCAPE '\\'",
                (pattern, pattern),
            )
            deleted = int(cur.rowcount)
        return {"ok": True, "deleted": deleted}

    def append_turn(self, user: str, assistant: str) -> dict:
        now = time.time()
        with self._db() as conn:
            cur = conn.execute(
                "INSERT INTO turns (user, assistant, created_at) VALUES (?, ?, ?)",
                (str(user), str(assistant), now),
            )
            row = conn.execute("SELECT * FROM turns WHERE id = ?", (cur.lastrowid,)).fetchone()
        return {
            "id": int(row["id"]),
            "user": str(row["user"]),
            "assistant": str(row["assistant"]),
            "created_at": float(row["created_at"]),
        }

    def recent_turns(self, limit: int = 6) -> list[dict]:
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM (SELECT * FROM turns ORDER BY id DESC LIMIT ?) ORDER BY id ASC",
                (_limit(limit, 6),),
            ).fetchall()
        return [
            {
                "id": int(row["id"]),
                "user": str(row["user"]),
                "assistant": str(row["assistant"]),
                "created_at": float(row["created_at"]),
            }
            for row in rows
        ]

    def compact_turns(self, keep: int = 6) -> dict:
        """Fold old turns into a short summary so context never grows without bound."""
        keep = max(1, _limit(keep, 6))
        with self._db() as conn:
            rows = conn.execute("SELECT * FROM turns ORDER BY id ASC").fetchall()
            if len(rows) <= keep:
                return {"ok": True, "compacted": 0, "turns": len(rows)}
            old = rows[:-keep]
            parts = ["Q: %s A: %s" % (r["user"], r["assistant"]) for r in old]
            summary = " | ".join(parts)[:3000]
            up_to = int(old[-1]["id"])
            conn.execute(
                "INSERT INTO summaries (text, up_to_turn, created_at) VALUES (?, ?, ?)",
                (summary, up_to, time.time()),
            )
            conn.execute("DELETE FROM turns WHERE id <= ?", (up_to,))
        return {"ok": True, "compacted": len(old), "up_to_turn": up_to}

    def latest_summary(self) -> str:
        with self._db() as conn:
            row = conn.execute("SELECT text FROM summaries ORDER BY id DESC LIMIT 1").fetchone()
        return str(row["text"]) if row else ""

    def delete_turn(self, turn_id) -> dict:
        """Delete one conversation turn by id; facts are never touched."""
        try:
            key = int(turn_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid turn id", "deleted": 0}
        if key <= 0:
            return {"ok": False, "error": "invalid turn id", "deleted": 0}
        with self._db() as conn:
            deleted = int(conn.execute("DELETE FROM turns WHERE id = ?", (key,)).rowcount)
        return {"ok": True, "deleted": deleted, "id": key}

    def clear_turns(self) -> dict:
        """Delete all saved conversation turns; remembered facts stay intact."""
        with self._db() as conn:
            deleted = int(conn.execute("DELETE FROM turns").rowcount)
        return {"ok": True, "deleted": deleted}

    def set_feedback(self, fact_id, vote: str) -> dict:
        """Apply a thumbs up/down to a remembered fact, clamped to -5..+5."""
        try:
            key = int(fact_id)
        except (TypeError, ValueError):
            return {"ok": False, "error": "invalid fact id"}
        direction = 1 if str(vote).strip().lower() in ("up", "like", "1", "true") else -1
        if str(vote).strip().lower() not in ("up", "like", "1", "true", "down", "dislike", "-1", "false"):
            return {"ok": False, "error": "vote must be up or down"}
        with self._db() as conn:
            row = conn.execute("SELECT id, feedback FROM facts WHERE id = ?", (key,)).fetchone()
            if row is None:
                return {"ok": False, "error": "fact not found"}
            value = max(-5, min(5, int(row["feedback"] or 0) + direction))
            conn.execute("UPDATE facts SET feedback = ? WHERE id = ?", (value, key))
        return {"ok": True, "id": key, "feedback": value}

    def all_facts(self, limit: int = 100, include_superseded: bool = False) -> list[dict]:
        where = "" if include_superseded else "WHERE active = 1"
        with self._db() as conn:
            rows = conn.execute(
                "SELECT * FROM facts %s ORDER BY id DESC LIMIT ?" % where,
                (_limit(limit, 100),),
            ).fetchall()
        return [self._fact(row) for row in rows]

    def stats(self) -> dict:
        with self._db() as conn:
            facts = int(conn.execute("SELECT COUNT(*) FROM facts WHERE active = 1").fetchone()[0])
            turns = int(conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0])
            uses = int(conn.execute("SELECT COALESCE(SUM(uses), 0) FROM facts").fetchone()[0])
            summaries = int(conn.execute("SELECT COUNT(*) FROM summaries").fetchone()[0])
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return {"facts": facts, "turns": turns, "uses": uses, "summaries": summaries,
                "path": self.path, "size_bytes": int(size)}


def default_store() -> MemoryStore:
    """One process-wide store, written under the agent's data directory."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        _DEFAULT_STORE = MemoryStore(config.data_path("assistant_memory.sqlite3"))
    return _DEFAULT_STORE
