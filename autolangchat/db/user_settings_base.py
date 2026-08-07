"""
Per-user settings storage — abstract interface.

Concrete backends live in:

* :mod:`autolangchat.db.user_settings_sqlite` (zero-config default)
* :mod:`autolangchat.db.user_settings_postgres` (production)

Use :func:`autolangchat.db.create_user_settings_store` to build the backend
selected by ``ChatConfig.user_settings_storage_type``.

This store persists the chat configuration a user picked in the Settings
sidebar (``model_id``, ``temperature``, ``max_tokens``, ``top_p``,
``enable_ai_summarization``, ``enable_rag``, ...) so it survives page
refresh, reconnect, a new session, and redeploys, instead of living only in
``session.metadata["config_overrides"]``.

The ``settings`` payload is deliberately an **opaque JSON document**, not a
typed or relational schema: the meaningful parameter set varies from model
to model (some models reject ``temperature``, output-token caps differ,
future models may expose new knobs). Validation happens at write time
through :meth:`ChatConfig.validate_overrides`, and unknown or stale keys
read back from the document are ignored by the caller rather than being
fatal — so renaming or removing an overridable parameter never bricks an
existing user's row.

Row shape
---------
Every method that returns settings data (:meth:`get_settings`,
:meth:`get_or_create_settings`, :meth:`set_settings`) returns a plain
``dict`` row with the keys:

* ``user_id`` (str) — the canonical identity, equal to ``session.user_id``.
* ``settings`` (dict) — the opaque configuration document.
* ``created_at`` / ``updated_at`` (str, ISO-8601 UTC)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional


class BaseUserSettingsStore(ABC):
    """Abstract async data-access layer for per-user chat settings.

    Concrete backends (SQLite, Postgres) implement the same surface so the
    rest of the codebase — notably the WebSocket handler — doesn't depend
    on the storage technology.
    """

    @abstractmethod
    async def open(self) -> None:
        """Acquire any underlying resources and (optionally) bootstrap schema."""

    @abstractmethod
    async def close(self) -> None:
        """Release underlying resources."""

    @abstractmethod
    async def get_settings(self, user_id: str) -> Optional[Dict[str, Any]]:
        """Return the settings row for ``user_id``, or ``None`` if missing."""

    @abstractmethod
    async def get_or_create_settings(
        self,
        user_id: str,
        defaults: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Return ``user_id``'s settings row, creating it from ``defaults`` if absent.

        Must be race safe: a user can open several tabs at once, so
        implementations insert with ``ON CONFLICT (user_id) DO NOTHING``
        (or the backend equivalent) and then read the winning row back.
        The row that already existed is never overwritten — ``defaults``
        only applies to a first-ever insert.
        """

    @abstractmethod
    async def set_settings(self, user_id: str, settings: Dict[str, Any]) -> Dict[str, Any]:
        """Replace ``user_id``'s settings document and return the stored row.

        Upsert semantics: creates the row if it does not exist yet. The
        document is replaced wholesale rather than merged — the caller owns
        merge/validation via :meth:`ChatConfig.validate_overrides`.
        """

    @abstractmethod
    async def delete_settings(self, user_id: str) -> None:
        """Remove ``user_id``'s settings row.

        Idempotent: deleting settings that don't exist is not an error.
        Note that ``config_reset`` resets the row to the default payload via
        :meth:`set_settings` rather than deleting it; this method exists for
        account cleanup.
        """
