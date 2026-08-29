"""LanceDB schema definitions for the `kb` and `manifest` tables (§8.5).

The schema is pinned at table-creation time. `vector`'s dimension is
discovered from the first embedding response (§8.4.4) and passed in here.
"""

from __future__ import annotations

# pyarrow is only pulled in when someone actually constructs a schema —
# so `rag --help` and config inspection don't require the [rag] extras.

KB_COLUMNS = [
    "id",
    "kb_path",
    "chunk_index",
    "source_kind",
    "text",
    "vector",
    "char_start",
    "char_end",
    "headings",
    "image_path",
    "token_estimate",
    "content_hash",
    "metadata",
    "indexed_at",
]

MANIFEST_COLUMNS = [
    "kb_path",
    "content_hash",
    "bytes",
    "mtime",
    "chunk_count",
    "source_kind",
    "indexed_at",
]


def kb_schema(vector_dim: int):
    import pyarrow as pa

    return pa.schema(
        [
            ("id", pa.string()),
            ("kb_path", pa.string()),
            ("chunk_index", pa.int32()),
            ("source_kind", pa.string()),
            ("text", pa.string()),
            ("vector", pa.list_(pa.float32(), vector_dim)),
            ("char_start", pa.int64()),
            ("char_end", pa.int64()),
            ("headings", pa.list_(pa.string())),
            ("image_path", pa.string()),
            ("token_estimate", pa.int32()),
            ("content_hash", pa.string()),
            ("metadata", pa.string()),
            ("indexed_at", pa.timestamp("us", tz="UTC")),
        ]
    )


def manifest_schema():
    import pyarrow as pa

    return pa.schema(
        [
            ("kb_path", pa.string()),
            ("content_hash", pa.string()),
            ("bytes", pa.int64()),
            ("mtime", pa.float64()),
            ("chunk_count", pa.int32()),
            ("source_kind", pa.string()),
            ("indexed_at", pa.timestamp("us", tz="UTC")),
        ]
    )
