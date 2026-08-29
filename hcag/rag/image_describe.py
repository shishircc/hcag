"""Multimodal image description via LiteLLM (§8.4.3).

Each image → one LLM call → one text description. The description is what
gets embedded and stored in the index; the row also retains ``image_path``
so consumers can dereference the source bytes on a hit.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from .config import ImageConfig


_MIME_BY_EXT = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


@dataclass
class DescribeResult:
    text: str
    error: str = ""


def _packaged_prompt() -> str:
    return resources.files("hcag.rag.prompts").joinpath("image_describe.md").read_text(encoding="utf-8")


def load_image_prompt(path: str) -> str:
    if not path:
        return _packaged_prompt()
    return Path(path).read_text(encoding="utf-8")


def _mime_for(path: Path) -> str:
    return _MIME_BY_EXT.get(path.suffix.lower(), "application/octet-stream")


def _read_data_url(image_path: Path) -> str:
    raw = image_path.read_bytes()
    b64 = base64.b64encode(raw).decode("ascii")
    return f"data:{_mime_for(image_path)};base64,{b64}"


def _litellm_model(cfg: ImageConfig) -> str:
    """Match the LLMConfig.litellm_model() convention for a few providers."""
    m = cfg.model
    p = cfg.provider
    if p == "anthropic":
        return m
    if p == "bedrock":
        return m if m.startswith("bedrock/") else f"bedrock/{m}"
    if p == "openai":
        return m if m.startswith("openai/") else f"openai/{m}"
    if p == "ollama":
        return m if m.startswith("ollama/") else f"ollama/{m}"
    return m


def describe_image(image_path: Path, cfg: ImageConfig) -> DescribeResult:
    """Return a text description for ``image_path``. On persistent failure
    returns a ``DescribeResult`` with empty text and a non-empty ``error``.
    """
    if not image_path.is_file():
        return DescribeResult(text="", error=f"missing_file: {image_path}")

    try:
        data_url = _read_data_url(image_path)
    except OSError as e:
        return DescribeResult(text="", error=f"read_failed: {e}")

    prompt = load_image_prompt(cfg.prompt_path)
    payload = {
        "model": _litellm_model(cfg),
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": cfg.max_output_tokens,
        "temperature": cfg.temperature,
    }

    last_err = ""
    for _ in range(cfg.max_retries + 1):
        try:
            import litellm

            resp = litellm.completion(**payload)
            text = ""
            if resp.choices and resp.choices[0].message:
                text = getattr(resp.choices[0].message, "content", "") or ""
            text = text.strip()
            if text:
                return DescribeResult(text=text)
            last_err = "empty_description"
        except Exception as e:  # noqa: BLE001
            last_err = f"{type(e).__name__}: {e}"

    return DescribeResult(text="", error=last_err)
