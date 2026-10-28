from pathlib import Path

from waypoint.ingest.load import load_chunks, read_corpus
from waypoint.llm.embeddings import HashEmbedder
from waypoint.retrieval.hybrid import HybridRetriever, reciprocal_rank_fusion
from waypoint.retrieval.store import InMemoryStore


def test_rrf_rewards_agreement():
    scores = reciprocal_rank_fusion([["a", "b", "c"], ["b", "a", "d"]])
    assert scores["a"] == scores["b"] > scores["c"] == scores["d"]


def make_retriever() -> HybridRetriever:
    store = InMemoryStore()
    embedder = HashEmbedder(256)
    load_chunks(store, read_corpus(Path("data/corpus/calendar_chunks.jsonl")), embedder)
    return HybridRetriever(store, embedder)


def test_keyword_path_finds_course_by_bare_code():
    # The offline hash embedder makes vector ranks near-random, so offline we check the keyword path;
    # hybrid quality on real embeddings is measured by the eval (evals/), not by unit tests.
    results = make_retriever().search("What are the prerequisites for MAT224?", top_k=3, mode="keyword")
    assert results[0].chunk.chunk_id == "cal:mathematical-sciences#mat224h5"


def test_modes_for_ablation():
    retriever = make_retriever()
    assert all(r.vector_rank is None for r in retriever.search("major completion", mode="keyword"))
    assert all(r.keyword_rank is None for r in retriever.search("major completion", mode="vector"))


def test_check_citations_drops_ids_not_retrieved():
    from waypoint.agents.runtime import check_citations

    text = "MAT224 needs MAT223 [cal:a#mat224h5]. Made up [cal:a#fake]."
    cleaned, valid, invalid = check_citations(text, ["cal:a#mat224h5"])
    assert valid == ["cal:a#mat224h5"] and invalid == ["cal:a#fake"]
    assert "[cal:a#fake]" not in cleaned and "[cal:a#mat224h5]" in cleaned
