"""Importing the e-commerce example under an ASGI package name still wires chat routes."""

import runpy
from pathlib import Path

import autolangchat


def test_package_qualified_import_registers_plugin(monkeypatch):
    registered = []

    def fake_add_autolangchat(app, **_kwargs):
        registered.append(app)
        return object()

    monkeypatch.setattr(autolangchat, "add_autolangchat", fake_add_autolangchat)
    example = Path(__file__).resolve().parent.parent / "examples" / "fastAPI" / "app_plugin.py"

    module = runpy.run_path(str(example), run_name="examples.fastAPI.app_plugin")

    assert registered == [module["app"]]
