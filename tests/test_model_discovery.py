"""Tests for live Bedrock catalog discovery (XMGPLAT-11193).

Covers the control-plane client construction, the blocking fetch, the async
degrade-on-failure wrapper, the ChatConfig filtering it feeds, and the plugin
startup hook.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from autolangchat.config import ChatConfig
from autolangchat.model_capabilities import (
    _build_bedrock_control_client,
    discover_invocable_model_ids,
    fetch_invocable_model_ids,
)


def _config(**kwargs):
    base = {
        "aws_region": "us-west-2",
        "aws_access_key_id": None,
        "aws_secret_access_key": None,
    }
    base.update(kwargs)
    return SimpleNamespace(**base)


def _client(foundation_ids=(), profile_ids=(), profiles_raise=None):
    client = MagicMock()
    client.list_foundation_models.return_value = {"modelSummaries": [{"modelId": m} for m in foundation_ids]}
    paginator = MagicMock()
    if profiles_raise is not None:
        paginator.paginate.side_effect = profiles_raise
    else:
        paginator.paginate.return_value = [
            {"inferenceProfileSummaries": [{"inferenceProfileId": p} for p in profile_ids]}
        ]
    client.get_paginator.return_value = paginator
    return client


class TestControlClientConstruction:
    def test_uses_configured_region(self):
        with patch("boto3.client") as mock_client:
            _build_bedrock_control_client(_config(aws_region="eu-central-1"))
        assert mock_client.call_args.kwargs["region_name"] == "eu-central-1"

    def test_defaults_region_when_unset(self):
        with patch("boto3.client") as mock_client:
            _build_bedrock_control_client(_config(aws_region=None))
        assert mock_client.call_args.kwargs["region_name"] == "us-east-1"

    def test_passes_explicit_credentials_when_both_present(self):
        with patch("boto3.client") as mock_client:
            _build_bedrock_control_client(_config(aws_access_key_id="AK", aws_secret_access_key="SK"))
        kwargs = mock_client.call_args.kwargs
        assert kwargs["aws_access_key_id"] == "AK"
        assert kwargs["aws_secret_access_key"] == "SK"

    def test_omits_credentials_when_only_one_half_present(self):
        with patch("boto3.client") as mock_client:
            _build_bedrock_control_client(_config(aws_access_key_id="AK", aws_secret_access_key=None))
        assert "aws_access_key_id" not in mock_client.call_args.kwargs

    def test_targets_the_control_plane_not_the_runtime(self):
        with patch("boto3.client") as mock_client:
            _build_bedrock_control_client(_config())
        assert mock_client.call_args.args[0] == "bedrock"


class TestFetchInvocableModelIds:
    def test_combines_foundation_and_profile_ids(self):
        client = _client(
            foundation_ids=["meta.llama3-3-70b-instruct-v1:0"],
            profile_ids=["us.meta.llama3-3-70b-instruct-v1:0"],
        )
        with patch("autolangchat.model_capabilities._build_bedrock_control_client", return_value=client):
            result = fetch_invocable_model_ids(_config())

        assert result == {
            "meta.llama3-3-70b-instruct-v1:0",
            "us.meta.llama3-3-70b-instruct-v1:0",
        }

    def test_survives_denied_inference_profiles(self):
        client = _client(
            foundation_ids=["anthropic.claude-sonnet-5"],
            profiles_raise=Exception("AccessDeniedException"),
        )
        with patch("autolangchat.model_capabilities._build_bedrock_control_client", return_value=client):
            result = fetch_invocable_model_ids(_config())

        assert result == {"anthropic.claude-sonnet-5"}

    def test_propagates_foundation_model_failure(self):
        client = MagicMock()
        client.list_foundation_models.side_effect = Exception("AccessDeniedException")
        with patch("autolangchat.model_capabilities._build_bedrock_control_client", return_value=client):
            with pytest.raises(Exception, match="AccessDenied"):
                fetch_invocable_model_ids(_config())

    def test_skips_entries_without_an_id(self):
        client = MagicMock()
        client.list_foundation_models.return_value = {"modelSummaries": [{"modelId": "a"}, {}, {"modelId": None}]}
        paginator = MagicMock()
        paginator.paginate.return_value = [{"inferenceProfileSummaries": []}]
        client.get_paginator.return_value = paginator
        with patch("autolangchat.model_capabilities._build_bedrock_control_client", return_value=client):
            assert fetch_invocable_model_ids(_config()) == {"a"}


class TestDiscoverInvocableModelIds:
    @pytest.mark.asyncio
    async def test_returns_the_fetched_set(self):
        with patch(
            "autolangchat.model_capabilities.fetch_invocable_model_ids",
            return_value={"anthropic.claude-sonnet-5"},
        ):
            result = await discover_invocable_model_ids(_config())
        assert result == {"anthropic.claude-sonnet-5"}

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self):
        with patch(
            "autolangchat.model_capabilities.fetch_invocable_model_ids",
            side_effect=Exception("AccessDeniedException"),
        ):
            assert await discover_invocable_model_ids(_config()) is None

    @pytest.mark.asyncio
    async def test_returns_none_on_timeout(self):
        import time

        with patch(
            "autolangchat.model_capabilities.fetch_invocable_model_ids",
            side_effect=lambda cfg: time.sleep(0.5),
        ):
            assert await discover_invocable_model_ids(_config(), timeout=0.01) is None


class TestChatConfigCatalogFiltering:
    def test_unfiltered_by_default(self):
        config = ChatConfig()
        assert len(config.get_available_models()) > 1

    def test_intersects_with_invocable_ids(self):
        config = ChatConfig()
        catalog = config.get_available_models()
        keep = set(catalog[:3])

        config.set_invocable_model_ids(keep)
        assert set(config.get_available_models()) == keep

    def test_drops_phantom_ids(self):
        """The concrete XMGPLAT-11193 bug: openai.gpt-5.5 is in _PROFILES but
        does not exist in us-west-2."""
        config = ChatConfig(AUTOCHAT_AVAILABLE_MODELS=["openai.gpt-5.5", "anthropic.claude-sonnet-5"])
        config.set_invocable_model_ids({"anthropic.claude-sonnet-5"})

        assert config.get_available_models() == ["anthropic.claude-sonnet-5"]

    def test_empty_intersection_falls_back_to_unfiltered(self):
        """An empty dropdown is worse than an optimistic one."""
        config = ChatConfig()
        catalog = config.get_available_models()

        config.set_invocable_model_ids({"something.entirely-unrelated"})
        assert config.get_available_models() == catalog

    def test_none_restores_unfiltered_behaviour(self):
        config = ChatConfig()
        catalog = config.get_available_models()

        config.set_invocable_model_ids({catalog[0]})
        config.set_invocable_model_ids(None)
        assert config.get_available_models() == catalog

    def test_composes_with_provider_allowlist(self):
        config = ChatConfig(AUTOCHAT_PROVIDERS=["Anthropic"])
        anthropic_models = config.get_available_models()
        assert anthropic_models

        keep = {anthropic_models[0], "meta.llama3-3-70b-instruct-v1:0"}
        config.set_invocable_model_ids(keep)

        # The Meta model survives discovery but is still excluded by provider.
        assert config.get_available_models() == [anthropic_models[0]]


class TestPluginStartupHook:
    def _plugin(self, config):
        from autolangchat.plugin import AutoLangChatPlugin

        plugin = object.__new__(AutoLangChatPlugin)
        plugin.config = config
        return plugin

    @pytest.mark.asyncio
    async def test_skipped_when_disabled(self):
        config = ChatConfig(AUTOCHAT_MODEL_DISCOVERY_ENABLED=False)
        plugin = self._plugin(config)

        with patch(
            "autolangchat.model_capabilities.discover_invocable_model_ids",
            new=AsyncMock(return_value={"x"}),
        ) as mock_discover:
            await plugin._startup_discover_models()

        mock_discover.assert_not_called()
        assert config._invocable_model_ids is None

    @pytest.mark.asyncio
    async def test_applies_discovered_ids(self):
        config = ChatConfig()
        catalog = config.get_available_models()
        plugin = self._plugin(config)

        with patch(
            "autolangchat.model_capabilities.discover_invocable_model_ids",
            new=AsyncMock(return_value={catalog[0]}),
        ):
            await plugin._startup_discover_models()

        assert config.get_available_models() == [catalog[0]]

    @pytest.mark.asyncio
    async def test_failure_leaves_catalog_unfiltered(self):
        config = ChatConfig()
        catalog = config.get_available_models()
        plugin = self._plugin(config)

        with patch(
            "autolangchat.model_capabilities.discover_invocable_model_ids",
            new=AsyncMock(return_value=None),
        ):
            await plugin._startup_discover_models()

        assert config._invocable_model_ids is None
        assert config.get_available_models() == catalog
