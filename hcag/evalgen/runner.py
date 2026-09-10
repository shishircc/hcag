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
    ExamFraming,
    GeneratedItem,
    GenerationError,
    Kind,
    PersonaMismatch,
    gen_complex,
    gen_hard1,
    gen_hard2,
    gen_medium,
    gen_simple,
    render_persona_framing,
)
from .kb_scan import PacketRecord, scan_kb
from .personas import Persona


KIND_ORDER: list[Kind] = ["simple", "medium", "complex", "hard-1", "hard-2"]


@dataclass
class EvalGenRequest:
    kb_root: Path
    out: Path
    counts: dict[Kind, int]
    seed: int | None = None
    id_prefix: str = "q"
    #: The roster, in row order — allocation is positional (§6.5.1). Empty is
    #: persona-free generation, which stays a first-class mode.
    personas: list[Persona] = field(default_factory=list)


@dataclass
class EvalGenStats:
    requested: dict[Kind, int] = field(default_factory=dict)
    generated: dict[Kind, int] = field(default_factory=dict)
    dropped: dict[Kind, int] = field(default_factory=dict)
    #: Per-persona counts, because that is where an unproductive persona shows
    #: up: a role dropping half its items to `persona_content_mismatch` has a
    #: description that does not match what the KB covers, and the CSV shows
    #: only the rows that survived (§6.10).
    generated_by_persona: dict[str, int] = field(default_factory=dict)
    dropped_by_persona: dict[str, int] = field(default_factory=dict)
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


def _pick_packet(
    kind: Kind,
    packets: list[PacketRecord],
    rng: random.Random,
    used: dict[Kind, set[str]],
    persona: Persona | None = None,
    topic_bias: bool = True,
) -> PacketRecord | None:
    """Pick a packet suitable for this kind, preferring packets not yet
    used for this kind so the eval set spans the KB.

    A persona's `topics` narrow the pool when they match something — a bias,
    never a filter (§6.4.6). Applied after the kind's own eligibility test, so
    a persona whose subtree holds no image-bearing packet still gets `hard-2`
    from the wider KB instead of nothing at all.
    """
    if kind == "hard-2":
        pool = [p for p in packets if p.has_images]
    elif kind == "complex":
        pool = [p for p in packets if len(p.paragraphs) >= 3]
    else:
        pool = [p for p in packets if p.paragraphs]

    if not pool:
        return None

    if persona is not None and topic_bias and persona.topics:
        preferred = [p for p in pool if persona.matches(p.id)]
        if preferred:
            pool = preferred

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
    framing: str = "",
    persona_id: str = "",
) -> GeneratedItem:
    if kind == "simple":
        return gen_simple(cfg.llm, packet, rng, persona_framing=framing, persona_id=persona_id)
    if kind == "medium":
        return gen_medium(cfg.llm, packet, rng, persona_framing=framing, persona_id=persona_id)
    if kind == "complex":
        return gen_complex(cfg.llm, packet, rng, persona_framing=framing, persona_id=persona_id)
    if kind == "hard-1":
        return gen_hard1(
            cfg.llm, packet, packets, cfg.generation.cross_packet_bias, rng,
            persona_framing=framing, persona_id=persona_id,
        )
    if kind == "hard-2":
        return gen_hard2(cfg.llm, packet, rng, persona_framing=framing, persona_id=persona_id)
    raise ValueError(f"unknown kind: {kind}")


def run_evalgen(
    request: EvalGenRequest,
    cfg: EvalGenConfig,
    logger: HcagLogger,
    generator_override: Callable[..., GeneratedItem] | None = None,
) -> EvalGenStats:
    """Scan the KB, generate the requested per-kind counts, write the CSV.

    `generator_override` lets tests substitute a deterministic stub; when None
    the real LLM-backed generators are used. It is called as
    `(kind, packet, packets, cfg, rng)` — a stub does not need to know about
    personas, and the runner stamps the `persona_id` it returns without.
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
        # In allocation order, so a roster silently short by a row is visible
        # here rather than only in a coverage gap nobody looks for (§6.2.2).
        personas=[p.id for p in request.personas],
        allocation=cfg.personas.allocation if request.personas else "none",
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
    personas = list(request.personas)
    # Rendered once per persona rather than once per item: the framing is a
    # fixed block of text, and re-rendering it per question buys nothing.
    framings = {p.id: render_persona_framing(cfg, p) for p in personas}
    matched = bool(personas) and cfg.personas.allocation == "matched"

    for p in personas:
        stats.generated_by_persona[p.id] = 0
        stats.dropped_by_persona[p.id] = 0
        if cfg.personas.topic_bias and p.topics and not any(p.matches(pk.id) for pk in packets):
            # A bias, not a filter: this persona still generates, from the
            # whole KB. A filter would turn one mistyped prefix into a
            # silently empty persona (§6.4.6).
            logger.warn(
                "evalgen.persona.topics_unmatched",
                persona=p.id,
                topics=p.topics,
                detail="no packet matches; sampling falls back to the whole KB",
            )
            stats.warnings += 1

    used_packets: dict[Kind, set[str]] = {k: set() for k in KIND_ORDER}
    seen_questions: set[str] = set()

    rows: list[tuple[str, GeneratedItem]] = []
    next_index = 1

    def _generate(
        kind: Kind,
        persona: Persona | None,
        fixed_packet: PacketRecord | None,
    ) -> tuple[GeneratedItem | None, PacketRecord | None, str, str]:
        """One item, with the retry/resample loop of §6.6.

        Returns `(item, packet, reason, error)` — `item` is None when the
        retries were exhausted and `reason` says what to log.

        The two persona failures are retried differently, which is the whole
        reason they are distinct exceptions. A mismatch means this content
        supports nothing this role would ask, so retrying the same packet fails
        the same way: resample the content and hold the persona fixed —
        swapping the persona to salvage the sample would quietly rewrite the
        allocation of §6.5.1. Exam framing is a phrasing failure on content
        that does fit, so the same packet is worth another try.
        """
        framing = framings.get(persona.id, "") if persona else ""
        pid = persona.id if persona else ""
        packet = fixed_packet
        reason = "validation_failed_or_llm_error"
        last_error = ""
        for attempt in range(cfg.generation.max_retries_per_item + 1):
            if packet is None:
                packet = _pick_packet(
                    kind, packets, rng, used_packets, persona, cfg.personas.topic_bias
                )
                if packet is None:
                    return None, None, "no_eligible_packet", ""
            try:
                if generator_override is not None:
                    item = generator_override(kind, packet, packets, cfg, rng)
                    # Stubs predate personas and need not know about them; the
                    # column is the runner's to fill either way.
                    if pid and not item.persona_id:
                        item.persona_id = pid
                else:
                    item = _generate_one(kind, packet, packets, cfg, rng, framing, pid)
                return item, packet, "", ""
            except PersonaMismatch as e:
                last_error = str(e)
                reason = "persona_content_mismatch"
                logger.debug(
                    "evalgen.item.resample",
                    kind=kind, packet=packet.id, persona=pid,
                    attempt=attempt, error=last_error,
                )
                if fixed_packet is None:
                    packet = None
            except ExamFraming as e:
                last_error = str(e)
                reason = "exam_framing"
                logger.debug(
                    "evalgen.item.retry",
                    kind=kind, packet=packet.id, persona=pid,
                    attempt=attempt, error=last_error,
                )
            except GenerationError as e:
                last_error = str(e)
                reason = "validation_failed_or_llm_error"
                logger.debug("evalgen.item.retry", kind=kind, packet=packet.id, attempt=attempt, error=last_error)
            except Exception as e:  # pragma: no cover - LLM/network failures
                last_error = f"{type(e).__name__}: {e}"
                reason = "validation_failed_or_llm_error"
                logger.debug("evalgen.item.retry", kind=kind, packet=packet.id, attempt=attempt, error=last_error)
        return None, packet, reason, last_error

    def _record_drop(kind: Kind, persona: Persona | None, packet: PacketRecord | None, reason: str, error: str) -> None:
        logger.warn(
            "evalgen.item.dropped",
            kind=kind,
            packet=packet.id if packet else None,
            persona=persona.id if persona else "",
            reason=reason,
            error=error,
        )
        stats.dropped[kind] += 1
        stats.warnings += 1
        if persona is not None:
            stats.dropped_by_persona[persona.id] += 1

    def _accept(kind: Kind, persona: Persona | None, packet: PacketRecord, item: GeneratedItem) -> bool:
        """De-dupe, assign the id, and append. False when the row was dropped."""
        nonlocal next_index
        # Exact-text duplicates only (§6.10). Two personas asking about the
        # same fact in different words is the point, not a defect — but two
        # that generate byte-identical text are two that are not pulling apart.
        key = item.question.strip().lower()
        if key in seen_questions:
            _record_drop(kind, persona, packet, "duplicate_question", "")
            return False
        seen_questions.add(key)

        used_packets[kind].add(packet.id)
        qid = question_id(request.id_prefix, next_index)
        rows.append((qid, item))
        logger.info(
            "evalgen.item",
            question_id=qid,
            kind=kind,
            persona=item.persona_id,
            source_packets=item.source_packet_ids,
        )
        next_index += 1
        stats.generated[kind] += 1
        if persona is not None:
            stats.generated_by_persona[persona.id] += 1
        return True

    for kind in KIND_ORDER:
        target = resolved[kind]
        for i in range(target):
            if matched:
                # One grounding, every persona: the controlled comparison of
                # §6.5.1. The packet is picked persona-neutrally — it is shared
                # — and the rng state is rewound before each persona so the
                # paragraph and pairing draws inside the generator coincide.
                # A mismatch here drops the row rather than resampling, because
                # resampling would give that persona a different grounding and
                # there would be nothing left to compare.
                shared = _pick_packet(kind, packets, rng, used_packets)
                if shared is None:
                    for persona in personas:
                        _record_drop(kind, persona, None, "no_eligible_packet", "")
                    continue
                state = rng.getstate()
                for persona in personas:
                    rng.setstate(state)
                    item, packet, reason, error = _generate(kind, persona, shared)
                    if item is None:
                        _record_drop(kind, persona, packet, reason, error)
                        continue
                    _accept(kind, persona, packet or shared, item)
                continue

            # Round-robin, dealt inside the kind rather than across the run, so
            # every persona is represented in every kind (§6.5.1).
            persona = personas[i % len(personas)] if personas else None
            item, packet, reason, error = _generate(kind, persona, None)
            if item is None:
                _record_drop(kind, persona, packet, reason, error)
                continue
            _accept(kind, persona, packet, item)

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
        generated_by_persona=stats.generated_by_persona,
        dropped_by_persona=stats.dropped_by_persona,
        warnings=stats.warnings,
        errors=stats.errors,
    )
    return stats
