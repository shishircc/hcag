"""Live progress for `evalrun` (§7.11.1).

The problem this solves: `evalrun` hands the whole run to a promptfoo
subprocess whose output is captured, so a 30-minute run prints nothing until
it is over. There is no way to tell a slow run from a wedged one, and no basis
for deciding whether to wait or kill it.

The parent process cannot see progress directly — promptfoo owns the loop and
fans out to N workers. But *our* provider is what each worker calls, once per
row, and it knows everything worth reporting: which question, which kind, what
the judge said, how long the row took. So the workers report and the parent
renders.

The channel is an append-only JSON-lines file, its path passed to the workers
in an environment variable (the same handoff `HCAG_EVAL_CONFIG_JSON` already
uses). A file rather than a pipe because the workers are processes promptfoo
spawns, not ones we control: we never hold their stdout, and a pipe nobody
drains is a deadlock. One `write()` of a single line under `O_APPEND` is
atomic for the sizes involved, so concurrent workers interleave lines without
tearing them.

Reporting is best-effort on the worker side: a row that scored must never fail
because it could not announce that it scored.
"""

from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: Env var carrying the progress file path from runner to provider.
PROGRESS_ENV = "HCAG_EVAL_PROGRESS"


def emit(event: dict) -> None:
    """Append one progress event. Called from a promptfoo worker process.

    Silent on every failure. This is telemetry about an eval; an eval that
    crashed because it could not write a progress line would be a worse
    outcome than the missing line.
    """
    path = os.environ.get(PROGRESS_ENV, "")
    if not path:
        return
    try:
        line = json.dumps(event, ensure_ascii=False, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:  # noqa: BLE001
        pass


@dataclass
class ProgressState:
    """What has been reported so far."""

    total: int
    done: int = 0
    scores: list[int] = field(default_factory=list)
    failures: int = 0
    last_kind: str = ""
    last_qid: str = ""

    @property
    def mean_score(self) -> float | None:
        return sum(self.scores) / len(self.scores) if self.scores else None


class ProgressReporter:
    """Tails the progress file and renders a one-line status on stderr.

    stderr, and a single rewritten line, for the same reason `crawl` uses it:
    progress is transient status, and stdout belongs to the run's actual
    output. When stderr is not a TTY (CI, a pipe, a log file) the line cannot
    be rewritten, so it is printed as periodic append-only updates instead —
    a carriage return into a log file produces one unreadable mega-line.
    """

    #: Don't reprint an unchanged line more often than this, so a non-TTY log
    #: gets a readable heartbeat rather than a flood.
    NON_TTY_MIN_INTERVAL = 15.0

    def __init__(
        self,
        path: Path,
        total: int,
        quiet: bool = False,
        on_row: "Callable[[dict], None] | None" = None,
    ) -> None:
        self.path = path
        self.state = ProgressState(total=total)
        self.quiet = quiet
        # Each row event is also handed to the caller, which logs it. The
        # aggregate line is for whoever is watching; the per-row record is for
        # whoever reads the log afterwards, and both come from these events —
        # so they cannot disagree about what happened.
        self.on_row = on_row
        self.started = time.monotonic()
        self._offset = 0
        self._last_render = 0.0
        self._tty = sys.stderr.isatty()

    # ---- reading ---------------------------------------------------------

    def poll(self) -> None:
        """Consume whatever workers have appended since the last call."""
        try:
            if not self.path.is_file():
                return
            with self.path.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError:
            return

        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                # A partially-flushed final line; the next poll re-reads it
                # only if the offset did not advance past it, which it did.
                # One dropped progress line is not worth complicating this for.
                continue
            self._apply(event)

    def _apply(self, event: dict) -> None:
        if event.get("event") != "row.done":
            return
        if self.on_row is not None:
            self.on_row(event)
        self.state.done += 1
        self.state.last_qid = str(event.get("question_id") or "")
        self.state.last_kind = str(event.get("kind") or "")
        score = event.get("score")
        if isinstance(score, int) and not isinstance(score, bool):
            self.state.scores.append(score)
        else:
            self.state.failures += 1

    # ---- rendering -------------------------------------------------------

    def render(self, force: bool = False) -> None:
        if self.quiet:
            return
        now = time.monotonic()
        if not self._tty and not force and now - self._last_render < self.NON_TTY_MIN_INTERVAL:
            return
        if self.state.done == 0 and not force:
            # Nothing has finished yet; say so once rather than showing 0/N
            # with a nonsense ETA.
            if self._last_render == 0.0:
                self._write(f"running {self.state.total} row(s) — waiting for the first result")
                self._last_render = now
            return
        self._last_render = now
        self._write(self._line())

    def _line(self) -> str:
        s = self.state
        elapsed = time.monotonic() - self.started
        parts = [f"{s.done}/{s.total} rows"]
        if s.scores:
            parts.append(f"mean {s.mean_score:.2f}")
        if s.failures:
            parts.append(f"{s.failures} unscored")
        parts.append(f"{_dur(elapsed)} elapsed")
        if s.done and s.done < s.total:
            remaining = (elapsed / s.done) * (s.total - s.done)
            parts.append(f"~{_dur(remaining)} left")
        if s.last_qid:
            parts.append(f"last {s.last_qid}")
        return " · ".join(parts)

    def _write(self, text: str) -> None:
        if self._tty:
            # \r + clear-to-end, so a shorter line does not leave debris.
            print(f"\r\033[K{text}", end="", file=sys.stderr, flush=True)
        else:
            print(text, file=sys.stderr, flush=True)

    def finish(self) -> None:
        """Close out the progress line so the next output starts clean."""
        if self.quiet:
            return
        self.poll()
        if self._tty:
            self.render(force=True)
            print("", file=sys.stderr, flush=True)
        else:
            self.render(force=True)


def _dur(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    m, s = divmod(seconds, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


__all__ = ["PROGRESS_ENV", "ProgressReporter", "ProgressState", "emit"]
