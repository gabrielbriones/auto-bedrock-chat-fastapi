import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


def _load_kb_sqlite_module():
    package_root = Path(__file__).resolve().parents[1] / "autolangchat"
    module_path = package_root / "db" / "kb_sqlite.py"

    autolangchat_pkg = types.ModuleType("autolangchat")
    autolangchat_pkg.__path__ = [str(package_root)]
    autolangchat_db_pkg = types.ModuleType("autolangchat.db")
    autolangchat_db_pkg.__path__ = [str(package_root / "db")]

    exceptions_mod = types.ModuleType("autolangchat.exceptions")

    class KBDocumentNotFoundError(Exception):
        pass

    exceptions_mod.KBDocumentNotFoundError = KBDocumentNotFoundError

    models_mod = types.ModuleType("autolangchat.models")
    models_mod.KBDocument = object
    models_mod.KBDocumentListFilters = object

    kb_base_mod = types.ModuleType("autolangchat.db.kb_base")

    class BaseKBStore:
        pass

    kb_base_mod.BaseKBStore = BaseKBStore

    original_modules = {
        name: sys.modules.get(name)
        for name in [
            "autolangchat",
            "autolangchat.db",
            "autolangchat.exceptions",
            "autolangchat.models",
            "autolangchat.db.kb_base",
        ]
    }

    sys.modules["autolangchat"] = autolangchat_pkg
    sys.modules["autolangchat.db"] = autolangchat_db_pkg
    sys.modules["autolangchat.exceptions"] = exceptions_mod
    sys.modules["autolangchat.models"] = models_mod
    sys.modules["autolangchat.db.kb_base"] = kb_base_mod

    try:
        spec = spec_from_file_location("autolangchat.db.kb_sqlite", module_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        sys.modules["autolangchat.db.kb_sqlite"] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, original in original_modules.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


SQLiteKBStore = _load_kb_sqlite_module().SQLiteKBStore


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
