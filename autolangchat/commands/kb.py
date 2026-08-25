"""Knowledge Base CLI commands for population and management"""

import asyncio
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import yaml

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def kb_status(config_path: Optional[str] = None, db_path: Optional[str] = None) -> Dict[str, Any]:
    """
    Check knowledge base status

    Args:
        config_path: Path to kb_sources.yaml (default: kb_sources.yaml)
        db_path: Path to vector database (default: data/knowledge_base.db)

    Returns:
        Dict with status information
    """
    try:
        # Import here to avoid loading heavy dependencies if RAG not enabled
        from ..config import load_config
        from ..db import create_kb_store

        # Load configuration
        config = load_config()

        # Use provided paths or defaults from config
        config_path = config_path or config.kb_sources_config
        db_path = db_path or config.kb_database_path

        status = {
            "rag_enabled": config.enable_rag,
            "config_file": config_path,
            "config_exists": os.path.exists(config_path),
            "database_file": db_path,
            "database_exists": os.path.exists(db_path),
            "total_chunks": 0,
            "total_documents": 0,
            "sources": [],
        }

        # Check if RAG is enabled
        if not config.enable_rag:
            logger.info("❌ RAG is disabled (ENABLE_RAG=false)")
            logger.info("   Set ENABLE_RAG=true to enable knowledge base features")
            return status

        logger.info("✅ RAG is enabled (ENABLE_RAG=true)")

        # Check config file
        if not status["config_exists"]:
            logger.warning(f"⚠️  Configuration file not found: {config_path}")
            logger.info(f"   Create {config_path} to define knowledge base sources")
            return status

        logger.info(f"✅ Configuration file found: {config_path}")

        # Parse config to show sources
        try:
            with open(config_path, "r") as f:
                kb_config = yaml.safe_load(f)

            if kb_config and "knowledge_base" in kb_config:
                kb_data = kb_config["knowledge_base"]
                enabled = kb_data.get("enabled", False)
                sources = kb_data.get("sources", [])

                status["kb_config_enabled"] = enabled
                status["sources"] = sources

                if not enabled:
                    logger.warning("⚠️  Knowledge base is disabled in config (enabled: false)")
                    logger.info("   Set 'enabled: true' in kb_sources.yaml to activate")
                else:
                    logger.info(f"✅ Knowledge base enabled with {len(sources)} source(s)")
                    for i, source in enumerate(sources, 1):
                        logger.info(f"   {i}. {source.get('name', 'Unnamed')} ({source.get('type', 'unknown')})")
        except Exception as e:
            logger.error(f"❌ Failed to parse config: {e}")
            return status

        # Check database
        if not status["database_exists"]:
            logger.warning(f"⚠️  Database not found: {db_path}")
            logger.info("   Run 'kb:populate' to create and populate the knowledge base")
            return status

        logger.info(f"✅ Database found: {db_path}")

        # Get database statistics
        try:
            if db_path != config.kb_database_path and config.kb_storage_type == "sqlite":
                config.kb_database_path = db_path
            db = create_kb_store(config)
            try:
                stats = db.get_stats()
            finally:
                db.close()

            status["total_chunks"] = stats["chunks"]
            status["total_documents"] = stats["documents"]

            logger.info("📊 Database statistics:")
            logger.info(f"   Total chunks: {stats['chunks']}")
            logger.info(f"   Total documents: {stats['documents']}")
            logger.info(f"   Total vectors: {stats['vectors']}")

            if stats["chunks"] == 0:
                logger.warning("⚠️  Database is empty - no content indexed")
                logger.info("   Run 'kb:populate' to populate the knowledge base")
            else:
                logger.info("✅ Knowledge base is ready for RAG queries")

        except Exception as e:
            logger.error(f"❌ Failed to read database: {e}")
            return status

        return status

    except Exception as e:
        logger.error(f"❌ Failed to check status: {e}")
        return {"error": str(e)}


async def kb_populate(
    config_path: Optional[str] = None, db_path: Optional[str] = None, force: bool = False, config: Optional[Any] = None
) -> bool:
    """
    Populate knowledge base from sources defined in config

    Args:
        config_path: Path to kb_sources.yaml (default: kb_sources.yaml)
        db_path: Path to vector database (default: data/knowledge_base.db)
        force: Force repopulation even if database exists
        config: Config object (if None, loads from environment)

    Returns:
        True if successful, False otherwise
    """
    try:
        # Import here to avoid loading heavy dependencies if RAG not enabled
        from ..config import load_config
        from ..db import create_kb_store
        from ..rag.bedrock_embeddings import BedrockEmbeddingClient
        from ..rag.kb_ingestion import ingest_local_source, ingest_web_source

        # Load configuration (use provided config or load from environment)
        if config is None:
            config = load_config()

        # Check if RAG is enabled
        if not config.enable_rag:
            logger.error("❌ RAG is disabled (ENABLE_RAG=false)")
            logger.info("   Set ENABLE_RAG=true to enable knowledge base features")
            return False

        logger.info("✅ RAG is enabled - proceeding with population")

        # Use provided paths or defaults from config
        config_path = config_path or config.kb_sources_config
        db_path = db_path or config.kb_database_path

        # Check if config exists
        if not os.path.exists(config_path):
            logger.error(f"❌ Configuration file not found: {config_path}")
            logger.info(f"   Create {config_path} with knowledge base sources")
            return False

        # Load and validate config
        logger.info(f"📖 Loading configuration from: {config_path}")
        with open(config_path, "r") as f:
            kb_config = yaml.safe_load(f)

        if not kb_config or "knowledge_base" not in kb_config:
            logger.error("❌ Invalid configuration: missing 'knowledge_base' section")
            return False

        kb_data = kb_config["knowledge_base"]

        # Check if KB is enabled in config
        if not kb_data.get("enabled", False):
            logger.error("❌ Knowledge base is disabled in config (enabled: false)")
            logger.info("   Set 'enabled: true' in kb_sources.yaml to activate")
            return False

        sources = kb_data.get("sources", [])
        if not sources:
            logger.error("❌ No sources defined in configuration")
            return False

        logger.info(f"✅ Found {len(sources)} source(s) to process")

        # Check if database exists and handle force flag
        if os.path.exists(db_path) and not force:
            logger.warning(f"⚠️  Database already exists: {db_path}")
            logger.info("   Use --force to overwrite existing database")
            logger.info("   Or use 'kb:update' to add new content without clearing")
            return False

        # Create database directory if needed
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
            logger.info(f"📁 Created directory: {db_dir}")

        # Initialize components
        logger.info("🔧 Initializing components...")
        bedrock_client = BedrockEmbeddingClient(config)
        if db_path != config.kb_database_path and config.kb_storage_type == "sqlite":
            config.kb_database_path = db_path
        vector_db = create_kb_store(config)

        # Create text chunker for document processing
        from ..rag.embedding_pipeline import TextChunker

        chunker = TextChunker(
            chunk_size=config.kb_chunk_size,
            chunk_overlap=config.kb_chunk_overlap,
        )

        total_chunks = 0
        total_documents = 0
        all_errors: List[str] = []

        # Track processed document URLs across sources to avoid re-embedding duplicates
        processed_urls = set()

        # Track visited URLs across sources to avoid re-crawling HTML pages
        shared_visited_urls = set()

        # Process each source
        for i, source in enumerate(sources, 1):
            source_name = source.get("name", f"Source {i}")
            source_type = source.get("type", "unknown")

            logger.info(f"\n📥 Processing source {i}/{len(sources)}: {source_name} ({source_type})")

            if source_type == "web":
                urls = source.get("urls", [])
                max_pages = source.get("max_pages", 100)

                if not urls:
                    logger.warning(f"⚠️  No URLs defined for source: {source_name}")
                    continue

                logger.info(f"   Crawling {len(urls)} URL(s), max_pages={max_pages}")

                result = await ingest_web_source(
                    vector_db=vector_db,
                    bedrock_client=bedrock_client,
                    chunker=chunker,
                    embedding_model=config.kb_embedding_model,
                    source_name=source_name,
                    urls=urls,
                    topic=source.get("topic"),
                    max_depth=source.get("max_depth", 2),
                    allowed_domains=source.get("allowed_domains"),
                    exclude_patterns=source.get("exclude_patterns"),
                    max_pages=max_pages,
                    extra_headers=source.get("headers"),
                    cookies=source.get("cookies"),
                    shared_visited_urls=shared_visited_urls,
                    processed_urls=processed_urls,
                )
                total_documents += result["documents"]
                total_chunks += result["chunks"]
                all_errors.extend(result["errors"])

            elif source_type == "local":
                path = source.get("path")
                if not path:
                    logger.warning(f"⚠️  No path defined for source: {source_name}")
                    continue

                if not os.path.exists(path):
                    logger.warning(f"⚠️  Path not found: {path}")
                    continue

                logger.info(f"   Processing local path: {path}")

                result = await ingest_local_source(
                    vector_db=vector_db,
                    bedrock_client=bedrock_client,
                    chunker=chunker,
                    embedding_model=config.kb_embedding_model,
                    source_name=source_name,
                    path=path,
                    extensions=source.get("extensions"),
                    topic=source.get("topic"),
                )
                total_documents += result["documents"]
                total_chunks += result["chunks"]
                all_errors.extend(result["errors"])

            else:
                logger.warning(f"⚠️  Unknown source type: {source_type}")

        # Final summary
        logger.info(f"\n{'=' * 60}")
        if all_errors:
            logger.warning(f"⚠️  Knowledge base population completed with {len(all_errors)} error(s):")
            for error in all_errors:
                logger.warning(f"   - {error}")
        else:
            logger.info("✅ Knowledge base population complete!")
        logger.info(f"   Database: {db_path}")
        logger.info(f"   Total documents: {total_documents}")
        logger.info(f"   Total chunks: {total_chunks}")
        logger.info(f"   Unique URLs processed: {len(processed_urls)}")
        logger.info(f"{'=' * 60}")

        vector_db.close()
        # Any per-item failure (a page/file that couldn't be chunked/embedded/
        # stored) means this wasn't a clean run, even though most content may
        # have indexed successfully -- surface that via a non-zero exit
        # rather than always reporting success.
        return not all_errors

    except Exception as e:
        logger.error(f"❌ Failed to populate knowledge base: {e}")
        import traceback

        logger.error(traceback.format_exc())
        if "vector_db" in locals():
            vector_db.close()
        return False


async def kb_update(config_path: Optional[str] = None, db_path: Optional[str] = None) -> bool:
    """
    Update knowledge base with new content (incremental update)

    This is similar to kb_populate but doesn't clear existing data

    Args:
        config_path: Path to kb_sources.yaml (default: kb_sources.yaml)
        db_path: Path to vector database (default: data/knowledge_base.db)

    Returns:
        True if successful, False otherwise
    """
    logger.info("🔄 Updating knowledge base (incremental)")
    logger.info("   Note: This does not remove old content. Use 'kb:populate --force' for full rebuild")

    # Same as populate but without the force check
    return await kb_populate(config_path=config_path, db_path=db_path, force=False)


def kb_clear(db_path: Optional[str] = None, confirm: bool = False) -> bool:
    """
    Clear all data from knowledge base

    Args:
        db_path: Path to vector database (default: data/knowledge_base.db)
        confirm: Skip confirmation prompt

    Returns:
        True if successful, False otherwise
    """
    try:
        from ..config import load_config

        # Load configuration
        config = load_config()
        db_path = db_path or config.kb_database_path

        # Check if database exists
        if not os.path.exists(db_path):
            logger.info(f"ℹ️  Database does not exist: {db_path}")
            return True

        # Confirmation prompt
        if not confirm:
            logger.warning(f"⚠️  This will DELETE all data from: {db_path}")
            response = input("   Are you sure? (yes/no): ")
            if response.lower() not in ["yes", "y"]:
                logger.info("❌ Operation cancelled")
                return False

        # Delete database file
        os.remove(db_path)
        logger.info(f"✅ Knowledge base cleared: {db_path}")
        return True

    except Exception as e:
        logger.error(f"❌ Failed to clear knowledge base: {e}")
        return False


# CLI entry point
def main():
    """CLI entry point for KB commands"""
    import argparse

    parser = argparse.ArgumentParser(
        description="Knowledge Base CLI for autolangchat",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check KB status
  python -m autolangchat.commands.kb status

  # Populate KB from config
  python -m autolangchat.commands.kb populate

  # Force repopulation (overwrites existing)
  python -m autolangchat.commands.kb populate --force

  # Update KB (incremental)
  python -m autolangchat.commands.kb update

  # Clear all KB data
  python -m autolangchat.commands.kb clear
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Status command
    status_parser = subparsers.add_parser("status", help="Check knowledge base status")
    status_parser.add_argument("--config", help="Path to kb_sources.yaml")
    status_parser.add_argument("--db", help="Path to vector database")

    # Populate command
    populate_parser = subparsers.add_parser("populate", help="Populate knowledge base")
    populate_parser.add_argument("--config", help="Path to kb_sources.yaml")
    populate_parser.add_argument("--db", help="Path to vector database")
    populate_parser.add_argument("--force", action="store_true", help="Force repopulation")

    # Update command
    update_parser = subparsers.add_parser("update", help="Update knowledge base (incremental)")
    update_parser.add_argument("--config", help="Path to kb_sources.yaml")
    update_parser.add_argument("--db", help="Path to vector database")

    # Clear command
    clear_parser = subparsers.add_parser("clear", help="Clear all knowledge base data")
    clear_parser.add_argument("--db", help="Path to vector database")
    clear_parser.add_argument("--yes", action="store_true", help="Skip confirmation")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute command
    if args.command == "status":
        kb_status(config_path=args.config, db_path=args.db)

    elif args.command == "populate":
        success = asyncio.run(kb_populate(config_path=args.config, db_path=args.db, force=args.force))
        sys.exit(0 if success else 1)

    elif args.command == "update":
        success = asyncio.run(kb_update(config_path=args.config, db_path=args.db))
        sys.exit(0 if success else 1)

    elif args.command == "clear":
        success = kb_clear(db_path=args.db, confirm=args.yes)
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
