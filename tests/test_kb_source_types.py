"""Phase 1 source-type and summary-model contracts (before aggregation rollout)."""

import sqlite3
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from autolangchat.db.kb_source_types import effective_source_type_sql
from autolangchat.rag.kb_ingestion import ingest_local_source

from ._autolangchat_imports import load_module


@pytest.mark.parametrize(
    ("source", "metadata", "expected"),
    [
        ("docs", '{"source_type": "web"}', "web"),
        ("docs", '{"source_type": "file"}', "file"),
        ("docs", '{"source_type": "local"}', "file"),
        ("feedback", '{"synthesized": true}', "feedback"),
        ("feedback", '{"source_type": null}', "feedback"),
        ("feedback", '{"source_type": "web"}', "web"),
        ("other", None, None),
        ("other", '{"source_type": "other"}', None),
    ],
)
def test_sqlite_effective_source_type(source, metadata, expected):
    with sqlite3.connect(":memory:") as conn:
        row = conn.execute(
            f"SELECT {effective_source_type_sql('sqlite')} FROM (SELECT ? AS source, ? AS metadata) d",
            (source, metadata),
        ).fetchone()
    assert row == (expected,)


def test_postgres_effective_source_type_uses_jsonb_text_extraction():
    sql = effective_source_type_sql("postgres")
    assert "d.metadata::jsonb ->> 'source_type'" in sql
    assert "= 'local' THEN 'file'" in sql
    assert "d.source = 'feedback'" in sql
    assert "ELSE NULL END" in sql


def test_source_type_sql_rejects_unknown_backend():
    with pytest.raises(ValueError, match="Unsupported KB backend"):
        effective_source_type_sql("unknown")  # type: ignore[arg-type]


def test_source_summary_and_list_models_validate_new_contract():
    routes = load_module("autolangchat.admin.admin_kb_routes", "admin/admin_kb_routes.py")
    created_at = datetime(2026, 10, 6, tzinfo=timezone.utc)
    row = routes.KBSourceSummary(
        source="docs", source_type="file", document_count=2, chunk_count=4, last_created_at=created_at
    )
    mixed = routes.KBSourceSummary(
        source="mixed", source_type=None, document_count=3, chunk_count=0, last_created_at=None
    )
    response = routes.KBSourceListResponse(items=[row, mixed], total=2, limit=50, offset=0)
    assert response.items[0].last_created_at == created_at
    assert response.items[1].source_type is None
    with pytest.raises(ValidationError):
        routes.KBSourceSummary(source="docs", source_type="local", document_count=1, chunk_count=0)
    with pytest.raises(ValidationError):
        routes.KBSourceListResponse(items=[row], total=-1, limit=50, offset=0)


@pytest.mark.asyncio
async def test_local_ingestion_writes_file_type_and_retains_source_path(tmp_path):
    file_path = tmp_path / "notes.txt"
    file_path.write_text("hello world", encoding="utf-8")
    store = MagicMock()
    store.get_document.return_value = None
    chunker = SimpleNamespace(chunk_document=lambda doc: [{"text": doc["content"]}])
    bedrock = SimpleNamespace(generate_embeddings_batch=AsyncMock(return_value=[[0.1, 0.2]]))

    result = await ingest_local_source(
        vector_db=store,
        bedrock_client=bedrock,
        chunker=chunker,
        embedding_model="test",
        source_name="runbook",
        path=str(file_path),
    )

    assert result["documents"] == 1
    assert result["chunks"] == 1
    assert result["errors"] == []
    metadata = store.add_document.call_args.kwargs["metadata"]
    assert metadata == {"source_type": "file", "source_path": str(file_path), "filename": "notes.txt"}
