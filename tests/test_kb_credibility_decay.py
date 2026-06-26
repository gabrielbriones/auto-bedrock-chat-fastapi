"""Unit tests for the KB credibility decay task (XMGPLAT-10933).

Tests apply_credibility_decay and reset_credibility on a real in-memory
SQLiteKBStore so the SQL logic is exercised without mocking.
The decay loop async wrapper is tested via a lightweight stub store.
"""

import asyncio
import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "autolangchat"


# ---------------------------------------------------------------------------
# Module loader (mirrors test_kb_sqlite_query_sanitizer.py)
# ---------------------------------------------------------------------------


def _load_modules():
    """Load exceptions, models, kb_base, and kb_sqlite with stub packages."""
    from ._autolangchat_imports import load_module

    exceptions_mod = load_module("autolangchat.exceptions", "exceptions.py")
    models_mod = load_module(
        "autolangchat.models",
        "models.py",
        extra_modules={"autolangchat.exceptions": exceptions_mod},
    )

    # kb_base needs exceptions + models
    kb_base_mod = load_module(
        "autolangchat.db.kb_base",
        "db/kb_base.py",
        extra_modules={
            "autolangchat.exceptions": exceptions_mod,
            "autolangchat.models": models_mod,
        },
    )

    # kb_sqlite needs all of the above
    kb_sqlite_mod = load_module(
        "autolangchat.db.kb_sqlite",
        "db/kb_sqlite.py",
        extra_modules={
            "autolangchat.exceptions": exceptions_mod,
            "autolangchat.models": models_mod,
            "autolangchat.db.kb_base": kb_base_mod,
        },
    )
    return exceptions_mod, models_mod, kb_sqlite_mod


try:
    _exceptions_mod, _models_mod, _kb_sqlite_mod = _load_modules()
    SQLiteKBStore = _kb_sqlite_mod.SQLiteKBStore
    KBDocument = _models_mod.KBDocument
    KBDocumentNotFoundError = _exceptions_mod.KBDocumentNotFoundError
    _AVAILABLE = True
except Exception as _e:
    _AVAILABLE = False
    _e_msg = str(_e)

pytestmark = pytest.mark.skipif(
    not _AVAILABLE,
    reason=f"kb_sqlite modules could not be loaded: {_e_msg if not _AVAILABLE else ''}",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_store(tmp_path):
    return SQLiteKBStore(db_path=str(tmp_path / "test_kb.db"))


def _insert_doc(store, doc_id, source="feedback", score=1.0, flagged=False):
    """Insert a minimal document directly, bypassing add_document."""
    store.conn.execute(
        "INSERT INTO documents (id, content, source, credibility_score, removal_flagged) " "VALUES (?, ?, ?, ?, ?)",
        (doc_id, f"content of {doc_id}", source, score, 1 if flagged else 0),
    )
    store.conn.commit()


def _get_doc_row(store, doc_id):
    row = store.conn.execute(
        "SELECT credibility_score, removal_flagged FROM documents WHERE id = ?",
        (doc_id,),
    ).fetchone()
    return {"credibility_score": row[0], "removal_flagged": bool(row[1])}


# ---------------------------------------------------------------------------
# apply_credibility_decay
# ---------------------------------------------------------------------------


def test_decay_subtracts_rate(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.9)
    store.apply_credibility_decay(decay_rate=0.1, threshold=0.3)
    row = _get_doc_row(store, "d1")
    assert abs(row["credibility_score"] - 0.8) < 1e-6


def test_decay_clamps_at_zero(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.05)
    store.apply_credibility_decay(decay_rate=0.2, threshold=0.3)
    row = _get_doc_row(store, "d1")
    assert row["credibility_score"] == 0.0


def test_decay_sets_removal_flagged_at_threshold(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.35)  # 0.35 - 0.1 = 0.25 <= 0.3
    store.apply_credibility_decay(decay_rate=0.1, threshold=0.3)
    row = _get_doc_row(store, "d1")
    assert row["removal_flagged"] is True
    assert abs(row["credibility_score"] - 0.25) < 1e-6


def test_decay_does_not_flag_above_threshold(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.8)  # 0.8 - 0.1 = 0.7 > 0.3
    store.apply_credibility_decay(decay_rate=0.1, threshold=0.3)
    row = _get_doc_row(store, "d1")
    assert row["removal_flagged"] is False


def test_decay_skips_non_feedback_sources(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", source="crawler", score=0.9)
    _insert_doc(store, "d2", source="operator", score=0.9)
    updated, _ = store.apply_credibility_decay(decay_rate=0.1, threshold=0.3)
    assert updated == 0
    assert _get_doc_row(store, "d1")["credibility_score"] == 0.9
    assert _get_doc_row(store, "d2")["credibility_score"] == 0.9


def test_decay_skips_already_flagged(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.2, flagged=True)
    updated, _ = store.apply_credibility_decay(decay_rate=0.1, threshold=0.3)
    assert updated == 0


def test_decay_returns_counts(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.9)  # will NOT be flagged
    _insert_doc(store, "d2", score=0.35)  # will be flagged (0.35 - 0.1 = 0.25 <= 0.3)
    total, newly_flagged = store.apply_credibility_decay(decay_rate=0.1, threshold=0.3)
    assert total == 2
    assert newly_flagged == 1


# ---------------------------------------------------------------------------
# reset_credibility
# ---------------------------------------------------------------------------


def test_reset_credibility_restores_score(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.2, flagged=True)
    doc = store.reset_credibility("d1")
    assert doc.credibility_score == 1.0
    assert doc.removal_flagged is False
    row = _get_doc_row(store, "d1")
    assert row["credibility_score"] == 1.0
    assert row["removal_flagged"] is False


def test_reset_credibility_returns_kbdocument(tmp_path):
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.5)
    doc = store.reset_credibility("d1")
    assert isinstance(doc, KBDocument)
    assert doc.id == "d1"


def test_reset_credibility_not_found_raises(tmp_path):
    store = _make_store(tmp_path)
    with pytest.raises(KBDocumentNotFoundError):
        store.reset_credibility("nonexistent")


# ---------------------------------------------------------------------------
# Decay loop async wrapper (_run_credibility_decay_loop)
# ---------------------------------------------------------------------------


def test_decay_loop_calls_apply_credibility_decay():
    """The async loop calls apply_credibility_decay and honours CancelledError."""

    class _Config:
        kb_credibility_decay_interval_hours = 0  # sleep(0) fires immediately
        kb_credibility_decay_rate = 0.05
        kb_credibility_removal_threshold = 0.3

    calls = []

    class _StubStore:
        def apply_credibility_decay(self, rate, threshold):
            calls.append((rate, threshold))
            raise asyncio.CancelledError("stop loop")  # propagates through except Exception

    async def _run():
        from ._autolangchat_imports import load_module

        mod = load_module("autolangchat.kb_credibility", "kb_credibility.py")
        with pytest.raises(asyncio.CancelledError):
            await mod.run_credibility_decay_loop(_StubStore(), _Config())

    asyncio.run(_run())
    assert len(calls) == 1
    assert calls[0] == (0.05, 0.3)


# ---------------------------------------------------------------------------
# semantic_search: exclude_flagged and credibility weighting
# ---------------------------------------------------------------------------


def test_semantic_search_exclude_flagged_filters_flagged_docs(tmp_path):
    """Flagged documents are excluded when exclude_flagged=True."""
    import numpy as np

    store = _make_store(tmp_path)
    store.conn.execute(
        "INSERT INTO documents (id, content, source, removal_flagged, credibility_score) "
        "VALUES ('good', 'relevant', 'operator', 0, 1.0)"
    )
    store.conn.execute(
        "INSERT INTO documents (id, content, source, removal_flagged, credibility_score) "
        "VALUES ('bad', 'relevant', 'feedback', 1, 0.2)"
    )
    emb = np.zeros(1536, dtype=np.float32).tobytes()
    for doc_id, chunk_id in [("good", "c1"), ("bad", "c2")]:
        store.conn.execute(
            "INSERT INTO chunks (id, document_id, content, chunk_index) VALUES (?, ?, 'relevant', 0)",
            (chunk_id, doc_id),
        )
        store.conn.execute("INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)", (chunk_id, emb))
    store.conn.commit()

    results = store.semantic_search([0.0] * 1536, limit=10, exclude_flagged=True)
    ids = {r["document_id"] for r in results}
    assert "bad" not in ids
    assert "good" in ids


def test_semantic_search_include_flagged_when_exclude_false(tmp_path):
    """Flagged documents appear when exclude_flagged=False."""
    import numpy as np

    store = _make_store(tmp_path)
    emb = np.zeros(1536, dtype=np.float32).tobytes()
    store.conn.execute(
        "INSERT INTO documents (id, content, source, removal_flagged, credibility_score) "
        "VALUES ('flagged', 'content', 'feedback', 1, 0.2)"
    )
    store.conn.execute(
        "INSERT INTO chunks (id, document_id, content, chunk_index) VALUES ('c1', 'flagged', 'content', 0)"
    )
    store.conn.execute("INSERT INTO vec_chunks (chunk_id, embedding) VALUES ('c1', ?)", (emb,))
    store.conn.commit()

    results = store.semantic_search([0.0] * 1536, limit=10, exclude_flagged=False)
    assert any(r["document_id"] == "flagged" for r in results)


def test_semantic_search_credibility_weighting_ranks_higher_score_first(tmp_path):
    """High-credibility article ranks above low-credibility with same cosine distance."""
    import numpy as np

    store = _make_store(tmp_path)
    emb = np.array([1.0] + [0.0] * 1535, dtype=np.float32)
    emb = emb / np.linalg.norm(emb)
    emb_bytes = emb.tobytes()

    for doc_id, credibility in [("high", 1.0), ("low", 0.5)]:
        store.conn.execute(
            "INSERT INTO documents (id, content, source, removal_flagged, credibility_score) "
            "VALUES (?, ?, 'feedback', 0, ?)",
            (doc_id, f"content {doc_id}", credibility),
        )
        chunk_id = f"c_{doc_id}"
        store.conn.execute(
            "INSERT INTO chunks (id, document_id, content, chunk_index) VALUES (?, ?, ?, 0)",
            (chunk_id, doc_id, f"content {doc_id}"),
        )
        store.conn.execute("INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)", (chunk_id, emb_bytes))
    store.conn.commit()

    results = store.semantic_search(emb.tolist(), limit=10, exclude_flagged=False)
    ordered = [r["document_id"] for r in results]
    assert ordered.index("high") < ordered.index("low")


def test_row_to_document_maps_credibility_fields(tmp_path):
    """_row_to_document correctly maps new positional columns."""
    store = _make_store(tmp_path)
    _insert_doc(store, "d1", score=0.75, flagged=True)
    row = store.conn.execute(
        "SELECT id, content, title, source, source_url, topic, "
        "date_published, metadata, created_at, credibility_score, removal_flagged "
        "FROM documents WHERE id = 'd1'"
    ).fetchone()
    doc = store._row_to_document(row)
    assert abs(doc.credibility_score - 0.75) < 1e-6
    assert doc.removal_flagged is True
