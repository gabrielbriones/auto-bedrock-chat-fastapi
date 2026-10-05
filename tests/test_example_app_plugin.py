"""Importing the e-commerce example under an ASGI package name still wires chat routes."""

import runpy
import sys
from pathlib import Path
from types import ModuleType


def test_package_qualified_import_registers_plugin(monkeypatch):
    registered = []

    def fake_add_autolangchat(app, **_kwargs):
        registered.append(app)
        return object()

    # Other tests reload or replace autolangchat in sys.modules during collection.
    # Replace the import itself so this example can never initialize AWS in any test order.
    fake_package = ModuleType("autolangchat")
    fake_package.add_autolangchat = fake_add_autolangchat
    monkeypatch.setitem(sys.modules, "autolangchat", fake_package)
    example = Path(__file__).resolve().parent.parent / "examples" / "fastAPI" / "app_plugin.py"

    module = runpy.run_path(str(example), run_name="examples.fastAPI.app_plugin")

    assert registered == [module["app"]]
