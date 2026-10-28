"""Hybrid retrieval: vector search + full-text search, merged with reciprocal rank fusion (RRF).

RRF scores each chunk as sum(1 / (k + rank)) over the ranked lists it appears in. It needs no score
calibration between the two retrievers (cosine similarity and ts_rank are on different scales),
which is why it's the standard way to merge them.
"""

from dataclasses import dataclass

from waypoint.llm.embeddings import Embedder
from waypoint.retrieval.store import ChunkStore, StoredChunk

RRF_K = 60


@dataclass
class RetrievedChunk:
    chunk: StoredChunk
    score: float
    vector_rank: int | None
    keyword_rank: int | None


def reciprocal_rank_fusion(rankings: list[list[str]], k: int = RRF_K) -> dict[str, float]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, chunk_id in enumerate(ranking, start=1):
            scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (k + rank)
    return scores


class HybridRetriever:
    def __init__(self, store: ChunkStore, embedder: Embedder, candidates: int = 20) -> None:
        self.store = store
        self.embedder = embedder
        self.candidates = candidates

    def search(self, query: str, top_k: int = 5, mode: str = "hybrid") -> list[RetrievedChunk]:
        """mode: 'hybrid' (default), 'vector' or 'keyword' — the latter two exist for eval ablations."""
        vector_hits = (
            self.store.vector_search(self.embedder.embed_query(query), self.candidates) if mode != "keyword" else []
        )
        keyword_hits = self.store.keyword_search(query, self.candidates) if mode != "vector" else []
        vector_ids = [h.chunk.chunk_id for h in vector_hits]
        keyword_ids = [h.chunk.chunk_id for h in keyword_hits]
        chunks = {h.chunk.chunk_id: h.chunk for h in vector_hits + keyword_hits}
        fused = reciprocal_rank_fusion([vector_ids, keyword_ids])
        ranked = sorted(fused.items(), key=lambda item: -item[1])[:top_k]
        return [
            RetrievedChunk(
                chunk=chunks[cid],
                score=score,
                vector_rank=vector_ids.index(cid) + 1 if cid in vector_ids else None,
                keyword_rank=keyword_ids.index(cid) + 1 if cid in keyword_ids else None,
            )
            for cid, score in ranked
        ]
