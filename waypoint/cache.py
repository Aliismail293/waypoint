"""Answer cache: identical normalised questions get the stored answer for 24 hours.

Only the first message of a conversation is cached (a follow-up depends on earlier turns), and only clean
answers (no tool error, no step-limit hit, no out-of-scope reply, no unverified course code). Entries hold the
question's hash, the answer text and its chunk ids; no user id, no session id. They are keyed by a corpus
version too, so a re-ingest (which bumps the version, see `bump_corpus_version`) invalidates every entry.
"""

import hashlib
import re
import time
from dataclasses import asdict, dataclass, field
from typing import Protocol

from waypoint.config import Settings

COLLECTION = "waypoint_answer_cache"
META_COLLECTION = "waypoint_meta"
META_DOC = "corpus"


def normalise(question: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace: 'MAT224 prereqs?' and 'mat224  prereqs' match."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", question.lower())).strip()


def cache_key(question: str, version: str, context: str = "") -> str:
    return hashlib.sha256(f"{version}|{context}|{normalise(question)}".encode()).hexdigest()


@dataclass
class CachedAnswer:
    text: str
    chunk_ids: list[str]
    route: str = ""
    stored_at: float = field(default_factory=time.time)


class AnswerCache(Protocol):
    def version(self) -> str: ...

    def get(self, key: str) -> CachedAnswer | None: ...

    def put(self, key: str, answer: CachedAnswer) -> None: ...


class MemoryAnswerCache:
    def __init__(self, ttl_s: float = 86400.0) -> None:
        self.ttl_s = ttl_s
        self._items: dict[str, CachedAnswer] = {}
        self._version = "0"

    def version(self) -> str:
        return self._version

    def bump(self) -> None:
        self._version = str(int(self._version) + 1)

    def get(self, key: str) -> CachedAnswer | None:
        item = self._items.get(key)
        if item is None or time.time() - item.stored_at > self.ttl_s:
            return None
        return item

    def put(self, key: str, answer: CachedAnswer) -> None:
        self._items[key] = answer


class FirestoreAnswerCache:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore

        self._db = firestore.Client(project=settings.gcp_project)
        self.ttl_s = settings.answer_cache_ttl_hours * 3600
        self._version = ("0", 0.0)  # (value, fetched_at): re-read at most once a minute

    def version(self) -> str:
        value, fetched = self._version
        if time.time() - fetched > 60:
            doc = self._db.collection(META_COLLECTION).document(META_DOC).get()
            value = str((doc.to_dict() or {}).get("version", "0")) if doc.exists else "0"
            self._version = (value, time.time())
        return value

    def get(self, key: str) -> CachedAnswer | None:
        doc = self._db.collection(COLLECTION).document(key).get()
        if not doc.exists:
            return None
        item = CachedAnswer(**(doc.to_dict() or {}))
        return item if time.time() - item.stored_at <= self.ttl_s else None

    def put(self, key: str, answer: CachedAnswer) -> None:
        self._db.collection(COLLECTION).document(key).set(asdict(answer))


def bump_corpus_version(settings: Settings) -> str:
    """Called after a re-ingest: every cached answer made against the old corpus stops matching."""
    from google.cloud import firestore

    version = str(int(time.time()))
    firestore.Client(project=settings.gcp_project).collection(META_COLLECTION).document(META_DOC).set(
        {"version": version}
    )
    return version


def build_answer_cache(settings: Settings) -> AnswerCache | None:
    if settings.answer_cache == "firestore":
        return FirestoreAnswerCache(settings)
    if settings.answer_cache == "memory":
        return MemoryAnswerCache(settings.answer_cache_ttl_hours * 3600)
    return None
