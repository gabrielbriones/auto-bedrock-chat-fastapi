"""Shared SQL semantics for a KB document's effective public source type.

Both KB backends use the same CASE rules when aggregating sources. A CLI
``type: local`` source still describes an input path, but stored documents
from that path have public type ``file``. Older documents storing ``local``
are normalized on read without a data migration. Synthesized feedback
documents identify themselves by source name and lack source_type metadata.
"""

from typing import Literal


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


def source_groups_cte_sql(backend: Literal["sqlite", "postgres"], *, include_chunks: bool) -> str:
    """Shared grouped-source query for both list and unpaginated count.

    The filter marker is bound to the requested type (or NULL). Grouping
    always occurs *before* filtering, so returned counts describe the entire
    name, including documents of other types. Chunk counts are pre-aggregated
    per document to avoid inflating document counts on the join.
    """
    marker = "?" if backend == "sqlite" else "%s"
    char_function = "CHAR" if backend == "sqlite" else "CHR"
    # The default TRIM only removes spaces; a tab/newline-only source name
    # must not consume a page or appear in the total either.
    whitespace = "' ' || " + " || ".join(f"{char_function}({n})" for n in (9, 10, 11, 12, 13))
    chunk_cte = (
        ", chunk_counts AS (SELECT document_id, COUNT(*) AS chunk_count FROM chunks GROUP BY document_id)"
        if include_chunks
        else ""
    )
    chunk_join = "LEFT JOIN chunk_counts cc ON cc.document_id = td.id" if include_chunks else ""
    chunk_column = ", COALESCE(SUM(cc.chunk_count), 0) AS chunk_count" if include_chunks else ""
    return f"""
        WITH typed_documents AS (
            SELECT d.id, d.source, d.created_at,
                   {effective_source_type_sql(backend)} AS effective_type
            FROM documents d
                 WHERE d.source IS NOT NULL AND TRIM(d.source, {whitespace}) <> ''
        ){chunk_cte}, source_groups AS (
            SELECT td.source, COUNT(*) AS document_count{chunk_column},
                   MAX(td.created_at) AS last_created_at,
                   CASE WHEN COUNT(td.effective_type) = COUNT(*)
                             AND MIN(td.effective_type) = MAX(td.effective_type)
                        THEN MIN(td.effective_type) ELSE NULL END AS source_type,
                   MAX(CASE WHEN td.effective_type = {marker} THEN 1 ELSE 0 END) AS matches_filter
            FROM typed_documents td
            {chunk_join}
            GROUP BY td.source
        )
    """
