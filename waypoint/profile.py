"""Student profile: the courses a student has taken or is taking, their program and graduation year. Nothing else.

No grades, no student number, no name or email. Course codes are checked against the catalog (so a typo or a made-up
code is rejected rather than stored), and free text that looks like a student number is refused. The profile is keyed
by a pseudonymous hash of the Google account id, and "Delete my data" removes it entirely.
"""

import re
from datetime import date
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from waypoint.config import Settings
from waypoint.datastore import get_store
from waypoint.observability.tracing import hash_user
from waypoint.tools.catalog import Catalog, normalise_code

COLLECTION = "profiles"
MAX_COURSES = 80
COURSE_CODE = re.compile(r"^[A-Z]{3}[A-D0-9]\d{2}[HY]\d$")
# A U of T student number is 9-10 digits; refuse any run of 7+ digits (or digits split by spaces/dashes) in free text.
STUDENT_NUMBER = re.compile(r"\d[\d\s-]{5,}\d")


class ProfileCourse(BaseModel):
    code: str = Field(max_length=12)
    status: Literal["done", "now"]


class Profile(BaseModel):
    courses: list[ProfileCourse] = Field(default_factory=list, max_length=MAX_COURSES)
    program: str = Field(default="", max_length=40)  # the primary program code such as ERMAJ2511, or empty
    programs: list[str] = Field(default_factory=list, max_length=4)  # every selected program (Programs tab)
    grad_year: int | None = None
    onboarded: bool = False  # false until the student finishes or skips onboarding
    # One-time toggle: "I completed the high-school prerequisites for university math (MHF4U, MCV4U)".
    highschool_done: bool = False

    def codes(self, status: str | None = None) -> list[str]:
        return [c.code for c in self.courses if status is None or c.status == status]


class ProfileError(ValueError):
    """The profile can't be saved; the message is safe to show to the student."""


def looks_like_student_number(text: str) -> bool:
    return bool(STUDENT_NUMBER.search(text))


def validate_profile(raw: dict[str, Any], catalog: Catalog, today: date | None = None) -> Profile:
    """Parse and check a profile from the browser. Raises ProfileError with a student-friendly message."""
    this_year = (today or date.today()).year
    program = str(raw.get("program") or "").strip().upper()
    if looks_like_student_number(program):
        raise ProfileError("That looks like a student number. Waypoint never stores student numbers.")
    programs: list[str] = []
    for raw_code in [program, *(raw.get("programs") or [])]:
        code = str(raw_code or "").strip().upper()
        if not code or code in programs:
            continue
        if looks_like_student_number(code):
            raise ProfileError("That looks like a student number. Waypoint never stores student numbers.")
        if not catalog.run("get_program", {"code": code, "pattern": "%\x00%"}) and get_store().program(code) is None:
            raise ProfileError(f"{code} isn't a program in Waypoint's data yet.")
        programs.append(code)
    if len(programs) > 4:
        raise ProfileError("Pick at most 4 programs.")
    program = programs[0] if programs else ""
    year = raw.get("grad_year")
    if year in ("", None):
        grad_year = None
    elif isinstance(year, int) and not isinstance(year, bool) and this_year <= year <= this_year + 8:
        grad_year = year
    else:
        raise ProfileError(f"Graduation year must be between {this_year} and {this_year + 8}.")

    courses: dict[str, str] = {}
    for item in raw.get("courses") or []:
        code = normalise_code(str(item.get("code", "")))
        status = item.get("status")
        if looks_like_student_number(code) or not COURSE_CODE.match(code):
            raise ProfileError(f"{str(item.get('code', ''))[:20]!r} isn't a course code like MAT223H5.")
        if status not in ("done", "now"):
            raise ProfileError("Each course is either taken or in progress.")
        if not catalog.run("get_course", {"code": code}):
            raise ProfileError(f"{code} isn't in Waypoint's course data yet.")
        courses[code] = "now" if courses.get(code) == "now" or status == "now" else "done"
    if len(courses) > MAX_COURSES:
        raise ProfileError("That's more courses than a degree needs. Remove a few.")
    return Profile(
        courses=[ProfileCourse(code=c, status=s) for c, s in sorted(courses.items())],  # type: ignore[arg-type]
        program=program,
        programs=programs,
        grad_year=grad_year,
        onboarded=bool(raw.get("onboarded", True)),
        highschool_done=bool(raw.get("highschool_done", False)),
    )


def profile_context(profile: Profile) -> str:
    """The text the agents see. Course codes, statuses, program and year only: nothing identifying."""
    if not profile.courses and not profile.program and not profile.grad_year and not profile.highschool_done:
        return ""
    parts = []
    if done := profile.codes("done"):
        parts.append(f"Courses completed: {', '.join(done)}.")
    if now := profile.codes("now"):
        parts.append(f"Courses in progress: {', '.join(now)}.")
    if profile.programs or profile.program:
        parts.append(f"Program: {', '.join(profile.programs) or profile.program}.")
    if profile.grad_year:
        parts.append(f"Expected graduation year: {profile.grad_year}.")
    if profile.highschool_done:
        parts.append("High-school math prerequisites completed.")
    return " ".join(parts)


class ProfileStore(Protocol):
    def get(self, user_id: str) -> Profile | None: ...

    def put(self, user_id: str, profile: Profile) -> None: ...

    def delete(self, user_id: str) -> None: ...


class MemoryProfileStore:
    def __init__(self) -> None:
        self._docs: dict[str, dict[str, Any]] = {}

    def get(self, user_id: str) -> Profile | None:
        doc = self._docs.get(hash_user(user_id))
        return Profile(**doc) if doc else None

    def put(self, user_id: str, profile: Profile) -> None:
        self._docs[hash_user(user_id)] = profile.model_dump()

    def delete(self, user_id: str) -> None:
        self._docs.pop(hash_user(user_id), None)


class FirestoreProfileStore:
    def __init__(self, settings: Settings) -> None:
        from google.cloud import firestore

        self._collection = firestore.Client(project=settings.gcp_project).collection(COLLECTION)

    def get(self, user_id: str) -> Profile | None:
        snap = self._collection.document(hash_user(user_id)).get()
        doc = snap.to_dict()
        return Profile(**doc) if snap.exists and doc else None

    def put(self, user_id: str, profile: Profile) -> None:
        self._collection.document(hash_user(user_id)).set(profile.model_dump())

    def delete(self, user_id: str) -> None:
        self._collection.document(hash_user(user_id)).delete()


def profile_store(settings: Settings) -> ProfileStore:
    return FirestoreProfileStore(settings) if settings.sessions == "firestore" else MemoryProfileStore()
