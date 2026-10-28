"""Deterministic prerequisite checking: evaluates calendar prerequisite text against completed courses.

Calendar prerequisites are mostly boolean expressions over course codes, e.g.
"MAT102H5 and (MAT223H5 or MAT240H5)". We evaluate those exactly. Anything we can't parse
(grade minimums, "permission of instructor", CGPA) makes the result "unclear" with the original text,
so the agent states the condition instead of guessing.
"""

import re
from dataclasses import dataclass, field

CODE = re.compile(r"[A-Z]{3}[A-D0-9]\d{2}[HY]\d")
CONNECTIVES = re.compile(r"\b(and|or)\b|[()\[\],;]")
SAFE_EXPR = re.compile(r"(?:\s|True|False|and|or|\(|\))+")


@dataclass
class PrereqResult:
    status: str  # "met" | "unmet" | "unclear" | "none"
    missing: list[str] = field(default_factory=list)
    text: str = ""


def check_prerequisites(prerequisite_text: str, completed: list[str]) -> PrereqResult:
    text = (prerequisite_text or "").strip()
    if not text:
        return PrereqResult("none")
    done = {c.strip().upper() for c in completed}
    done |= {c[:6] for c in done}  # a bare "MAT223" satisfies "MAT223H5"

    codes = CODE.findall(text)
    leftover = CODE.sub("", text)
    leftover = CONNECTIVES.sub("", leftover).strip(" .")
    missing = [c for c in codes if c not in done and c[:6] not in done]
    if leftover:
        # Not a pure boolean expression; still report codes not yet taken.
        return PrereqResult("unclear", missing, text)

    expr = text.replace("[", "(").replace("]", ")").replace(";", " and ").replace(",", " or ")
    expr = CODE.sub(lambda m: str(m.group(0) in done or m.group(0)[:6] in done), expr)
    if not SAFE_EXPR.fullmatch(expr):
        return PrereqResult("unclear", missing, text)
    try:
        satisfied = bool(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307 - expr is only True/False/and/or/()
    except SyntaxError:
        return PrereqResult("unclear", missing, text)
    return PrereqResult("met" if satisfied else "unmet", [] if satisfied else missing, text)
