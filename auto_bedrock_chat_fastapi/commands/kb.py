"""Knowledge Base CLI commands for population and management"""

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, Optional

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
        from ..vector_db import VectorDB

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
            db = VectorDB(db_path)
            stats = db.get_stats()

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
        from ..bedrock_client import BedrockClient
        from ..config import load_config
        from ..content_crawler import ContentCrawler
        from ..vector_db import VectorDB

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
        bedrock_client = BedrockClient(config)
        vector_db = VectorDB(db_path)

        # Create text chunker for document processing
        from ..embedding_pipeline import TextChunker

        chunker = TextChunker(
            chunk_size=config.kb_chunk_size,
            chunk_overlap=config.kb_chunk_overlap,
        )

        total_chunks = 0
        total_documents = 0

        # Track processed document URLs across sources to avoid re-embedding duplicates
        processed_urls = set()

        # Process each source
        for i, source in enumerate(sources, 1):
            source_name = source.get("name", f"Source {i}")
            source_type = source.get("type", "unknown")

            logger.info(f"\n📥 Processing source {i}/{len(sources)}: {source_name} ({source_type})")

            if source_type == "web":
                # Web crawling
                urls = source.get("urls", [])
                max_pages = source.get("max_pages", 100)

                if not urls:
                    logger.warning(f"⚠️  No URLs defined for source: {source_name}")
                    continue

                logger.info(f"   Crawling {len(urls)} URL(s), max_pages={max_pages}")

                # Initialize crawler with shared visited_urls to skip already-crawled pages
                crawler = ContentCrawler(visited_urls=processed_urls)

                documents = []
                for url in urls:
                    logger.info(f"   🌐 Crawling: {url}")
                    # Pass crawl parameters including max_pages
                    crawled_docs = await crawler.crawl_url(
                        url=url,
                        source=source_name,
                        recursive=True,
                        max_depth=source.get("max_depth", 2),
                        allowed_domains=source.get("allowed_domains"),
                        exclude_patterns=source.get("exclude_patterns"),
                    )
                    documents.extend(crawled_docs)
                    logger.info(f"      Crawled {len(crawled_docs)} page(s)")

                logger.info(f"   ✅ Total pages crawled: {len(documents)}")

                # Process and store
                skipped_duplicates = 0
                for doc in documents:
                    doc_url = doc["url"]

                    # Skip if already processed (cross-source deduplication)
                    if doc_url in processed_urls:
                        skipped_duplicates += 1
                        logger.debug(f"      Skipped duplicate: {doc_url}")
                        continue

                    processed_urls.add(doc_url)

                    # Add document to documents table first
                    vector_db.add_document(
                        doc_id=doc_url,
                        content=doc["content"],
                        title=doc.get("title", ""),
                        source=source_name,
                        source_url=doc_url,
                        topic=source.get("topic"),
                        date_published=None,  # Web crawled content doesn't have publish date
                        metadata={
                            "source_type": "web",
                            "crawled_at": doc.get("crawled_at"),
                        },
                    )

                    # Create document dict for chunking with proper structure
                    doc_dict = {
                        "id": doc_url,
                        "content": doc["content"],
                        "title": doc.get("title", ""),
                        "source": source_name,
                        "url": doc_url,
                        "topic": source.get("topic"),
                    }

                    # Chunk the document
                    chunks_data = chunker.chunk_document(doc_dict)

                    # Extract texts for embedding
                    texts = [chunk["text"] for chunk in chunks_data]

                    # Generate embeddings directly using bedrock_client (async)
                    embeddings = await bedrock_client.generate_embeddings_batch(
                        texts=texts, model_id=config.kb_embedding_model, batch_size=25
                    )

                    # Store chunks with embeddings and proper metadata
                    for idx, (chunk_data, embedding) in enumerate(zip(chunks_data, embeddings)):
                        chunk_id = f"{doc_url}_{idx}"

                        # Build chunk metadata with document references
                        chunk_metadata = {
                            "doc_id": doc_url,
                            "title": doc.get("title", ""),
                            "source": source_name,
                            "url": doc_url,
                            "topic": source.get("topic"),
                            "date_published": None,
                        }

                        vector_db.add_chunk(
                            chunk_id=chunk_id,
                            document_id=doc_url,
                            content=chunk_data["text"],
                            embedding=embedding,
                            chunk_index=idx,
                            start_char=chunk_data.get("start_char"),
                            end_char=chunk_data.get("end_char"),
                            metadata=chunk_metadata,
                        )

                    total_chunks += len(chunks_data)
                    total_documents += 1
                    logger.info(f"      Indexed: {doc_url} ({len(chunks_data)} chunks)")

                if skipped_duplicates > 0:
                    logger.info(f"   ℹ Skipped {skipped_duplicates} duplicate(s) from other sources")

            elif source_type == "local":
                # Local file processing
                path = source.get("path")
                if not path:
                    logger.warning(f"⚠️  No path defined for source: {source_name}")
                    continue

                if not os.path.exists(path):
                    logger.warning(f"⚠️  Path not found: {path}")
                    continue

                logger.info(f"   Processing local path: {path}")

                # Read file or directory
                files = []
                if os.path.isfile(path):
                    files = [path]
                elif os.path.isdir(path):
                    # Find all text files
                    for ext in source.get("extensions", [".txt", ".md", ".rst"]):
                        files.extend(Path(path).rglob(f"*{ext}"))

                logger.info(f"   Found {len(files)} file(s) to process")

                for file_path in files:
                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()

                        doc_id = str(file_path)

                        # Add document to documents table first
                        vector_db.add_document(
                            doc_id=doc_id,
                            content=content,
                            title=os.path.basename(file_path),
                            source=source_name,
                            source_url=None,
                            topic=source.get("topic"),
                            date_published=None,
                            metadata={
                                "source_type": "local",
                                "source_path": doc_id,
                                "filename": os.path.basename(file_path),
                            },
                        )

                        # Create document dict for chunking with proper structure
                        doc_dict = {
                            "id": doc_id,
                            "content": content,
                            "title": os.path.basename(file_path),
                            "source": source_name,
                            "topic": source.get("topic"),
                        }

                        # Chunk the document
                        chunks_data = chunker.chunk_document(doc_dict)

                        # Extract texts for embedding
                        texts = [chunk["text"] for chunk in chunks_data]

                        # Generate embeddings directly using bedrock_client (async)
                        embeddings = await bedrock_client.generate_embeddings_batch(
                            texts=texts, model_id=config.kb_embedding_model, batch_size=25
                        )

                        # Store chunks with embeddings and proper metadata
                        for idx, (chunk_data, embedding) in enumerate(zip(chunks_data, embeddings)):
                            chunk_id = f"{doc_id}_{idx}"

                            # Build chunk metadata with document references
                            chunk_metadata = {
                                "doc_id": doc_id,
                                "title": os.path.basename(file_path),
                                "source": source_name,
                                "url": None,
                                "topic": source.get("topic"),
                                "date_published": None,
                            }

                            vector_db.add_chunk(
                                chunk_id=chunk_id,
                                document_id=doc_id,
                                content=chunk_data["text"],
                                embedding=embedding,
                                chunk_index=idx,
                                start_char=chunk_data.get("start_char"),
                                end_char=chunk_data.get("end_char"),
                                metadata=chunk_metadata,
                            )

                        total_chunks += len(chunks_data)
                        total_documents += 1
                        logger.info(f"      Indexed: {file_path} ({len(chunks_data)} chunks)")

                    except Exception as e:
                        logger.error(f"      ❌ Failed to process {file_path}: {e}")

            else:
                logger.warning(f"⚠️  Unknown source type: {source_type}")

        # Final summary
        logger.info(f"\n{'='*60}")
        logger.info("✅ Knowledge base population complete!")
        logger.info(f"   Database: {db_path}")
        logger.info(f"   Total documents: {total_documents}")
        logger.info(f"   Total chunks: {total_chunks}")
        logger.info(f"   Unique URLs processed: {len(processed_urls)}")
        logger.info(f"{'='*60}")

        return True

    except Exception as e:
        logger.error(f"❌ Failed to populate knowledge base: {e}")
        import traceback

        logger.error(traceback.format_exc())
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
        description="Knowledge Base CLI for auto-bedrock-chat-fastapi",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Check KB status
  python -m auto_bedrock_chat_fastapi.commands.kb status

  # Populate KB from config
  python -m auto_bedrock_chat_fastapi.commands.kb populate

  # Force repopulation (overwrites existing)
  python -m auto_bedrock_chat_fastapi.commands.kb populate --force

  # Update KB (incremental)
  python -m auto_bedrock_chat_fastapi.commands.kb update

  # Clear all KB data
  python -m auto_bedrock_chat_fastapi.commands.kb clear
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
