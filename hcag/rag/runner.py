"""Orchestrator: walk KB -> chunk -> embed -> upsert -> refresh indexes.

See DESIGN.md §8.4 for the pipeline overview.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..logger import HcagLogger
from .chunker import Chunk, chunks_for, make_token_estimator
from .config import RagConfig
from .embedder import DimensionDriftError, Embedder
from .image_describe import describe_image
from .indexer import Index, utc_now
from .manifest import ManifestEntry, content_hash, load_manifest_dict, stable_chunk_id
from .walker import Candidate, SkipReason, extract_text, walk


class RunError(RuntimeError):
    """Raised for ERROR-level conditions per §8.8."""


@dataclass
class _PendingRow:
    """A chunk that has text + metadata but no vector yet — waiting on the embed batch."""

    kb_path: str
    chunk_index: int
    source_kind: str
    text: str
    char_start: int
    char_end: int
    headings: list[str]
    image_path: str
    token_estimate: int
    file_hash: str
    metadata: dict[str, Any]


@dataclass
class RunSummary:
    files_scanned: int = 0
    files_indexed: int = 0
    files_unchanged: int = 0
    files_skipped: int = 0
    chunks_written: int = 0
    images_described: int = 0
    images_dropped: int = 0
    file_errors: int = 0
    embed_errors: int = 0
    dimension: int = 0
    elapsed_sec: float = 0.0
    by_kind: dict[str, int] = field(default_factory=dict)


def _serialize_metadata(md: dict[str, Any]) -> str:
    try:
        return json.dumps(md, sort_keys=True, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps({"error": "unserializable_metadata"})


def _process_text_file(
    candidate: Candidate,
    cfg: RagConfig,
    est,
) -> tuple[list[_PendingRow], str]:
    """Return (rows_without_vector, content_hash)."""
    fh = content_hash(candidate.abs_path)
    try:
        text = extract_text(candidate)
    except Exception as e:  # noqa: BLE001
        raise RunError(f"extract_failed: {candidate.kb_path}: {type(e).__name__}: {e}") from e

    chunks: list[Chunk] = chunks_for(
        text,
        source_kind=candidate.source_kind,
        target_tokens=cfg.chunking.target_tokens,
        overlap_tokens=cfg.chunking.overlap_tokens,
        est=est,
    )

    meta_base = {
        "chunker": "markdown-aware" if candidate.source_kind != "text" else "plain",
        "target_tokens": cfg.chunking.target_tokens,
        "overlap_tokens": cfg.chunking.overlap_tokens,
        "embed_model": cfg.embedding.model,
    }

    rows: list[_PendingRow] = []
    for idx, ch in enumerate(chunks):
        rows.append(
            _PendingRow(
                kb_path=candidate.kb_path,
                chunk_index=idx,
                source_kind=candidate.source_kind,
                text=ch.text,
                char_start=ch.char_start,
                char_end=ch.char_end,
                headings=ch.headings,
                image_path="",
                token_estimate=ch.token_estimate,
                file_hash=fh,
                metadata=meta_base,
            )
        )
    return rows, fh


def _process_image_file(
    candidate: Candidate,
    cfg: RagConfig,
    est,
    logger: HcagLogger,
) -> tuple[list[_PendingRow], str, bool]:
    """Return (rows, file_hash, was_dropped)."""
    fh = content_hash(candidate.abs_path)
    res = describe_image(candidate.abs_path, cfg.image)
    if not res.text:
        logger.warn(
            "rag.image.description_failed",
            kb_path=candidate.kb_path,
            error=res.error,
        )
        return [], fh, True

    row = _PendingRow(
        kb_path=candidate.kb_path,
        chunk_index=0,
        source_kind="image",
        text=res.text,
        char_start=0,
        char_end=0,
        headings=[],
        image_path=candidate.kb_path,
        token_estimate=est(res.text),
        file_hash=fh,
        metadata={
            "image_prompt_version": "v1",
            "image_model": cfg.image.model,
            "embed_model": cfg.embedding.model,
        },
    )
    return [row], fh, False


def _pending_to_row(pending: _PendingRow, vector: list[float]) -> dict[str, Any]:
    return {
        "id": stable_chunk_id(pending.kb_path, pending.chunk_index, pending.file_hash),
        "kb_path": pending.kb_path,
        "chunk_index": pending.chunk_index,
        "source_kind": pending.source_kind,
        "text": pending.text,
        "vector": vector,
        "char_start": pending.char_start,
        "char_end": pending.char_end,
        "headings": pending.headings,
        "image_path": pending.image_path,
        "token_estimate": pending.token_estimate,
        "content_hash": pending.file_hash,
        "metadata": _serialize_metadata(pending.metadata),
        "indexed_at": utc_now(),
    }


def _embed_and_write(
    pending: list[_PendingRow],
    embedder: Embedder,
    idx: Index,
    logger: HcagLogger,
    summary: RunSummary,
) -> None:
    """Embed a pending list in batches and write rows to LanceDB."""
    if not pending:
        return
    texts = [p.text for p in pending]
    written = 0
    for offset, result in embedder.embed_iter(texts):
        rows = [_pending_to_row(pending[offset + i], v) for i, v in enumerate(result.vectors)]
        try:
            idx.add_chunks(rows)
            written += len(rows)
            logger.debug(
                "rag.embed.batch_written",
                offset=offset,
                batch_size=len(rows),
                dim=result.dimension,
            )
        except Exception as e:  # noqa: BLE001
            summary.embed_errors += 1
            logger.warn(
                "rag.embed.write_failed",
                offset=offset,
                batch_size=len(rows),
                error=f"{type(e).__name__}: {e}",
            )
    summary.chunks_written += written


def run_rag(
    kb_root: Path,
    index_dir: Path,
    cfg: RagConfig,
    logger: HcagLogger,
    *,
    recreate: bool = False,
) -> RunSummary:
    started = time.monotonic()
    summary = RunSummary()

    if not kb_root.is_dir():
        raise RunError(f"kb_root is not a directory: {kb_root}")

    if index_dir.exists() and not index_dir.is_dir():
        raise RunError(f"index path exists but is not a directory: {index_dir}")

    logger.info(
        "rag.start",
        kb_root=str(kb_root),
        index_dir=str(index_dir),
        table=cfg.index.table,
        embedding_model=cfg.embedding.model,
        image_model=cfg.image.model,
        include_images=cfg.index.include_images,
        recreate=recreate,
    )

    est = make_token_estimator()

    # ---- Discovery ----------------------------------------------------
    candidates: list[Candidate] = []
    for item in walk(kb_root, include_images=cfg.index.include_images):
        if isinstance(item, Candidate):
            candidates.append(item)
            summary.by_kind[item.source_kind] = summary.by_kind.get(item.source_kind, 0) + 1
        else:
            summary.files_skipped += 1
            level = "debug" if item.reason in ("unknown_ext", "images_disabled") else "debug"
            getattr(logger, level)(
                f"rag.file.skip_{item.reason}",
                kb_path=item.kb_path,
            )

    summary.files_scanned = len(candidates)
    if not candidates:
        raise RunError("no in-scope files found under kb_root")

    logger.info(
        "rag.discovery.done",
        candidates=summary.files_scanned,
        by_kind=summary.by_kind,
        skipped=summary.files_skipped,
    )

    # ---- Embedder + Index --------------------------------------------
    embedder = Embedder(cfg.embedding)
    # Dimension may be pinned in config; otherwise we discover it on the first
    # batch and create the LanceDB tables lazily.
    idx: Index | None = None

    if recreate:
        # Drop tables before we know the dimension — connect happens later.
        placeholder = Index(index_dir, cfg.index.table, vector_dim=cfg.embedding.dimension or 1)
        placeholder.drop()
        logger.info("rag.recreate.dropped_tables", index_dir=str(index_dir))

    manifest_map: dict[str, ManifestEntry] = {}
    if not recreate and cfg.embedding.dimension:
        # If dimension is pinned we can open the tables up front and read the manifest.
        idx = Index(index_dir, cfg.index.table, vector_dim=cfg.embedding.dimension)
        idx.connect()
        manifest_map = load_manifest_dict(idx.load_manifest())

    # ---- Per-file loop ------------------------------------------------
    for candidate in candidates:
        file_started = time.monotonic()
        try:
            if candidate.source_kind == "image":
                pending, fh, dropped = _process_image_file(candidate, cfg, est, logger)
                if dropped:
                    summary.images_dropped += 1
                    continue
                summary.images_described += 1
            else:
                pending, fh = _process_text_file(candidate, cfg, est)
        except RunError as e:
            summary.file_errors += 1
            logger.warn("rag.file.failed", kb_path=candidate.kb_path, error=str(e))
            continue

        # Skip if unchanged.
        prev = manifest_map.get(candidate.kb_path)
        if prev and prev.content_hash == fh and idx is not None and not recreate:
            summary.files_unchanged += 1
            logger.debug("rag.file.unchanged", kb_path=candidate.kb_path)
            continue

        if not pending:
            continue

        # Lazily open the index on the very first embedded chunk so we can pin
        # the vector dimension from the provider's actual response.
        if idx is None:
            try:
                first_result = embedder.embed([pending[0].text])
            except DimensionDriftError as e:
                raise RunError(str(e)) from e
            except Exception as e:  # noqa: BLE001
                summary.file_errors += 1
                logger.warn(
                    "rag.embed.probe_failed",
                    kb_path=candidate.kb_path,
                    error=f"{type(e).__name__}: {e}",
                )
                continue
            summary.dimension = first_result.dimension
            idx = Index(index_dir, cfg.index.table, vector_dim=first_result.dimension)
            idx.connect()
            manifest_map = load_manifest_dict(idx.load_manifest())
            # Now that idx is up, re-check whether this file is actually unchanged
            # against the manifest we just loaded.
            prev = manifest_map.get(candidate.kb_path)
            if prev and prev.content_hash == fh and not recreate:
                summary.files_unchanged += 1
                logger.debug("rag.file.unchanged", kb_path=candidate.kb_path)
                continue
            # Write the probed vector directly, then embed remaining chunks.
            head_row = _pending_to_row(pending[0], first_result.vectors[0])
            idx.delete_file_rows(candidate.kb_path)
            idx.add_chunks([head_row])
            summary.chunks_written += 1
            remaining = pending[1:]
        else:
            idx.delete_file_rows(candidate.kb_path)
            remaining = pending

        try:
            _embed_and_write(remaining, embedder, idx, logger, summary)
        except DimensionDriftError as e:
            raise RunError(str(e)) from e
        except Exception as e:  # noqa: BLE001
            summary.embed_errors += 1
            logger.warn(
                "rag.embed.file_failed",
                kb_path=candidate.kb_path,
                error=f"{type(e).__name__}: {e}",
            )
            continue

        idx.upsert_manifest(
            {
                "kb_path": candidate.kb_path,
                "content_hash": fh,
                "bytes": candidate.bytes,
                "mtime": candidate.mtime,
                "chunk_count": len(pending),
                "source_kind": candidate.source_kind,
                "indexed_at": utc_now(),
            }
        )
        summary.files_indexed += 1
        logger.info(
            "rag.file.indexed",
            kb_path=candidate.kb_path,
            source_kind=candidate.source_kind,
            chunks=len(pending),
            elapsed_ms=round((time.monotonic() - file_started) * 1000.0, 1),
        )

    # ---- Index refresh ------------------------------------------------
    if idx is not None:
        idx.refresh_indexes()
        summary.dimension = summary.dimension or embedder.dimension or 0
        logger.info("rag.indexes.refreshed", dimension=summary.dimension)
    else:
        logger.warn("rag.indexes.no_writes", reason="all_files_unchanged_or_failed")

    summary.elapsed_sec = round(time.monotonic() - started, 2)
    logger.info(
        "rag.done",
        **{
            k: v
            for k, v in summary.__dict__.items()
            if k not in ("by_kind",)
        },
        by_kind=summary.by_kind,
    )
    return summary
