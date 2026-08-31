import types

from ._autolangchat_imports import load_module


class _KBDocumentNotFoundError(Exception):
    pass


_exceptions_mod = types.ModuleType("autolangchat.exceptions")
_exceptions_mod.KBDocumentNotFoundError = _KBDocumentNotFoundError

_models_mod = types.ModuleType("autolangchat.models")
_models_mod.KBDocument = object
_models_mod.KBDocumentListFilters = object


class _BaseKBStore:
    pass


_kb_base_mod = types.ModuleType("autolangchat.db.kb_base")
_kb_base_mod.BaseKBStore = _BaseKBStore

SQLiteKBStore = load_module(
    "autolangchat.db.kb_sqlite",
    "db/kb_sqlite.py",
    extra_modules={
        "autolangchat.exceptions": _exceptions_mod,
        "autolangchat.models": _models_mod,
        "autolangchat.db.kb_base": _kb_base_mod,
    },
).SQLiteKBStore


def test_sanitize_fts5_query_removes_commas_and_punctuation():
    query = "If I return additional status codes and responses directly, will they be included in the OpenAPI schema?"

    sanitized = SQLiteKBStore._sanitize_fts5_query(query)

    assert "," not in sanitized
    assert "?" not in sanitized
    assert "directly" in sanitized
    assert "OR" in sanitized


def test_sanitize_fts5_query_drops_boolean_keywords():
    query = "alpha AND beta OR gamma NOT delta NEAR epsilon"

    sanitized = SQLiteKBStore._sanitize_fts5_query(query)

    assert sanitized == "alpha OR beta OR gamma OR delta OR epsilon"
