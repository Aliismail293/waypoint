from pathlib import Path

import pytest

from waypoint.ingest.calendar_parser import doc_slug, parse_page, split_text
from waypoint.ingest.load import load_chunks, read_corpus
from waypoint.llm.embeddings import HashEmbedder
from waypoint.retrieval.store import InMemoryStore, code_keywords

FIXTURE = Path(__file__).parent / "fixtures/calendar/section%2FMathematical-Sciences.html"


@pytest.fixture(scope="module")
def page():
    return parse_page("/section/Mathematical-Sciences", FIXTURE.read_text())


def test_parses_courses_with_structured_fields(page):
    courses = {c.code: c for c in page.courses}
    mat224 = courses["MAT224H5"]
    assert mat224.title == "Linear Algebra II"
    assert mat224.prerequisite_codes == ["MAT102H5", "MAT223H5"]
    assert "MAT240H5" in mat224.exclusion_codes
    assert "MATB24H3" in mat224.exclusion_codes  # UTSC codes have a letter in them
    assert mat224.distribution == "Science"
    assert mat224.credits == 0.5 and courses["MAT257Y5"].credits == 1.0


def test_parses_programs(page):
    programs = {p.code: p for p in page.programs}
    assert set(programs) == {"ERSPE2511", "ERMAJ2511", "ERMAJ2512", "ERMIN2511"}
    major = programs["ERMAJ2511"]
    assert major.program_type == "Major"
    assert "credits are required" in major.completion_requirements
    assert "MAT224H5" in major.course_codes


def test_chunk_ids_are_stable_and_unique(page):
    ids = [c.chunk_id for c in page.chunks]
    assert len(ids) == len(set(ids))
    assert "cal:mathematical-sciences#mat224h5" in ids
    assert any(i.startswith("cal:mathematical-sciences#ermaj2511-completion") for i in ids)
    again = parse_page("/section/Mathematical-Sciences", FIXTURE.read_text())
    assert [c.chunk_id for c in again.chunks] == ids
    assert [c.content_hash for c in again.chunks] == [c.content_hash for c in page.chunks]


def test_split_text_respects_limit():
    text = "\n".join(f"line {i} " + "x" * 50 for i in range(100))
    pieces = split_text(text, limit=500)
    assert all(len(p) <= 560 for p in pieces) and len(pieces) > 5


def test_doc_slug():
    assert doc_slug("/section/Statistics,-Applied") == "statistics-applied"
    assert code_keywords("needs MAT224H5") == "MAT224H5 MAT224"


def test_committed_corpus_matches_parser(page):
    corpus = read_corpus(Path("data/corpus/calendar_chunks.jsonl"))
    assert {c.chunk_id for c in corpus} >= {c.chunk_id for c in page.chunks}


def test_load_only_embeds_changed_chunks():
    corpus = read_corpus(Path("data/corpus/calendar_chunks.jsonl"))
    store = InMemoryStore()
    embedder = HashEmbedder(64)
    assert load_chunks(store, corpus, embedder) == len(corpus)
    assert load_chunks(store, corpus, embedder) == 0
    corpus[0].text += " edited"
    corpus[0].content_hash = "changed"
    assert load_chunks(store, corpus, embedder) == 1
