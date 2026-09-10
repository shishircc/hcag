"""The persona roster — who the generated questions are asked by (§6.2.3).

A CSV, not a config format: the roster is a table of short records with one
long prose field, authored by whoever knows the audience — a policy, service
or support team — rather than by whoever maintains the prompts. Handing those
people a spreadsheet is the difference between a roster that gets revised when
the audience is better understood and one that is written once and forgotten.

Every failure here is fatal at startup (§6.9). The roster shapes every row of
the output, so there is no degraded mode worth continuing into: a run that
silently lost a persona to a stray quote still writes a full CSV, and nothing
downstream can tell it apart from one the author asked for.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path


ID_COLUMN = "persona_id"
NAME_COLUMN = "persona_name"
DESCRIPTION_COLUMN = "persona_description"
TOPICS_COLUMN = "topics"

REQUIRED_COLUMNS = [ID_COLUMN, NAME_COLUMN, DESCRIPTION_COLUMN]

#: Ids are the comparability key between eval sets (§6.7.2), so they are kept
#: to characters that survive a CSV cell, a CLI flag and a filename unchanged.
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

#: `topics` is space-separated so a cell needs no inner quoting (§6.2.3), but a
#: comma is what people type. Both split the same way — a taxonomy id contains
#: neither.
_TOPIC_SPLIT_RE = re.compile(r"[\s,]+")


class PersonaError(RuntimeError):
    """The roster could not be read or is malformed. Always fatal at startup."""


@dataclass(frozen=True)
class Persona:
    """One role, as the generator sees it."""

    id: str
    name: str
    description: str
    #: Packet-id prefixes biasing sampling toward the part of the taxonomy this
    #: role lives in (§6.4.6). A *bias*, never a filter: a mistyped prefix costs
    #: retries, it does not silently empty the persona.
    topics: list[str] = field(default_factory=list)

    def matches(self, packet_id: str) -> bool:
        """Whether a packet falls under one of this persona's topics."""
        return any(
            packet_id == t or packet_id.startswith(t + ".") for t in self.topics
        )


def load_personas(path: Path) -> list[Persona]:
    """Read and validate a persona CSV. Raises `PersonaError`.

    Columns are matched by header name, so order does not matter and extra
    columns — an owner, a review date — are ignored rather than rejected.
    """
    try:
        # utf-8-sig for the same reason the eval CSV uses it: a roster written
        # in Excel or Sheets carries a BOM, which under plain utf-8 glues
        # itself to `persona_id` and makes a fine file look like it is missing
        # its first column. `newline=""` is what lets a multi-paragraph
        # description live in one quoted cell.
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            header = reader.fieldnames or []
            missing = [c for c in REQUIRED_COLUMNS if c not in header]
            if missing:
                raise PersonaError(
                    f"persona file {path} is missing required column(s): "
                    f"{', '.join(missing)}. Found: {header or '(no header row)'}"
                )
            rows = list(reader)
    except OSError as e:
        raise PersonaError(f"persona file {path} could not be read: {e}") from e

    personas: list[Persona] = []
    seen: dict[str, int] = {}
    for line, raw in enumerate(rows, start=2):  # line 1 is the header
        pid = (raw.get(ID_COLUMN) or "").strip()
        name = (raw.get(NAME_COLUMN) or "").strip()
        description = (raw.get(DESCRIPTION_COLUMN) or "").strip()

        if not pid and not name and not description:
            continue  # a trailing blank line, not a persona

        if not pid:
            raise PersonaError(f"persona file {path} line {line}: blank {ID_COLUMN}")
        if not _ID_RE.match(pid):
            raise PersonaError(
                f"persona file {path} line {line}: {ID_COLUMN}={pid!r} — ids are "
                "lowercase letters, digits, '-' and '_', starting with a letter "
                "or digit"
            )
        if not description:
            # A role with no description renders a framing that says nothing,
            # and every question generated under it is persona-free while the
            # `persona` column claims otherwise.
            raise PersonaError(
                f"persona file {path} line {line} ({pid}): blank "
                f"{DESCRIPTION_COLUMN}"
            )
        if pid in seen:
            raise PersonaError(
                f"persona file {path} line {line}: duplicate {ID_COLUMN}={pid!r} "
                f"(first seen on line {seen[pid]}) — the `persona` column would "
                "be ambiguous for every row either one generated"
            )
        seen[pid] = line

        topics = [t for t in _TOPIC_SPLIT_RE.split(raw.get(TOPICS_COLUMN) or "") if t]
        # A blank name falls back to the id rather than failing: the name is a
        # display label, and refusing to run over one is a worse trade than
        # showing an id in the report.
        personas.append(Persona(id=pid, name=name or pid, description=description, topics=topics))

    if not personas:
        raise PersonaError(
            f"persona file {path} declares no personas. An empty roster is a "
            "mis-authored file, not a request for persona-free generation — "
            "omit --personas for that."
        )
    return personas


def select(personas: list[Persona], ids: list[str]) -> list[Persona]:
    """Narrow the roster to `ids` (the `--persona` flag), preserving row order.

    Row order is preserved rather than the order the flags were typed, because
    allocation is positional (§6.5.1) and the roster is the authority on it.
    """
    if not ids:
        return personas
    known = {p.id for p in personas}
    unknown = [i for i in ids if i not in known]
    if unknown:
        raise PersonaError(
            f"--persona {', '.join(unknown)} not in the roster. "
            f"Present: {', '.join(p.id for p in personas)}"
        )
    wanted = set(ids)
    return [p for p in personas if p.id in wanted]
