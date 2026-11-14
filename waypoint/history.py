"""History: the student's past questions and answers, with pins, search and topic filters.

Each answered question is stored as one turn: the (PII-redacted) question, the checked answer text, and the
citations it carried (chunk id, heading, official URL), so a pinned answer can go into the advisor pack with real
source links even if the calendar data changes later. Stored per user under a pseudonymous hash, capped, and removed
by "Delete my data".
"""

import re
from datetime import UTC, datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field

from waypoint.config import Settings
from waypoint.observability.tracing import hash_user

COLLECTION = "history"
MAX_TURNS = 150  # oldest unpinned turns are dropped first
MAX_ANSWER_CHARS = 6000
FILTERS = ("all", "pinned", "prerequisites", "plans", "timetable")
PREREQ_WORDS = re.compile(r"prereq|unlock|eligible|can i take|exclusion|requirement", re.I)


class SourceRef(BaseModel):
    chunk_id: str
    heading: str = ""
    url: str = ""


class Turn(BaseModel):
    id: str  # the answer id the chat UI already has
    session_id: str = ""
    ts: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    question: str = Field(max_length=2000)
    answer: str = Field(max_length=MAX_ANSWER_CHARS)
    category: str = ""  # the router's label: simple_lookup | timetable | planning | unsure
    sources: list[SourceRef] = Field(default_factory=list)
    pinned: bool = False


def topic_of(turn: Turn) -> str:
    """The filter chip a turn belongs to. Planning and timetable come from the router; the rest from its words."""
    if turn.category == "planning":
        return "plans"
    if turn.category == "timetable":
        return "timetable"
    return "prerequisites" if PREREQ_WORDS.search(turn.question) or PREREQ_WORDS.search(turn.answer[:400]) else "other"


def matches(turn: Turn, query: str, flt: str) -> bool:
    if flt == "pinned" and not turn.pinned:
        return False
    if flt in ("prerequisites", "plans", "timetable") and topic_of(turn) != flt:
        return False
    needle = query.strip().lower()
    return not needle or needle in turn.question.lower() or needle in turn.answer.lower()


def record(turns: list[Turn], turn: Turn) -> list[Turn]:
    """Newest first. Past the cap the oldest unpinned turns go; pinned turns are kept."""
    updated = [turn, *[t for t in turns if t.id != turn.id]]
    while len(updated) > MAX_TURNS:
        drop = next((i for i in range(len(updated) - 1, -1, -1) if not updated[i].pinned), None)
        if drop is None:
            break
        updated.pop(drop)
    return updated


class HistoryStore(Protocol):
    def get(self, user_id: str) -> list[Turn]: ...

    def put(self, user_id: str, turns: list[Turn]) -> None: ...

    def delete(self, user_id: str) -> None: ...


class MemoryHistoryStore:
    def __init__(self) -> None:
        self._docs: dict[str, list[dict[str, Any]]] = {}

    def get(self, user_id: str) -> list[Turn]:
        return [Turn(**t) for t in self._docs.get(hash_user(user_id), [])]

    def put(self, user_id: str, turns: list[Turn]) -> None:
        self._docs[hash_user(user_id)] = [t.model_dump() for t in turns]

    def delete(self, user_id: str) -> None:
        self._docs.pop(hash_user(user_id), None)


class FirestoreHistoryStore:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore

        self._collection = firestore.Client(project=settings.gcp_project).collection(COLLECTION)

    def get(self, user_id: str) -> list[Turn]:
        snap = self._collection.document(hash_user(user_id)).get()
        doc = snap.to_dict() if snap.exists else None
        return [Turn(**t) for t in (doc or {}).get("turns", [])]

    def put(self, user_id: str, turns: list[Turn]) -> None:
        self._collection.document(hash_user(user_id)).set({"turns": [t.model_dump() for t in turns]})

    def delete(self, user_id: str) -> None:
        self._collection.document(hash_user(user_id)).delete()


def history_store(settings: Settings) -> HistoryStore:
    return FirestoreHistoryStore(settings) if settings.sessions == "firestore" else MemoryHistoryStore()
