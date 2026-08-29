"""Orchestrate KB scan → per-kind generation → CSV write (§6.5, §6.6).

Called by both the CLI (`main.py`) and the tests. Keeps side effects — LLM
calls, filesystem writes, logging — contained in a single function that
callers can drive with any `Generator` callable (real or stubbed).
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..config import EvalGenConfig
from ..logger import HcagLogger
from .csv_writer import question_id, write_csv
from .generators import (
    GeneratedItem,
    GenerationError,
    Kind,
    gen_complex,
    gen_hard1,
    gen_hard2,
    gen_medium,
    gen_simple,
)
from .kb_scan import PacketRecord, scan_kb


KIND_ORDER: list[Kind] = ["simple", "medium", "complex", "hard-1", "hard-2"]


@dataclass
class EvalGenRequest:
    kb_root: Path
    out: Path
    counts: dict[Kind, int]
    seed: int | None = None
    id_prefix: str = "q"


@dataclass
class EvalGenStats:
    requested: dict[Kind, int] = field(default_factory=dict)
    generated: dict[Kind, int] = field(default_factory=dict)
    dropped: dict[Kind, int] = field(default_factory=dict)
    total_written: int = 0
    warnings: int = 0
    errors: int = 0


def split_total(total: int) -> dict[Kind, int]:
    """Split a total across five kinds, distributing remainder in KIND_ORDER."""
    if total < 0:
        raise ValueError("total must be non-negative")
    base, rem = divmod(total, 5)
    out: dict[Kind, int] = {k: base for k in KIND_ORDER}
    for i in range(rem):
        out[KIND_ORDER[i]] += 1
    return out


def _feasibility_limit(kind: Kind, packets: list[PacketRecord]) -> int:
    """Max feasible questions of this kind given the scanned KB.

    Rough upper bound — we do not enforce uniqueness by paragraph within a
    kind, so the true generation cap is much higher, but for kinds with
    hard prerequisites (hard-2 needs images; hard-1 needs >=2 packets;
    complex needs a packet with >=3 paragraphs) we can pre-compute what
    the run is capable of and warn on shortfalls.
    """
    if kind == "hard-2":
        return sum(1 for p in packets if p.has_images) * 100  # each image-bearing packet can source many
    if kind == "hard-1":
        return 0 if len(packets) < 2 else len(packets) * 100
    if kind == "complex":
        eligible = sum(1 for p in packets if len(p.paragraphs) >= 3)
        return eligible * 100
    return len(packets) * 100  # simple, medium — one per packet is a very rough bound


def _hard2_packet_shortfall(requested: int, packets: list[PacketRecord]) -> tuple[int, int]:
    """Return (allowed, requested) for hard-2 based on image-bearing packet count.

    Per §6.4.5, hard-2 is only generated for image-bearing packets; the
    total distinct image-bearing packets caps the total emittable count
    (we don't ask twice about the same packet's image set)."""
    n_image_packets = sum(1 for p in packets if p.has_images)
    return min(requested, n_image_packets), requested


def _pick_packet(kind: Kind, packets: list[PacketRecord], rng: random.Random, used: dict[Kind, set[str]]) -> PacketRecord | None:
    """Pick a packet suitable for this kind, preferring packets not yet
    used for this kind so the eval set spans the KB."""
    if kind == "hard-2":
        pool = [p for p in packets if p.has_images]
    elif kind == "complex":
        pool = [p for p in packets if len(p.paragraphs) >= 3]
    else:
        pool = [p for p in packets if p.paragraphs]

    if not pool:
        return None

    unused = [p for p in pool if p.id not in used[kind]]
    if unused:
        return rng.choice(unused)
    return rng.choice(pool)


def _generate_one(
    kind: Kind,
    packet: PacketRecord,
    packets: list[PacketRecord],
    cfg: EvalGenConfig,
    rng: random.Random,
) -> GeneratedItem:
    if kind == "simple":
        return gen_simple(cfg.llm, packet, rng)
    if kind == "medium":
        return gen_medium(cfg.llm, packet, rng)
    if kind == "complex":
        return gen_complex(cfg.llm, packet, rng)
    if kind == "hard-1":
        return gen_hard1(cfg.llm, packet, packets, cfg.generation.cross_packet_bias, rng)
    if kind == "hard-2":
        return gen_hard2(cfg.llm, packet, rng)
    raise ValueError(f"unknown kind: {kind}")


def run_evalgen(
    request: EvalGenRequest,
    cfg: EvalGenConfig,
    logger: HcagLogger,
    generator_override: Callable[..., GeneratedItem] | None = None,
) -> EvalGenStats:
    """Scan the KB, generate the requested per-kind counts, write the CSV.

    `generator_override` lets tests substitute a deterministic stub; when None
    the real LLM-backed generators are used.
    """
    stats = EvalGenStats()

    packets = scan_kb(request.kb_root, cfg.generation.paragraph_min_chars, logger=logger)
    if not packets:
        logger.error("evalgen.start.failed", reason="no_packets", kb_root=str(request.kb_root))
        stats.errors += 1
        return stats

    n_image_packets = sum(1 for p in packets if p.has_images)
    logger.info(
        "evalgen.start",
        kb_root=str(request.kb_root),
        out=str(request.out),
        packets=len(packets),
        image_packets=n_image_packets,
        requested={k: v for k, v in request.counts.items()},
        seed=request.seed,
    )

    # Feasibility check — resolve requested counts against what the KB can support.
    resolved: dict[Kind, int] = {}
    for kind in KIND_ORDER:
        want = request.counts.get(kind, 0)
        stats.requested[kind] = want
        stats.generated[kind] = 0
        stats.dropped[kind] = 0
        if want <= 0:
            resolved[kind] = 0
            continue
        if kind == "hard-2":
            allowed, _ = _hard2_packet_shortfall(want, packets)
            if allowed < want:
                logger.warn(
                    "evalgen.shortfall",
                    kind=kind,
                    requested=want,
                    generated=allowed,
                    reason="insufficient_image_packets" if allowed > 0 else "no_image_packets",
                )
                stats.warnings += 1
            resolved[kind] = allowed
        elif kind == "hard-1" and len(packets) < 2:
            logger.warn(
                "evalgen.shortfall",
                kind=kind,
                requested=want,
                generated=0,
                reason="need_at_least_two_packets",
            )
            stats.warnings += 1
            resolved[kind] = 0
        elif kind == "complex" and not any(len(p.paragraphs) >= 3 for p in packets):
            logger.warn(
                "evalgen.shortfall",
                kind=kind,
                requested=want,
                generated=0,
                reason="no_packet_has_three_paragraphs",
            )
            stats.warnings += 1
            resolved[kind] = 0
        else:
            resolved[kind] = want

    rng = random.Random(request.seed)
    used_packets: dict[Kind, set[str]] = {k: set() for k in KIND_ORDER}
    seen_questions: set[str] = set()

    rows: list[tuple[str, GeneratedItem]] = []
    next_index = 1

    for kind in KIND_ORDER:
        target = resolved[kind]
        for _ in range(target):
            packet = _pick_packet(kind, packets, rng, used_packets)
            if packet is None:
                logger.warn("evalgen.item.dropped", kind=kind, reason="no_eligible_packet")
                stats.dropped[kind] += 1
                stats.warnings += 1
                continue

            item: GeneratedItem | None = None
            last_error: str = ""
            for attempt in range(cfg.generation.max_retries_per_item + 1):
                try:
                    if generator_override is not None:
                        item = generator_override(kind, packet, packets, cfg, rng)
                    else:
                        item = _generate_one(kind, packet, packets, cfg, rng)
                    break
                except GenerationError as e:
                    last_error = str(e)
                    logger.debug("evalgen.item.retry", kind=kind, packet=packet.id, attempt=attempt, error=last_error)
                except Exception as e:  # pragma: no cover - LLM/network failures
                    last_error = f"{type(e).__name__}: {e}"
                    logger.debug("evalgen.item.retry", kind=kind, packet=packet.id, attempt=attempt, error=last_error)

            if item is None:
                logger.warn(
                    "evalgen.item.dropped",
                    kind=kind,
                    packet=packet.id,
                    reason="validation_failed_or_llm_error",
                    error=last_error,
                )
                stats.dropped[kind] += 1
                stats.warnings += 1
                continue

            # De-dupe by exact question text (§6.10 WARN case).
            key = item.question.strip().lower()
            if key in seen_questions:
                logger.warn("evalgen.item.dropped", kind=kind, packet=packet.id, reason="duplicate_question")
                stats.dropped[kind] += 1
                stats.warnings += 1
                continue
            seen_questions.add(key)

            used_packets[kind].add(packet.id)
            qid = question_id(request.id_prefix, next_index)
            rows.append((qid, item))
            logger.info(
                "evalgen.item",
                question_id=qid,
                kind=kind,
                source_packets=item.source_packet_ids,
            )
            next_index += 1
            stats.generated[kind] += 1

    try:
        stats.total_written = write_csv(request.out, rows)
    except OSError as e:
        logger.error("evalgen.write_failed", path=str(request.out), error=str(e))
        stats.errors += 1
        return stats

    logger.info(
        "evalgen.done",
        out=str(request.out),
        written=stats.total_written,
        generated=stats.generated,
        dropped=stats.dropped,
        warnings=stats.warnings,
        errors=stats.errors,
    )
    return stats
