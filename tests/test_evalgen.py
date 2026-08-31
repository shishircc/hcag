"""Tests for the `evalgen` CLI (Part 6)."""

from __future__ import annotations

import csv
import random
from pathlib import Path

import pytest

from hcag.compiled_io import CompiledFrontMatter, write_compiled_md
from hcag.config import EvalGenConfig
from hcag.evalgen.csv_writer import COLUMNS, question_id, write_csv
from hcag.evalgen.generators import GeneratedItem
from hcag.evalgen.kb_scan import scan_kb, taxonomy_prefix
from hcag.evalgen.runner import (
    EvalGenRequest,
    KIND_ORDER,
    run_evalgen,
    split_total,
)
from hcag.logger import build_logger


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


# ---------------------------------------------------------------------------
# split_total (§6.5)
# ---------------------------------------------------------------------------


def test_split_total_divides_equally() -> None:
    assert split_total(100) == {"simple": 20, "medium": 20, "complex": 20, "hard-1": 20, "hard-2": 20}


def test_split_total_distributes_remainder_in_kind_order() -> None:
    # 12 → 3, 3, 2, 2, 2 per §6.5
    assert split_total(12) == {"simple": 3, "medium": 3, "complex": 2, "hard-1": 2, "hard-2": 2}


def test_split_total_zero() -> None:
    assert split_total(0) == {k: 0 for k in KIND_ORDER}


# ---------------------------------------------------------------------------
# taxonomy_prefix / KB scan (§6.2)
# ---------------------------------------------------------------------------


def test_taxonomy_prefix() -> None:
    assert taxonomy_prefix("billing.refunds") == "billing"
    assert taxonomy_prefix("billing.refunds.edge") == "billing.refunds"
    assert taxonomy_prefix("standalone") == ""


def _write_packet(folder: Path, packet_id: str, source_texts: list[tuple[str, str]], with_image: bool = False) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    fm = CompiledFrontMatter(
        id=packet_id,
        title=f"Title for {packet_id}",
        short_description=f"Short for {packet_id}",
        long_description=f"Long description for {packet_id}.",
        token_size_estimate=1000,
        kind="leaf",
        source_files=[name for name, _ in source_texts],
        children=[],
    )
    body_sections = [(name, content) for name, content in source_texts]
    write_compiled_md(folder / "compiled.md", fm, children=[], own_sections=body_sections)
    if with_image:
        assets = folder / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "diagram.png").write_bytes(PNG_BYTES)


def test_scan_kb_reads_packets_and_paragraphs(tmp_path: Path) -> None:
    _write_packet(
        tmp_path / "billing" / "refunds",
        "billing.refunds",
        [
            ("policy.md", "Refunds are issued within five business days. Customers may request a refund via the portal or by contacting support."),
            ("edge.md", "Partial refunds are supported. Currency conversion uses the rate at the time of purchase, not at the time of refund."),
        ],
        with_image=True,
    )
    _write_packet(
        tmp_path / "billing" / "invoices",
        "billing.invoices",
        [("main.md", "Invoices are generated on the first of the month for the prior billing period. Line items reflect actual usage.")],
    )

    packets = scan_kb(tmp_path, paragraph_min_chars=40)

    ids = {p.id for p in packets}
    assert ids == {"billing.refunds", "billing.invoices"}
    refunds = next(p for p in packets if p.id == "billing.refunds")
    assert len(refunds.paragraphs) >= 2
    assert refunds.has_images
    assert refunds.assets[0].name == "diagram.png"


def test_scan_kb_drops_packets_without_paragraphs(tmp_path: Path) -> None:
    _write_packet(
        tmp_path / "tiny",
        "tiny",
        [("main.md", "short")],
    )
    packets = scan_kb(tmp_path, paragraph_min_chars=120)
    assert packets == []


# ---------------------------------------------------------------------------
# CSV writer (§6.7)
# ---------------------------------------------------------------------------


def test_csv_writer_schema_and_empty_columns(tmp_path: Path) -> None:
    out = tmp_path / "eval.csv"
    items = [
        (question_id("q", 1), GeneratedItem("simple", "Q1?", "A1", ["p.a"])),
        (question_id("q", 2), GeneratedItem("hard-2", "Q2?", "A2", ["p.b"])),
    ]
    n = write_csv(out, items)
    assert n == 2

    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == COLUMNS
    assert rows[1] == ["q-0001", "simple", "Q1?", "A1", "", "", ""]
    assert rows[2] == ["q-0002", "hard-2", "Q2?", "A2", "", "", ""]


def test_question_id_zero_padded() -> None:
    assert question_id("q", 1) == "q-0001"
    assert question_id("run3", 42) == "run3-0042"


# ---------------------------------------------------------------------------
# run_evalgen — end-to-end with a stubbed generator
# ---------------------------------------------------------------------------


def _stub_generator(kind, packet, packets, cfg, rng):
    """Deterministic stand-in for the LLM. Returns a well-formed item."""
    return GeneratedItem(
        kind=kind,
        question=f"[{kind}] question about {packet.id}?",
        expected_answer=f"answer grounded in {packet.id}",
        source_packet_ids=[packet.id],
    )


def _make_kb(root: Path, n_packets: int = 3, with_images: bool = True) -> None:
    paragraphs = [
        "Refunds are issued within five business days for eligible transactions. Customers may request a refund through the portal.",
        "Partial refunds are supported and applied proportionally. Currency conversions use the historical rate at the original purchase.",
        "Chargebacks bypass the standard refund flow and are handled through the payments provider's dispute channel by the accounts team.",
        "Invoices consolidate line items across a billing period and are generated on the first day of the following month for review.",
    ]
    for i in range(n_packets):
        _write_packet(
            root / "billing" / f"pkt_{i}",
            f"billing.pkt_{i}",
            [(f"src_{j}.md", paragraphs[j % len(paragraphs)]) for j in range(3)],
            with_image=with_images,
        )


def test_run_evalgen_end_to_end_with_stub(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    _make_kb(kb, n_packets=3, with_images=True)

    cfg = EvalGenConfig()
    cfg.log.file_path = str(tmp_path / "evalgen.log")
    logger = build_logger(cfg.log, name="test.evalgen.e2e")

    out = tmp_path / "eval.csv"
    request = EvalGenRequest(
        kb_root=kb,
        out=out,
        counts=split_total(10),  # 2 per kind
        seed=42,
    )
    stats = run_evalgen(request, cfg, logger, generator_override=_stub_generator)

    assert stats.errors == 0
    assert stats.total_written == 10
    assert stats.generated == {"simple": 2, "medium": 2, "complex": 2, "hard-1": 2, "hard-2": 2}

    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == COLUMNS
    assert len(rows) == 11  # 1 header + 10 rows
    kinds_in_order = [r[1] for r in rows[1:]]
    # Generated in fixed KIND_ORDER per §6.6
    assert kinds_in_order == ["simple", "simple", "medium", "medium", "complex", "complex", "hard-1", "hard-1", "hard-2", "hard-2"]
    # question_ids monotonically increase
    assert [r[0] for r in rows[1:]] == [question_id("q", i) for i in range(1, 11)]
    # Last three columns always empty
    for r in rows[1:]:
        assert r[4] == "" and r[5] == "" and r[6] == ""


def test_run_evalgen_hard2_shortfall_when_no_images(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    _make_kb(kb, n_packets=2, with_images=False)

    cfg = EvalGenConfig()
    cfg.log.file_path = str(tmp_path / "evalgen.log")
    logger = build_logger(cfg.log, name="test.evalgen.hard2")

    out = tmp_path / "eval.csv"
    request = EvalGenRequest(
        kb_root=kb,
        out=out,
        counts={"simple": 0, "medium": 0, "complex": 0, "hard-1": 0, "hard-2": 3},
        seed=1,
    )
    stats = run_evalgen(request, cfg, logger, generator_override=_stub_generator)
    assert stats.errors == 0
    assert stats.generated["hard-2"] == 0
    assert stats.warnings >= 1
    assert stats.total_written == 0


def test_run_evalgen_errors_when_kb_empty(tmp_path: Path) -> None:
    kb = tmp_path / "empty_kb"
    kb.mkdir()

    cfg = EvalGenConfig()
    cfg.log.file_path = str(tmp_path / "evalgen.log")
    logger = build_logger(cfg.log, name="test.evalgen.empty")

    request = EvalGenRequest(
        kb_root=kb,
        out=tmp_path / "eval.csv",
        counts=split_total(5),
    )
    stats = run_evalgen(request, cfg, logger, generator_override=_stub_generator)
    assert stats.errors == 1
    assert stats.total_written == 0
    assert not (tmp_path / "eval.csv").exists()


def test_run_evalgen_dedupes_identical_questions(tmp_path: Path) -> None:
    kb = tmp_path / "kb"
    _make_kb(kb, n_packets=2, with_images=False)

    cfg = EvalGenConfig()
    cfg.log.file_path = str(tmp_path / "evalgen.log")
    logger = build_logger(cfg.log, name="test.evalgen.dedupe")

    def constant_gen(kind, packet, packets, cfg, rng):
        return GeneratedItem(
            kind=kind,
            question="Same question every time?",
            expected_answer="same",
            source_packet_ids=[packet.id],
        )

    request = EvalGenRequest(
        kb_root=kb,
        out=tmp_path / "eval.csv",
        counts={"simple": 3, "medium": 0, "complex": 0, "hard-1": 0, "hard-2": 0},
        seed=1,
    )
    stats = run_evalgen(request, cfg, logger, generator_override=constant_gen)
    # First one accepted; next two dropped as duplicates
    assert stats.generated["simple"] == 1
    assert stats.dropped["simple"] == 2
    assert stats.total_written == 1


# ---------------------------------------------------------------------------
# CLI argument validation (§6.3)
# ---------------------------------------------------------------------------


def test_cli_rejects_both_total_and_per_kind(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    import typer
    from hcag.evalgen.main import _cli

    kb = tmp_path / "kb"
    _make_kb(kb, n_packets=1, with_images=False)

    app = typer.Typer()
    app.command()(_cli)
    runner = CliRunner()
    result = runner.invoke(app, [str(kb), "--out", str(tmp_path / "e.csv"), "--total", "5", "--simple", "1"])
    assert result.exit_code == 2
    assert "Pass either --total" in result.output


def test_cli_rejects_missing_count(tmp_path: Path) -> None:
    from typer.testing import CliRunner
    import typer
    from hcag.evalgen.main import _cli

    kb = tmp_path / "kb"
    _make_kb(kb, n_packets=1, with_images=False)

    app = typer.Typer()
    app.command()(_cli)
    runner = CliRunner()
    result = runner.invoke(app, [str(kb), "--out", str(tmp_path / "e.csv")])
    assert result.exit_code == 2
    assert "Nothing to generate" in result.output
