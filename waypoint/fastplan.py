"""The fast planner: a term-by-term plan toward a program, built in code from the parsed calendar data.

    request text (+ profile)  ->  parse_request   program, completed and wanted courses, start term, term/load limits
    program requirements      ->  choose          the cheapest courses that satisfy each requirement line, plus any
                                                  prerequisites they need (cost = half-credits still to take)
    chosen courses            ->  schedule        earliest term whose prerequisites are met by completed courses and
                                                  earlier terms, at most 2.5 credits (or the asked-for course count)
    the plan                  ->  validate_plan_text, the same deterministic check the agent planner's critic uses

Nothing here is guessed: a requirement line Waypoint can't read is listed for the student to check, a course that
isn't in the data is named and left out, and a plan that fails validation is not returned (the agent planner then
answers instead). The model only writes a short explanation after the plan is already on screen.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from waypoint.datastore import DataStore
from waypoint.ingest.prereq_parser import Node, is_fully_parsed
from waypoint.plans import MAX_CREDITS_PER_TERM, next_term, term_key
from waypoint.prereq_eval import evaluate
from waypoint.tools.catalog import Catalog
from waypoint.tools.plan import validate_plan_text
from waypoint.tools.prereqs import check_prerequisites

CODE = re.compile(r"\b[A-Z]{3}[A-D0-9]\d{2}[HY]\d\b")
TERM_MENTION = re.compile(r"\b(?:(20\d\d)\s+(Fall|Winter|Summer)|(Fall|Winter|Summer)\s+(20\d\d))\b", re.I)
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8}
N_TERMS = re.compile(r"\b(?:next|first|over)?\s*(\d|one|two|three|four|five|six|seven|eight)\s+terms?\b", re.I)
N_YEARS = re.compile(r"\b(?:(first|second|third|fourth|next) year|(\d|one|two|three|four) years?)\b", re.I)
PER_TERM = re.compile(
    r"\b(?:max(?:imum)?|at most|no more than|only)\s+(\d|one|two|three|four|five)\s+courses?\s+(?:per|a|each)\s+term"
    r"|\b(\d|one|two|three|four|five)\s+courses?\s+(?:per|a|each)\s+term",
    re.I,
)
WANT = re.compile(r"\b(?:want|wants|wanted|like to|hoping|interested|include|as soon as|asap|plan to take)\b", re.I)
TYPE_WORDS = ("specialist", "major", "minor")
GENERIC_WORDS = {"science", "sciences", "arts", "stream", "honours", "bachelor", "of", "and", "the"}
MAX_TERMS = 8
DEFAULT_COURSES_PER_TERM = 5
INF = 10**6


@dataclass
class PlanRequest:
    program: str
    completed: list[str]
    wanted: list[str] = field(default_factory=list)
    start: str = ""
    terms: int | None = None  # how many Fall/Winter terms to plan; None = until the program is done (max 8)
    end: str = ""
    per_term: int = DEFAULT_COURSES_PER_TERM


@dataclass
class FastPlan:
    request: PlanRequest
    program_name: str
    version: str
    terms: list[tuple[str, list[str]]]
    reasons: dict[str, str]  # course -> the requirement line it satisfies (or "prerequisite for X")
    leftover: list[str]  # chosen courses that didn't fit in the asked-for terms
    check_yourself: list[str]  # requirement lines Waypoint can't evaluate or fill
    not_in_data: list[str]  # required courses that aren't in Waypoint's data
    validation: dict[str, Any]
    chunk_ids: list[str]

    def plan_text(self) -> str:
        return "; ".join(f"{t}: {', '.join(cs)}" for t, cs in self.terms if cs)

    def structured(self) -> dict[str, Any]:
        """The `plan` stream event: everything the UI needs to show the plan before any model text."""
        return {
            "program": self.request.program,
            "program_name": self.program_name,
            "version": self.version,
            "completed": self.request.completed,
            "terms": [{"term": t, "courses": cs} for t, cs in self.terms],
            "reasons": self.reasons,
            "leftover": self.leftover,
            "check_yourself": self.check_yourself,
            "not_in_data": self.not_in_data,
            "valid": bool(self.validation.get("valid")),
            "errors": self.validation.get("errors", []),
            "warnings": self.validation.get("warnings", []),
        }


def _number(word: str) -> int:
    return int(word) if word.isdigit() else NUMBERS[word.lower()]


def default_start(today: date | None = None) -> str:
    today = today or date.today()
    return f"{today.year + 1} Winter" if today.month >= 9 else f"{today.year} Fall"


def match_program(text: str, store: DataStore) -> str:
    """The one program the question names: its type word (major/minor/specialist) plus its subject. When several
    programs of that type share a subject ("Mathematics major" vs "Applied Mathematics major"), the one whose
    extra name words all appear wins, else the plainest one. Empty if the question doesn't pin one down."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    wanted = [t for t in TYPE_WORDS if t in words]
    if len(wanted) != 1:
        return ""

    def name_words(p: dict[str, Any]) -> set[str]:
        name = re.sub(r"\(.*?\)", " ", p["name"]).lower()
        return {w for w in re.findall(r"[a-z]+", name) if w not in TYPE_WORDS and w != "program"}

    candidates = [p for p in store.programs.values() if p["type"] == wanted[0]]
    # the subject must be named: some name word, or its stem ("math" for "mathematics"), appears in the question
    # ("science" alone doesn't count: "Computer Science major" is not "Mathematical Sciences - Major")
    stems = {x[:4] for x in words}
    candidates = [p for p in candidates if any(w[:4] in stems for w in name_words(p) - GENERIC_WORDS)]
    if len(candidates) <= 1:
        return candidates[0]["code"] if candidates else ""
    common = set.intersection(*(name_words(p) for p in candidates))
    extra = {p["code"]: name_words(p) - common for p in candidates}
    full = [c for c, e in extra.items() if e and e <= words]
    if len(full) == 1:
        return full[0]
    plain = [c for c, e in extra.items() if not e or not (e & words)]
    plain.sort(key=lambda c: len(extra[c]))
    if plain and (len(plain) == 1 or len(extra[plain[0]]) < len(extra[plain[1]])):
        return plain[0]
    return ""


def parse_request(text: str, context: str, store: DataStore, today: date | None = None) -> PlanRequest | None:
    """None when the question doesn't name a program in the data and the profile doesn't either."""
    program = match_program(text, store)
    if not program:
        profile_programs = re.search(r"Program: ([A-Z0-9, ]+)\.", context)
        if profile_programs:
            program = next((p.strip() for p in profile_programs.group(1).split(",") if store.program(p.strip())), "")
    if not program:
        return None
    completed: list[str] = []
    wanted: list[str] = []
    for sentence in re.split(r"(?<=[.!?])\s+", text):
        target = wanted if WANT.search(sentence) else completed
        target += [c for c in CODE.findall(sentence.upper()) if c not in completed and c not in wanted]
    for label in ("Courses completed:", "Courses in progress:"):
        part = context.split(label, 1)[1].split(".", 1)[0] if label in context else ""
        completed += [c for c in CODE.findall(part) if c not in completed]
    wanted = [c for c in wanted if c not in completed]

    mentions = [f"{m.group(1) or m.group(4)} {(m.group(2) or m.group(3)).title()}" for m in TERM_MENTION.finditer(text)]
    start = mentions[0] if mentions else default_start(today)
    if start.endswith("Summer"):
        start = next_term(start)
    end = mentions[-1] if len(mentions) > 1 else ""
    terms: int | None = None
    if (m := N_TERMS.search(text)) and "per term" not in text[m.start() : m.end() + 10].lower():
        terms = _number(m.group(1))
    elif re.search(r"\bnext term\b", text, re.I) and not end:
        terms = 1
    elif m := N_YEARS.search(text):
        terms = 2 if m.group(1) else 2 * _number(m.group(2))
    per_term = DEFAULT_COURSES_PER_TERM
    if m := PER_TERM.search(text):
        per_term = _number(m.group(1) or m.group(2))
    return PlanRequest(program, completed, wanted, start, terms, end, per_term)


class _Chooser:
    """Picks courses for requirement lines. `have` = completed + already chosen."""

    def __init__(self, store: DataStore, completed: list[str], wanted: list[str]) -> None:
        self.store = store
        self.completed = set(completed)
        self.chosen: list[str] = []
        self.reasons: dict[str, str] = {}
        self.wanted = wanted
        self.reserved: set[str] = set()  # courses a program line requires outright: they'll be taken anyway
        self.helpful: dict[str, int] = {}  # how many reserved courses list a code in their prerequisites

    @property
    def have(self) -> set[str]:
        return self.completed | set(self.chosen)

    def excluded(self, code: str) -> bool:
        course = self.store.get(code) or {}
        mine = set(course.get("exclusion_codes") or [])
        others = self.have | (self.reserved - {code})
        return bool(mine & others) or any(code in (self.store.get(h) or {}).get("exclusion_codes", []) for h in others)

    def cost(self, code: str, depth: int = 0) -> int:
        """Half-credits still to take to have `code`, its missing prerequisites included."""
        if code in self.have or code in self.reserved:
            return 0
        course = self.store.get(code)
        if course is None or depth > 6 or self.excluded(code):
            return INF
        return round(float(course["credits"]) * 2) + self.tree_cost(course.get("prereq_tree"), depth + 1)

    def tree_cost(self, tree: Node | None, depth: int = 0) -> int:
        if tree is None:
            return 0
        if "all_of" in tree:
            return min(INF, sum(self.tree_cost(c, depth) for c in tree["all_of"]))
        if "one_of" in tree:
            return min((self.tree_cost(c, depth) for c in tree["one_of"]), default=0)
        if "course" in tree:
            return self.cost(tree["course"], depth)
        return 0  # grades, high-school courses, permission: shown as warnings, not courses to add

    def option_key(self, tree: Node) -> tuple[int, int, int]:
        codes = set(CODE.findall(str(tree)))
        helps = max((self.helpful.get(c, 0) for c in codes), default=0)
        return self.tree_cost(tree), 0 if codes & set(self.wanted) else 1, -helps

    def satisfy(self, tree: Node | None, reason: str, depth: int = 0) -> bool:
        """Adds courses so `tree` is met. False if it can't be (a course not in the data, or an exclusion)."""
        if tree is None or depth > 6:
            return tree is None
        if "all_of" in tree:
            return all([self.satisfy(c, reason, depth) for c in tree["all_of"]])
        if "one_of" in tree:
            if any(evaluate(c, self.have).status == "met" for c in tree["one_of"]):
                return True
            best = min(tree["one_of"], key=self.option_key)  # stable: ties keep the calendar's order
            return self.option_key(best)[0] < INF and self.satisfy(best, reason, depth)
        if "course" in tree:
            return self.add(tree["course"], reason, depth)
        return True

    def add(self, code: str, reason: str, depth: int = 0) -> bool:
        if code in self.have:
            return True
        course = self.store.get(code)
        if course is None or self.cost(code) >= INF:
            return False
        self.satisfy(course.get("prereq_tree"), f"prerequisite for {code}", depth + 1)
        self.chosen.append(code)
        self.reasons.setdefault(code, reason)
        return True


def _mandatory(tree: Node) -> list[str]:
    """Courses a requirement line needs whatever else is chosen (not inside an "or")."""
    if "course" in tree:
        return [tree["course"]]
    if "all_of" in tree:
        return [c for child in tree["all_of"] for c in _mandatory(child)]
    return []


def _at_level(subject: str, low: int) -> Callable[[str], bool]:
    def counts(code: str) -> bool:
        return code[:3] == subject and code[3].isdigit() and int(code[3]) >= low

    return counts


def _elective(text: str) -> tuple[float, Callable[[str], bool], set[str]] | None:
    """An elective line Waypoint can fill: (credits, which courses count, recommended codes). None if unreadable.

    Reads "2.0 additional credit from MAT302H5 or MAT309H5 or ...", "1.0 credit from the following: MAT at the 300/400
    level or CSC363H5 (except for MAT397H5 ...)", "0.5 additional credits in MAT at the 300+level, except MAT322H5"
    and "1.0 additional credits at the 300/400 level in CSC or STA or MAT"."""
    amount = re.match(r"\s*(\d+(?:\.\d+)?) (?:additional )?credits?\b", text)
    if not amount:
        return None
    excluded = set(CODE.findall(re.split(r"\bexcept\b", text, maxsplit=1)[1])) if "except" in text else set()
    recommended = set(CODE.findall(" ".join(re.findall(r"\(([^)]*recommended[^)]*)\)", text))))
    body = re.sub(r"\([^)]*\)", " ", re.split(r"\bexcept\b", text, maxsplit=1)[0])
    listed = set(CODE.findall(body))
    rest = CODE.sub(" ", body[amount.end() :])
    level = re.search(r"(\d)00(?:/(\d)00)?(\+)?\s*level", rest)
    subjects = set(re.findall(r"\b([A-Z]{3})\b", rest))
    if level and subjects:
        levels = {int(level.group(1))} | ({int(level.group(2))} if level.group(2) else set())
        if level.group(3):
            levels |= set(range(int(level.group(1)), 5))
    elif listed and re.fullmatch(r"[\s,]*(?:from(?: the following:)?)?[\s,]*(?:(?:or|and)[\s,]*)*", rest):
        levels = set()
    else:
        return None

    def counts(code: str) -> bool:
        if code in excluded:
            return False
        return code in listed or (code[:3] in subjects and code[3].isdigit() and int(code[3]) in levels)

    return float(amount.group(1)), counts, recommended


def _fill_elective(
    chooser: _Chooser, credits: float, counts: Callable[[str], bool], recommended: set[str], used: set[str], line: str
) -> bool:
    def eligible(code: str) -> bool:
        return counts(code) and code not in used

    have = sum(float((chooser.store.get(c) or {}).get("credits") or 0.5) for c in chooser.have if eligible(c))
    for code in sorted(chooser.have):
        if eligible(code):
            used.add(code)
    candidates = sorted(
        # a course whose own prerequisite Waypoint can't read ("see the timetable") is never picked for the student
        (
            c
            for c in chooser.store.courses
            if eligible(c) and c not in chooser.have and is_fully_parsed(chooser.store.courses[c].get("prereq_tree"))
        ),
        key=lambda c: (0 if c in chooser.wanted else 1, 0 if c in recommended else 1, chooser.cost(c), c),
    )
    for code in candidates:
        if have >= credits:
            break
        if chooser.cost(code) < INF and chooser.add(code, line):
            used.add(code)
            have += float(chooser.store.courses[code]["credits"])
    return have >= credits


def choose(store: DataStore, request: PlanRequest) -> tuple[_Chooser, str, list[str], list[str]]:
    program = store.program(request.program)
    assert program is not None
    version = program["requirements_tree"]["versions"][0]  # the current students' version (the calendar lists it first)
    chooser = _Chooser(store, request.completed, request.wanted)
    for code in request.wanted:
        chooser.add(code, "you asked for it")
    check_yourself: list[str] = []
    not_in_data: list[str] = []
    used: set[str] = set()  # courses already counted toward a specific line, so "additional" electives are extra
    lines = [req for group in version["groups"] for req in group["requirements"]]
    exact = [r for r in lines if r["tree"] is not None and "unparsed" not in r["tree"] and "credits" not in r["tree"]]
    # Courses a line names outright go in first, then upper-year lines before first-year ones: their prerequisites
    # steer the choices on earlier lines (a Specialist's required MAT257Y5 needs MAT159H5, so a first-year MAT139H5,
    # or a MAT232H5 for some later course, both exclusions of what's required, are never picked).
    chooser.reserved = {c for req in exact for c in _mandatory(req["tree"]) if store.get(c)}
    line_of = {c: req["text"] for req in exact for c in _mandatory(req["tree"])}
    # on a tie, the option that more required courses list as a prerequisite wins (MAT159H5 over MAT134H5 when
    # MAT257Y5 is required)
    for r in chooser.reserved:
        for c in set(CODE.findall(str(store.courses[r].get("prereq_tree")))):
            chooser.helpful[c] = chooser.helpful.get(c, 0) + 1
    reserved, chooser.reserved = chooser.reserved, set()
    cost = {c: chooser.cost(c) for c in reserved}
    chooser.reserved = reserved
    # highest level first, then the costliest: their prerequisite chains are the least flexible
    for code in sorted(reserved, key=lambda c: (-int(c[3]) if c[3].isdigit() else 0, -cost[c], c)):
        chooser.reserved.discard(code)
        chooser.add(code, line_of[code])
        chooser.reserved.add(code)
    for req in reversed(exact):
        tree = req["tree"]
        if not chooser.satisfy(tree, req["text"]):
            missing = [c for c in CODE.findall(str(tree)) if store.get(c) is None]
            not_in_data += [c for c in missing if c not in not_in_data]
            if not missing:
                check_yourself.append(f"{req['text']} (Waypoint couldn't fit it with your other courses)")
    for req in exact:  # a course named on a line is there for that line, not as someone's prerequisite
        for code in CODE.findall(str(req["tree"])):
            if code in chooser.chosen and chooser.reasons.get(code, "").startswith("prerequisite"):
                chooser.reasons[code] = req["text"]
        used |= {c for c in CODE.findall(str(req["tree"])) if c in chooser.have}
    for req in lines:
        if req in exact:
            continue
        tree = req["tree"]
        spec = _elective(req["text"])
        if spec is None and tree and "credits" in tree:
            spec = (float(tree["credits"]), _at_level(tree["subject"], tree.get("level", 100) // 100), set())
        if spec is None or not _fill_elective(chooser, *spec, used, req["text"]):
            check_yourself.append(req["text"])
    return chooser, version["label"], check_yourself, not_in_data


Terms = list[tuple[str, list[str]]]


def schedule(store: DataStore, chooser: _Chooser, request: PlanRequest) -> tuple[Terms, list[str]]:
    todo = list(chooser.chosen)
    dependents = {c: sum(c in CODE.findall(str((store.get(o) or {}).get("prereq_tree"))) for o in todo) for c in todo}
    have = set(request.completed)
    terms: list[tuple[str, list[str]]] = []
    term = request.start
    while todo and len(terms) < MAX_TERMS:
        if request.terms is not None and len(terms) >= request.terms:
            break
        if request.end and term_key(term) > term_key(request.end):
            break
        ready = [
            c
            for c in todo
            if evaluate((store.get(c) or {}).get("prereq_tree"), have, True).status != "unmet"
            and check_prerequisites(store.courses[c].get("prerequisites") or "", sorted(have)).status != "unmet"
            # unless the prerequisite is plainly met already, a course it names that the plan itself added comes in an
            # earlier term, even when the wording has an alternative code can't check (MAT334H5's "... or 0.5 credit at
            # the 300+ level with 60%" would otherwise read as "unclear" and let it share a term with MAT202H5)
            and (
                evaluate((store.get(c) or {}).get("prereq_tree"), have, True).status in ("met", "none")
                or not set(CODE.findall(store.courses[c].get("prerequisites") or "")) & set(todo)
            )
        ]
        ready.sort(key=lambda c: (0 if c in request.wanted else 1, -dependents[c], c[3], c))
        picked: list[str] = []
        credits = 0.0
        for code in ready:
            weight = float(store.courses[code]["credits"])
            if len(picked) < request.per_term and credits + weight <= MAX_CREDITS_PER_TERM:
                picked.append(code)
                credits += weight
        if not picked:
            break  # nothing left can be taken: the rest is reported, never forced into a term
        terms.append((term, sorted(picked)))
        have |= set(picked)
        todo = [c for c in todo if c not in picked]
        term = next_term(term)
    return terms, todo


def build_plan(
    text: str, context: str, store: DataStore, catalog: Catalog, today: date | None = None
) -> FastPlan | None:
    """A validated plan, or None when the question isn't one this planner can answer (the agent planner then does)."""
    request = parse_request(text, context, store, today)
    if request is None:
        return None
    program = store.program(request.program)
    assert program is not None
    chooser, version, check_yourself, not_in_data = choose(store, request)
    terms, leftover = schedule(store, chooser, request)
    plan = "; ".join(f"{t}: {', '.join(cs)}" for t, cs in terms)
    validation: dict[str, Any] = {"valid": not leftover, "errors": []}
    if terms:
        validation = validate_plan_text(plan, request.completed, catalog)
    if terms and not validation["valid"]:
        return None  # never show a plan our own check rejects
    chunk_ids = [store.courses[c]["chunk_id"] for _, cs in terms for c in cs if store.courses[c].get("chunk_id")]
    return FastPlan(
        request=request,
        program_name=program["name"],
        version=version,
        terms=terms,
        reasons={c: chooser.reasons.get(c, "") for _, cs in terms for c in cs},
        leftover=leftover,
        check_yourself=check_yourself,
        not_in_data=not_in_data,
        validation=validation,
        chunk_ids=list(dict.fromkeys(chunk_ids)),
    )


def plan_markdown(plan: FastPlan, store: DataStore) -> str:
    """The plan as answer text: one line per term (the format evals/plan_validity.py reads), then what's left."""
    lines = [
        f"Here's a plan for the **{plan.program_name}** ({plan.version.lower()}), checked against prerequisites, "
        "exclusions and term load:",
        "",
    ]
    for term, courses in plan.terms:
        cited = ", ".join(
            f"{c}" + (f" [{store.courses[c]['chunk_id']}]" if store.courses[c].get("chunk_id") else "") for c in courses
        )
        lines.append(f"- **{term}**: {cited}")
    if not plan.terms:
        lines.append("- No new courses are needed for the lines Waypoint can check: you've covered them.")
    notes = []
    if plan.request.completed:
        notes.append(f"Already done: {', '.join(plan.request.completed)}.")
    if plan.leftover:
        notes.append(f"Still to take after these terms: {', '.join(plan.leftover)}.")
    if plan.not_in_data:
        notes.append(f"Not in Waypoint's data yet, so add it yourself: {', '.join(plan.not_in_data)}.")
    for line in plan.check_yourself:
        notes.append(f"Check this requirement yourself: “{line}”")
    for warning in plan.validation.get("warnings", [])[:4]:
        # "2027 Winter: confirm X's prerequisite yourself: ..." becomes "Confirm X's prerequisite yourself (2027
        # Winter): ...", so no note line starts with a term, which would read as another term of the plan
        term, _, rest = warning.partition(": ")
        what, _, detail = rest.partition(": ")
        line = f"{what[0].upper()}{what[1:]} ({term}): {detail}" if detail else warning
        notes.append(line.rstrip(".") + ".")
    notes.append("Waypoint doesn't know which term each course is offered, so confirm offerings on the timetable.")
    return "\n".join(lines + [""] + [f"- {n}" for n in notes])


def needs_program_reply(store: DataStore) -> str:
    names = "; ".join(f"{p['name']} ({code})" for code, p in sorted(store.programs.items()))
    return (
        "Which program should I plan toward? Waypoint can build a checked term-by-term plan for: "
        f"{names}. Name one in your question (for example “plan the Mathematics minor starting 2027 Winter”), "
        "or pick your program in **Settings** so I use it every time."
    )
