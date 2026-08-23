"""Token estimation for catalog metadata (§2.13.3)."""

from __future__ import annotations

from ..config import TokenizerConfig


def estimate_tokens(text: str, cfg: TokenizerConfig, image_count: int = 0) -> int:
    """Rough estimate. Images budgeted at 1500 tokens each (typical mid-size image)."""
    img_tokens = 1500 * image_count
    if cfg.kind == "rough":
        return max(1, len(text) // 4) + img_tokens
    try:
        import tiktoken

        enc = tiktoken.get_encoding(cfg.encoding)
        return len(enc.encode(text)) + img_tokens
    except Exception:
        return max(1, len(text) // 4) + img_tokens
