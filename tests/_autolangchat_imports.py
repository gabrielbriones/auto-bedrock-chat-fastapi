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
    original = {name: sys.modules.get(name) for name in installed}

    try:
        sys.modules.update(installed)
        spec = spec_from_file_location(module_name, PACKAGE_ROOT / relative_path)
        module = module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for name, previous in original.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
