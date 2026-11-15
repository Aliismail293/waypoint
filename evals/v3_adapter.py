"""ragaudit adapter with real, tunable knobs, for the V3 accuracy loop (local Ollama only, no cloud calls).

Knobs (ragaudit `--config` YAML):
- chunk_size / chunk_overlap: characters. The canonical corpus chunks are re-split into windows; every window keeps
  the canonical chunk_id it came from, so retrieval is still scored against ragaudit's chunks.
- top_k: windows given to the generator (retrieval is scored over the top k distinct canonical chunks).
- hybrid_weight: 0 = keyword (BM25) only, 1 = embedding only.
- reranker: "none" or "overlap" (re-scores the top candidates by how many question words they contain).
- prompt_variant: "plain" | "concise" | "strict" (strict refuses when the context doesn't say).
- router_threshold: minimum normalised retrieval score to answer at all; below it the pipeline says it can't find
  the answer in the calendar instead of guessing. 0 disables.

Questions the structured fact route can answer go through `waypoint.facts` (no model), exactly as in the product.
Models: generator `qwen2.5:7b` (env V3_GENERATOR), embeddings `nomic-embed-text`, both on local Ollama.
"""

import hashlib
import json
import math
import os
import re
import time
from pathlib import Path

import httpx
from ragaudit.models.adapter import RAGResponse

from waypoint.datastore import get_store
from waypoint.facts import answer_fact, parse_fact_question

CORPUS = Path(__file__).parent / "corpus.jsonl"
CACHE = Path(__file__).parent / "results" / "v3_embed_cache.json"
OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
TOKEN = re.compile(r"[a-z0-9]+")
STOP = frozenset(
    "the a an of to in and or for is are what how do does can i my me at on with it be this that".split()  # noqa: SIM905
)
PROMPTS = {
    "plain": "Answer the question using the calendar passages below.",
    "concise": "Answer in at most two sentences using only the calendar passages below.",
    "strict": (
        "Answer in at most two sentences using only the calendar passages below. "
        "If they do not say, answer exactly: I can't find that in the calendar."
    ),
}


def tokens(text: str) -> list[str]:
    return [t for t in TOKEN.findall(text.lower()) if t not in STOP]


def windows(text: str, size: int, overlap: int) -> list[str]:
    if size <= 0 or len(text) <= size:
        return [text]
    step = max(size - overlap, 1)
    return [text[i : i + size] for i in range(0, len(text), step) if text[i : i + size].strip()]


class Embedder:
    """nomic-embed-text through Ollama, cached on disk by text hash so tuning trials don't re-embed."""

    def __init__(self) -> None:
        self.cache: dict[str, list[float]] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
        self.dirty = False

    def __call__(self, text: str) -> list[float]:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key not in self.cache:
            r = httpx.post(f"{OLLAMA}/api/embeddings", json={"model": "nomic-embed-text", "prompt": text}, timeout=120)
            r.raise_for_status()
            self.cache[key] = r.json()["embedding"]
            self.dirty = True
        return self.cache[key]

    def save(self) -> None:
        if self.dirty:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps(self.cache))
            self.dirty = False


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    return dot / (math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b)) or 1.0)


def norm(scores: list[float]) -> list[float]:
    low, high = min(scores, default=0), max(scores, default=0)
    return [(s - low) / (high - low) if high > low else 0.0 for s in scores]


class V3Adapter:
    def __init__(self, config: dict) -> None:
        self.chunk_size = int(config.get("chunk_size", 1200))
        self.chunk_overlap = int(config.get("chunk_overlap", 150))
        self.top_k = int(config.get("top_k", 5))
        self.hybrid_weight = float(config.get("hybrid_weight", 0.5))
        self.reranker = config.get("reranker", "none")
        self.prompt_variant = config.get("prompt_variant", "concise")
        self.router_threshold = float(config.get("router_threshold", 0.0))
        self.model = os.environ.get("V3_GENERATOR", "qwen2.5:7b")
        self.store = get_store()
        self.embed = Embedder()
        self.pieces: list[tuple[str, str]] = []  # (canonical chunk_id, window text)
        for line in CORPUS.read_text().splitlines():
            row = json.loads(line)
            self.pieces += [(row["chunk_id"], w) for w in windows(row["text"], self.chunk_size, self.chunk_overlap)]
        self.piece_tokens = [tokens(text) for _, text in self.pieces]
        self.piece_vectors = [self.embed(text) for _, text in self.pieces]
        self.embed.save()
        self.df: dict[str, int] = {}
        for toks in self.piece_tokens:
            for t in set(toks):
                self.df[t] = self.df.get(t, 0) + 1
        self.avg_len = sum(len(t) for t in self.piece_tokens) / max(len(self.piece_tokens), 1)

    def bm25(self, query: list[str], doc: list[str]) -> float:
        n, score = len(self.pieces), 0.0
        for term in set(query):
            tf = doc.count(term)
            if not tf:
                continue
            idf = math.log(1 + (n - self.df.get(term, 0) + 0.5) / (self.df.get(term, 0) + 0.5))
            score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * len(doc) / self.avg_len))
        return score

    def retrieve(self, question: str) -> list[tuple[str, str, float]]:
        q_tokens = tokens(question)
        q_vec = self.embed(question)
        self.embed.save()
        lexical = norm([self.bm25(q_tokens, d) for d in self.piece_tokens])
        semantic = norm([cosine(q_vec, v) for v in self.piece_vectors])
        scored = [
            self.hybrid_weight * s + (1 - self.hybrid_weight) * lx for s, lx in zip(semantic, lexical, strict=True)
        ]
        order = sorted(range(len(scored)), key=lambda i: -scored[i])
        if self.reranker == "overlap":
            head = order[: self.top_k * 3]
            wanted = set(q_tokens)

            def rescore(i: int) -> float:
                return scored[i] + 0.5 * len(wanted & set(self.piece_tokens[i])) / max(len(wanted), 1)

            order = sorted(head, key=lambda i: -rescore(i)) + order[self.top_k * 3 :]
        return [(self.pieces[i][0], self.pieces[i][1], scored[i]) for i in order[: self.top_k]]

    def generate(self, question: str, passages: list[str]) -> str:
        context = "\n\n".join(f"[{i + 1}] {p}" for i, p in enumerate(passages))
        r = httpx.post(
            f"{OLLAMA}/api/chat",
            json={
                "model": self.model,
                "stream": False,
                "options": {"temperature": 0},
                "messages": [
                    {"role": "system", "content": PROMPTS[self.prompt_variant]},
                    {"role": "user", "content": f"Passages:\n{context}\n\nQuestion: {question}"},
                ],
            },
            timeout=300,
        )
        r.raise_for_status()
        return r.json()["message"]["content"].strip()

    def query(self, question: str) -> RAGResponse:
        started = time.perf_counter()
        fact = parse_fact_question(question, self.store)
        if fact is not None:
            answer = answer_fact(fact, self.store)
            if answer is not None:
                ms = (time.perf_counter() - started) * 1000
                return RAGResponse(
                    answer=answer.text, retrieved_chunk_ids=answer.chunk_ids, latency_ms=ms, raw={"route": "fact"}
                )
        hits = self.retrieve(question)
        ids = list(dict.fromkeys(cid for cid, _, _ in hits))
        if hits and hits[0][2] < self.router_threshold:
            text, route = "I can't find that in the calendar.", "refused"
        else:
            text, route = self.generate(question, [p for _, p, _ in hits]), "rag"
        ms = (time.perf_counter() - started) * 1000
        return RAGResponse(
            answer=text, retrieved_chunk_ids=ids, contexts=[p for _, p, _ in hits], latency_ms=ms, raw={"route": route}
        )


def make_adapter(config: dict) -> V3Adapter:
    return V3Adapter(config)
