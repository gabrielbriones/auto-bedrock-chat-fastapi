"""PostgreSQL's indexed vectors and full-text data live on chunk rows."""

from unittest.mock import MagicMock

from ._autolangchat_imports import load_module
from .test_kb_credibility_decay import _load_modules


def test_delete_document_removes_chunks_before_parent_and_returns_count():
    exceptions_mod, models_mod, _ = _load_modules()
    base_mod = load_module(
        "autolangchat.db.kb_base",
        "db/kb_base.py",
        extra_modules={"autolangchat.exceptions": exceptions_mod, "autolangchat.models": models_mod},
    )
    postgres_mod = load_module(
        "autolangchat.db.kb_postgres",
        "db/kb_postgres.py",
        extra_modules={
            "autolangchat.exceptions": exceptions_mod,
            "autolangchat.models": models_mod,
            "autolangchat.db.kb_base": base_mod,
        },
    )
    store = object.__new__(postgres_mod.PgVectorKBStore)
    connection = MagicMock()
    connection.__enter__.return_value = connection
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.rowcount = 2
    store._get_conn = MagicMock(return_value=connection)

    assert store.delete_document("document-1") == 2
    assert cursor.execute.call_args_list[0].args[0].startswith("DELETE FROM chunks")
    assert cursor.execute.call_args_list[0].args[1] == ("document-1",)
    assert cursor.execute.call_args_list[1].args[0].startswith("DELETE FROM documents")
    connection.commit.assert_called_once_with()
