"""Program completion text -> requirements tree and machine-checkable rules.

Calendar programs list their requirements as year headings with one requirement per bullet, often in two versions
("For students entering the program in 2026-2027 (and earlier)" / "2027-2028 (and beyond)"). Every bullet keeps its
exact text; its `tree` is the prerequisite-style boolean tree, or `{"unparsed": ...}` when the wording isn't one we
model (e.g. "1.0 additional credits at the 300/400 level in CSC or STA"). Nothing is inferred beyond the text.
"""

import re
from typing import Any

from waypoint.ingest.prereq_parser import parse_prereq

CREDITS_REQUIRED = re.compile(r"^([\d.]+)(?:\s*-\s*([\d.]+))? credits? (?:are|is) required", re.I | re.M)
VERSION_LINE = re.compile(r"^For students (?:entering|applying)[^:]*:", re.I)
YEAR_HEADING = re.compile(r"^(First|Second|Third|Fourth|Higher) years?:?$", re.I)
PROGRAM_CODE = re.compile(r"\b(ER[A-Z]{3}\d{4}[A-Z]?)\b")


def parse_requirements(text: str) -> dict[str, Any]:
    """Return {"credits_required": {"min", "max"} | None, "versions": [...], "notes": [...], "other_text": [...]}."""
    credits = CREDITS_REQUIRED.search(text)
    credits_required = None
    if credits:
        low = float(credits.group(1))
        credits_required = {"min": low, "max": float(credits.group(2) or low)}
    versions: list[dict[str, Any]] = [{"label": "All students", "groups": []}]
    notes: list[str] = []
    other: list[str] = []
    group: dict[str, Any] | None = None
    in_notes = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or CREDITS_REQUIRED.match(line) or re.fullmatch(r"ER[A-Z]{3}\d{4}[A-Z]?", line):
            continue
        if VERSION_LINE.match(line):
            versions.append({"label": line.rstrip(":"), "groups": []})
            group, in_notes = None, False
        elif YEAR_HEADING.match(line):
            group = {"heading": line.rstrip(":"), "requirements": []}
            versions[-1]["groups"].append(group)
            in_notes = False
        elif line.lower().startswith("notes"):
            in_notes = True
        elif in_notes:
            notes.append(line.removeprefix("- ").strip())
        elif line.startswith("-") and group is not None:
            group["requirements"].append({"text": line[1:].strip()})
        elif group is not None and group["requirements"] and not line.startswith(("Students", "For ")):
            group["requirements"][-1]["text"] += " " + line  # a wrapped line continues the previous bullet
        else:
            other.append(line)
    for version in versions:
        for g in version["groups"]:
            for req in g["requirements"]:
                req["tree"] = parse_prereq(req["text"])
    versions = [v for v in versions if v["groups"]]
    return {"credits_required": credits_required, "versions": versions, "notes": notes, "other_text": other}


def program_rules(code: str, requirements: dict[str, Any], source_url: str) -> list[dict[str, Any]]:
    """Only rules the calendar states. Notes and anything unparsed become `manual_check` with the source text."""
    rules: list[dict[str, Any]] = []
    if requirements["credits_required"]:
        need = requirements["credits_required"]
        rules.append(
            {
                "program_code": code,
                "rule_type": "credits_required",
                "params": need,
                "source_text": f"{need['min']:g}-{need['max']:g} credits required",
                "calendar_url": source_url,
            }
        )
    for text in requirements["other_text"]:
        codes = PROGRAM_CODE.findall(text)
        if "cannot be simultaneously enrolled" in text and len(codes) >= 2:
            rules.append(
                {
                    "program_code": code,
                    "rule_type": "program_exclusion",
                    "params": {"programs": sorted(set(codes))},
                    "source_text": text,
                    "calendar_url": source_url,
                }
            )
        else:
            rules.append(
                {
                    "program_code": code,
                    "rule_type": "manual_check",
                    "params": {},
                    "source_text": text,
                    "calendar_url": source_url,
                }
            )
    for text in requirements["notes"]:
        rules.append(
            {
                "program_code": code,
                "rule_type": "manual_check",
                "params": {},
                "source_text": text,
                "calendar_url": source_url,
            }
        )
    return rules
