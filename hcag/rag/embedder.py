"""Batched embedding client via LiteLLM (§8.4.4).

Discovers the embedding dimension on the first successful batch and pins it
for the rest of the run. A subsequent batch that returns a different
dimension aborts with ``DimensionDriftError`` — mixing spaces silently would
poison the index.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import EmbeddingConfig


class DimensionDriftError(RuntimeError):
    """Raised when the provider returns vectors of unexpected length mid-run."""


@dataclass
class EmbedBatchResult:
    vectors: list[list[float]]
    dimension: int
    prompt_tokens: int = 0


class Embedder:
    def __init__(self, cfg: EmbeddingConfig) -> None:
        self.cfg = cfg
        self._pinned_dim: int | None = cfg.dimension

    @property
    def dimension(self) -> int | None:
        return self._pinned_dim

    def _litellm_model(self) -> str:
        m = self.cfg.model
        p = self.cfg.provider
        if p == "openai":
            return m if m.startswith("openai/") else f"openai/{m}"
        if p == "anthropic":
            # LiteLLM routes anthropic embeddings via a specific provider prefix.
            return m if "/" in m else f"anthropic/{m}"
        if p == "bedrock":
            return m if m.startswith("bedrock/") else f"bedrock/{m}"
        if p == "ollama":
            return m if m.startswith("ollama/") else f"ollama/{m}"
        return m

    def embed(self, texts: list[str]) -> EmbedBatchResult:
        """Embed a batch. Enforces the pinned dimension."""
        if not texts:
            return EmbedBatchResult(vectors=[], dimension=self._pinned_dim or 0)

        import litellm

        kwargs: dict = {
            "model": self._litellm_model(),
            "input": texts,
        }
        if self.cfg.endpoint:
            kwargs["api_base"] = self.cfg.endpoint

        resp = litellm.embedding(**kwargs)

        # LiteLLM normalizes to OpenAI-shaped: {"data": [{"embedding": [...]}]}
        data = getattr(resp, "data", None) or resp["data"]  # type: ignore[index]
        vectors: list[list[float]] = []
        for item in data:
            emb = getattr(item, "embedding", None) if hasattr(item, "embedding") else item.get("embedding")
            vectors.append(list(emb))

        if not vectors:
            raise RuntimeError("embedding provider returned no vectors")

        dim = len(vectors[0])
        # Every vector in the batch must share a dimension.
        for v in vectors:
            if len(v) != dim:
                raise DimensionDriftError(
                    f"embedding provider returned mixed dimensions in one batch: "
                    f"{dim} vs {len(v)}"
                )

        if self._pinned_dim is None:
            self._pinned_dim = dim
        elif dim != self._pinned_dim:
            raise DimensionDriftError(
                f"embedding dimension drift: expected {self._pinned_dim}, got {dim} "
                f"(model={self.cfg.model})"
            )

        usage = getattr(resp, "usage", None)
        prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0

        return EmbedBatchResult(vectors=vectors, dimension=dim, prompt_tokens=prompt_tokens)

    def embed_iter(self, texts: list[str]):
        """Yield (offset, vectors) tuples for each configured-size batch."""
        step = max(1, self.cfg.batch_size)
        for i in range(0, len(texts), step):
            batch = texts[i : i + step]
            yield i, self.embed(batch)
