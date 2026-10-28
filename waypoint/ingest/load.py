"""Loads the chunk corpus into a ChunkStore, embedding only chunks that are new or changed."""

import json
from pathlib import Path

from waypoint.config import Settings
from waypoint.llm.embeddings import Embedder, get_embedder
from waypoint.retrieval.store import ChunkStore, PgVectorStore, StoredChunk


def read_corpus(path: Path) -> list[StoredChunk]:
    with open(path) as f:
        return [StoredChunk(**json.loads(line)) for line in f if line.strip()]


def load_chunks(store: ChunkStore, chunks: list[StoredChunk], embedder: Embedder, batch: int = 50) -> int:
    """Upsert chunks whose content hash changed. Returns how many were embedded."""
    existing = store.existing_hashes()
    changed = [c for c in chunks if existing.get(c.chunk_id) != c.content_hash]
    for start in range(0, len(changed), batch):
        group = changed[start : start + batch]
        vectors = embedder.embed_documents([f"{c.heading}\n{c.text}" for c in group])
        store.upsert(group, vectors)
    return len(changed)


def load_chunks_into_pgvector(corpus: Path, settings: Settings, prune: bool = False) -> None:
    store = PgVectorStore(settings)
    store.create_schema()
    chunks = read_corpus(corpus)
    embedder = get_embedder(settings)
    embedded = load_chunks(store, chunks, embedder)
    pruned = store.delete_missing({c.chunk_id for c in chunks}) if prune else 0
    billable = getattr(embedder, "billable_characters", 0)
    print(
        f"{len(chunks)} chunks in corpus; embedded {embedded} new/changed; pruned {pruned}; "
        f"{store.count()} rows in {settings.db_schema}.chunks; embedding billable characters: {billable}"
    )
