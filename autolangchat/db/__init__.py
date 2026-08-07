"""
Database / storage layer for autolangchat.

Modules in this package implement the persistence backends used by the
plugin:

* :mod:`.kb_base`, :mod:`.kb_sqlite`, :mod:`.kb_postgres` \u2014
  knowledge-base vector storage (SQLite + sqlite-vec or PostgreSQL +
  pgvector).
* :mod:`.feedback_base`, :mod:`.feedback_sqlite`,
  :mod:`.feedback_postgres` \u2014 user-feedback storage.
* :mod:`.token_usage_base`, :mod:`.token_usage_sqlite`,
  :mod:`.token_usage_postgres` — per-turn token-usage storage.
* :mod:`.conversation_base`, :mod:`.conversation_sqlite`,
  :mod:`.conversation_postgres` — per-user conversation metadata storage.
* :mod:`.user_settings_base`, :mod:`.user_settings_sqlite`,
  :mod:`.user_settings_postgres` — per-user chat settings storage.

The five factory functions exported here — :func:`create_kb_store`,
:func:`create_feedback_store`, :func:`create_token_usage_store`,
:func:`create_conversation_store`, and :func:`create_user_settings_store` —
instantiate the backend selected by the matching
``ChatConfig.*_storage_type`` field.

The in-memory SSO session store remains at the top-level
:mod:`autolangchat.sso.sso_session_store` module because it is a
session cache rather than a persistent backend.
"""

from __future__ import annotations

import importlib
import logging
from typing import TYPE_CHECKING, Optional

from .conversation_base import BaseConversationStore
from .conversation_sqlite import SQLiteConversationStore
from .feedback_base import (
    AllowlistFeedbackAuthorizer,
    AuthenticatedUserAuthorizer,
    BaseFeedbackStore,
    FeedbackAuthorizer,
)
from .feedback_sqlite import SQLiteFeedbackStore
from .kb_base import BaseKBStore
from .kb_sqlite import SQLiteKBStore
from .token_usage_base import BaseTokenUsageStore
from .token_usage_sqlite import SQLiteTokenUsageStore
from .user_settings_base import BaseUserSettingsStore
from .user_settings_sqlite import SQLiteUserSettingsStore

try:  # optional [postgres] extra
    from .conversation_postgres import PostgresConversationStore
except ImportError:  # pragma: no cover - exercised only without the extra
    PostgresConversationStore = None  # type: ignore[assignment,misc]

try:  # optional [postgres] extra
    from .feedback_postgres import PostgresFeedbackStore
except ImportError:  # pragma: no cover - exercised only without the extra
    PostgresFeedbackStore = None  # type: ignore[assignment,misc]

try:  # optional [postgres] extra
    from .kb_postgres import PgVectorKBStore
except ImportError:  # pragma: no cover
    PgVectorKBStore = None  # type: ignore[assignment,misc]

try:  # optional [postgres] extra
    from .token_usage_postgres import PostgresTokenUsageStore
except ImportError:  # pragma: no cover
    PostgresTokenUsageStore = None  # type: ignore[assignment,misc]

try:  # optional [postgres] extra
    from .user_settings_postgres import PostgresUserSettingsStore
except ImportError:  # pragma: no cover
    PostgresUserSettingsStore = None  # type: ignore[assignment,misc]

if TYPE_CHECKING:
    from ..config import ChatConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# KB store factory
# ---------------------------------------------------------------------------

_KB_BACKENDS = {
    "sqlite": "autolangchat.db.kb_sqlite.SQLiteKBStore",
    "pgvector": "autolangchat.db.kb_postgres.PgVectorKBStore",
}


def create_kb_store(config: "ChatConfig") -> BaseKBStore:
    """Instantiate the KB store selected by ``config.kb_storage_type``.

    Raises
    ------
    ValueError
        If the requested storage type is unknown, or if a required
        configuration field (e.g. ``kb_postgres_url`` for pgvector) is
        missing.
    """
    storage_type = config.kb_storage_type.lower()

    if storage_type not in _KB_BACKENDS:
        raise ValueError(
            f"Unknown kb_storage_type={storage_type!r}. " f"Valid options: {', '.join(sorted(_KB_BACKENDS))}"
        )

    fqn = _KB_BACKENDS[storage_type]
    module_path, class_name = fqn.rsplit(".", 1)
    module = importlib.import_module(module_path)
    cls = getattr(module, class_name)

    if storage_type == "sqlite":
        return cls(db_path=config.kb_database_path)

    if storage_type == "pgvector":
        if not config.kb_postgres_url:
            raise ValueError("kb_storage_type='pgvector' requires AUTOCHAT_KB_POSTGRES_URL to be set.")
        return cls(
            connection_url=config.kb_postgres_url,
            pool_size=config.kb_postgres_pool_size,
            embedding_dimensions=config.kb_embedding_dimensions,
        )

    raise ValueError(f"No constructor logic for storage type {storage_type!r}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Feedback store factory
# ---------------------------------------------------------------------------


def create_feedback_store(config: "ChatConfig") -> Optional[BaseFeedbackStore]:
    """Build the feedback store selected by ``config.feedback_storage_type``.

    Returns ``None`` when feedback collection is disabled or when the
    requested backend is not usable in the current environment (missing
    optional dependency, missing required config). Such cases are logged
    at WARNING so deployments don't fail to boot just because feedback is
    misconfigured \u2014 the WebSocket handler simply replies with a
    ``feedback_unavailable`` error if a client tries to submit.

    The caller is responsible for awaiting :meth:`BaseFeedbackStore.open`
    on the returned instance and for closing it on shutdown.
    """
    if not config.feedback_enabled:
        return None

    storage_type = (config.feedback_storage_type or "sqlite").lower()

    if storage_type == "sqlite":
        from .feedback_sqlite import SQLiteFeedbackStore

        db_path = config.feedback_database_path or config.kb_database_path
        if not db_path:
            logger.warning(
                "feedback_storage_type='sqlite' but neither "
                "AUTOCHAT_FEEDBACK_DATABASE_PATH nor KB_DATABASE_PATH is set; "
                "feedback collection disabled."
            )
            return None
        return SQLiteFeedbackStore(db_path=db_path, init_schema=config.feedback_init_schema)

    if storage_type == "postgres":
        connection_url = config.feedback_postgres_url or config.kb_postgres_url
        if not connection_url:
            logger.warning(
                "feedback_storage_type='postgres' but neither "
                "AUTOCHAT_FEEDBACK_POSTGRES_URL nor AUTOCHAT_KB_POSTGRES_URL "
                "is set; feedback collection disabled."
            )
            return None
        try:
            from .feedback_postgres import PostgresFeedbackStore

            return PostgresFeedbackStore(
                connection_url=connection_url,
                pool_max_size=config.feedback_postgres_pool_size,
                init_schema=config.feedback_init_schema,
            )
        except ImportError:
            logger.warning(
                "feedback_storage_type='postgres' but the [postgres] extra "
                "is not installed; feedback collection disabled.",
                exc_info=True,
            )
            return None

    logger.warning(
        "Unknown feedback_storage_type=%r; feedback collection disabled. " "Valid values: 'sqlite', 'postgres'.",
        storage_type,
    )
    return None


# ---------------------------------------------------------------------------
# Token usage store factory
# ---------------------------------------------------------------------------


def create_token_usage_store(config: "ChatConfig") -> Optional[BaseTokenUsageStore]:
    """Build the token-usage store selected by ``config.token_usage_storage_type``.

    Returns ``None`` when token-usage recording is disabled or when the
    requested backend is not usable in the current environment (missing
    optional dependency, missing required config). Such cases are logged
    at WARNING so deployments don't fail to boot just because token-usage
    recording is misconfigured — the WebSocket handler simply skips
    persistence for that turn.

    The caller is responsible for awaiting :meth:`BaseTokenUsageStore.open`
    on the returned instance and for closing it on shutdown.
    """
    if not config.token_usage_enabled:
        return None

    storage_type = (config.token_usage_storage_type or "sqlite").lower()

    if storage_type == "sqlite":
        db_path = config.token_usage_database_path or config.feedback_database_path or config.kb_database_path
        if not db_path:
            logger.warning(
                "token_usage_storage_type='sqlite' but none of "
                "AUTOCHAT_TOKEN_USAGE_DATABASE_PATH, AUTOCHAT_FEEDBACK_DATABASE_PATH, "
                "or KB_DATABASE_PATH is set; token usage recording disabled."
            )
            return None
        return SQLiteTokenUsageStore(db_path=db_path)

    if storage_type == "postgres":
        connection_url = config.token_usage_postgres_url or config.feedback_postgres_url or config.kb_postgres_url
        if not connection_url:
            logger.warning(
                "token_usage_storage_type='postgres' but none of "
                "AUTOCHAT_TOKEN_USAGE_POSTGRES_URL, AUTOCHAT_FEEDBACK_POSTGRES_URL, "
                "or AUTOCHAT_KB_POSTGRES_URL is set; token usage recording disabled."
            )
            return None
        try:
            from .token_usage_postgres import PostgresTokenUsageStore

            return PostgresTokenUsageStore(connection_url=connection_url)
        except ImportError:
            logger.warning(
                "token_usage_storage_type='postgres' but the [postgres] extra "
                "is not installed; token usage recording disabled.",
                exc_info=True,
            )
            return None

    logger.warning(
        "Unknown token_usage_storage_type=%r; token usage recording disabled. " "Valid values: 'sqlite', 'postgres'.",
        storage_type,
    )
    return None


# ---------------------------------------------------------------------------
# Conversation store factory
# ---------------------------------------------------------------------------


def create_conversation_store(config: "ChatConfig") -> Optional[BaseConversationStore]:
    """Build the conversation store selected by ``config.conversation_storage_type``.

    Returns ``None`` when conversation persistence is disabled or when the
    requested backend is not usable in the current environment (missing
    optional dependency, missing required config). Such cases are logged
    at WARNING so deployments don't fail to boot just because conversation
    persistence is misconfigured — the WebSocket handler simply responds
    with a ``conversation_persistence_disabled`` error if a client tries to
    use a conversation message type.

    The caller is responsible for awaiting :meth:`BaseConversationStore.open`
    on the returned instance and for closing it on shutdown.
    """
    if not config.conversation_persistence_enabled:
        return None

    storage_type = (config.conversation_storage_type or "sqlite").lower()
    max_conversations = config.max_conversations_per_user

    if storage_type == "sqlite":
        db_path = config.conversation_db_path or config.feedback_database_path or config.kb_database_path
        if not db_path:
            logger.warning(
                "conversation_storage_type='sqlite' but none of "
                "AUTOCHAT_CONVERSATION_DB_PATH, AUTOCHAT_FEEDBACK_DATABASE_PATH, "
                "or KB_DATABASE_PATH is set; conversation persistence disabled."
            )
            return None
        return SQLiteConversationStore(db_path=db_path, max_conversations_per_user=max_conversations)

    if storage_type == "postgres":
        connection_url = config.conversation_postgres_url or config.feedback_postgres_url or config.kb_postgres_url
        if not connection_url:
            logger.warning(
                "conversation_storage_type='postgres' but none of "
                "AUTOCHAT_CONVERSATION_POSTGRES_URL, AUTOCHAT_FEEDBACK_POSTGRES_URL, "
                "or AUTOCHAT_KB_POSTGRES_URL is set; conversation persistence disabled."
            )
            return None
        try:
            from .conversation_postgres import PostgresConversationStore

            return PostgresConversationStore(
                connection_url=connection_url,
                max_conversations_per_user=max_conversations,
            )
        except ImportError:
            logger.warning(
                "conversation_storage_type='postgres' but the [postgres] extra "
                "is not installed; conversation persistence disabled.",
                exc_info=True,
            )
            return None

    logger.warning(
        "Unknown conversation_storage_type=%r; conversation persistence disabled. "
        "Valid values: 'sqlite', 'postgres'.",
        storage_type,
    )
    return None


# ---------------------------------------------------------------------------
# User settings store factory
# ---------------------------------------------------------------------------


def _uses_postgres(config: "ChatConfig") -> bool:
    """Whether the app's other stores already run on Postgres.

    The ``user_settings`` table is one small row per user and always lives
    alongside the existing stores, so there is deliberately no
    user-settings-specific backend/URL/path setting: the backend is whatever
    the enabled sibling stores are already using.
    """
    siblings = (
        (config.conversation_persistence_enabled, config.conversation_storage_type, "postgres"),
        (config.feedback_enabled, config.feedback_storage_type, "postgres"),
        (config.token_usage_enabled, config.token_usage_storage_type, "postgres"),
        (True, config.kb_storage_type, "pgvector"),
    )
    return any(
        enabled and (storage_type or "").lower() == postgres_value for enabled, storage_type, postgres_value in siblings
    )


def create_user_settings_store(config: "ChatConfig") -> Optional[BaseUserSettingsStore]:
    """Build the user-settings store for the database the app already uses.

    Both the backend (see :func:`_uses_postgres`) and its connection URL /
    file path are resolved from the sibling stores' settings, so enabling
    persistence needs no extra configuration.

    Returns ``None`` when user-settings persistence is disabled or when no
    usable backend can be built in the current environment (missing
    connection URL, missing optional dependency). Such cases are logged at
    WARNING so deployments don't fail to boot just because settings
    persistence is unavailable — the WebSocket handler simply falls back to
    today's in-memory, session-scoped ``config_overrides`` behaviour.

    The caller is responsible for awaiting :meth:`BaseUserSettingsStore.open`
    on the returned instance and for closing it on shutdown.
    """
    if not config.user_settings_persistence_enabled:
        return None

    if _uses_postgres(config):
        connection_url = config.conversation_postgres_url or config.feedback_postgres_url or config.kb_postgres_url
        if not connection_url:
            logger.warning(
                "User settings persistence needs the Postgres backend (the other "
                "stores use it) but no connection URL is available "
                "(AUTOCHAT_CONVERSATION_POSTGRES_URL, AUTOCHAT_FEEDBACK_POSTGRES_URL, "
                "AUTOCHAT_KB_POSTGRES_URL); user settings persistence disabled."
            )
            return None
        try:
            from .user_settings_postgres import PostgresUserSettingsStore

            return PostgresUserSettingsStore(connection_url=connection_url)
        except ImportError:
            logger.warning(
                "User settings persistence needs the Postgres backend but the "
                "[postgres] extra is not installed; user settings persistence disabled.",
                exc_info=True,
            )
            return None

    db_path = config.conversation_db_path or config.feedback_database_path or config.kb_database_path
    if not db_path:
        logger.warning(
            "User settings persistence needs a SQLite database but none of "
            "AUTOCHAT_CONVERSATION_DB_PATH, AUTOCHAT_FEEDBACK_DATABASE_PATH, or "
            "KB_DATABASE_PATH is set; user settings persistence disabled."
        )
        return None
    return SQLiteUserSettingsStore(db_path=db_path)


__all__ = [
    "AllowlistFeedbackAuthorizer",
    "AuthenticatedUserAuthorizer",
    "BaseConversationStore",
    "BaseFeedbackStore",
    "BaseKBStore",
    "BaseTokenUsageStore",
    "BaseUserSettingsStore",
    "FeedbackAuthorizer",
    "PgVectorKBStore",
    "PostgresConversationStore",
    "PostgresFeedbackStore",
    "PostgresTokenUsageStore",
    "PostgresUserSettingsStore",
    "SQLiteConversationStore",
    "SQLiteFeedbackStore",
    "SQLiteKBStore",
    "SQLiteTokenUsageStore",
    "SQLiteUserSettingsStore",
    "create_conversation_store",
    "create_feedback_store",
    "create_kb_store",
    "create_token_usage_store",
    "create_user_settings_store",
]
