"""Shared async helpers for ingesting KB content from web or local sources.

Extracted from the inline web/local blocks of
:func:`autolangchat.commands.kb.kb_populate` so the offline CLI populate
pipeline and the ``POST /admin/kb/sources/*`` HTTP routes execute the same
chunk/embed/store logic instead of maintaining two copies.

KB store writes (``add_document``/``add_chunk``) are synchronous and run via
``asyncio.to_thread`` so a long-running crawl/ingest triggered over HTTP does
not block the event loop of a live service.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .content_crawler import ContentCrawler

logger = logging.getLogger(__name__)

#: Called as ``progress_cb(metric_name, amount)`` after each unit of work
#: (e.g. ``("pages_processed", 1)``, ``("chunks_written", 7)``).
ProgressCallback = Callable[[str, int], None]


def _notify(progress_cb: Optional[ProgressCallback], metric: str, amount: int = 1) -> None:
    if progress_cb is not None:
        progress_cb(metric, amount)


async def ingest_web_source(
    *,
    vector_db: Any,
    bedrock_client: Any,
    chunker: Any,
    embedding_model: str,
    source_name: str,
    urls: List[str],
    topic: Optional[str] = None,
    max_depth: int = 2,
    allowed_domains: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    max_pages: int = 100,
    extra_headers: Optional[Dict[str, str]] = None,
    cookies: Optional[Dict[str, str]] = None,
    shared_visited_urls: Optional[Set[str]] = None,
    processed_urls: Optional[Set[str]] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Crawl ``urls`` and index the resulting pages into ``vector_db``.

    Used by both the offline ``kb_populate()`` CLI pipeline and
    ``POST /admin/kb/sources/web``. ``max_pages`` is enforced per top-level
    URL by :meth:`ContentCrawler.crawl_url`. ``extra_headers``/``cookies``
    are sent with every crawl request — for pages that gate content behind
    auth (e.g. a bearer token header or a session cookie). ``progress_cb``
    receives ``("pages_crawled", 1)`` as pages are fetched (the crawl phase
    can take a while before any indexing/``"pages_processed"`` progress is
    reported) in addition to the indexing-phase metrics below.

    Returns ``{"documents": int, "chunks": int, "errors": List[str]}`` for
    this source. A page failing to fetch (bad status, timeout, connection
    error) or failing during chunk/embed/store does not raise — it's
    skipped and recorded in ``errors`` so a run that indexes 0 pages
    doesn't silently report as a clean success.
    """
    processed_urls = processed_urls if processed_urls is not None else set()

    crawler = ContentCrawler(
        visited_urls=shared_visited_urls, extra_headers=extra_headers, cookies=cookies, progress_cb=progress_cb
    )

    documents = []
    for url in urls:
        logger.info(f"   🌐 Crawling: {url}")
        crawled_docs = await crawler.crawl_url(
            url=url,
            source=source_name,
            recursive=True,
            max_depth=max_depth,
            allowed_domains=allowed_domains,
            exclude_patterns=exclude_patterns,
            max_pages=max_pages,
        )
        documents.extend(crawled_docs)
        logger.info(f"      Crawled {len(crawled_docs)} page(s)")

    total_chunks = 0
    total_documents = 0
    skipped_duplicates = 0
    indexing_errors: List[str] = []

    for doc in documents:
        doc_url = doc["url"]

        # Skip if already processed (cross-source deduplication)
        if doc_url in processed_urls:
            skipped_duplicates += 1
            logger.debug(f"      Skipped duplicate: {doc_url}")
            continue

        processed_urls.add(doc_url)

        try:
            await asyncio.to_thread(
                vector_db.add_document,
                doc_id=doc_url,
                content=doc["content"],
                title=doc.get("title", ""),
                source=source_name,
                source_url=doc_url,
                topic=topic,
                date_published=None,  # Web crawled content doesn't have publish date
                metadata={
                    "source_type": "web",
                    "crawled_at": doc.get("crawled_at"),
                },
            )

            doc_dict = {
                "id": doc_url,
                "content": doc["content"],
                "title": doc.get("title", ""),
                "source": source_name,
                "url": doc_url,
                "topic": topic,
            }
            chunks_data = chunker.chunk_document(doc_dict)
            texts = [chunk["text"] for chunk in chunks_data]
            embeddings = await bedrock_client.generate_embeddings_batch(
                texts=texts, model_id=embedding_model, batch_size=25
            )

            for idx, (chunk_data, embedding) in enumerate(zip(chunks_data, embeddings)):
                chunk_id = f"{doc_url}_{idx}"
                chunk_metadata = {
                    "doc_id": doc_url,
                    "title": doc.get("title", ""),
                    "source": source_name,
                    "url": doc_url,
                    "topic": topic,
                    "date_published": None,
                }
                await asyncio.to_thread(
                    vector_db.add_chunk,
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
            _notify(progress_cb, "pages_processed")
            _notify(progress_cb, "chunks_written", len(chunks_data))
            logger.info(f"      Indexed: {doc_url} ({len(chunks_data)} chunks)")

        except Exception as e:
            message = f"failed to index {doc_url}: {e}"
            logger.error(f"      ❌ {message}")
            indexing_errors.append(message)

    if skipped_duplicates > 0:
        logger.info(f"   ℹ Skipped {skipped_duplicates} duplicate(s) from other sources")

    return {"documents": total_documents, "chunks": total_chunks, "errors": crawler.errors + indexing_errors}


async def ingest_local_source(
    *,
    vector_db: Any,
    bedrock_client: Any,
    chunker: Any,
    embedding_model: str,
    source_name: str,
    path: str,
    extensions: Optional[List[str]] = None,
    topic: Optional[str] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Index a local file or directory into ``vector_db``.

    Used by both ``kb_populate()`` and ``POST /admin/kb/sources/local``.
    Raises ``FileNotFoundError`` if ``path`` does not exist — callers
    running a single-source HTTP request should let this fail the run
    rather than silently skipping (unlike the multi-source CLI pipeline,
    which logs a warning and continues to the next configured source).

    Returns ``{"documents": int, "chunks": int, "errors": List[str]}``. A
    file failing to read/chunk/embed does not raise — it's skipped and
    recorded in ``errors`` instead.
    """
    extensions = extensions or [".txt", ".md", ".rst"]

    if not os.path.exists(path):
        raise FileNotFoundError(f"path not found: {path}")

    files: List[Path] = []
    if os.path.isfile(path):
        files = [Path(path)]
    elif os.path.isdir(path):
        for ext in extensions:
            files.extend(Path(path).rglob(f"*{ext}"))

    logger.info(f"   Found {len(files)} file(s) to process")

    total_chunks = 0
    total_documents = 0
    errors: List[str] = []

    for file_path in files:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

            doc_id = str(file_path)

            await asyncio.to_thread(
                vector_db.add_document,
                doc_id=doc_id,
                content=content,
                title=os.path.basename(file_path),
                source=source_name,
                source_url=None,
                topic=topic,
                date_published=None,
                metadata={
                    "source_type": "local",
                    "source_path": doc_id,
                    "filename": os.path.basename(file_path),
                },
            )

            doc_dict = {
                "id": doc_id,
                "content": content,
                "title": os.path.basename(file_path),
                "source": source_name,
                "topic": topic,
            }
            chunks_data = chunker.chunk_document(doc_dict)
            texts = [chunk["text"] for chunk in chunks_data]
            embeddings = await bedrock_client.generate_embeddings_batch(
                texts=texts, model_id=embedding_model, batch_size=25
            )

            for idx, (chunk_data, embedding) in enumerate(zip(chunks_data, embeddings)):
                chunk_id = f"{doc_id}_{idx}"
                chunk_metadata = {
                    "doc_id": doc_id,
                    "title": os.path.basename(file_path),
                    "source": source_name,
                    "url": None,
                    "topic": topic,
                    "date_published": None,
                }
                await asyncio.to_thread(
                    vector_db.add_chunk,
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
            _notify(progress_cb, "files_processed")
            _notify(progress_cb, "chunks_written", len(chunks_data))
            logger.info(f"      Indexed: {file_path} ({len(chunks_data)} chunks)")

        except Exception as e:
            message = f"failed to process {file_path}: {e}"
            logger.error(f"      ❌ {message}")
            errors.append(message)

    return {"documents": total_documents, "chunks": total_chunks, "errors": errors}


async def ingest_uploaded_files(
    *,
    vector_db: Any,
    bedrock_client: Any,
    chunker: Any,
    embedding_model: str,
    source_name: str,
    files: List[Tuple[str, str]],
    topic: Optional[str] = None,
    progress_cb: Optional[ProgressCallback] = None,
) -> Dict[str, Any]:
    """Index already-read file content into ``vector_db``.

    Used by ``POST /admin/kb/sources/file``. Unlike :func:`ingest_local_source`,
    this does not touch the server's filesystem — admins upload file content
    directly (they don't have shell access to the running service), so the
    caller is expected to have already read/decoded each upload into
    ``files`` as ``(filename, text_content)`` pairs before this is called.

    Returns ``{"documents": int, "chunks": int, "errors": List[str]}``. A
    file failing to chunk/embed does not raise — it's skipped and recorded
    in ``errors`` instead.
    """
    logger.info(f"   Processing {len(files)} uploaded file(s)")

    total_chunks = 0
    total_documents = 0
    errors: List[str] = []

    for filename, content in files:
        try:
            doc_id = f"{source_name}/{filename}"

            await asyncio.to_thread(
                vector_db.add_document,
                doc_id=doc_id,
                content=content,
                title=filename,
                source=source_name,
                source_url=None,
                topic=topic,
                date_published=None,
                metadata={
                    "source_type": "file",
                    "filename": filename,
                },
            )

            doc_dict = {
                "id": doc_id,
                "content": content,
                "title": filename,
                "source": source_name,
                "topic": topic,
            }
            chunks_data = chunker.chunk_document(doc_dict)
            texts = [chunk["text"] for chunk in chunks_data]
            embeddings = await bedrock_client.generate_embeddings_batch(
                texts=texts, model_id=embedding_model, batch_size=25
            )

            for idx, (chunk_data, embedding) in enumerate(zip(chunks_data, embeddings)):
                chunk_id = f"{doc_id}_{idx}"
                chunk_metadata = {
                    "doc_id": doc_id,
                    "title": filename,
                    "source": source_name,
                    "url": None,
                    "topic": topic,
                    "date_published": None,
                }
                await asyncio.to_thread(
                    vector_db.add_chunk,
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
            _notify(progress_cb, "files_processed")
            _notify(progress_cb, "chunks_written", len(chunks_data))
            logger.info(f"      Indexed: {filename} ({len(chunks_data)} chunks)")

        except Exception as e:
            message = f"failed to process uploaded file {filename}: {e}"
            logger.error(f"      ❌ {message}")
            errors.append(message)

    return {"documents": total_documents, "chunks": total_chunks, "errors": errors}
