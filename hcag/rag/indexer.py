"""LanceDB integration — table create / open / upsert / index refresh (§8.4.5, §8.5).

Only this module imports ``lancedb`` / ``pyarrow``, so callers can inspect
config without pulling the whole storage stack into memory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import kb_schema, manifest_schema


MANIFEST_TABLE = "manifest"


class Index:
    """Thin façade over a LanceDB connection + the (kb, manifest) tables."""

    def __init__(self, index_dir: Path, table: str, vector_dim: int):
        self.index_dir = Path(index_dir)
        self.table = table
        self.vector_dim = vector_dim
        self._db = None
        self._kb = None
        self._manifest = None

    # --- lifecycle --------------------------------------------------------

    def connect(self) -> None:
        import lancedb

        self.index_dir.mkdir(parents=True, exist_ok=True)
        self._db = lancedb.connect(str(self.index_dir))

        names = set(self._db.table_names())
        if self.table not in names:
            self._kb = self._db.create_table(self.table, schema=kb_schema(self.vector_dim))
        else:
            self._kb = self._db.open_table(self.table)

        if MANIFEST_TABLE not in names:
            self._manifest = self._db.create_table(MANIFEST_TABLE, schema=manifest_schema())
        else:
            self._manifest = self._db.open_table(MANIFEST_TABLE)

    def drop(self) -> None:
        """Drop both tables — used by ``--recreate``."""
        import lancedb

        self.index_dir.mkdir(parents=True, exist_ok=True)
        db = lancedb.connect(str(self.index_dir))
        for t in (self.table, MANIFEST_TABLE):
            try:
                db.drop_table(t)
            except Exception:  # noqa: BLE001 — LanceDB raises different types across versions
                pass

    # --- reads ------------------------------------------------------------

    def load_manifest(self) -> list[dict[str, Any]]:
        """Return every manifest row as a plain dict.

        Uses the pyarrow table interface — pandas is optional and shouldn't be
        a required install for the `rag` extras.
        """
        if self._manifest is None:
            return []
        try:
            arrow_tbl = self._manifest.to_arrow() if hasattr(self._manifest, "to_arrow") else self._manifest.to_lance().to_table()
            return arrow_tbl.to_pylist()
        except Exception:
            return []

    # --- writes -----------------------------------------------------------

    def delete_file_rows(self, kb_path: str) -> None:
        """Remove all chunk rows for a given source file."""
        if self._kb is None:
            return
        escaped = kb_path.replace("'", "''")
        self._kb.delete(f"kb_path = '{escaped}'")

    def add_chunks(self, rows: list[dict[str, Any]]) -> None:
        if not rows or self._kb is None:
            return
        self._kb.add(rows)

    def upsert_manifest(self, entry: dict[str, Any]) -> None:
        if self._manifest is None:
            return
        escaped = entry["kb_path"].replace("'", "''")
        self._manifest.delete(f"kb_path = '{escaped}'")
        self._manifest.add([entry])

    # --- indexes ----------------------------------------------------------

    def refresh_indexes(self) -> None:
        """(Re-)create vector + FTS indexes so downstream queries see the current
        table state. Both operations are safe no-ops on an empty table.
        """
        if self._kb is None:
            return
        # Vector index — LanceDB picks IVF-PQ parameters from the row count.
        try:
            self._kb.create_index(vector_column_name="vector", replace=True)
        except TypeError:
            # Older LanceDB signature.
            try:
                self._kb.create_index()
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        # FTS index for keyword / hybrid search (§8.6).
        try:
            self._kb.create_fts_index("text", replace=True)
        except TypeError:
            try:
                self._kb.create_fts_index("text")
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
