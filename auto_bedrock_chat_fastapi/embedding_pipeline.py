"""
Embedding pipeline for knowledge base content.

This module handles:
- Text chunking with configurable size and overlap
- Embedding generation using OpenAI or local models
- Batch processing for efficiency
- Caching to avoid re-processing
"""

import asyncio
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class TextChunker:
    """Chunk text into smaller segments for embedding."""

    def __init__(self, chunk_size: int = 512, chunk_overlap: int = 100, min_chunk_size: int = 50):
        """
        Initialize text chunker.

        Args:
            chunk_size: Target chunk size in tokens (approximate)
            chunk_overlap: Overlap between chunks in tokens
            min_chunk_size: Minimum chunk size (discard smaller chunks)
        """
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self.min_chunk_size = min_chunk_size

    def chunk_text(self, text: str, metadata: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        """
        Chunk text into segments with overlap.

        Args:
            text: Text to chunk
            metadata: Optional metadata to include with each chunk

        Returns:
            List of chunk dictionaries with text and metadata
        """
        # Approximate tokens by splitting on whitespace (rough estimate)
        words = text.split()

        if len(words) == 0:
            return []

        chunks = []
        chunk_id = 0

        # Calculate step size (words per chunk minus overlap)
        words_per_chunk = self.chunk_size
        overlap_words = self.chunk_overlap
        step_size = words_per_chunk - overlap_words

        if step_size <= 0:
            step_size = words_per_chunk // 2  # Fallback to 50% overlap

        start_idx = 0

        while start_idx < len(words):
            # Extract chunk
            end_idx = min(start_idx + words_per_chunk, len(words))
            chunk_words = words[start_idx:end_idx]
            chunk_text = " ".join(chunk_words)

            # Calculate character positions in original text
            # Find the start position of the first word in the chunk
            char_start = 0
            word_count = 0
            for i, word in enumerate(words):
                if i == start_idx:
                    break
                char_start += len(word) + 1  # +1 for space
                word_count += 1

            # Calculate end position
            char_end = char_start
            for i in range(start_idx, end_idx):
                if i < len(words):
                    char_end += len(words[i])
                    if i < end_idx - 1:  # Add space except after last word
                        char_end += 1

            # Only keep chunks above minimum size
            if len(chunk_words) >= self.min_chunk_size:
                chunk_dict = {
                    "chunk_id": chunk_id,
                    "text": chunk_text,
                    "start_word": start_idx,
                    "end_word": end_idx,
                    "start_char": char_start,
                    "end_char": char_end,
                    "word_count": len(chunk_words),
                    "is_continuation": chunk_id > 0,
                }

                # Add metadata if provided
                if metadata:
                    chunk_dict["metadata"] = metadata.copy()

                chunks.append(chunk_dict)
                chunk_id += 1

            # Move to next chunk with overlap
            start_idx += step_size

            # Break if we've covered all words
            if end_idx >= len(words):
                break

        logger.info(f"Chunked text into {len(chunks)} segments")
        return chunks

    def chunk_document(self, document: Dict[str, Any], preserve_structure: bool = True) -> List[Dict[str, Any]]:
        """
        Chunk a document while preserving metadata.

        Args:
            document: Document dict with 'content' and metadata
            preserve_structure: Try to split on paragraph boundaries

        Returns:
            List of chunk dictionaries
        """
        content = document.get("content", "")

        # Extract metadata to include with chunks - check both top level and nested metadata
        doc_metadata = document.get("metadata", {})
        metadata = {
            "doc_id": document.get("id"),
            "title": document.get("title") or doc_metadata.get("title"),
            "source": document.get("source") or doc_metadata.get("source"),
            "url": document.get("url") or doc_metadata.get("source_url"),
            "topic": document.get("topic") or doc_metadata.get("topic"),
            "date_published": document.get("date_published") or doc_metadata.get("date_published"),
        }

        if preserve_structure:
            # Try to split on paragraph boundaries
            paragraphs = re.split(r"\n\n+", content)

            # Chunk each paragraph separately, then combine small ones
            all_chunks = []
            current_text = ""

            for para in paragraphs:
                para = para.strip()
                if not para:
                    continue

                para_words = len(para.split())
                current_words = len(current_text.split())

                # If adding this paragraph exceeds chunk size, process current text
                if current_words > 0 and current_words + para_words > self.chunk_size:
                    chunks = self.chunk_text(current_text, metadata)
                    all_chunks.extend(chunks)
                    current_text = para
                else:
                    # Add to current text
                    if current_text:
                        current_text += "\n\n" + para
                    else:
                        current_text = para

            # Process remaining text
            if current_text:
                chunks = self.chunk_text(current_text, metadata)
                all_chunks.extend(chunks)

            # Renumber chunks
            for i, chunk in enumerate(all_chunks):
                chunk["chunk_id"] = i
                chunk["is_continuation"] = i > 0

            return all_chunks
        else:
            # Simple chunking without structure preservation
            return self.chunk_text(content, metadata)


class EmbeddingGenerator:
    """Generate embeddings using AWS Bedrock or other providers."""

    def __init__(
        self,
        bedrock_client=None,
        model: str = "amazon.titan-embed-text-v1",
        cache_dir: Optional[str] = None,
        batch_size: int = 25,
    ):
        """
        Initialize embedding generator.

        Args:
            bedrock_client: BedrockClient instance (required for AWS Bedrock models)
            model: Model name:
                - "amazon.titan-embed-text-v1" (1536 dimensions, default)
                - "amazon.titan-embed-text-v2:0" (configurable dimensions)
                - "cohere.embed-english-v3"
                - "cohere.embed-multilingual-v3"
            cache_dir: Directory to cache embeddings
            batch_size: Number of texts to embed in one batch
        """
        self.model = model
        self.batch_size = batch_size
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.bedrock_client = bedrock_client

        # Validate configuration
        if model.startswith(("amazon.titan", "cohere.embed")):
            if bedrock_client is None:
                raise ValueError("bedrock_client required for AWS Bedrock embedding models")
        else:
            raise ValueError(f"Unsupported model: {model}. Use AWS Bedrock models (amazon.titan-*, cohere.embed-*)")

        # Initialize cache
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info(f"Embedding cache: {self.cache_dir}")

    def _get_cache_key(self, text: str) -> str:
        """Generate cache key for text."""
        content = f"{self.model}:{text}"
        return hashlib.sha256(content.encode()).hexdigest()

    def _load_from_cache(self, cache_key: str) -> Optional[List[float]]:
        """Load embedding from cache."""
        if not self.cache_dir:
            return None

        cache_file = self.cache_dir / f"{cache_key}.json"
        if cache_file.exists():
            try:
                with open(cache_file, "r") as f:
                    data = json.load(f)
                return data["embedding"]
            except Exception as e:
                logger.warning(f"Failed to load from cache: {e}")

        return None

    def _save_to_cache(self, cache_key: str, embedding: List[float]):
        """Save embedding to cache."""
        if not self.cache_dir:
            return

        cache_file = self.cache_dir / f"{cache_key}.json"
        try:
            with open(cache_file, "w") as f:
                json.dump({"embedding": embedding}, f)
        except Exception as e:
            logger.warning(f"Failed to save to cache: {e}")

    def generate_embedding(self, text: str) -> List[float]:
        """
        Generate embedding for a single text.

        Args:
            text: Input text

        Returns:
            Embedding vector as list of floats
        """
        # Check cache
        cache_key = self._get_cache_key(text)
        cached = self._load_from_cache(cache_key)
        if cached:
            logger.debug(f"Cache hit for text: {text[:50]}...")
            return cached

        # Generate embedding using Bedrock (async operation)
        import asyncio

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)

        if loop.is_running():
            # If loop is already running, use run_coroutine_threadsafe
            future = asyncio.run_coroutine_threadsafe(self.bedrock_client.generate_embedding(text, self.model), loop)
            embedding = future.result()
        else:
            # If no loop is running, use run_until_complete
            embedding = loop.run_until_complete(self.bedrock_client.generate_embedding(text, self.model))

        # Save to cache
        self._save_to_cache(cache_key, embedding)

        return embedding

    def generate_embeddings_batch(self, texts: List[str], show_progress: bool = True) -> List[List[float]]:
        """
        Generate embeddings for multiple texts in batches.

        Args:
            texts: List of input texts
            show_progress: Show progress logging

        Returns:
            List of embedding vectors
        """
        embeddings = []

        # Process in batches
        for i in range(0, len(texts), self.batch_size):
            batch = texts[i : i + self.batch_size]

            if show_progress:
                logger.info(f"Processing batch {i // self.batch_size + 1}/{(len(texts) - 1) // self.batch_size + 1}")

            # Check cache for each text
            batch_embeddings = []
            texts_to_generate = []
            indices_to_generate = []

            for j, text in enumerate(batch):
                cache_key = self._get_cache_key(text)
                cached = self._load_from_cache(cache_key)

                if cached:
                    batch_embeddings.append((j, cached))
                else:
                    texts_to_generate.append(text)
                    indices_to_generate.append(j)

            # Generate embeddings for uncached texts using Bedrock batch API
            if texts_to_generate:
                # Check if we're in an async context
                try:
                    asyncio.get_running_loop()
                    # We're in an async context - this shouldn't happen in sync method
                    # Raise error to guide user to use async version
                    raise RuntimeError(
                        "Cannot call generate_embeddings_batch() from async context. "
                        "Use await bedrock_client.generate_embeddings_batch() directly instead."
                    )
                except RuntimeError:
                    # No running loop - we can use asyncio.run()
                    new_embeddings = asyncio.run(
                        self.bedrock_client.generate_embeddings_batch(texts_to_generate, self.model, self.batch_size)
                    )

                # Cache and add to results
                for text, embedding, idx in zip(texts_to_generate, new_embeddings, indices_to_generate):
                    cache_key = self._get_cache_key(text)
                    self._save_to_cache(cache_key, embedding)
                    batch_embeddings.append((idx, embedding))

            # Sort by original index and extract embeddings
            batch_embeddings.sort(key=lambda x: x[0])
            embeddings.extend([emb for _, emb in batch_embeddings])

        logger.info(f"Generated {len(embeddings)} embeddings")
        return embeddings


class EmbeddingPipeline:
    """Complete pipeline for chunking and embedding documents."""

    def __init__(self, chunker: Optional[TextChunker] = None, generator: Optional[EmbeddingGenerator] = None):
        """
        Initialize embedding pipeline.

        Args:
            chunker: Text chunker (uses default if not provided)
            generator: Embedding generator (must be provided)
        """
        self.chunker = chunker or TextChunker()

        if generator is None:
            raise ValueError("EmbeddingGenerator must be provided")

        self.generator = generator

    def process_document(self, document: Dict[str, Any], preserve_structure: bool = True) -> List[Dict[str, Any]]:
        """
        Process a single document: chunk and embed.

        Args:
            document: Document dict with content and metadata
            preserve_structure: Preserve paragraph structure when chunking

        Returns:
            List of chunks with embeddings
        """
        # Chunk document
        chunks = self.chunker.chunk_document(document, preserve_structure)

        if not chunks:
            logger.warning(f"No chunks generated for document: {document.get('id')}")
            return []

        # Extract texts
        texts = [chunk["text"] for chunk in chunks]

        # Generate embeddings
        embeddings = self.generator.generate_embeddings_batch(texts, show_progress=False)

        # Add embeddings to chunks
        for chunk, embedding in zip(chunks, embeddings):
            chunk["embedding"] = embedding

        logger.info(f"Processed document {document.get('id')}: {len(chunks)} chunks")
        return chunks

    def process_documents_batch(
        self, documents: List[Dict[str, Any]], preserve_structure: bool = True
    ) -> List[Tuple[str, List[Dict[str, Any]]]]:
        """
        Process multiple documents in batch.

        Args:
            documents: List of document dicts
            preserve_structure: Preserve paragraph structure

        Returns:
            List of (doc_id, chunks) tuples
        """
        results = []

        for i, doc in enumerate(documents):
            logger.info(f"Processing document {i + 1}/{len(documents)}: {doc.get('title', 'Untitled')}")

            chunks = self.process_document(doc, preserve_structure)
            results.append((doc.get("id"), chunks))

        total_chunks = sum(len(chunks) for _, chunks in results)
        logger.info(f"Processed {len(documents)} documents → {total_chunks} total chunks")

        return results
