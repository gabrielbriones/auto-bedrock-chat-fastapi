"""Shared SQL semantics for a KB document's effective public source type.

Both KB backends use the same CASE rules when aggregating sources. A CLI
``type: local`` source still describes an input path, but stored documents
from that path have public type ``file``. Older documents storing ``local``
are normalized on read without a data migration. Synthesized feedback
documents identify themselves by source name and lack source_type metadata.
"""

from datetime import datetime, timezone
from typing import Literal, Optional, Union

# Python's ``str.isspace()`` whitespace repertoire (CPython's
# ``Lib/test/test_unicode.py``). The route used to reject whitespace-only
# names (including NBSP and other non-ASCII space separators) via a
# Python-side ``.strip()`` before that check moved into this SQL.
_UNICODE_WHITESPACE_CODEPOINTS = (
    0x09,
    0x0A,
    0x0B,
    0x0C,
    0x0D,
    0x1C,
    0x1D,
    0x1E,
    0x1F,
    0x20,
    0x85,
    0xA0,
    0x1680,
    0x2000,
    0x2001,
    0x2002,
    0x2003,
    0x2004,
    0x2005,
    0x2006,
    0x2007,
    0x2008,
    0x2009,
    0x200A,
    0x2028,
    0x2029,
    0x202F,
    0x205F,
    0x3000,
)


def effective_source_type_sql(
    backend: Literal["sqlite", "postgres"],
    *,
    source_column: str = "d.source",
    metadata_column: str = "d.metadata",
) -> str:
    """Build the effective-type expression for trusted internal SQL columns.

    Unknown and missing types remain NULL, except an untyped synthesized
    ``source='feedback'`` document, which is classified as feedback. Callers
    must use fixed column identifiers, not values supplied by HTTP clients.
    """
    if backend == "sqlite":
        raw = f"json_extract({metadata_column}, '$.source_type')"
    elif backend == "postgres":
        raw = f"({metadata_column}::jsonb ->> 'source_type')"
    else:
        raise ValueError(f"Unsupported KB backend: {backend}")

    return (
        "CASE "
        f"WHEN {raw} IN ('web', 'file', 'feedback') THEN {raw} "
        f"WHEN {raw} = 'local' THEN 'file' "
        f"WHEN {source_column} = 'feedback' AND {raw} IS NULL THEN 'feedback' "
        "ELSE NULL END"
    )


def coerce_utc_datetime(value: Optional[Union[str, datetime]]) -> Optional[datetime]:
    """Normalize a backend's ``last_created_at`` value to an aware UTC datetime.

    SQLite's ``TIMESTAMP`` columns come back as a naive UTC string (its
    ``CURRENT_TIMESTAMP`` default); PostgreSQL's ``TIMESTAMPTZ`` columns come
    back as an aware ``datetime`` already in UTC. Either way, a naive value is
    treated as UTC rather than left to be misread as local time downstream
    (e.g. a browser's ``Date.parse`` on an offset-less ISO string).
    """
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value)
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _nonblank_name_sql(backend: Literal["sqlite", "postgres"], source_column: str) -> str:
    """A name predicate rejecting NULL and any Unicode-whitespace-only value."""
    char_function = "CHAR" if backend == "sqlite" else "CHR"
    whitespace = " || ".join(f"{char_function}({n})" for n in _UNICODE_WHITESPACE_CODEPOINTS)
    return f"{source_column} IS NOT NULL AND TRIM({source_column}, {whitespace}) <> ''"


def source_groups_cte_sql(backend: Literal["sqlite", "postgres"]) -> str:
    """Shared grouped-source query for both list and unpaginated count.

    The filter marker is bound to the requested type (or NULL). Grouping
    always occurs *before* filtering, so returned counts describe the entire
    name, including documents of other types. Chunk totals are intentionally
    excluded here: joining ``chunks`` would scan it for every name on every
    call, even though a page returns at most ``_LIMIT_MAX`` names. Callers
    fetch chunk totals separately, scoped to the already-paginated page of
    names (see :func:`chunk_counts_sql`).
    """
    marker = "?" if backend == "sqlite" else "%s"
    return f"""
        WITH typed_documents AS (
            SELECT d.id, d.source, d.created_at,
                   {effective_source_type_sql(backend)} AS effective_type
            FROM documents d
                 WHERE {_nonblank_name_sql(backend, "d.source")}
        ), source_groups AS (
            SELECT td.source, COUNT(*) AS document_count,
                   MAX(td.created_at) AS last_created_at,
                   CASE WHEN COUNT(td.effective_type) = COUNT(*)
                             AND MIN(td.effective_type) = MAX(td.effective_type)
                        THEN MIN(td.effective_type) ELSE NULL END AS source_type,
                   MAX(CASE WHEN td.effective_type = {marker} THEN 1 ELSE 0 END) AS matches_filter
            FROM typed_documents td
            GROUP BY td.source
        )
    """


def chunk_counts_sql(backend: Literal["sqlite", "postgres"], *, name_count: int) -> str:
    """Chunk totals for exactly ``name_count`` bound source names.

    Scoped to a known page of names (bound as parameters, in the same order)
    so this never scans ``chunks`` for the whole knowledge base — only for
    documents under the page already selected via :func:`source_groups_cte_sql`.
    """
    marker = "?" if backend == "sqlite" else "%s"
    placeholders = ", ".join([marker] * name_count)
    return f"""
        SELECT d.source, COUNT(*) AS chunk_count
        FROM chunks c
        JOIN documents d ON d.id = c.document_id
        WHERE d.source IN ({placeholders})
        GROUP BY d.source
    """
