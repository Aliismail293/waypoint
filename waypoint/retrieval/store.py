"""Chunk storage with two search paths: vector similarity and keyword (full-text) search.

`PgVectorStore` is the real one: Postgres 16 + pgvector on Cloud SQL, with an HNSW index on the
embedding and a generated tsvector column with a GIN index for full-text search.
`InMemoryStore` implements the same interface for offline tests and CI.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from typing import Protocol

from waypoint.config import Settings

CODE = re.compile(r"\b([A-Z]{3}[A-D0-9]\d{2})([HY]\d)\b")


@dataclass
class StoredChunk:
    chunk_id: str
    doc_id: str
    kind: str
    heading: str
    text: str
    source_url: str
    content_hash: str = ""


@dataclass
class Hit:
    chunk: StoredChunk
    score: float


def code_keywords(text: str) -> str:
    """'MAT224H5' -> 'MAT224H5 MAT224', so a search for 'MAT224' also matches full-text."""
    found = {f"{base}{suffix} {base}" for base, suffix in CODE.findall(text)}
    return " ".join(sorted(found))


class ChunkStore(Protocol):
    def upsert(self, chunks: list[StoredChunk], embeddings: list[list[float]]) -> None: ...

    def existing_hashes(self) -> dict[str, str]: ...

    def vector_search(self, embedding: list[float], k: int) -> list[Hit]: ...

    def keyword_search(self, query: str, k: int) -> list[Hit]: ...

    def get(self, chunk_ids: list[str]) -> list[StoredChunk]: ...


SCHEMA_SQL = """
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS {schema};
CREATE TABLE IF NOT EXISTS {schema}.chunks (
    chunk_id     text PRIMARY KEY,
    doc_id       text NOT NULL,
    kind         text NOT NULL,
    heading      text NOT NULL,
    text         text NOT NULL,
    source_url   text NOT NULL,
    content_hash text NOT NULL,
    keywords     text NOT NULL DEFAULT '',
    embedding    vector({dim}) NOT NULL,
    tsv          tsvector GENERATED ALWAYS AS (
                     to_tsvector('english', heading || ' ' || text || ' ' || keywords)) STORED,
    updated_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw ON {schema}.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON {schema}.chunks USING gin (tsv);
"""

COLUMNS = "chunk_id, doc_id, kind, heading, text, source_url, content_hash"


class PgVectorStore:
    def __init__(self, settings: Settings) -> None:
        import psycopg
        from pgvector.psycopg import register_vector

        self.schema = settings.db_schema
        self.dim = settings.embedding_dim
        conninfo = psycopg.conninfo.make_conninfo(
            host=settings.db_host,
            port=settings.db_port,
            dbname=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
        )
        self._conn = psycopg.connect(conninfo, autocommit=True)
        self._conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        register_vector(self._conn)

    def create_schema(self) -> None:
        self._conn.execute(SCHEMA_SQL.format(schema=self.schema, dim=self.dim))

    def upsert(self, chunks: list[StoredChunk], embeddings: list[list[float]]) -> None:
        from pgvector import Vector

        sql = f"""
            INSERT INTO {self.schema}.chunks ({COLUMNS}, keywords, embedding)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (chunk_id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id, kind = EXCLUDED.kind, heading = EXCLUDED.heading,
                text = EXCLUDED.text, source_url = EXCLUDED.source_url,
                content_hash = EXCLUDED.content_hash, keywords = EXCLUDED.keywords,
                embedding = EXCLUDED.embedding, updated_at = now()
        """
        with self._conn.cursor() as cur:
            for chunk, embedding in zip(chunks, embeddings, strict=True):
                cur.execute(
                    sql,
                    (
                        chunk.chunk_id,
                        chunk.doc_id,
                        chunk.kind,
                        chunk.heading,
                        chunk.text,
                        chunk.source_url,
                        chunk.content_hash,
                        code_keywords(chunk.heading + " " + chunk.text),
                        Vector(embedding),
                    ),
                )

    def delete_missing(self, keep_ids: set[str]) -> int:
        current = set(self.existing_hashes())
        stale = sorted(current - keep_ids)
        if stale:
            self._conn.execute(f"DELETE FROM {self.schema}.chunks WHERE chunk_id = ANY(%s)", (stale,))
        return len(stale)

    def existing_hashes(self) -> dict[str, str]:
        rows = self._conn.execute(f"SELECT chunk_id, content_hash FROM {self.schema}.chunks").fetchall()
        return {r[0]: r[1] for r in rows}

    def vector_search(self, embedding: list[float], k: int) -> list[Hit]:
        from pgvector import Vector

        rows = self._conn.execute(
            f"SELECT {COLUMNS}, 1 - (embedding <=> %s) AS score FROM {self.schema}.chunks "
            "ORDER BY embedding <=> %s LIMIT %s",
            (Vector(embedding), Vector(embedding), k),
        ).fetchall()
        return [Hit(StoredChunk(*row[:7]), float(row[7])) for row in rows]

    def keyword_search(self, query: str, k: int) -> list[Hit]:
        # websearch_to_tsquery ANDs terms; fall back to OR-ing them so long questions still match.
        terms = " | ".join(re.findall(r"[A-Za-z0-9]{2,}", query + " " + code_keywords(query.upper())))
        if not terms:
            return []
        rows = self._conn.execute(
            f"SELECT {COLUMNS}, ts_rank_cd(tsv, q) AS score "
            f"FROM {self.schema}.chunks, to_tsquery('english', %s) AS q "
            "WHERE tsv @@ q ORDER BY score DESC LIMIT %s",
            (terms, k),
        ).fetchall()
        return [Hit(StoredChunk(*row[:7]), float(row[7])) for row in rows]

    def get(self, chunk_ids: list[str]) -> list[StoredChunk]:
        rows = self._conn.execute(
            f"SELECT {COLUMNS} FROM {self.schema}.chunks WHERE chunk_id = ANY(%s)",
            (chunk_ids,),
        ).fetchall()
        return [StoredChunk(*row) for row in rows]

    def count(self) -> int:
        row = self._conn.execute(f"SELECT count(*) FROM {self.schema}.chunks").fetchone()
        return int(row[0]) if row else 0


WORD = re.compile(r"[a-z0-9]{2,}")
STOP = {"the", "and", "for", "are", "can", "what", "which", "how", "does", "you", "with", "this", "that", "from"}


def _terms(text: str) -> list[str]:
    return [t for t in WORD.findall((text + " " + code_keywords(text.upper())).lower()) if t not in STOP]


class InMemoryStore:
    """Same interface as PgVectorStore: exact cosine search + a small BM25 for keyword search."""

    def __init__(self) -> None:
        self.chunks: dict[str, StoredChunk] = {}
        self.vectors: dict[str, list[float]] = {}

    def upsert(self, chunks: list[StoredChunk], embeddings: list[list[float]]) -> None:
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            self.chunks[chunk.chunk_id] = replace(chunk)  # copy, like a real database row
            self.vectors[chunk.chunk_id] = embedding

    def existing_hashes(self) -> dict[str, str]:
        return {cid: c.content_hash for cid, c in self.chunks.items()}

    def vector_search(self, embedding: list[float], k: int) -> list[Hit]:
        scored = [
            Hit(self.chunks[cid], sum(a * b for a, b in zip(embedding, vec, strict=True)))
            for cid, vec in self.vectors.items()
        ]
        return sorted(scored, key=lambda h: -h.score)[:k]

    def keyword_search(self, query: str, k: int) -> list[Hit]:
        docs = {cid: Counter(_terms(c.heading + " " + c.text)) for cid, c in self.chunks.items()}
        if not docs:
            return []
        avg_len = sum(sum(d.values()) for d in docs.values()) / len(docs)
        hits = []
        for cid, tf in docs.items():
            score = 0.0
            for term in set(_terms(query)):
                df = sum(1 for d in docs.values() if term in d)
                if term not in tf:
                    continue
                idf = math.log(1 + (len(docs) - df + 0.5) / (df + 0.5))
                length = sum(tf.values())
                score += idf * tf[term] * 2.2 / (tf[term] + 1.2 * (0.25 + 0.75 * length / avg_len))
            if score > 0:
                hits.append(Hit(self.chunks[cid], score))
        return sorted(hits, key=lambda h: -h.score)[:k]

    def get(self, chunk_ids: list[str]) -> list[StoredChunk]:
        return [self.chunks[c] for c in chunk_ids if c in self.chunks]
