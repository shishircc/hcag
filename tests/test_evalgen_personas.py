"""Personas: the roster, the allocation, and the typicality rule (§6.2.3, §6.4.6, §6.5.1)."""

from __future__ import annotations

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from hcag.compiled_io import CompiledFrontMatter, write_compiled_md
from hcag.config import EvalGenConfig
from hcag.evalgen import generators as g
from hcag.evalgen.csv_writer import COLUMNS
from hcag.evalgen.generators import (
    ExamFraming,
    GeneratedItem,
    PersonaMismatch,
    _check_typicality,
    render_persona_framing,
)
from hcag.evalgen.personas import Persona, PersonaError, load_personas, select
from hcag.evalgen.runner import EvalGenRequest, run_evalgen
from hcag.logger import build_logger
from hcag.prompting import load_prompts


HR = "Files Employment Pass applications for a Singapore employer and cancels them when staff resign."
CANDIDATE = "A foreign professional working out whether they qualify for a work pass."


def _roster(path: Path, rows: list[list[str]], header: list[str] | None = None) -> Path:
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header or ["persona_id", "persona_name", "persona_description", "topics"])
        w.writerows(rows)
    return path


# ---------------------------------------------------------------------------
# The roster file (§6.2.3)
# ---------------------------------------------------------------------------


def test_a_roster_parses_id_name_description_and_topics(tmp_path: Path) -> None:
    path = _roster(
        tmp_path / "p.csv",
        [
            ["hr-professional", "HR professional", HR, "passes.employment-pass passes.work-permit"],
            ["prospective-pass-holder", "Prospective pass holder", CANDIDATE, ""],
        ],
    )
    personas = load_personas(path)
    assert [p.id for p in personas] == ["hr-professional", "prospective-pass-holder"]
    assert personas[0].name == "HR professional"
    assert personas[0].description == HR
    assert personas[0].topics == ["passes.employment-pass", "passes.work-permit"]
    assert personas[1].topics == []


def test_columns_are_matched_by_name_and_extras_ignored(tmp_path: Path) -> None:
    """Order does not matter, and a roster may carry an owner or a review date
    without evalgen needing to know about them."""
    path = _roster(
        tmp_path / "p.csv",
        [["HR professional", "reviewed 2026-01", HR, "hr-professional"]],
        header=["persona_name", "owner", "persona_description", "persona_id"],
    )
    personas = load_personas(path)
    assert personas[0].id == "hr-professional"
    assert personas[0].description == HR


def test_topics_may_be_comma_separated(tmp_path: Path) -> None:
    """Space-separated needs no inner quoting, but a comma is what people type."""
    path = _roster(tmp_path / "p.csv", [["hr", "HR", HR, "a.b, a.c"]])
    assert load_personas(path)[0].topics == ["a.b", "a.c"]


def test_a_description_survives_commas_and_newlines(tmp_path: Path) -> None:
    """RFC 4180 quoting is what lets a multi-paragraph description live in one
    cell — the reason the roster can be prose at all."""
    long_desc = "Files passes, cancels them, and appeals.\n\nTypically asks about deadlines."
    path = _roster(tmp_path / "p.csv", [["hr", "HR", long_desc, ""]])
    assert load_personas(path)[0].description == long_desc


def test_a_bom_does_not_break_the_header(tmp_path: Path) -> None:
    """A roster exported from Excel or Sheets carries one."""
    path = tmp_path / "p.csv"
    path.write_bytes(
        "﻿persona_id,persona_name,persona_description\nhr,HR,Files passes.\n".encode("utf-8")
    )
    assert load_personas(path)[0].id == "hr"


def test_a_trailing_blank_line_is_not_a_persona(tmp_path: Path) -> None:
    path = tmp_path / "p.csv"
    path.write_text(
        "persona_id,persona_name,persona_description\nhr,HR,Files passes.\n,,\n",
        encoding="utf-8",
    )
    assert [p.id for p in load_personas(path)] == ["hr"]


def test_a_missing_required_column_is_a_startup_error(tmp_path: Path) -> None:
    path = _roster(
        tmp_path / "p.csv", [["hr", "HR"]], header=["persona_id", "persona_name"]
    )
    with pytest.raises(PersonaError, match="persona_description"):
        load_personas(path)


def test_an_empty_roster_is_a_startup_error(tmp_path: Path) -> None:
    """A mis-authored file, not a request for persona-free generation — omitting
    the flag is how that is requested."""
    path = _roster(tmp_path / "p.csv", [])
    with pytest.raises(PersonaError, match="declares no personas"):
        load_personas(path)


def test_a_blank_description_is_a_startup_error(tmp_path: Path) -> None:
    """It would render a framing that says nothing while the `persona` column
    claims the row was asked in character."""
    path = _roster(tmp_path / "p.csv", [["hr", "HR", "  ", ""]])
    with pytest.raises(PersonaError, match="line 2 .hr.: blank persona_description"):
        load_personas(path)


def test_a_duplicate_id_is_a_startup_error(tmp_path: Path) -> None:
    path = _roster(
        tmp_path / "p.csv", [["hr", "HR", HR, ""], ["hr", "HR again", CANDIDATE, ""]]
    )
    with pytest.raises(PersonaError, match="duplicate persona_id"):
        load_personas(path)


def test_an_id_outside_the_allowed_charset_is_rejected(tmp_path: Path) -> None:
    path = _roster(tmp_path / "p.csv", [["HR Professional", "HR", HR, ""]])
    with pytest.raises(PersonaError, match="lowercase letters"):
        load_personas(path)


def test_an_unreadable_roster_is_a_startup_error(tmp_path: Path) -> None:
    with pytest.raises(PersonaError, match="could not be read"):
        load_personas(tmp_path / "missing.csv")


def test_select_narrows_the_roster_in_row_order(tmp_path: Path) -> None:
    """Row order, not flag order: allocation is positional (§6.5.1)."""
    path = _roster(
        tmp_path / "p.csv",
        [["a", "A", HR, ""], ["b", "B", CANDIDATE, ""], ["c", "C", HR, ""]],
    )
    personas = load_personas(path)
    assert [p.id for p in select(personas, ["c", "a"])] == ["a", "c"]
    assert [p.id for p in select(personas, [])] == ["a", "b", "c"]


def test_select_names_the_ids_that_are_present(tmp_path: Path) -> None:
    path = _roster(tmp_path / "p.csv", [["hr", "HR", HR, ""]])
    with pytest.raises(PersonaError, match="Present: hr"):
        select(load_personas(path), ["nope"])


def test_topics_match_a_subtree_not_a_prefix_string() -> None:
    p = Persona(id="hr", name="HR", description=HR, topics=["passes.ep"])
    assert p.matches("passes.ep")
    assert p.matches("passes.ep.eligibility")
    assert not p.matches("passes.epass")  # a longer sibling, not a child
    assert not p.matches("levies.ep")


# ---------------------------------------------------------------------------
# The framing prompt (§6.4.6, §2.15.5)
# ---------------------------------------------------------------------------


def test_no_persona_renders_no_framing() -> None:
    """Persona-free generation stays byte-identical to what it was: the slot is
    in all five prompts either way and renders to nothing."""
    assert render_persona_framing(EvalGenConfig().llm, None) == ""


def test_the_framing_carries_the_role_and_the_typicality_rule() -> None:
    p = Persona(id="hr", name="HR professional", description=HR, topics=[])
    text = render_persona_framing(EvalGenConfig().llm, p)
    assert "HR professional" in text
    assert HR in text
    assert "TYPICAL, NOT AN EXAM" in text
    # The escape hatch the generator's mismatch path depends on.
    assert "persona_mismatch" in text


def test_every_kind_prompt_carries_the_framing_slot() -> None:
    """A prompt file missing the slot fails at startup rather than generating a
    full CSV with the personas silently discarded."""
    lib = load_prompts()
    for name in ("simple", "medium", "complex", "hard1", "hard2"):
        assert "persona_framing" in lib.specs[f"evalgen.{name}"].required


# ---------------------------------------------------------------------------
# Typicality (§6.4.6)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "According to the guidance, when must a pass be cancelled?",
        "Based on the document, what is the salary floor?",
        "In the table above, which sector pays most?",
        "Which of the following candidates qualifies?",
        "List all four COMPASS criteria.",
        "What are the two conditions for an appeal?",
        "How many criteria does COMPASS score?",
    ],
)
def test_exam_framing_is_rejected(question: str) -> None:
    """A well-formed, grounded, difficult question that no user would ever ask
    scores the agent against a population it will never see."""
    with pytest.raises(ExamFraming):
        _check_typicality(question)


@pytest.mark.parametrize(
    "question",
    [
        "One of our engineers resigned last week — how long do we have to cancel the pass?",
        "My offer is 5,600 a month and I turn 32 next month. Is that enough for an EP?",
        "We want to move a candidate from a Personalised EP onto a company-tied one before "
        "their project starts. What do we need to do, and in what order?",
    ],
)
def test_a_question_a_person_would_ask_is_accepted(question: str) -> None:
    _check_typicality(question)  # must not raise


def test_typicality_is_only_enforced_on_a_persona_run() -> None:
    """Persona-free eval sets predate the rule and stay comparable with the ones
    generated after it."""
    packet = _packet_stub()
    reply = '{"question": "According to the guidance, what applies?", "expected_answer": "Five days."}'
    with patch.object(g, "_complete", return_value=reply):
        with patch.object(g, "_check_grounded", lambda *a: None):
            item = g.gen_simple(EvalGenConfig().llm, packet, _rng())
    assert item.question.startswith("According to")

    with patch.object(g, "_complete", return_value=reply):
        with patch.object(g, "_check_grounded", lambda *a: None):
            with pytest.raises(ExamFraming):
                g.gen_simple(EvalGenConfig().llm, packet, _rng(), persona_framing="F")


def test_the_model_can_report_a_mismatch_instead_of_inventing_a_question() -> None:
    """Forcing the pairing writes either a question the role would never ask or
    an answer stretched past the packet."""
    with patch.object(g, "_complete", return_value='{"persona_mismatch": "levy rules, not a candidate concern"}'):
        with pytest.raises(PersonaMismatch, match="levy rules"):
            g.gen_simple(EvalGenConfig().llm, _packet_stub(), _rng(), persona_framing="F")


def test_the_framing_reaches_the_kind_prompt() -> None:
    seen: list[str] = []

    def capture(cfg, content):
        seen.append(content if isinstance(content, str) else str(content))
        return '{"question": "How long do we have to cancel?", "expected_answer": "Five days."}'

    with patch.object(g, "_complete", capture):
        with patch.object(g, "_check_grounded", lambda *a: None):
            item = g.gen_simple(
                EvalGenConfig().llm, _packet_stub(), _rng(),
                persona_framing="THE PERSON ASKING — HR professional",
                persona_id="hr",
            )
    assert "THE PERSON ASKING — HR professional" in seen[0]
    assert item.persona_id == "hr"


def _rng():
    import random

    return random.Random(0)


def _packet_stub():
    from hcag.evalgen.kb_scan import PacketRecord

    return PacketRecord(
        id="passes.ep",
        title="EP",
        short_description="s",
        long_description="l",
        path=Path("/tmp/passes/ep"),
        body="A pass must be cancelled within five days.",
        paragraphs=["A pass must be cancelled within five days."],
    )


# ---------------------------------------------------------------------------
# Allocation and the CSV column (§6.5.1, §6.7.2)
# ---------------------------------------------------------------------------


PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32

#: Each comfortably over `paragraph_min_chars` (120), or the scan drops the
#: folder for having no paragraphs and there is nothing to generate against.
PARAGRAPHS = [
    "Employment Pass applications are submitted by the employer through the online portal, "
    "take about three weeks to process, and are assessed against the criteria in force on "
    "the date of submission.",
    "A pass must be cancelled within one week of the last day of employment, or the employer "
    "remains liable for the holder, including for repatriation and for any salary owed up to "
    "the date of cancellation.",
    "Candidates are assessed on salary and on qualifications, and the qualifying bar rises "
    "with age and differs by sector, with financial services held to a higher threshold than "
    "every other sector at each age.",
]


def _write_packet(folder: Path, packet_id: str, with_image: bool = False) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    fm = CompiledFrontMatter(
        id=packet_id,
        title=f"Title for {packet_id}",
        short_description=f"Short for {packet_id}",
        long_description=f"Long description for {packet_id}.",
        token_size_estimate=1000,
        kind="leaf",
        source_files=[f"src_{i}.md" for i in range(len(PARAGRAPHS))],
        children=[],
    )
    write_compiled_md(
        folder / "compiled.md",
        fm,
        subtopics=[],
        own_sections=[(f"src_{i}.md", p) for i, p in enumerate(PARAGRAPHS)],
    )
    if with_image:
        (folder / "assets").mkdir(exist_ok=True)
        (folder / "assets" / "d.png").write_bytes(PNG_BYTES)


def _kb(root: Path) -> Path:
    for i in range(3):
        _write_packet(root / "passes" / f"ep_{i}", f"passes.ep_{i}")
    for i in range(2):
        _write_packet(root / "levies" / f"lv_{i}", f"levies.lv_{i}")
    return root


def _counting_stub():
    """Distinct question text per call — two personas that generate byte-identical
    text are two that are not pulling apart, and the second is dropped (§6.10)."""
    n = {"i": 0}

    def gen(kind, packet, packets, cfg, rng):
        n["i"] += 1
        return GeneratedItem(
            kind=kind,
            question=f"Question {n['i']}?",
            expected_answer="answer",
            source_packet_ids=[packet.id],
        )

    return gen


def _run(tmp_path: Path, personas, counts, cfg=None, gen=None):
    kb = _kb(tmp_path / "kb")
    cfg = cfg or EvalGenConfig()
    cfg.log.file_path = str(tmp_path / "evalgen.log")
    # A unique name per test: `build_logger` reuses a logger's existing file
    # handler, so a shared name would keep writing to the first test's tmp_path.
    logger = build_logger(cfg.log, name=f"test.evalgen.personas.{tmp_path.name}")
    out = tmp_path / "eval.csv"
    stats = run_evalgen(
        EvalGenRequest(kb_root=kb, out=out, counts=counts, seed=7, personas=personas),
        cfg,
        logger,
        generator_override=gen or _counting_stub(),
    )
    with out.open(encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    return stats, rows


def _no_counts(**kw):
    base = {"simple": 0, "medium": 0, "complex": 0, "hard-1": 0, "hard-2": 0}
    base.update(kw)
    return base


def test_round_robin_partitions_the_counts_and_interleaves(tmp_path: Path) -> None:
    """The totals do not change — they are dealt across the roster, inside each
    kind, so every persona is represented in every kind."""
    personas = [
        Persona(id="hr", name="HR", description=HR),
        Persona(id="cand", name="Candidate", description=CANDIDATE),
    ]
    stats, rows = _run(tmp_path, personas, _no_counts(simple=4, medium=2))

    assert stats.total_written == 6
    assert rows[0] == COLUMNS
    col = COLUMNS.index("persona")
    assert [r[col] for r in rows[1:]] == ["hr", "cand", "hr", "cand", "hr", "cand"]
    assert stats.generated_by_persona == {"hr": 3, "cand": 3}


def test_an_uneven_split_favours_the_earlier_rows(tmp_path: Path) -> None:
    personas = [
        Persona(id="a", name="A", description=HR),
        Persona(id="b", name="B", description=HR),
        Persona(id="c", name="C", description=HR),
    ]
    stats, _ = _run(tmp_path, personas, _no_counts(simple=5))
    assert stats.generated_by_persona == {"a": 2, "b": 2, "c": 1}


def test_persona_free_leaves_the_column_empty(tmp_path: Path) -> None:
    """Not a missing value: eval sets generated before a roster existed stay
    comparable with ones generated after it."""
    stats, rows = _run(tmp_path, [], _no_counts(simple=2))
    col = COLUMNS.index("persona")
    assert [r[col] for r in rows[1:]] == ["", ""]
    assert stats.generated_by_persona == {}


def test_matched_allocation_shares_one_grounding_across_the_roster(tmp_path: Path) -> None:
    """Requested counts are read as per-persona, and each sampled grounding is
    put to every persona in turn."""
    cfg = EvalGenConfig()
    cfg.personas.allocation = "matched"
    personas = [
        Persona(id="hr", name="HR", description=HR),
        Persona(id="cand", name="Candidate", description=CANDIDATE),
    ]
    stats, rows = _run(tmp_path, personas, _no_counts(simple=2), cfg=cfg)

    assert stats.total_written == 4  # 2 groups x 2 personas
    col = COLUMNS.index("persona")
    assert [r[col] for r in rows[1:]] == ["hr", "cand", "hr", "cand"]
    # Same grounding inside a group, a different one between groups.
    src = COLUMNS.index("source")
    grounding = [r[src] for r in rows[1:]]
    assert grounding[0] == grounding[1]
    assert grounding[2] == grounding[3]


def test_topics_bias_sampling_towards_the_persona_subtree(tmp_path: Path) -> None:
    seen: list[str] = []

    def gen(kind, packet, packets, cfg, rng):
        seen.append(packet.id)
        return GeneratedItem(
            kind=kind, question=f"Q{len(seen)}?", expected_answer="a",
            source_packet_ids=[packet.id],
        )

    personas = [Persona(id="hr", name="HR", description=HR, topics=["levies"])]
    _run(tmp_path, personas, _no_counts(simple=4), gen=gen)
    assert seen and all(p.startswith("levies.") for p in seen)


def test_topics_that_match_nothing_warn_and_fall_back(tmp_path: Path) -> None:
    """A bias, not a filter — one mistyped prefix would otherwise turn a persona
    into a silently empty one."""
    personas = [Persona(id="hr", name="HR", description=HR, topics=["typo"])]
    stats, rows = _run(tmp_path, personas, _no_counts(simple=2))
    assert stats.generated["simple"] == 2
    assert stats.warnings >= 1
    log = (tmp_path / "evalgen.log").read_text(encoding="utf-8")
    assert "topics_unmatched" in log


def test_a_mismatch_resamples_and_is_dropped_with_its_own_reason(tmp_path: Path) -> None:
    """Retrying the same packet fails the same way, so the content is resampled
    and the persona held fixed."""
    seen: list[str] = []

    def always_mismatch(kind, packet, packets, cfg, rng):
        seen.append(packet.id)
        raise PersonaMismatch("nothing here this role would ask")

    personas = [Persona(id="cand", name="Candidate", description=CANDIDATE)]
    stats, rows = _run(tmp_path, personas, _no_counts(simple=1), gen=always_mismatch)

    assert stats.total_written == 0
    assert stats.dropped_by_persona == {"cand": 1}
    # One attempt plus max_retries_per_item, each against a fresh sample.
    assert len(seen) == 3
    log = (tmp_path / "evalgen.log").read_text(encoding="utf-8")
    assert "persona_content_mismatch" in log


def test_exam_framing_retries_the_same_packet(tmp_path: Path) -> None:
    """A phrasing failure on content that does fit — worth another try where a
    mismatch is not."""
    seen: list[str] = []

    def always_exam(kind, packet, packets, cfg, rng):
        seen.append(packet.id)
        raise ExamFraming("question references the source")

    personas = [Persona(id="hr", name="HR", description=HR)]
    stats, _ = _run(tmp_path, personas, _no_counts(simple=1), gen=always_exam)

    assert stats.total_written == 0
    assert len(set(seen)) == 1
    log = (tmp_path / "evalgen.log").read_text(encoding="utf-8")
    assert "exam_framing" in log


def test_the_roster_is_echoed_in_the_run_log(tmp_path: Path) -> None:
    """A roster silently short by a row still writes a full CSV, and nothing in
    the output says so (§6.2.2)."""
    personas = [Persona(id="hr", name="HR", description=HR)]
    _run(tmp_path, personas, _no_counts(simple=1))
    log = (tmp_path / "evalgen.log").read_text(encoding="utf-8")
    assert '"personas": ["hr"]' in log
    assert '"allocation": "round-robin"' in log
