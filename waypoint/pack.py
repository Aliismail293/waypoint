"""Advisor meeting pack: a short PDF (and email text) of the student's plan, open critic notes, pinned answers with
their official source links, and optionally a term's timetable. Built only from data the signed-in student owns.

PDF library: fpdf2. It is pure Python (its only dependency with native code is Pillow, which ships wheels), so it
installs on Cloud Run with no system libraries; WeasyPrint needs Pango/Cairo and ReportLab is much larger. fpdf2's
built-in fonts are Latin-1 only, so text goes through `latin1()` first (smart quotes and dashes are mapped, anything
else becomes '?') rather than shipping a font file.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from fpdf import FPDF

NOT_AFFILIATED = "Student project, not affiliated with U of T. Always confirm plans with an academic advisor."
SAMPLE_NOTE = "Timetable data is SAMPLE data (synthetic, not real UTM offerings)."
MAX_ANSWERS = 5
ANSWER_CHARS = 520
CITATION = re.compile(r"\s*\[cal:[a-z0-9-]+#[a-z0-9-]+\]")
GROUPED = re.compile(r"\s*\[(?:cal:[a-z0-9-]+#[a-z0-9-]+(?:,\s*)?)+\]")
REPLACEMENTS = {
    "–": "-", "—": "-", "‘": "'", "’": "'", "“": '"', "”": '"', "…": "...", "✓": "[ok]", "→": "->", "•": "-",
    " ": " ", "≥": ">=", "≤": "<=",
}  # fmt: skip


def latin1(text: str) -> str:
    for old, new in REPLACEMENTS.items():
        text = text.replace(old, new)
    return text.encode("latin-1", "replace").decode("latin-1")


def plain(text: str) -> str:
    """Answer markdown -> plain text: citation markers removed, bold and list marks dropped."""
    text = GROUPED.sub("", CITATION.sub("", text)).replace("**", "")
    return re.sub(r"(?m)^\s*[*-]\s+", "- ", text).strip()


def trim(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rsplit(" ", 1)[0] + "..."


@dataclass
class Pack:
    generated: str
    program: str = ""
    grad_year: int | None = None
    plan_name: str = ""
    terms: list[tuple[str, list[str]]] = field(default_factory=list)  # (term, ["MAT224H5 Linear Algebra II"])
    credits_line: str = ""
    notes: list[tuple[str, str, list[tuple[str, str]]]] = field(default_factory=list)  # (level, text, [(label, url)])
    answers: list[dict[str, Any]] = field(default_factory=list)  # question, answer, sources [(label, url)]
    answers_omitted: int = 0
    timetable: dict[str, Any] | None = None  # term, rows [(course, section, when)], clashes: str


def build_pack(
    *,
    profile: dict[str, Any],
    plan: dict[str, Any] | None,
    turns: list[Any],
    timetable: dict[str, Any] | None,
    include_critic: bool,
    today: date | None = None,
) -> Pack:
    pack = Pack(
        generated=(today or date.today()).isoformat(),
        program=profile.get("program", ""),
        grad_year=profile.get("grad_year"),
    )
    if plan:
        pack.plan_name = plan["name"]
        pack.terms = [
            (t["term"], [f"{c} {plan['titles'].get(c, '')}".strip() for c in t["courses"]])
            for t in plan["terms"]
            if t["courses"]
        ]
        credits = plan["review"].get("credits")
        if credits:
            need = credits.get("required")
            label = "?" if not need else f"{need[0]:g}" if need[0] == need[1] else f"{need[0]:g}-{need[1]:g}"
            pack.credits_line = f"Credits counted (taken plus planned): {credits['counted']:g} of {label}"
        if include_critic:
            by_chunk = {s["chunk_id"]: s for s in plan.get("sources", [])}
            for item in plan["review"]["items"]:
                if item["level"] == "ok":
                    continue
                links = [
                    (by_chunk[c].get("code") or "calendar", by_chunk[c]["url"])
                    for c in item["chunk_ids"]
                    if c in by_chunk
                ]
                pack.notes.append((item["level"], item["text"], links))
    for turn in turns[:MAX_ANSWERS]:
        pack.answers.append(
            {
                "question": turn.question,
                "answer": trim(plain(turn.answer), ANSWER_CHARS),
                "sources": [(s.heading.split(" > ")[-1] or s.chunk_id, s.url) for s in turn.sources if s.url],
            }
        )
    pack.answers_omitted = max(0, len(turns) - MAX_ANSWERS)
    if timetable:
        rows = []
        for s in timetable["chosen"]:
            when = ", ".join(f"{m['day'][:3]} {m['start']}-{m['end']}" for m in s["meetings"]) or "TBA"
            rows.append((s["course"], f"{s['type'][:3].upper()}{s['number']}", when))
        pack.timetable = {"term": timetable["term"], "rows": rows, "clashes": timetable["explanation"]}
    return pack


def pack_email(pack: Pack) -> str:
    lines = [
        "Hi,",
        "",
        "I'd like to go over my degree plan with you. A summary (prepared with Waypoint, a student tool):",
        "",
    ]
    if pack.program or pack.grad_year:
        lines.append(f"Program: {pack.program or 'not set'} · Expected graduation: {pack.grad_year or 'not set'}")
    if pack.terms:
        lines += ["", f"{pack.plan_name}:"] + [f"- {term}: {', '.join(courses)}" for term, courses in pack.terms]
        if pack.credits_line:
            lines.append(pack.credits_line)
    if pack.notes:
        lines += ["", "Questions and open notes:"]
        for _, text, links in pack.notes:
            lines.append(f"- {text}" + "".join(f" ({label}: {url})" for label, url in links))
    if pack.answers:
        lines += ["", "Answers I'd like you to check:"]
        for a in pack.answers:
            lines += [f"Q: {a['question']}", f"A: {a['answer']}"] + [
                f"   Source: {label}: {url}" for label, url in a["sources"]
            ]
        if pack.answers_omitted:
            lines.append(f"(+{pack.answers_omitted} more pinned answers not shown)")
    if pack.timetable:
        lines += ["", f"Timetable for {pack.timetable['term']} (sample data, not real offerings):"]
        lines += [f"- {c} {s}: {w}" for c, s, w in pack.timetable["rows"]]
        lines.append(pack.timetable["clashes"])
    lines += ["", "Thank you,", "", f"({NOT_AFFILIATED})"]
    return "\n".join(lines)


class _Pdf(FPDF):
    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "", 7)
        self.set_text_color(79, 87, 107)
        self.cell(0, 4, latin1(f"{NOT_AFFILIATED}   Page {self.page_no()}"), align="C")


def pack_pdf(pack: Pack, compress: bool = True) -> bytes:
    pdf = _Pdf(format="A4")
    pdf.set_compression(compress)
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(14, 14, 14)
    pdf.set_title("Waypoint advisor pack")
    pdf.set_author("Waypoint")
    pdf.add_page()

    def heading(text: str) -> None:
        pdf.ln(2)
        pdf.set_font("Helvetica", "B", 11)
        pdf.set_text_color(49, 46, 129)
        pdf.cell(0, 6, latin1(text), new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(20, 26, 46)

    def body(text: str, size: float = 9, style: str = "") -> None:
        pdf.set_font("Helvetica", style, size)
        pdf.multi_cell(0, 4.6, latin1(text), new_x="LMARGIN", new_y="NEXT")

    def links(items: list[tuple[str, str]]) -> None:
        pdf.set_font("Helvetica", "U", 8)
        pdf.set_text_color(67, 56, 202)
        for label, url in items:
            pdf.cell(0, 4.2, latin1(f"Source: {label}"), link=url, new_x="LMARGIN", new_y="NEXT")
        pdf.set_text_color(20, 26, 46)

    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 8, "Questions for my advisor", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 8)
    pdf.set_text_color(79, 87, 107)
    meta = f"Waypoint advisor pack - {pack.generated}"
    if pack.program or pack.grad_year:
        meta += f" - Program: {pack.program or 'not set'} - Expected graduation: {pack.grad_year or 'not set'}"
    pdf.cell(0, 5, latin1(meta), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(20, 26, 46)

    if pack.terms:
        heading(f"Plan: {pack.plan_name}")
        for term, courses in pack.terms:
            body(f"{term}: {', '.join(courses)}")
        if pack.credits_line:
            body(pack.credits_line, 8, "I")
    if pack.notes:
        heading("Open critic notes (checked by code)")
        for level, text, srcs in pack.notes:
            body(f"{'!' if level == 'error' else '-'} {text}")
            links(srcs)
    if pack.answers:
        heading("Pinned answers to check")
        for a in pack.answers:
            body(f"Q: {a['question']}", 9, "B")
            body(a["answer"])
            links(a["sources"])
            pdf.ln(1)
        if pack.answers_omitted:
            body(f"+{pack.answers_omitted} more pinned answers not shown.", 8, "I")
    if pack.timetable:
        heading(f"Timetable: {pack.timetable['term']} (SAMPLE DATA)")
        for course, section, when in pack.timetable["rows"]:
            body(f"{course} {section}: {when}")
        body(pack.timetable["clashes"], 8, "I")
        body(SAMPLE_NOTE, 8, "I")
    return bytes(pdf.output())
