"""Live progress for `evalrun` (§7.11.1).

The failure this prevents: a 30-minute run that prints nothing until it ends,
where a slow eval and a wedged one look identical and there is no basis for
deciding whether to wait.
"""

from __future__ import annotations

import io
import json
import os
import contextlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hcag.eval.progress import PROGRESS_ENV, ProgressReporter, emit


def _reporter(path: Path, total: int, tty: bool = True, **kw) -> ProgressReporter:
    r = ProgressReporter(path, total=total, **kw)
    r._tty = tty
    return r


def _render(r: ProgressReporter, force: bool = True) -> str:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        r.poll()
        r.render(force=force)
    return err.getvalue()


def test_progress_reflects_rows_as_workers_report_them(tmp_path, monkeypatch) -> None:
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=4)

    emit({"event": "row.done", "question_id": "q1", "kind": "simple", "score": 3})
    emit({"event": "row.done", "question_id": "q2", "kind": "medium", "score": 1})

    out = _render(r)
    assert "2/4 rows" in out
    assert "mean 2.00" in out
    assert "q2" in out


def test_unscored_rows_are_counted_separately(tmp_path, monkeypatch) -> None:
    """A judge failure is not a score of 0 — averaging it in would be a lie."""
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=2)

    emit({"event": "row.done", "question_id": "q1", "kind": "simple", "score": 3})
    emit({"event": "row.done", "question_id": "q2", "kind": "simple", "score": None})

    out = _render(r)
    assert "2/2 rows" in out and "1 unscored" in out and "mean 3.00" in out
    assert r.state.mean_score == 3.0


def test_eta_appears_only_while_work_remains(tmp_path, monkeypatch) -> None:
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=2)

    emit({"event": "row.done", "question_id": "q1", "kind": "simple", "score": 2})
    assert "left" in _render(r)

    emit({"event": "row.done", "question_id": "q2", "kind": "simple", "score": 2})
    assert "left" not in _render(r)


def test_concurrent_workers_do_not_tear_each_others_lines(tmp_path, monkeypatch) -> None:
    """promptfoo fans out; every worker appends to one file."""
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))

    def worker(i: int) -> None:
        emit({"event": "row.done", "question_id": f"q{i}", "kind": "simple",
              "score": i % 4, "remark": "x" * 200})

    with ThreadPoolExecutor(max_workers=16) as ex:
        list(ex.map(worker, range(200)))

    lines = [l for l in p.read_text().splitlines() if l.strip()]
    assert len(lines) == 200
    for line in lines:
        json.loads(line)          # every line is intact JSON

    r = _reporter(p, total=200)
    r.poll()
    assert r.state.done == 200


def test_emit_never_raises_when_the_channel_is_broken(tmp_path, monkeypatch) -> None:
    """A row that scored must not fail because it could not announce it."""
    monkeypatch.setenv(PROGRESS_ENV, str(tmp_path / "no-such-dir" / "p.jsonl"))
    emit({"event": "row.done", "question_id": "q1", "score": 3})   # must not raise

    monkeypatch.delenv(PROGRESS_ENV, raising=False)
    emit({"event": "row.done", "question_id": "q1", "score": 3})   # unset: no-op


def test_polling_is_incremental(tmp_path, monkeypatch) -> None:
    """Each poll consumes only what is new — no re-counting from the top."""
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=3)

    emit({"event": "row.done", "question_id": "q1", "score": 3})
    r.poll()
    r.poll()                                   # second poll sees nothing new
    assert r.state.done == 1

    emit({"event": "row.done", "question_id": "q2", "score": 3})
    r.poll()
    assert r.state.done == 2


def test_quiet_writes_nothing(tmp_path, monkeypatch) -> None:
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=1, quiet=True)
    emit({"event": "row.done", "question_id": "q1", "score": 3})
    assert _render(r) == ""


def test_non_tty_output_has_no_carriage_returns(tmp_path, monkeypatch) -> None:
    """A \\r into a log file makes one unreadable mega-line."""
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=1, tty=False)
    emit({"event": "row.done", "question_id": "q1", "score": 3})
    out = _render(r)
    assert "\r" not in out and "\033" not in out
    assert "1/1 rows" in out


def test_before_the_first_result_it_says_so(tmp_path) -> None:
    """0/N with an ETA computed from no data would be noise."""
    p = tmp_path / "progress.jsonl"
    p.touch()
    r = _reporter(p, total=50)
    out = _render(r, force=False)
    assert "waiting for the first result" in out and "50 row(s)" in out


def test_unrelated_events_are_ignored(tmp_path, monkeypatch) -> None:
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=1)
    emit({"event": "something.else", "question_id": "q1"})
    r.poll()
    assert r.state.done == 0


def test_every_row_is_handed_to_the_logger(tmp_path, monkeypatch) -> None:
    """The single-line display stays readable only because the detail is kept
    somewhere — the log. Both come from the same events (§7.11.1)."""
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))

    seen: list[dict] = []
    r = _reporter(p, total=2, on_row=seen.append)

    emit({"event": "row.done", "question_id": "q1", "kind": "simple",
          "score": 3, "turns": 1, "elapsed_ms": 120})
    emit({"event": "other", "question_id": "q2"})
    r.poll()

    assert [e["question_id"] for e in seen] == ["q1"]
    assert seen[0]["score"] == 3 and seen[0]["elapsed_ms"] == 120


def test_the_callback_is_optional(tmp_path, monkeypatch) -> None:
    p = tmp_path / "progress.jsonl"
    p.touch()
    monkeypatch.setenv(PROGRESS_ENV, str(p))
    r = _reporter(p, total=1)          # no on_row
    emit({"event": "row.done", "question_id": "q1", "score": 3})
    r.poll()
    assert r.state.done == 1
