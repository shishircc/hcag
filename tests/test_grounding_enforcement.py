"""An answer cannot leave the runtime with nothing loaded (D3b, §2.7.2).

D3b — the catalog routes, only packet content answers — was enforced by prompt
wording at three points and by nothing else. Wording loses to a catalog that
reads like a reference document: a row stating a deadline *is* the answer,
sitting in the system prompt, and a model that uses it is behaving reasonably
given what it was shown.

Observed: asked how soon a ONE Pass holder must report a new address, the agent
answered "within 2 weeks, via the EP eService" without a single load. Both facts
were in the catalog row. Neither the penalty for missing the deadline nor the
requirement that the new address meet housing rules was, so the user got an
answer that sounded complete and left out the part that would have changed what
they did.

This is the half of the rule that is mechanically checkable: with an empty
active set there is no packet content in the conversation at all, so no
judgement about what the model concluded is needed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from hcag.config import AgentConfig
from hcag.runtime.llm import LLMResponse, ToolCall
from hcag.runtime.agent import AgentRuntime


ROOT = """<!-- HCAG:COMPILED id=_root -->
---
id: ''
title: KB
token_size_estimate: 40
kind: node
source_files: []
children: [onepass]
---

# KB

<!-- HCAG:CATALOG BEGIN -->
## Catalog

| id | path | depth | title | long |
|---|---|---|---|---|
| `onepass` | `onepass/` | 1 | ONE Pass: notify MOM of updates | Defines the requirement to update residential address or mobile number within 2 weeks, via EP eService. |
<!-- HCAG:CATALOG END -->
"""

LEAF = """<!-- HCAG:COMPILED id=onepass -->
---
id: onepass
title: ONE Pass notifications
long_description: Notification duties.
token_size_estimate: 40
content_token_estimate: 40
kind: leaf
source_files: [n.md]
children: []
---

# ONE Pass notifications

<!-- HCAG:CONTENT BEGIN -->
## Content

You must update us within 2 weeks of changing your residential address.
Otherwise, you may be penalised. The new address must meet housing requirements.
<!-- HCAG:CONTENT END -->
"""

UNGROUNDED = "You need to update your address within 2 weeks via the EP eService."
GROUNDED = "Within 2 weeks, and the new address must meet housing requirements."


class _Scripted:
    """Replies in order, one per model round trip."""

    def __init__(self, *replies: LLMResponse) -> None:
        self.replies = list(replies)
        self.calls = 0

    def chat(self, messages, tools=None):  # noqa: ARG002
        self.calls += 1
        return self.replies[min(self.calls - 1, len(self.replies) - 1)]


def _answer(text: str) -> LLMResponse:
    return LLMResponse(text=text, tool_calls=[], model="m")


def _load(ids: list[str]) -> LLMResponse:
    return LLMResponse(
        text="",
        tool_calls=[
            ToolCall(
                id="c1",
                name="check_and_load_kb",
                arguments={"context": "address", "requested_packet_ids": ids},
            )
        ],
        model="m",
    )


@pytest.fixture
def runtime(tmp_path: Path):
    def _make(llm):
        (tmp_path / "compiled.md").write_text(ROOT, encoding="utf-8")
        leaf = tmp_path / "onepass"
        leaf.mkdir(exist_ok=True)
        (leaf / "compiled.md").write_text(LEAF, encoding="utf-8")
        cfg = AgentConfig(kb_root=str(tmp_path))
        cfg.observability.log.file_path = str(tmp_path / "a.log")
        return AgentRuntime(cfg=cfg, llm=llm, session_id="s1")

    return _make


def _read_log() -> str:
    """Everything the runtime has logged this session.

    Read from the handler's own path rather than a per-test one: `build_logger`
    reuses a logger's existing file handler, so every runtime in this process
    writes to whichever tmp_path came first. The logger also sets
    `propagate = False`, which puts the records out of caplog's reach.
    """
    import logging.handlers

    logger = logging.getLogger("hcag.runtime")
    for h in logger.handlers:
        if isinstance(h, logging.handlers.RotatingFileHandler):
            h.flush()
            return Path(h.baseFilename).read_text(encoding="utf-8")
    return ""


@pytest.fixture
def new_events():
    """Only what this test logged.

    The log file is shared across tests (see `_read_log`), so a negative
    assertion has to look at the tail rather than the whole file.
    """
    start = len(_read_log())
    return lambda: _read_log()[start:]


# --- The rule ---------------------------------------------------------------


def test_an_answer_with_nothing_loaded_is_sent_back(runtime, new_events) -> None:
    llm = _Scripted(_answer(UNGROUNDED), _load(["onepass"]), _answer(GROUNDED))
    assert runtime(llm).run_turn("how soon must I report a new address?") == GROUNDED
    assert llm.calls == 3
    assert "agent.answer.withheld" in new_events()


def test_the_withheld_draft_never_reaches_the_client(runtime, tmp_path: Path) -> None:
    """Withheld text is buffered, not streamed: a user must never watch an
    ungrounded answer appear and then be replaced by a different one."""
    llm = _Scripted(_answer(UNGROUNDED), _load(["onepass"]), _answer(GROUNDED))
    events = list(runtime(llm).run_turn_stream("q"))

    streamed = "".join(e.data["text"] for e in events if e.kind == "assistant.delta")
    assert UNGROUNDED not in streamed
    assert streamed == GROUNDED
    assert events[-1].data["text"] == GROUNDED


def test_the_client_is_told_the_turn_went_around_again(runtime, new_events) -> None:
    """Carries no text — the draft is being replaced, not shown. A client's use
    for it is a progress indicator and a debug trail."""
    llm = _Scripted(_answer(UNGROUNDED), _load(["onepass"]), _answer(GROUNDED))
    events = list(runtime(llm).run_turn_stream("q"))

    withheld = [e for e in events if e.kind == "assistant.withheld"]
    assert len(withheld) == 1
    assert "text" not in withheld[0].data
    assert withheld[0].data["reason"] == "no_packet_loaded"


def test_the_model_is_told_why_in_band(runtime, new_events) -> None:
    llm = _Scripted(_answer(UNGROUNDED), _load(["onepass"]), _answer(GROUNDED))
    rt = runtime(llm)
    rt.run_turn("q")

    note = next(m.content for m in rt._history if m.role == "user" and "NOT sent" in (m.content or ""))
    assert "no knowledge packet" in note
    assert "check_and_load_kb" in note
    # And it does not turn a greeting into a refusal.
    assert "greeting" in note


# --- What it must not do ----------------------------------------------------


def test_it_never_fires_when_a_packet_is_loaded(runtime, new_events) -> None:
    """Once content is in the conversation an answer *could* be grounded, and
    deciding whether it actually is would need the semantics this check refuses
    to guess at (§2.7.1 also wants most turns to make no call at all)."""
    llm = _Scripted(_load(["onepass"]), _answer(GROUNDED))
    rt = runtime(llm)
    rt.run_turn("q")
    assert llm.calls == 2

    # Second turn answers from the already-active set, with no call and no nudge.
    assert rt.run_turn("and what if I am late?") == GROUNDED
    assert llm.calls == 3
    assert "agent.answer.withheld" not in new_events()


def test_a_model_that_stands_by_its_answer_is_not_looped_on(runtime, new_events) -> None:
    """A greeting is indistinguishable from a knowledge question here, so the
    second pass is always allowed through. The cost of being wrong is one round
    trip, never a refusal and never a loop."""
    llm = _Scripted(_answer("Hello, how can I help?"))
    assert runtime(llm).run_turn("hi") == "Hello, how can I help?"
    assert llm.calls == 2  # nudged once, then delivered

    events = new_events()
    assert "agent.answer.withheld" in events
    # Recorded, because from here it is indistinguishable from the real failure.
    assert "agent.answer.ungrounded" in events


def test_an_empty_reply_is_not_an_answer(runtime, new_events) -> None:
    """A model emitting nothing is not answering, and nudging it would spend a
    round trip to say so."""
    llm = _Scripted(_answer(""), _answer(""))
    runtime(llm).run_turn("q")
    assert llm.calls == 1
    assert "agent.answer.withheld" not in new_events()


# --- Check 2: figures no loaded packet contains (§2.7.2) --------------------


PARTIAL = "The fee is $105 and processing takes 10 business days."


def test_a_figure_no_loaded_packet_contains_is_sent_back(runtime, new_events) -> None:
    """Reaches the turn that loaded *something* and answered partly from a row
    it did not load — the case an empty-active-set check cannot see."""
    llm = _Scripted(_load(["onepass"]), _answer(PARTIAL), _answer(GROUNDED))
    assert runtime(llm).run_turn("what does it cost?") == GROUNDED

    log = new_events()
    assert "agent.answer.withheld" in log
    assert "unsupported_figures" in log
    assert "105" in log


def test_a_figure_the_loaded_packet_contains_is_delivered(runtime, new_events) -> None:
    llm = _Scripted(_load(["onepass"]), _answer("You have 2 weeks to tell us."))
    assert runtime(llm).run_turn("how long?") == "You have 2 weeks to tell us."
    assert llm.calls == 2
    assert "agent.answer.withheld" not in new_events()


def test_the_users_own_figures_are_not_treated_as_claims(runtime, new_events) -> None:
    """Their salary quoted back to them is not a claim about the guidance."""
    llm = _Scripted(_load(["onepass"]), _answer("Your S$31,500 offer is above the bar."))
    rt = runtime(llm)
    assert rt.run_turn("I earn S$31,500 a month — is that enough?").startswith("Your")
    assert "agent.answer.withheld" not in new_events()


def test_formatting_differences_are_not_unsupported(runtime, new_events) -> None:
    """`2 weeks` in the packet and `2` in the answer are the same figure; commas
    are separators, not digits."""
    llm = _Scripted(_load(["onepass"]), _answer("Within 2 weeks — see the 2,000 word guide."))
    rt = runtime(llm)
    rt.run_turn("q")
    log = new_events()
    # 2 is in the packet; 2000 is not, so the check fires on the second only.
    assert "unsupported_figures" in log
    assert '"2000"' in log
    assert '"2"' not in log.split("figures")[-1].split("}")[0].replace('"2000"', "")


def test_an_answer_with_no_figures_at_all_is_delivered(runtime, new_events) -> None:
    llm = _Scripted(_load(["onepass"]), _answer("Update your address promptly."))
    runtime(llm).run_turn("q")
    assert "agent.answer.withheld" not in new_events()


def test_the_note_names_the_figures_and_asks_only_for_the_covering_row(runtime) -> None:
    """A note saying "load something" would trade this failure for the
    over-calling one §2.7.1 exists to prevent."""
    llm = _Scripted(_load(["onepass"]), _answer(PARTIAL), _answer(GROUNDED))
    rt = runtime(llm)
    rt.run_turn("q")

    note = next(
        m.content for m in rt._history if m.role == "user" and "NOT sent" in (m.content or "")
    )
    assert "105" in note
    assert "not everything that looks adjacent" in note


def test_it_still_yields_on_the_second_pass(runtime, new_events) -> None:
    """Arithmetic is indistinguishable from invention here, so a model that
    stands by its figures is delivered, once, with a WARN."""
    llm = _Scripted(_load(["onepass"]), _answer(PARTIAL))
    assert runtime(llm).run_turn("q") == PARTIAL

    log = new_events()
    assert "agent.answer.ungrounded" in log


def test_the_note_naming_a_figure_does_not_make_it_supported(runtime, new_events) -> None:
    """The note names the unsupported figures. Read the asker's figures back off
    user-role history and the check passes itself on the next round."""
    llm = _Scripted(_load(["onepass"]), _answer(PARTIAL), _answer(PARTIAL))
    runtime(llm).run_turn("q")

    log = new_events()
    assert log.count("agent.answer.withheld") == 1
    assert "agent.answer.ungrounded" in log  # still unsupported the second time


def test_the_nudge_count_is_reported_on_the_turn(runtime, new_events) -> None:
    """The signal D3b describes, in the one place it can be seen."""
    llm = _Scripted(_answer(UNGROUNDED), _load(["onepass"]), _answer(GROUNDED))
    rt = runtime(llm)
    rt.run_turn("q")
    assert rt._grounding_nudges == 1

    assert '"grounding_nudges": 1' in new_events()
