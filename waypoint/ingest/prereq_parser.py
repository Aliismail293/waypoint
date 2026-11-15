"""Prerequisite text -> boolean tree.

Tree nodes (plain dicts, JSON-serialisable):
    {"all_of": [node, ...]}        {"one_of": [node, ...]}
    {"course": "MAT135H5", "min_grade": 60}            (min_grade optional)
    {"credits": 4.0, "subject": "MAT", "level": 200}   (subject, level, min_grade optional; "additional": true)
    {"highschool": "MHF4U", "min_grade": 70}
    {"permission": "instructor"}     {"cgpa": 3.2}     {"program": "..."}
    {"unparsed": "<exact text>"}     anything we are not sure about

The parser never guesses. Mixed "and"/"or" without brackets, unbalanced brackets, a bare comma list and
any wording it doesn't recognise all become `unparsed`, which the UI shows as "check the calendar".
"""

import re
from typing import Any

Node = dict[str, Any]

COURSE = r"[A-Z]{3}[A-D0-9]\d{2}[HY]\d"
HIGHSCHOOL = r"[A-Z]{3}[34][UMC]"

_TOKEN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "permission",
        re.compile(r"(?:Departmental permission|Permission of (?:the )?instructor(?: and (?:the )?department)?)", re.I),
    ),
    ("cgpa", re.compile(r"(?:a )?minimum CGPA of (\d(?:\.\d+)?)", re.I)),
    (
        "credits",
        re.compile(
            r"(?:completion of )?(?:at least )?(\d+(?:\.\d+)?) (additional )?credits? (?:of|in) ([A-Z]{3})(?: courses)?"
            r"(?: at the (\d)00\+? level)?(?: with a mark of at least (\d+)%)?",
            re.I,
        ),
    ),
    ("hs_grade", re.compile(rf"(?:Minimum )?(\d+)% in Grade 12 [A-Za-z ,&]+? \(({HIGHSCHOOL})\)", re.I)),
    ("hs_group", re.compile(rf"Grade 12 [A-Za-z ,&]+? \(({HIGHSCHOOL})(?: or ({COURSE}))?\)", re.I)),
    ("hs_plain", re.compile(rf"Grade 12 [A-Za-z ,&]+? \(({HIGHSCHOOL})\)", re.I)),
    ("grade_course", re.compile(rf"(\d+)% in ({COURSE})")),
    ("course", re.compile(COURSE)),
]


def parse_prereq(text: str | None) -> Node | None:
    """Parse one prerequisite/corequisite string. None means the course lists no requirement."""
    cleaned = _clean(text)
    if not cleaned:
        return None
    return _parse_list(cleaned)


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("–", "-")).strip().rstrip(".").strip()


def _split_top_level(text: str, separator: str) -> list[str]:
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == separator and depth == 0:
            parts.append(text[start:i].strip())
            start = i + 1
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _split_word(text: str, word: str) -> list[str]:
    """Split on a bracket-level-0 ' and ' / ' or '."""
    parts, depth, start = [], 0, 0
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif depth == 0 and text[i : i + len(word) + 2] == f" {word} ":
            parts.append(text[start:i].strip())
            start = i + len(word) + 2
    parts.append(text[start:].strip())
    return [p for p in parts if p]


def _parse_list(text: str) -> Node:
    """Top level: a comma list must end with 'and X' or 'or X'; each segment is then parsed on its own."""
    segments = _split_top_level(text, ";")
    if len(segments) == 1:
        segments = _split_top_level(text, ",")
    if len(segments) == 1:
        return _parse_expression(text)
    last = segments[-1]
    if last.lower().startswith("and "):
        group = "all_of"
        segments[-1] = last.split(" ", 1)[1]
    elif last.lower().startswith("or "):
        group = "one_of"
        segments[-1] = last.split(" ", 1)[1]
    else:
        # "A, B and C" / "A, B or C": the last segment carries the connective
        ands, ors = _split_word(last, "and"), _split_word(last, "or")
        if len(ands) > 1 and len(ors) == 1:
            group, segments = "all_of", segments[:-1] + ands
        elif len(ors) > 1 and len(ands) == 1:
            group, segments = "one_of", segments[:-1] + ors
        else:
            return {"unparsed": text}
    nodes = [_parse_expression(s) for s in segments]
    return {group: nodes}


def _tokenise(text: str) -> list[tuple[str, Any]] | None:
    """Words and brackets -> tokens, or None if any stretch of text isn't understood."""
    tokens: list[tuple[str, Any]] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == " ":
            i += 1
        elif ch in "([":
            tokens.append(("(", ch))
            i += 1
        elif ch in ")]":
            tokens.append((")", ch))
            i += 1
        elif ch == "/":
            tokens.append(("or", None))
            i += 1
        else:
            word = re.match(r"(and|or)\b", text[i:], re.I)
            matched = _match_atom(text, i)
            if matched:
                node, end = matched
                tokens.append(("atom", node))
                i = end
            elif word:
                tokens.append((word.group(1).lower(), None))
                i += len(word.group(0))
            else:
                return None
    return tokens


def _match_atom(text: str, i: int) -> tuple[Node, int] | None:
    for name, pattern in _TOKEN_PATTERNS:
        m = pattern.match(text, i)
        if not m:
            continue
        if name == "permission":
            who = (
                "instructor and department"
                if "and" in m.group(0).lower()
                else ("department" if m.group(0).lower().startswith("depart") else "instructor")
            )
            return {"permission": who}, m.end()
        if name == "cgpa":
            return {"cgpa": float(m.group(1))}, m.end()
        if name == "credits":
            node: Node = {"credits": float(m.group(1)), "subject": m.group(3).upper()}
            if m.group(2):
                node["additional"] = True
            if m.group(4):
                node["level"] = int(m.group(4)) * 100
            if m.group(5):
                node["min_grade"] = int(m.group(5))
            return node, m.end()
        if name == "hs_grade":
            return {"highschool": m.group(2).upper(), "min_grade": int(m.group(1))}, m.end()
        if name == "hs_group":
            leaf: Node = {"highschool": m.group(1).upper()}
            if m.group(2):
                return {"one_of": [leaf, {"course": m.group(2)}]}, m.end()
            return leaf, m.end()
        if name == "hs_plain":
            return {"highschool": m.group(1).upper()}, m.end()
        if name == "grade_course":
            return {"course": m.group(2), "min_grade": int(m.group(1))}, m.end()
        return {"course": m.group(0)}, m.end()
    return None


def _parse_expression(text: str) -> Node:
    tokens = _tokenise(text)
    if tokens is None:
        return {"unparsed": text}
    # a trailing "(Fall 2019 or ...)" style remark is not understood by the tokeniser, so it already returned None.
    try:
        node, pos = _expr(tokens, 0)
    except ValueError:
        return {"unparsed": text}
    if pos != len(tokens):
        return {"unparsed": text}
    return node


def _expr(tokens: list[tuple[str, Any]], pos: int) -> tuple[Node, int]:
    """One bracket level: atoms joined by a single kind of connective. Mixed connectives are ambiguous."""
    items: list[Node] = []
    connective: str | None = None
    node, pos = _operand(tokens, pos)
    items.append(node)
    while pos < len(tokens) and tokens[pos][0] in ("and", "or"):
        kind = tokens[pos][0]
        if connective not in (None, kind):
            raise ValueError("mixed and/or without brackets")
        connective = kind
        node, pos = _operand(tokens, pos + 1)
        items.append(node)
    if connective is None:
        return items[0], pos
    return {
        "all_of" if connective == "and" else "one_of": _flatten(items, "all_of" if connective == "and" else "one_of")
    }, pos


def _operand(tokens: list[tuple[str, Any]], pos: int) -> tuple[Node, int]:
    if pos >= len(tokens):
        raise ValueError("unexpected end")
    kind, value = tokens[pos]
    if kind == "atom":
        return value, pos + 1
    if kind == "(":
        node, pos = _expr(tokens, pos + 1)
        # "[" must close with "]" and "(" with ")": a mismatch is a typo in the calendar, so we don't guess
        if pos >= len(tokens) or tokens[pos][0] != ")" or tokens[pos][1] != {"(": ")", "[": "]"}[value]:
            raise ValueError("unbalanced bracket")
        return node, pos + 1
    raise ValueError(f"unexpected {kind}")


def _flatten(items: list[Node], key: str) -> list[Node]:
    flat: list[Node] = []
    for item in items:
        flat.extend(item[key] if list(item) == [key] else [item])
    return flat


def is_fully_parsed(tree: Node | None) -> bool:
    """True when no leaf is `unparsed` (a missing requirement counts as parsed)."""
    if tree is None:
        return True
    if "unparsed" in tree:
        return False
    return all(is_fully_parsed(child) for key in ("all_of", "one_of") for child in tree.get(key, []))


def course_leaves(tree: Node | None) -> list[str]:
    """Every course code mentioned in the tree, in order, without repeats."""
    if tree is None:
        return []
    if "course" in tree:
        return [tree["course"]]
    found: list[str] = []
    for key in ("all_of", "one_of"):
        for child in tree.get(key, []):
            found += [c for c in course_leaves(child) if c not in found]
    return found


def describe(tree: Node | None) -> str:
    """Plain-language rendering, used by the 'i' popover and the fact route. Unparsed text is quoted."""
    if tree is None:
        return "No prerequisites listed."
    return _describe(tree)


def _describe(tree: Node, top: bool = True) -> str:
    if "all_of" in tree:
        text = " and ".join(_describe(c, False) for c in tree["all_of"])
        return text if top else f"({text})"
    if "one_of" in tree:
        text = " or ".join(_describe(c, False) for c in tree["one_of"])
        return text if top else f"({text})"
    if "course" in tree:
        grade = f" (minimum {tree['min_grade']}%)" if "min_grade" in tree else ""
        return f"{tree['course']}{grade}"
    if "highschool" in tree:
        grade = f"minimum {tree['min_grade']}% in " if "min_grade" in tree else ""
        return f"{grade}Grade 12 {tree['highschool']}"
    if "credits" in tree:
        more = "additional " if tree.get("additional") else ""
        level = f" at the {tree['level']}+ level" if "level" in tree else ""
        grade = f" with a mark of at least {tree['min_grade']}%" if "min_grade" in tree else ""
        return f"{tree['credits']:g} {more}credit(s) in {tree['subject']}{level}{grade}"
    if "permission" in tree:
        return f"permission of the {tree['permission']}"
    if "cgpa" in tree:
        return f"a minimum CGPA of {tree['cgpa']:g}"
    if "program" in tree:
        return f"enrolment in {tree['program']}"
    return f"check the calendar: “{tree.get('unparsed', '')}”"
