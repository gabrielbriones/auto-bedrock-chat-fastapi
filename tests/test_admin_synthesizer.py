import json
import sys
import types
from datetime import datetime, timezone
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ._autolangchat_imports import load_module

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "autolangchat"


def _load_synthesizer_module():
    module_name = "autolangchat.admin.synthesizer"
    module_path = PACKAGE_ROOT / "admin" / "synthesizer.py"

    rag_pkg = types.ModuleType("autolangchat.rag")
    rag_pkg.__path__ = [str(PACKAGE_ROOT / "rag")]

    embedding_pipeline_mod = load_module("autolangchat.rag.embedding_pipeline", "rag/embedding_pipeline.py")
    models_mod = load_module("autolangchat.models", "models.py")
    exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
    feedback_base_mod = load_module("autolangchat.db.feedback_base", "db/feedback_base.py")
    kb_base_mod = load_module("autolangchat.db.kb_base", "db/kb_base.py")

    installed = {
        "autolangchat": types.ModuleType("autolangchat"),
        "autolangchat.admin": types.ModuleType("autolangchat.admin"),
        "autolangchat.db": types.ModuleType("autolangchat.db"),
        "autolangchat.rag": rag_pkg,
        "autolangchat.rag.embedding_pipeline": embedding_pipeline_mod,
        "autolangchat.models": models_mod,
        "autolangchat.exceptions": exceptions_mod,
        "autolangchat.db.feedback_base": feedback_base_mod,
        "autolangchat.db.kb_base": kb_base_mod,
    }
    installed["autolangchat"].__path__ = [str(PACKAGE_ROOT)]
    installed["autolangchat.admin"].__path__ = [str(PACKAGE_ROOT / "admin")]
    installed["autolangchat.db"].__path__ = [str(PACKAGE_ROOT / "db")]

    original = {name: sys.modules.get(name) for name in installed}
    try:
        sys.modules.update(installed)
        spec = spec_from_file_location(module_name, module_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module, models_mod
    finally:
        for name, previous in original.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous


synth_mod, models_mod = _load_synthesizer_module()
FeedbackSynthesizer = synth_mod.FeedbackSynthesizer
SynthesisAction = synth_mod.SynthesisAction
SynthesisRunResult = synth_mod.SynthesisRunResult
TagGroupResult = synth_mod.TagGroupResult
_parse_article = synth_mod._parse_article
_article_to_document_content = synth_mod._article_to_document_content
_build_messages = synth_mod._build_messages

FeedbackEntry = models_mod.FeedbackEntry
Rating = models_mod.Rating
ReviewStatus = models_mod.ReviewStatus


def _approved_entry(tags=None, correction_text="The correct answer is X.", reviewer_comment=None):
    reviewer_tags = ["perf"] if tags is None else tags
    return FeedbackEntry(
        session_id="sess-test",
        user_id="expert-1",
        query="What is the workload type?",
        ai_response="It is Y.",
        rating=Rating.NEGATIVE,
        correction_text=correction_text,
        reviewer_tags=reviewer_tags,
        reviewer_id="reviewer-1",
        reviewer_comment=reviewer_comment,
        review_status=ReviewStatus.APPROVED,
        reviewed_at=datetime.now(timezone.utc),
        model_id="anthropic.claude-test",
        created_at=datetime.now(timezone.utc),
    )


def _kb_doc(tag="perf", doc_id=None):
    return models_mod.KBDocument(
        id=doc_id or f"synthesis-{tag}-abcd1234",
        content=f"# Existing article for {tag}\n\n## Problem\nOld problem.",
        title=f"Learned correction: {tag}",
        source="feedback",
        topic=tag,
        tags=[tag],
        metadata={"tags": [tag], "synthesized": True, "source_feedback_ids": []},
    )


def _make_bedrock_client(llm_response_json):
    client = MagicMock()
    client.config = MagicMock()
    client.config.model_id = "anthropic.claude-test"
    client.generate_embeddings_batch = AsyncMock(return_value=[[0.1] * 1536])
    client._llm_response_json = llm_response_json
    return client


def _make_kb_store(existing_docs=None):
    store = MagicMock()
    store.list_documents = MagicMock(return_value=existing_docs or [])
    store.add_document = MagicMock()
    store.update_document = MagicMock(return_value=_kb_doc())
    store.add_chunk = MagicMock()
    return store


def _make_feedback_store(entries=None):
    store = MagicMock()
    store.list_entries = AsyncMock(return_value=entries or [])
    store.get = AsyncMock(return_value=entries[0] if entries else None)
    store.mark_integrated = AsyncMock()
    return store


def test_parse_article_strips_fences():
    result = _parse_article('```json\n{"action": "update"}\n```')
    assert result["action"] == "update"


def test_article_to_document_content_includes_sections():
    content = _article_to_document_content(
        {
            "title": "My Article",
            "problem": "Bad output",
            "correct_methodology": "Use X",
            "key_terms": ["alpha", "beta"],
            "examples": "Example: alpha = 1",
        }
    )
    assert "# My Article" in content
    assert "## Problem" in content
    assert "## Correct Methodology" in content
    assert "## Key Terms" in content


def test_build_messages_includes_system_and_user_roles():
    messages = _build_messages("perf", [_approved_entry()], existing_content=None)
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"


@pytest.mark.asyncio
async def test_embed_and_add_chunks_calls_generate_embeddings_batch():
    synth = FeedbackSynthesizer(model_id="anthropic.claude-test")
    kb_store = _make_kb_store()
    bedrock_client = _make_bedrock_client("{}")
    long_content = " ".join(f"word{i}" for i in range(60))

    await synth._embed_and_add_chunks("doc-1", long_content, kb_store, bedrock_client)

    bedrock_client.generate_embeddings_batch.assert_awaited_once()
    kb_store.add_chunk.assert_called()


@pytest.mark.asyncio
async def test_synthesize_all_creates_new_document():
    entry = _approved_entry(tags=["perf"])
    fb_store = _make_feedback_store(entries=[entry])
    kb_store = _make_kb_store(existing_docs=[])
    bedrock_client = _make_bedrock_client(
        json.dumps(
            {
                "title": "Perf Article",
                "problem": "Wrong metric",
                "correct_methodology": "Use X",
                "key_terms": ["perf"],
                "examples": "Example",
                "source_feedback_ids": [str(entry.id)],
                "action": "create",
            }
        )
    )
    synth = FeedbackSynthesizer(model_id="anthropic.claude-test")
    llm_response_json = bedrock_client._llm_response_json

    with patch.dict(
        sys.modules,
        {
            "langchain_aws": types.SimpleNamespace(
                ChatBedrockConverse=lambda **kwargs: MagicMock(
                    ainvoke=AsyncMock(return_value=MagicMock(content=llm_response_json))
                )
            ),
            "langchain_core.messages": types.SimpleNamespace(SystemMessage=MagicMock, HumanMessage=MagicMock),
        },
    ):
        result = await synth.synthesize_all(fb_store, kb_store, bedrock_client)

    assert isinstance(result, SynthesisRunResult)
    assert result.total_integrated == 1
