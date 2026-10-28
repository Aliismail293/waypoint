"""Usage tracking for launch metrics: users, questions, answer ratings, advisor escalations.

Events hold no message text and no Google identity: a salted user hash, the session id, and numbers.
They go to Firestore (collection `waypoint_usage`) in production and to memory in tests and local dev.

    uv run python -m waypoint.analytics          # print real usage numbers from Firestore (operator)
"""

import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from waypoint.config import Settings
from waypoint.observability.tracing import hash_user

EventType = Literal["question", "feedback", "escalation"]
COLLECTION = "waypoint_usage"


@dataclass
class UsageEvent:
    type: EventType
    user: str  # hash_user(google sub), never the raw id
    session_id: str
    answer_id: str = ""
    rating: int = 0  # +1 thumbs up, -1 thumbs down (feedback events)
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    out_of_scope: bool = False
    ts: float = field(default_factory=time.time)


class UsageStore(Protocol):
    def add(self, event: UsageEvent) -> None: ...

    def all(self) -> list[UsageEvent]: ...


class MemoryUsageStore:
    def __init__(self) -> None:
        self.events: list[UsageEvent] = []

    def add(self, event: UsageEvent) -> None:
        self.events.append(event)

    def all(self) -> list[UsageEvent]:
        return list(self.events)


class FirestoreUsageStore:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore

        self._collection = firestore.Client(project=settings.gcp_project).collection(COLLECTION)

    def add(self, event: UsageEvent) -> None:
        self._collection.add(asdict(event))

    def all(self) -> list[UsageEvent]:
        return [UsageEvent(**(doc.to_dict() or {})) for doc in self._collection.stream()]


def usage_store(settings: Settings) -> UsageStore:
    return FirestoreUsageStore(settings) if settings.sessions == "firestore" else MemoryUsageStore()


def new_answer_id() -> str:
    return uuid.uuid4().hex


def question_event(user_id: str, session_id: str, answer_id: str, metrics: dict[str, Any]) -> UsageEvent:
    return UsageEvent(
        type="question",
        user=hash_user(user_id),
        session_id=session_id,
        answer_id=answer_id,
        latency_ms=metrics["latency_ms"],
        cost_usd=metrics["cost_usd"],
        out_of_scope=metrics.get("out_of_scope", False),
    )


def summarise(events: list[UsageEvent]) -> dict[str, Any]:
    """The launch numbers. Ratings and escalations are counted once per answer (latest click wins)."""
    questions = [e for e in events if e.type == "question"]
    ratings: dict[str, int] = {}
    for e in sorted((e for e in events if e.type == "feedback"), key=lambda e: e.ts):
        ratings[e.answer_id] = e.rating
    escalated = {e.answer_id or e.session_id for e in events if e.type == "escalation"}
    rated = len(ratings)
    return {
        "users": len({e.user for e in events}),
        "questions": len(questions),
        "sessions": len({e.session_id for e in questions}),
        "rated_answers": rated,
        "thumbs_up_rate": round(sum(r > 0 for r in ratings.values()) / rated, 3) if rated else None,
        "escalations": len(escalated),
        "escalation_rate": round(len(escalated) / len(questions), 3) if questions else None,
        "answered_without_escalation_rate": round(1 - len(escalated) / len(questions), 3) if questions else None,
        "total_cost_usd": round(sum(e.cost_usd for e in questions), 4),
    }


if __name__ == "__main__":
    import json

    from waypoint.config import get_settings

    settings = get_settings().model_copy(update={"sessions": "firestore"})
    print(json.dumps(summarise(usage_store(settings).all()), indent=2))
