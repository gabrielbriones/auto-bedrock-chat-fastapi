"""Per-model Bedrock capability handling.

``ChatBedrockConverse`` is constructed in four places (the chat graph's
``_build_llm()``, the conversation-title client, the ``/chat/health`` probe
and the feedback synthesizer) and each one used to hand-roll its kwargs with
Claude-Sonnet-5 semantics baked in: ``temperature`` was always sent, and
``max_tokens`` came straight from config with no regard for the selected
model's output cap. Models that reject ``temperature`` (or whose cap is
lower than the configured ``max_tokens``) failed the turn with a Bedrock
``ValidationException``.

This module centralises that logic: :func:`build_bedrock_kwargs` derives the
supported parameter set for a given model id from
``langchain_aws.data._profiles._PROFILES``, clamps ``max_tokens`` to the
model's cap and drops parameters the model doesn't support, so every
construction site behaves identically for every model in the catalog.

Profile data is incomplete in two ways this module papers over:

* Cross-region inference-profile ids (``us.meta.llama3-3-70b-instruct-v1:0``)
  are not always present in ``_PROFILES`` even when the bare foundation-model
  id is (and vice versa) -- :func:`get_model_profile` falls back between the
  two forms. This matters because ``llm_call_node`` rebuilds the client with
  a region-prefixed id when Bedrock demands an inference profile.
* Anything else genuinely wrong or missing in the upstream data goes in
  :data:`CAPABILITY_OVERRIDES`, which is merged on top of the profile entry.

Finally, :func:`discover_invocable_model_ids` answers the question the profile
table can't: which of those models the *deployment's* AWS account can actually
invoke in its region. See the "Live catalog discovery" section at the bottom.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from .config import MODEL_ID_REGION_PREFIXES, split_model_id

try:
    from langchain_aws.data._profiles import _PROFILES
except ImportError:  # pragma: no cover - config.py already fails fast on this
    _PROFILES = {}  # type: ignore[assignment]

logger = logging.getLogger(__name__)

# Explicit per-model capability corrections, merged on top of the upstream
# ``_PROFILES`` entry (see :func:`get_model_profile`). Keys are model ids;
# values are partial profile dicts using the same field names as ``_PROFILES``
# (``temperature``, ``max_output_tokens``, ``tool_calling``, ...).
#
# Empty by default: the upstream profile data is correct for every model in
# the shipped catalog. This exists so a single misreported capability can be
# fixed here instead of forking the parameter-building logic again.
#
# NOTE (XMGPLAT-11193): Llama 3.3 was previously forced to `tool_calling:
# False` here after a production sighting of it emitting an unexecuted tool
# call as plain-text JSON instead of a structured toolUse block. That was
# reverted -- disabling tool binding entirely just made the model fabricate
# full report data instead (see `_looks_like_unexecuted_tool_call` in
# `graph/nodes/llm_call.py`, which now detects and surfaces that failure mode
# directly instead of gating it on a capability flag) -- and it wasn't backed
# by a confirmed root cause (couldn't get a live Bedrock repro: this
# environment's AWS credentials lack `bedrock:InvokeModel` for that model).
CAPABILITY_OVERRIDES: Dict[str, Dict[str, Any]] = {}

# Bedrock Converse read timeout floor (seconds) for generation calls. The
# botocore default of 60s is not enough for large-output requests (e.g.
# max_tokens=8192).
DEFAULT_READ_TIMEOUT = 300

# Sentinel distinguishing "caller didn't specify, take it from chat_config"
# from "caller explicitly wants this parameter omitted" (``None``).
_UNSET = object()


def _alternate_model_ids(model_id: str) -> list:
    """Other spellings of ``model_id`` worth looking up in ``_PROFILES``.

    A model may be referenced either by its bare foundation-model id or by a
    cross-region inference-profile id; ``_PROFILES`` doesn't always carry
    both, so try the other form before giving up.
    """
    region_prefix, _provider_key = split_model_id(model_id)
    if region_prefix:
        return [model_id[len(region_prefix) + 1 :]]
    # "us" first: it has the broadest model coverage, and capabilities are the
    # same across regional variants of the same model anyway.
    prefixes = ["us"] + sorted(MODEL_ID_REGION_PREFIXES - {"us"})
    return [f"{prefix}.{model_id}" for prefix in prefixes]


def _lookup(table: Dict[str, Dict[str, Any]], model_id: str) -> Dict[str, Any]:
    """First entry in ``table`` keyed by ``model_id`` or an alternate spelling of it."""
    for candidate in (model_id, *_alternate_model_ids(model_id)):
        entry = table.get(candidate)
        if entry is not None:
            return entry
    return {}


def get_model_profile(model_id: str) -> Dict[str, Any]:
    """Capability profile for ``model_id``, or ``{}`` when nothing is known.

    Falls back to the other region-prefix spelling of the id (see
    :func:`_alternate_model_ids`) and merges :data:`CAPABILITY_OVERRIDES` on
    top of whatever was found -- looked up the same way, so an override keyed
    by the bare model id still applies to a region-prefixed inference-profile
    id (e.g. the one `llm_call_node` retries with) and vice versa.
    """
    return {**_lookup(_PROFILES, model_id), **_lookup(CAPABILITY_OVERRIDES, model_id)}


def supports_temperature(model_id: str) -> bool:
    """Whether ``model_id`` accepts a ``temperature`` (and, by extension,
    ``top_p``) sampling parameter.

    ``_PROFILES`` has no separate ``top_p`` flag, and models that disable
    temperature sampling (e.g. reasoning models such as Claude Sonnet 5)
    don't accept ``top_p`` either -- the settings sidebar already shows and
    hides both controls together off this same flag. Unknown models default
    to ``True``, preserving the previous always-send behaviour.
    """
    return bool(get_model_profile(model_id).get("temperature", True))


def supports_tool_calling(model_id: str) -> bool:
    """Whether ``model_id`` can be bound with tools. Unknown models default to
    ``True`` so an unprofiled model still gets its tools (and fails loudly at
    call time) rather than silently losing them."""
    return bool(get_model_profile(model_id).get("tool_calling", True))


def get_max_output_tokens(model_id: str) -> Optional[int]:
    """The model's output-token cap, or ``None`` when unknown."""
    cap = get_model_profile(model_id).get("max_output_tokens")
    return cap if isinstance(cap, int) and cap > 0 else None


def clamp_max_tokens(model_id: str, max_tokens: Optional[int]) -> Optional[int]:
    """Clamp ``max_tokens`` down to ``model_id``'s output cap.

    Returns ``max_tokens`` unchanged when it already fits, when the model's
    cap is unknown, or when ``max_tokens`` is ``None``.
    """
    if max_tokens is None:
        return None
    cap = get_max_output_tokens(model_id)
    if cap is not None and max_tokens > cap:
        logger.debug(
            "Clamping max_tokens %d -> %d for model '%s'",
            max_tokens,
            cap,
            model_id,
        )
        return cap
    return max_tokens


def build_bedrock_kwargs(
    model_id: str,
    chat_config: Any = None,
    *,
    max_tokens: Any = _UNSET,
    temperature: Any = _UNSET,
    top_p: Any = _UNSET,
    region_name: Optional[str] = None,
    include_credentials: bool = True,
    read_timeout: Optional[int] = None,
) -> Dict[str, Any]:
    """Build the ``ChatBedrockConverse(**kwargs)`` payload for ``model_id``.

    Only parameters the model actually supports are included, and
    ``max_tokens`` is clamped to the model's cap.

    Args:
        model_id: The Bedrock model id the client will be built for.
        chat_config: A :class:`~autolangchat.config.ChatConfig` (or anything
            exposing the same attributes) used as the default source for
            region, credentials and sampling parameters. Optional.
        max_tokens: Explicit output-token cap. Defaults to
            ``chat_config.max_tokens``; pass ``None`` to omit it entirely.
        temperature: Explicit sampling temperature. Defaults to
            ``chat_config.temperature``; pass ``None`` to omit it.
        top_p: Explicit nucleus-sampling value. Defaults to
            ``chat_config.top_p``; pass ``None`` to omit it. Only used when
            ``temperature`` resolves to ``None``, since Bedrock Converse
            rejects requests specifying both.
        region_name: AWS region override; defaults to
            ``chat_config.aws_region``.
        include_credentials: When ``True`` (default), copy explicit AWS
            credentials from ``chat_config`` if both id and secret are set.
        read_timeout: When set, attach a ``botocore`` client config with this
            read timeout (floored at :data:`DEFAULT_READ_TIMEOUT`) so
            long-running generations don't trip botocore's 60s default.
    """

    def _from_config(name: str, default: Any = None) -> Any:
        return getattr(chat_config, name, default) if chat_config is not None else default

    kwargs: Dict[str, Any] = {
        "model": model_id,
        "region_name": region_name or _from_config("aws_region") or "us-east-1",
    }

    resolved_max_tokens = _from_config("max_tokens") if max_tokens is _UNSET else max_tokens
    resolved_max_tokens = clamp_max_tokens(model_id, resolved_max_tokens)
    if resolved_max_tokens is not None:
        kwargs["max_tokens"] = resolved_max_tokens

    resolved_temperature = _from_config("temperature") if temperature is _UNSET else temperature
    resolved_top_p = _from_config("top_p") if top_p is _UNSET else top_p

    if not supports_temperature(model_id):
        # The model rejects both sampling knobs -- sending either one is a
        # ValidationException, so fall back to the model's own defaults.
        if resolved_temperature is not None or resolved_top_p is not None:
            logger.debug(
                "Model '%s' does not support temperature/top_p; omitting both",
                model_id,
            )
    elif resolved_temperature is not None:
        # Bedrock Converse rejects requests carrying both temperature and
        # top_p, so temperature wins when both are configured.
        kwargs["temperature"] = resolved_temperature
    elif resolved_top_p is not None:
        kwargs["top_p"] = resolved_top_p

    if include_credentials:
        access_key = _from_config("aws_access_key_id")
        secret_key = _from_config("aws_secret_access_key")
        if access_key and secret_key:
            kwargs["aws_access_key_id"] = access_key
            kwargs["aws_secret_access_key"] = secret_key

    if read_timeout is not None:
        try:
            from botocore.config import Config as BotocoreConfig

            kwargs["config"] = BotocoreConfig(
                read_timeout=max(DEFAULT_READ_TIMEOUT, read_timeout),
                retries={"max_attempts": 1},
            )
        except ImportError:  # pragma: no cover - botocore ships with langchain-aws
            pass

    return kwargs


# ---------------------------------------------------------------------------
# Live catalog discovery
# ---------------------------------------------------------------------------
# Everything above answers "what parameters does this model accept". The
# functions below answer the prior question: "does this model exist here at
# all". ``DEFAULT_AVAILABLE_MODELS`` is derived from ``_PROFILES``, a static
# table compiled into the installed langchain-aws release, so it describes
# models that exist somewhere in AWS -- not models this account can invoke in
# this region. Selecting one of the gaps fails at call time with::
#
#     ValidationException: The provided model identifier is invalid.
#
# Two distinct causes were observed in us-west-2 (XMGPLAT-11193): inference
# profiles for other regions (``eu.``/``jp.``/``au.`` prefixes, where only the
# deployment region's prefix resolves), and ids not offered in the account at
# all (``openai.gpt-5.4`` / ``openai.gpt-5.5``) or whose invocable id carries a
# version suffix the profile table omits (``openai.gpt-oss-120b`` vs
# ``openai.gpt-oss-120b-1:0``).

# Bedrock control-plane calls are small and fast; this only needs to tolerate a
# cold connection, not a slow generation.
DEFAULT_DISCOVERY_TIMEOUT_SECONDS = 15


def _build_bedrock_control_client(chat_config: Any) -> Any:
    """Create a boto3 ``bedrock`` (control-plane) client from ``chat_config``.

    Mirrors the credential/region resolution in :func:`build_bedrock_kwargs`:
    explicit credentials are used only when both halves are present, otherwise
    boto3's default chain (instance role, env vars, shared config) applies.
    """
    import boto3
    from botocore.config import Config as BotocoreConfig

    client_kwargs: Dict[str, Any] = {
        "region_name": getattr(chat_config, "aws_region", None) or "us-east-1",
        "config": BotocoreConfig(
            connect_timeout=5,
            read_timeout=10,
            retries={"max_attempts": 2},
        ),
    }

    access_key = getattr(chat_config, "aws_access_key_id", None)
    secret_key = getattr(chat_config, "aws_secret_access_key", None)
    if access_key and secret_key:
        client_kwargs["aws_access_key_id"] = access_key
        client_kwargs["aws_secret_access_key"] = secret_key

    return boto3.client("bedrock", **client_kwargs)


def fetch_invocable_model_ids(chat_config: Any) -> set:
    """Return every model id addressable in this account/region (blocking).

    Combines foundation-model ids with cross-region inference-profile ids,
    since the catalog may legitimately offer either form.

    Raises whatever boto3 raises when ``ListFoundationModels`` fails -- the
    async wrapper is responsible for degrading. A failure of the *optional*
    ``ListInferenceProfiles`` call is swallowed with a warning, since a
    partial catalog still beats one full of phantoms.

    Note: ``ListFoundationModels`` reports models *offered* in the region and
    does not reflect per-model access grants, so a listed model can still fail
    with ``AccessDeniedException`` until access is requested in the Bedrock
    console. This narrows the catalog; it doesn't guarantee every entry works.
    """
    client = _build_bedrock_control_client(chat_config)

    model_ids: set = set()
    for summary in client.list_foundation_models().get("modelSummaries", []):
        model_id = summary.get("modelId")
        if model_id:
            model_ids.add(model_id)

    try:
        paginator = client.get_paginator("list_inference_profiles")
        for page in paginator.paginate():
            for summary in page.get("inferenceProfileSummaries", []):
                profile_id = summary.get("inferenceProfileId")
                if profile_id:
                    model_ids.add(profile_id)
    except Exception as exc:
        # Typically AccessDeniedException: bedrock:ListInferenceProfiles is a
        # separate IAM action from bedrock:ListFoundationModels.
        logger.warning(
            "Could not list Bedrock inference profiles (%s); the model catalog "
            "will be filtered against foundation-model ids only, so "
            "region-prefixed ids may be dropped.",
            type(exc).__name__,
        )

    return model_ids


async def discover_invocable_model_ids(
    chat_config: Any,
    timeout: float = DEFAULT_DISCOVERY_TIMEOUT_SECONDS,
) -> Optional[set]:
    """Async wrapper around :func:`fetch_invocable_model_ids`.

    boto3 is synchronous, so the call is dispatched to a worker thread and
    bounded by ``timeout`` to keep a slow or unreachable control plane from
    stalling application startup.

    Returns ``None`` on any failure (denied, unreachable, timed out, boto3
    missing), which callers must treat as "don't filter the catalog".
    Discovery must never block startup.
    """
    import asyncio

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(fetch_invocable_model_ids, chat_config),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "Bedrock model discovery timed out after %ss; using the static model catalog unfiltered.",
            timeout,
        )
        return None
    except Exception as exc:
        logger.warning(
            "Bedrock model discovery failed (%s: %s); using the static model "
            "catalog unfiltered. Grant bedrock:ListFoundationModels and "
            "bedrock:ListInferenceProfiles to enable catalog filtering.",
            type(exc).__name__,
            exc,
        )
        return None
