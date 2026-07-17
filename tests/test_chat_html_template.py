"""XMGPLAT-9697 Phase 4 — chat.html dynamic-overrides sidebar template tests.

Renders the real `chat.html` template (via a plain jinja2 Environment, with a
stub `url_for` global standing in for FastAPI's Jinja2Templates context
processor) to verify the settings sidebar and `window.CONFIG` fields render
correctly, and that the sidebar/gear icon are omitted entirely (not just
hidden) when `enable_config_sidebar` is False.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "autolangchat" / "templates"


def _render(**context_overrides):
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)))
    env.globals["url_for"] = lambda name, **kwargs: f"/static/{kwargs.get('path', '')}"

    base_context = {
        "app_title": "Test App",
        "ui_title": "AI Assistant",
        "model_id": "us.anthropic.claude-sonnet-5",
        "model_display_name": "Claude Sonnet 5 (US)",
        "ui_welcome_message": "Welcome",
        "websocket_url": "/chat/ws",
        "auth_enabled": False,
        "require_tool_auth": False,
        "supported_auth_types": ["bearer_token"],
        "default_auth_type": "",
        "preset_prompts": [],
        "preset_variables": [],
        "sso_enabled": False,
        "sso_login_url": "/chat/auth/sso/login",
        "sso_authenticated": False,
        "sso_user_display": "",
        "feedback_enabled": False,
        "lock_input_while_responding": True,
        "admin_enabled": False,
        "admin_prefix": "",
        "dashboard_url": "",
        "conversation_persistence_enabled": False,
        "enable_config_sidebar": False,
        "allowed_dynamic_overrides": None,
        "available_models": [{"id": "us.anthropic.claude-sonnet-5", "name": "Claude Sonnet 5 (US)"}],
        "override_defaults": {},
    }
    base_context.update(context_overrides)

    template = env.get_template("chat.html")
    return template.render(**base_context)


class TestConfigSidebarRendering:
    def test_powered_by_header_renders_display_name_not_model_id(self):
        html = _render(model_id="us.anthropic.claude-sonnet-5", model_display_name="Claude Sonnet 5 (US)")

        assert '<span id="modelIdDisplay">Claude Sonnet 5 (US)</span>' in html

    def test_sidebar_and_gear_icon_omitted_when_disabled(self):
        html = _render(enable_config_sidebar=False)

        assert "configSidebarToggleButton" not in html
        assert 'id="configSidebar"' not in html
        assert "configResetButton" not in html

    def test_sidebar_and_gear_icon_rendered_when_enabled(self):
        html = _render(enable_config_sidebar=True)

        assert "configSidebarToggleButton" in html
        assert 'id="configSidebar"' in html
        assert "configResetButton" in html
        assert "configSidebarBackdrop" in html

    def test_window_config_carries_sidebar_flags(self):
        html = _render(
            enable_config_sidebar=True,
            allowed_dynamic_overrides=["temperature", "max_tokens"],
            override_defaults={"temperature": 0.7, "max_tokens": 4096},
        )

        assert "enableConfigSidebar: true" in html
        assert json.dumps(["temperature", "max_tokens"]) in html
        assert '"temperature": 0.7' in html or '"temperature":0.7' in html
        assert '"max_tokens": 4096' in html or '"max_tokens":4096' in html

    def test_window_config_reflects_disabled_flag_and_null_allowlist(self):
        html = _render(enable_config_sidebar=False, allowed_dynamic_overrides=None)

        assert "enableConfigSidebar: false" in html
        assert "allowedDynamicOverrides: null" in html
