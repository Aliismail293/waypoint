"""Text embeddings: Vertex AI for real runs, a deterministic hashing embedder for tests.

Both return unit-length vectors of `dim` floats, so cosine distance in pgvector works the same way.
"""

import hashlib
import math
import re
from typing import Protocol

from waypoint.config import Settings, get_settings

TOKEN = re.compile(r"[a-z0-9]+")


class Embedder(Protocol):
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class HashEmbedder:
    """Feature-hashed bag of words (plus course-code tokens). Offline, deterministic, lexical only."""

    def __init__(self, dim: int = 768) -> None:
        self.dim = dim

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self.dim
        for token in TOKEN.findall(text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


class VertexEmbedder:
    """Vertex AI text embeddings (gemini-embedding-001 by default) via the google-genai SDK."""

    def __init__(self, settings: Settings) -> None:
        from google import genai

        self.dim = settings.embedding_dim
        self.model = settings.embedding_model
        self._client = genai.Client(vertexai=True, project=settings.gcp_project, location=settings.gcp_region)
        self.billable_characters = 0

    def _embed(self, texts: list[str], task_type: str) -> list[list[float]]:
        from google.genai import types

        vectors: list[list[float]] = []
        for text in texts:  # gemini-embedding-001 accepts one input per request on Vertex AI
            response = self._client.models.embed_content(
                model=self.model,
                contents=text,
                config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=self.dim),
            )
            values = response.embeddings[0].values if response.embeddings else None
            if not values:
                raise RuntimeError("Vertex AI returned no embedding")
            norm = math.sqrt(sum(v * v for v in values)) or 1.0
            vectors.append([v / norm for v in values])
            self.billable_characters += len(text)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "RETRIEVAL_DOCUMENT")

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text], "RETRIEVAL_QUERY")[0]


def get_embedder(settings: Settings | None = None) -> Embedder:
    settings = settings or get_settings()
    if settings.embeddings == "vertex":
        return VertexEmbedder(settings)
    return HashEmbedder(settings.embedding_dim)
