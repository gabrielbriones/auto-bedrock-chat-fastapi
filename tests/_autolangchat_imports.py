import sys
import types
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_ROOT = ROOT / "autolangchat"


def _stub_package(name, path):
    package = types.ModuleType(name)
    package.__path__ = [str(path)]
    return package


def install_package_stubs(extra_modules=None):
    stubs = {
        "autolangchat": _stub_package("autolangchat", PACKAGE_ROOT),
        "autolangchat.admin": _stub_package("autolangchat.admin", PACKAGE_ROOT / "admin"),
        "autolangchat.db": _stub_package("autolangchat.db", PACKAGE_ROOT / "db"),
        "autolangchat.rag": _stub_package("autolangchat.rag", PACKAGE_ROOT / "rag"),
        "autolangchat.sso": _stub_package("autolangchat.sso", PACKAGE_ROOT / "sso"),
    }
    if extra_modules:
        stubs.update(extra_modules)
    return stubs


def load_module(module_name, relative_path, extra_modules=None):
    installed = install_package_stubs(extra_modules)
    # Snapshot the loaded module's own entry too, so a pre-existing real module
    # (already imported by an earlier test) is restored rather than left
    # replaced by this file-loaded duplicate.
    original = {name: sys.modules.get(name) for name in (*installed, module_name)}
    pre_keys = set(sys.modules)

    try:
        sys.modules.update(installed)
        spec = spec_from_file_location(module_name, PACKAGE_ROOT / relative_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        # Drop the loaded module and any ``autolangchat.*`` modules imported as
        # a side effect of executing it. Leaving them in ``sys.modules`` creates
        # duplicate module objects that can later shadow the real package (e.g.
        # ``autolangchat.db.feedback_base``), breaking unrelated test modules
        # collected afterwards (see XMGPLAT-10766).
        for name in set(sys.modules) - pre_keys:
            if name == module_name or name.startswith("autolangchat"):
                del sys.modules[name]
        # Restore the package stubs / extra modules we swapped in.
        for name, previous in original.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
