"""Deletion must remove SQLite KB search and vector artifacts together."""

import pytest

from .test_kb_credibility_decay import SQLiteKBStore


def test_delete_document_removes_chunks_vectors_and_fts(tmp_path):
    store = SQLiteKBStore(db_path=str(tmp_path / "kb.db"))
    try:
        for doc_id in ("removed", "retained"):
            store.add_document(doc_id, "document", source=doc_id)
            store.add_chunk(
                f"{doc_id}-chunk",
                doc_id,
                "uniquekeyword",
                [0.0] * 1536,
                0,
            )

        assert store.delete_document("removed") == 1
        for table, column, value in (
            ("documents", "id", "removed"),
            ("chunks", "document_id", "removed"),
            ("vec_chunks", "chunk_id", "removed-chunk"),
            ("fts_chunks", "chunk_id", "removed-chunk"),
        ):
            assert store.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)).fetchone()[0] == 0
        assert (
            store.conn.execute("SELECT COUNT(*) FROM fts_chunks WHERE fts_chunks MATCH 'uniquekeyword'").fetchone()[0]
            == 1
        )
        assert store.get_document("retained") is not None
        assert store.delete_document("removed") == 0
    finally:
        store.conn.close()


def test_delete_document_rolls_back_when_chunk_cleanup_fails(tmp_path):
    class FailingCleanupStore(SQLiteKBStore):
        def _delete_chunks_for(self, cursor, doc_id):
            super()._delete_chunks_for(cursor, doc_id)
            raise RuntimeError("simulated cleanup failure")

    store = FailingCleanupStore(db_path=str(tmp_path / "kb.db"))
    try:
        store.add_document("removed", "document")
        store.add_chunk("removed-chunk", "removed", "uniquecontent", [0.0] * 1536, 0)
        with pytest.raises(RuntimeError, match="simulated cleanup failure"):
            store.delete_document("removed")

        for table, column, value in (
            ("documents", "id", "removed"),
            ("chunks", "document_id", "removed"),
            ("vec_chunks", "chunk_id", "removed-chunk"),
            ("fts_chunks", "chunk_id", "removed-chunk"),
        ):
            assert store.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column} = ?", (value,)).fetchone()[0] == 1
    finally:
        store.conn.close()
