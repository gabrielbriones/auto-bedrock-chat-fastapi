"""Background credibility-decay task for KB synthesized articles.

XMGPLAT-10933: Documents ingested via the synthesizer (``source='feedback'``)
have their ``credibility_score`` reduced on each decay cycle.  When the score
falls to or below the configured threshold the document is flagged for removal
so it is excluded from RAG queries by default.

Wire-up: ``AutoLangChatPlugin.startup()`` calls::

    asyncio.create_task(run_credibility_decay_loop(plugin._kb_store, plugin.config))
"""

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import ChatConfig
    from .db.kb_base import BaseKBStore

logger = logging.getLogger(__name__)


async def run_credibility_decay_loop(store: "BaseKBStore", config: "ChatConfig") -> None:
    """Periodically decay credibility scores for synthesized KB articles.

    Sleeps for ``config.kb_credibility_decay_interval_hours`` hours between
    runs.  On each cycle it delegates the SQL work to
    :meth:`BaseKBStore.apply_credibility_decay`, then logs a summary.

    Only documents with ``source='feedback'`` that are not already flagged are
    affected.  The loop runs until the process exits.
    """
    interval_seconds = config.kb_credibility_decay_interval_hours * 3600

    while True:
        await asyncio.sleep(interval_seconds)
        try:
            updated, newly_flagged = store.apply_credibility_decay(
                config.kb_credibility_decay_rate,
                config.kb_credibility_removal_threshold,
            )
            if updated:
                logger.info(
                    "KB credibility decay: updated %d synthesized article(s); " "newly flagged for removal: %d",
                    updated,
                    newly_flagged,
                )
            else:
                logger.debug("KB credibility decay: no synthesized articles to update")
        except Exception:
            logger.exception("KB credibility decay task failed — will retry next cycle")
