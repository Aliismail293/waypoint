"""Parses cached UTM Academic Calendar HTML into courses, programs and retrieval chunks.

Two page shapes are handled:
- Section pages (/section/<Area>): an intro, a programs list and a courses list, each rendered as
  accordion headers followed by a body. Courses carry labelled fields (Prerequisites, Exclusions, ...).
- Plain pages (policies): headings followed by paragraphs; chunked by heading.

Chunk ids are stable: they derive from the page path and the item's own id (course code, program
code, or heading text), never from position, so re-ingesting an edited page keeps the same ids.
"""

import hashlib
import re
from dataclasses import asdict, dataclass, field
from urllib.parse import unquote

from bs4 import BeautifulSoup, Tag

from waypoint.ingest.fetch import BASE_URL

COURSE_CODE = re.compile(r"\b[A-Z]{3}[A-D0-9]\d{2}[HY]\d\b")
PROGRAM_CODE = re.compile(r"\b(ER[A-Z]{3}\d{4}[A-Z]?)\b")
MAX_CHUNK_CHARS = 1800


@dataclass
class Course:
    code: str
    title: str
    description: str
    prerequisites: str = ""
    prerequisite_codes: list[str] = field(default_factory=list)
    corequisites: str = ""
    exclusions: str = ""
    exclusion_codes: list[str] = field(default_factory=list)
    recommended_preparation: str = ""
    distribution: str = ""
    hours: str = ""
    delivery: str = ""
    other: dict[str, str] = field(default_factory=dict)
    area: str = ""
    source_url: str = ""

    @property
    def credits(self) -> float:
        return 1.0 if self.code[6] == "Y" else 0.5

    def to_row(self) -> dict:
        row = asdict(self)
        row["credits"] = self.credits
        row["level"] = int(self.code[3])
        row["department"] = self.code[:3]
        row["other"] = "; ".join(f"{k}: {v}" for k, v in self.other.items())
        return row


@dataclass
class Program:
    code: str
    name: str
    program_type: str
    description: str
    enrolment_requirements: str
    completion_requirements: str
    course_codes: list[str]
    area: str
    source_url: str

    def to_row(self) -> dict:
        return asdict(self)


@dataclass
class Chunk:
    chunk_id: str
    doc_id: str
    kind: str
    heading: str
    text: str
    source_url: str

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(f"{self.heading}\n{self.text}".encode()).hexdigest()[:16]

    def to_row(self) -> dict:
        return {**asdict(self), "content_hash": self.content_hash}


@dataclass
class ParsedPage:
    path: str
    title: str
    courses: list[Course] = field(default_factory=list)
    programs: list[Program] = field(default_factory=list)
    chunks: list[Chunk] = field(default_factory=list)


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def doc_slug(path: str) -> str:
    """'/section/Statistics,-Applied' -> 'statistics-applied'; '/degree-requirements' -> 'degree-requirements'."""
    return slugify(unquote(path).removeprefix("/section/").strip("/"))


def to_text(element: Tag) -> str:
    """Readable text that keeps list items and paragraphs on their own lines."""
    for br in element.find_all("br"):
        br.replace_with("\n")
    for li in element.find_all("li"):
        li.insert_before("\n- ")
    for block in element.find_all(["p", "div", "ol", "ul", "h2", "h3", "h4", "dt", "dd"]):
        block.insert_after("\n")
    text = element.get_text(" ")
    lines = [re.sub(r"[ \t\xa0]+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(line for line in lines if line)
    return re.sub(r"\s+([,.;:)])", r"\1", text).replace("( ", "(")


def split_text(text: str, limit: int = MAX_CHUNK_CHARS) -> list[str]:
    """Split on line boundaries into pieces under `limit` characters."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        if current and size + len(line) > limit:
            pieces.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        pieces.append("\n".join(current))
    return pieces


def make_chunks(doc_id: str, anchor: str, kind: str, heading: str, body: str, url: str) -> list[Chunk]:
    pieces = split_text(body)
    chunks = []
    for i, piece in enumerate(pieces):
        chunk_id = f"{doc_id}#{anchor}" + (f"-p{i + 1}" if len(pieces) > 1 else "")
        part = f" (part {i + 1} of {len(pieces)})" if len(pieces) > 1 else ""
        chunks.append(Chunk(chunk_id, doc_id, kind, heading + part, piece, url))
    return chunks


def parse_page(path: str, html: str) -> ParsedPage:
    soup = BeautifulSoup(html, "html.parser")
    title_tag = soup.select_one("h1.page-title")
    title = title_tag.get_text(strip=True) if title_tag else doc_slug(path)
    page = ParsedPage(path=path, title=title)
    main = soup.select_one("#block-w3css-subtheme-content") or soup.body
    if main is None:
        return page
    if path.startswith("/section/"):
        _parse_section(page, main)
    else:
        _parse_plain(page, main)
    return page


def _parse_section(page: ParsedPage, main: Tag) -> None:
    doc_id = f"cal:{doc_slug(page.path)}"
    url = BASE_URL + page.path
    body = main.select_one(".view-section-view .views-field-body .field-content")
    if body is not None:
        for dt in body.select("dl.ckeditor-accordion > dt"):
            dd = dt.find_next_sibling("dd")
            if isinstance(dd, Tag):
                heading = f"{page.title} > {dt.get_text(strip=True)}"
                page.chunks += make_chunks(
                    doc_id, "intro-" + slugify(dt.get_text()), "section", heading, to_text(dd), url
                )
        for dl in body.select("dl.ckeditor-accordion"):
            dl.decompose()
        intro = to_text(body)
        if intro:
            page.chunks += make_chunks(doc_id, "intro", "section", f"{page.title} > Overview", intro, url)

    for header in main.select("h3.js-views-accordion-group-header"):
        label = header.get_text(" ", strip=True)
        content = header.find_next_sibling("div")
        if not isinstance(content, Tag):
            continue
        if "•" in label:
            _parse_course(page, doc_id, label, content)
        elif PROGRAM_CODE.search(label):
            _parse_program(page, doc_id, label, content)


def _parse_course(page: ParsedPage, doc_id: str, label: str, content: Tag) -> None:
    code, _, title = (part.strip() for part in label.partition("•"))
    url = f"{BASE_URL}/course/{code}"
    course = Course(code=code, title=title, description="", area=page.title, source_url=url)
    desc = content.select_one(".views-field-field-desc")
    course.description = to_text(desc) if desc else ""
    field_lines = []
    for span in content.select("[class*='views-field-field-']"):
        label_tag = span.select_one(".views-label")
        value_tag = span.select_one(".field-content")
        if label_tag is None or value_tag is None:
            continue
        name = label_tag.get_text(strip=True).rstrip(":").strip()
        codes = sorted(set(COURSE_CODE.findall(value_tag.get_text(" "))))
        value = to_text(value_tag)
        field_lines.append(f"{name}: {value}")
        key = name.lower()
        if key.startswith("prerequisite"):
            course.prerequisites, course.prerequisite_codes = value, codes
        elif key.startswith("exclusion"):
            course.exclusions, course.exclusion_codes = value, codes
        elif key.startswith("corequisite"):
            course.corequisites = value
        elif key.startswith("recommended preparation"):
            course.recommended_preparation = value
        elif key.startswith("distribution"):
            course.distribution = value
        elif key.startswith("total instructional hours"):
            course.hours = value
        elif key.startswith("mode of delivery"):
            course.delivery = value
        else:
            course.other[name] = value
    page.courses.append(course)
    body = "\n".join([f"{code}: {title}", course.description, *field_lines])
    page.chunks += make_chunks(doc_id, slugify(code), "course", f"{page.title} > {code} {title}", body, url)


def _parse_program(page: ParsedPage, doc_id: str, label: str, content: Tag) -> None:
    match = PROGRAM_CODE.search(label)
    assert match is not None
    code = match.group(1)
    name = label.rsplit(" - ", 1)[0].strip()
    program_type = next((t for t in ("Specialist", "Major", "Minor") if t in label), "Other")
    url = f"{BASE_URL}{page.path}#programs"
    sections: dict[str, list[str]] = {"description": [], "enrolment": [], "completion": [], "notes": []}
    current = "description"
    for child in content.children:
        if not isinstance(child, Tag) or "views-field-title" in (child.get("class") or []):
            continue
        heading = child.get_text(strip=True).lower() if child.name in ("h3", "h4") else ""
        if heading.startswith("enrolment requirements"):
            current = "enrolment"
            continue
        if heading.startswith("completion requirements"):
            current = "completion"
            continue
        if heading.startswith("notes"):
            current = "notes"
        text = to_text(child)
        if text:
            sections[current].append(text)
    texts = {k: "\n".join(v) for k, v in sections.items()}
    page.programs.append(
        Program(
            code=code,
            name=name,
            program_type=program_type,
            description=texts["description"],
            enrolment_requirements=texts["enrolment"],
            completion_requirements="\n".join(filter(None, [texts["completion"], texts["notes"]])),
            course_codes=sorted(set(COURSE_CODE.findall(content.get_text(" ")))),
            area=page.title,
            source_url=url,
        )
    )
    anchor = slugify(code)
    heading = f"{page.title} > {label}"
    if texts["description"]:
        page.chunks += make_chunks(doc_id, anchor, "program", heading, texts["description"], url)
    if texts["enrolment"]:
        body = f"Enrolment requirements for {label}:\n{texts['enrolment']}"
        page.chunks += make_chunks(doc_id, f"{anchor}-enrolment", "program", heading + " > Enrolment", body, url)
    completion = "\n".join(filter(None, [texts["completion"], texts["notes"]]))
    if completion:
        body = f"Completion requirements for {label}:\n{completion}"
        page.chunks += make_chunks(doc_id, f"{anchor}-completion", "program", heading + " > Completion", body, url)


def _parse_plain(page: ParsedPage, main: Tag) -> None:
    doc_id = f"cal:{doc_slug(page.path)}"
    url = BASE_URL + page.path
    heading = page.title
    buffer: list[str] = []
    used: set[str] = set()

    def flush() -> None:
        body = "\n".join(buffer).strip()
        if body:
            anchor = slugify(heading) or "body"
            while anchor in used:
                anchor += "-x"
            used.add(anchor)
            page.chunks.extend(make_chunks(doc_id, anchor, "policy", f"{page.title} > {heading}", body, url))
        buffer.clear()

    for element in main.find_all(["h2", "h3", "h4", "p", "ul", "ol", "table", "dt", "dd"]):
        if element.find_parent(["ul", "ol", "table", "dd"]) is not None:
            continue
        if element.name in ("h2", "h3", "h4", "dt"):
            flush()
            heading = element.get_text(" ", strip=True)
        else:
            buffer.append(to_text(element))
    flush()
