"""Timetable builder logic: which sections to offer, clash detection and one-click fixes. All plain code.

Data comes from the synthetic sample timetable (see waypoint/mcp/timetable); every response carries its label.
A clash is two chosen sections with meetings on the same day whose times overlap. A fix switches one chosen
section to another section of the same course and type, and is offered only if it removes clashes without
creating new ones.
"""

import itertools
from typing import Any

from waypoint.mcp.timetable.adapter import Section, TimetableAdapter


def minutes(hhmm: str) -> int:
    hours, mins = hhmm.split(":")
    return int(hours) * 60 + int(mins)


def hhmm(total: int) -> str:
    return f"{total // 60:02d}:{total % 60:02d}"


def clashes_between(a: Section, b: Section) -> list[dict[str, Any]]:
    found = []
    for ma, mb in itertools.product(a.meetings, b.meetings):
        if ma.day != mb.day:
            continue
        start = max(minutes(ma.start), minutes(mb.start))
        end = min(minutes(ma.end), minutes(mb.end))
        if start < end:
            found.append({"a": a.section_id, "b": b.section_id, "day": ma.day, "start": hhmm(start), "end": hhmm(end)})
    return found


def find_clashes(sections: list[Section]) -> list[dict[str, Any]]:
    clashes: list[dict[str, Any]] = []
    for a, b in itertools.combinations(sections, 2):
        clashes += clashes_between(a, b)
    return clashes


def label(section: Section) -> str:
    """'LEC0101 · Tue, Thu 13:00' (days and start of the first meeting on each day)."""
    days = ", ".join(dict.fromkeys(m.day[:3] for m in section.meetings)) or "TBA"
    start = section.meetings[0].start if section.meetings else ""
    return f"{section.section_type[:3].upper()}{section.section_number} · {days} {start}".strip()


def section_kind(section: Section) -> str:
    return section.section_type


def options_for(timetable: TimetableAdapter, course: str, term: str) -> list[Section]:
    return timetable.search_sections(course, term)


def default_selection(options: dict[str, list[Section]]) -> list[str]:
    """First section of each type per course, so the grid is never empty. It can clash: that is the demo."""
    chosen = []
    for sections in options.values():
        for kind in dict.fromkeys(s.section_type for s in sections):
            chosen.append(next(s for s in sections if s.section_type == kind).section_id)
    return chosen


def clash_count(sections: list[Section]) -> int:
    return len(find_clashes(sections))


def suggest_fixes(chosen: list[Section], options: dict[str, list[Section]]) -> list[dict[str, Any]]:
    """For each clash, the best one-section switch that reduces the total clash count without adding a new
    clashing pair. Returns [{from, to, course, resolves, reason}] best first, one per section."""
    base = clash_count(chosen)
    fixes: dict[str, dict[str, Any]] = {}
    for clash in find_clashes(chosen):
        for victim_id in (clash["a"], clash["b"]):
            victim = next(s for s in chosen if s.section_id == victim_id)
            for alt in options.get(victim.course, []):
                if alt.section_id == victim.section_id or alt.section_type != victim.section_type:
                    continue
                trial = [alt if s.section_id == victim.section_id else s for s in chosen]
                after = clash_count(trial)
                resolves = base - after
                if resolves > 0 and (victim_id not in fixes or resolves > fixes[victim_id]["resolves"]):
                    fixes[victim_id] = {
                        "from": victim.section_id,
                        "to": alt.section_id,
                        "course": victim.course,
                        "resolves": resolves,
                        "remaining": after,
                        "to_label": label(alt),
                    }
    return sorted(fixes.values(), key=lambda f: (-f["resolves"], f["remaining"], f["to"]))


def explain(clashes: list[dict[str, Any]], fixes: list[dict[str, Any]], chosen: list[Section]) -> str:
    """Pip's plain-language account of the clashes. Template text from the checked facts, never a model's guess."""
    if not clashes:
        return "No clashes: none of your chosen sections overlap."
    by_id = {s.section_id: s for s in chosen}
    pairs: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for c in clashes:
        pairs.setdefault((c["a"], c["b"]), []).append(c)
    parts = []
    for (a, b), hits in pairs.items():
        days = " and ".join(dict.fromkeys(h["day"] for h in hits))
        h = hits[0]
        sa, sb = by_id[a], by_id[b]
        parts.append(
            f"{sa.course} {sa.section_type[:3].upper()}{sa.section_number} and {sb.course} "
            f"{sb.section_type[:3].upper()}{sb.section_number} overlap on {days} from {h['start']} to {h['end']}."
        )
    text = " ".join(parts)
    if fixes:
        f = fixes[0]
        outcome = "fixes it" if f["remaining"] == 0 else "reduces the overlap"
        text += f" Switching {f['course']} to {f['to_label'].split(' · ')[0]} {outcome}."
    else:
        text += (
            " No other section of these courses avoids the overlap, so you may need to pick a different term or course."
        )
    return text


def timetable_view(
    timetable: TimetableAdapter, courses: list[str], term: str, selected_ids: list[str]
) -> dict[str, Any]:
    options = {c: options_for(timetable, c, term) for c in courses}
    with_sections = {c: s for c, s in options.items() if s}
    valid_ids = {s.section_id for sections in with_sections.values() for s in sections}
    chosen_ids = [i for i in selected_ids if i in valid_ids] or default_selection(with_sections)
    # Make sure every course/type has exactly one choice: keep the user's, fill the rest with defaults.
    kept: dict[tuple[str, str], str] = {}
    for sid in chosen_ids:
        sec = next(s for sections in with_sections.values() for s in sections if s.section_id == sid)
        kept.setdefault((sec.course, sec.section_type), sid)
    for sections in with_sections.values():
        for kind in dict.fromkeys(s.section_type for s in sections):
            first = next(s for s in sections if s.section_type == kind)
            kept.setdefault((first.course, kind), first.section_id)
    chosen = [s for sections in with_sections.values() for s in sections if s.section_id in kept.values()]
    clashes = find_clashes(chosen)
    fixes = suggest_fixes(chosen, with_sections)
    return {
        "term": term,
        "data_source": timetable.source.label,
        "courses": [
            {
                "code": code,
                "available": bool(options[code]),
                "types": [
                    {
                        "type": kind,
                        "options": [
                            {
                                "section_id": s.section_id,
                                "label": label(s),
                                "meetings": [m.model_dump() for m in s.meetings],
                            }
                            for s in options[code]
                            if s.section_type == kind
                        ],
                        "selected": kept.get((code, kind)),
                    }
                    for kind in dict.fromkeys(s.section_type for s in options[code])
                ],
            }
            for code in courses
        ],
        "chosen": [
            {
                "section_id": s.section_id,
                "course": s.course,
                "type": s.section_type,
                "number": s.section_number,
                "meetings": [m.model_dump() for m in s.meetings],
            }
            for s in chosen
        ],
        "clashes": clashes,
        "fixes": fixes,
        "explanation": explain(clashes, fixes, chosen),
    }
