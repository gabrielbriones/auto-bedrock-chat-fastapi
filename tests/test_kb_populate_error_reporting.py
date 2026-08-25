"""Tests for kb_populate() aggregating and reporting per-item ingestion errors.

Covers the CLI regression flagged in PR review round 3: previously
``result["errors"]`` from ingest_web_source()/ingest_local_source() was
discarded entirely, so a run where every page/file failed to index still
logged "population complete" and returned True.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# Importing these submodules directly (rather than relying on kb_populate()'s
# own lazy imports to do it first) guarantees they're set as attributes of
# their parent packages before any patch("autolangchat.rag.bedrock_embeddings...")
# call below resolves that dotted path -- otherwise whether the attribute is
# already set depends on test collection/execution order, which is not
# guaranteed across Python versions/environments.
import autolangchat.db  # noqa: F401
import autolangchat.rag.bedrock_embeddings  # noqa: F401
from autolangchat.commands.kb import kb_populate


def _make_config(tmp_path, sources):
    config_path = tmp_path / "kb_sources.yaml"
    config_path.write_text(yaml.safe_dump({"knowledge_base": {"enabled": True, "sources": sources}}))
    db_path = tmp_path / "kb.db"
    config = SimpleNamespace(
        enable_rag=True,
        kb_sources_config=str(config_path),
        kb_database_path=str(db_path),
        kb_storage_type="sqlite",
        kb_embedding_model="fake-model",
        kb_chunk_size=512,
        kb_chunk_overlap=100,
    )
    return config, str(config_path), str(db_path)


@pytest.mark.asyncio
async def test_kb_populate_returns_false_and_logs_errors_from_web_source(tmp_path, caplog):
    sources = [{"name": "web-src", "type": "web", "urls": ["https://example.com"]}]
    config, config_path, db_path = _make_config(tmp_path, sources)

    web_result = {
        "documents": 1,
        "chunks": 3,
        "errors": ["failed to index https://example.com/broken: boom"],
    }

    with (
        patch("autolangchat.rag.bedrock_embeddings.BedrockEmbeddingClient", return_value=MagicMock()),
        patch("autolangchat.db.create_kb_store", return_value=MagicMock()),
        patch("autolangchat.rag.kb_ingestion.ingest_web_source", AsyncMock(return_value=web_result)),
        caplog.at_level("WARNING"),
    ):
        result = await kb_populate(config_path=config_path, db_path=db_path, config=config)

    assert result is False
    assert any("boom" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_kb_populate_returns_true_when_no_errors(tmp_path):
    sources = [{"name": "web-src", "type": "web", "urls": ["https://example.com"]}]
    config, config_path, db_path = _make_config(tmp_path, sources)

    web_result = {"documents": 1, "chunks": 3, "errors": []}

    with (
        patch("autolangchat.rag.bedrock_embeddings.BedrockEmbeddingClient", return_value=MagicMock()),
        patch("autolangchat.db.create_kb_store", return_value=MagicMock()),
        patch("autolangchat.rag.kb_ingestion.ingest_web_source", AsyncMock(return_value=web_result)),
    ):
        result = await kb_populate(config_path=config_path, db_path=db_path, config=config)

    assert result is True


@pytest.mark.asyncio
async def test_kb_populate_aggregates_errors_from_web_and_local_sources(tmp_path):
    sources = [
        {"name": "web-src", "type": "web", "urls": ["https://example.com"]},
        {"name": "local-src", "type": "local", "path": str(tmp_path)},
    ]
    config, config_path, db_path = _make_config(tmp_path, sources)

    web_result = {"documents": 1, "chunks": 2, "errors": ["web error"]}
    local_result = {"documents": 0, "chunks": 0, "errors": ["local error"]}

    with (
        patch("autolangchat.rag.bedrock_embeddings.BedrockEmbeddingClient", return_value=MagicMock()),
        patch("autolangchat.db.create_kb_store", return_value=MagicMock()),
        patch("autolangchat.rag.kb_ingestion.ingest_web_source", AsyncMock(return_value=web_result)),
        patch("autolangchat.rag.kb_ingestion.ingest_local_source", AsyncMock(return_value=local_result)),
    ):
        result = await kb_populate(config_path=config_path, db_path=db_path, config=config)

    assert result is False
