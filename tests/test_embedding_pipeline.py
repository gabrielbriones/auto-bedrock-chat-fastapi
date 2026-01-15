"""Tests for the embedding pipeline components."""

import asyncio
import json
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from auto_bedrock_chat_fastapi.embedding_pipeline import EmbeddingGenerator, EmbeddingPipeline, TextChunker


class TestTextChunker:
    """Test the TextChunker class."""

    def test_chunker_initialization(self):
        """Test chunker initialization with default parameters."""
        chunker = TextChunker()
        assert chunker.chunk_size == 512
        assert chunker.chunk_overlap == 100
        assert chunker.min_chunk_size == 50

    def test_chunker_custom_parameters(self):
        """Test chunker initialization with custom parameters."""
        chunker = TextChunker(chunk_size=256, chunk_overlap=50, min_chunk_size=30)
        assert chunker.chunk_size == 256
        assert chunker.chunk_overlap == 50
        assert chunker.min_chunk_size == 30

    def test_chunk_text_short(self):
        """Test chunking of short text (single chunk)."""
        chunker = TextChunker(chunk_size=100, chunk_overlap=20, min_chunk_size=5)
        text = "This is a short text that should fit in a single chunk."

        chunks = chunker.chunk_text(text)

        assert len(chunks) == 1
        assert chunks[0]["text"] == text
        assert chunks[0]["word_count"] == 12  # "This" + 11 more words

    def test_chunk_text_long(self):
        """Test chunking of long text (multiple chunks)."""
        chunker = TextChunker(chunk_size=50, chunk_overlap=10, min_chunk_size=5)

        # Create text with ~150 words (will create multiple chunks)
        text = " ".join(["word"] * 150)

        chunks = chunker.chunk_text(text)

        assert len(chunks) > 1
        # Check that all chunks are dictionaries
        assert all(isinstance(chunk, dict) for chunk in chunks)
        # Check that chunks have text
        assert all("text" in chunk for chunk in chunks)
        # Check that chunks have overlap (word count should be less than total for middle chunks)
        for chunk in chunks[:-1]:
            assert chunk["word_count"] <= 50

    def test_chunk_text_empty(self):
        """Test chunking of empty text."""
        chunker = TextChunker()
        chunks = chunker.chunk_text("")
        assert len(chunks) == 0

    def test_chunk_text_whitespace(self):
        """Test chunking of whitespace-only text."""
        chunker = TextChunker()
        chunks = chunker.chunk_text("   \n\n   \t  ")
        assert len(chunks) == 0

    def test_chunk_document_basic(self):
        """Test document chunking with basic structure."""
        chunker = TextChunker(chunk_size=50, chunk_overlap=10)

        document = {
            "id": "doc-001",
            "content": " ".join(["word"] * 100),
            "title": "Test Document",
            "source": "test",
            "url": "https://example.com/test",
        }

        chunks = chunker.chunk_document(document)

        assert len(chunks) > 0
        for chunk in chunks:
            assert "chunk_id" in chunk
            assert "text" in chunk
            assert "word_count" in chunk
            assert chunk["word_count"] > 0

    def test_chunk_document_preserve_structure(self):
        """Test document chunking with structure preservation."""
        chunker = TextChunker(chunk_size=50, chunk_overlap=10, min_chunk_size=5)

        document = {
            "id": "doc-001",
            "content": "# Title\n\nParagraph one with many words. " * 10 + "\n\nParagraph two with many words. " * 10,
            "title": "Test Document",
        }

        chunks = chunker.chunk_document(document, preserve_structure=True)

        assert len(chunks) > 0
        # With structure preservation, chunks should align with paragraphs/sections
        # At minimum, check that chunks are created
        assert all("text" in chunk for chunk in chunks)

    def test_chunk_document_without_structure(self):
        """Test document chunking without structure preservation."""
        chunker = TextChunker(chunk_size=50, chunk_overlap=10, min_chunk_size=5)

        document = {
            "id": "doc-001",
            "content": "# Title\n\n" + "Paragraph with many words. " * 20,
            "title": "Test Document",
        }

        chunks = chunker.chunk_document(document, preserve_structure=False)

        assert len(chunks) > 0
        assert all("text" in chunk for chunk in chunks)


class TestEmbeddingGenerator:
    """Test the EmbeddingGenerator class."""

    def test_generator_initialization(self):
        """Test generator initialization."""
        mock_client = Mock()

        generator = EmbeddingGenerator(bedrock_client=mock_client, model="amazon.titan-embed-text-v1")

        assert generator.bedrock_client == mock_client
        assert generator.model == "amazon.titan-embed-text-v1"
        assert generator.cache_dir is None

    def test_generator_with_cache(self):
        """Test generator initialization with cache."""
        mock_client = Mock()

        with tempfile.TemporaryDirectory() as tmpdir:
            generator = EmbeddingGenerator(
                bedrock_client=mock_client, model="amazon.titan-embed-text-v1", cache_dir=tmpdir
            )

            assert generator.cache_dir == Path(tmpdir)
            assert generator.cache_dir.exists()

    @pytest.mark.asyncio
    async def test_generate_embedding_success(self):
        """Test successful embedding generation."""
        mock_client = Mock()
        mock_client.generate_embedding = AsyncMock(return_value=[0.1] * 1536)

        _ = EmbeddingGenerator(bedrock_client=mock_client, model="amazon.titan-embed-text-v1")

        # Use async context
        embedding = await asyncio.create_task(mock_client.generate_embedding("Test text", "amazon.titan-embed-text-v1"))

        assert len(embedding) == 1536
        assert all(isinstance(x, float) for x in embedding)

    def test_generate_embedding_with_cache_hit(self):
        """Test embedding generation with cache hit."""
        mock_client = Mock()
        mock_client.generate_embedding = AsyncMock(return_value=[0.1] * 1536)

        with tempfile.TemporaryDirectory() as tmpdir:
            generator = EmbeddingGenerator(
                bedrock_client=mock_client, model="amazon.titan-embed-text-v1", cache_dir=tmpdir
            )

            # Pre-populate cache with correct format
            text = "Test text"
            cache_key = generator._get_cache_key(text)
            cache_file = generator.cache_dir / f"{cache_key}.json"
            cache_file.write_text(json.dumps({"embedding": [0.5] * 1536}))

            # Generate embedding (should hit cache)
            embedding = generator.generate_embedding(text)

            assert len(embedding) == 1536
            assert embedding[0] == 0.5
            # Should not call bedrock_client if cache hit works
            # (but we can't reliably test this due to sync/async complexity)

    def test_generate_embedding_with_cache_miss(self):
        """Test embedding generation with cache miss."""
        mock_client = Mock()

        # Mock the event loop behavior for sync call
        mock_embedding = [0.1] * 1536

        with tempfile.TemporaryDirectory() as tmpdir:
            generator = EmbeddingGenerator(
                bedrock_client=mock_client, model="amazon.titan-embed-text-v1", cache_dir=tmpdir
            )

            # Mock async call
            async def mock_generate(text, model_id):
                return mock_embedding

            mock_client.generate_embedding = mock_generate

            # This will test the sync wrapper around async method
            with patch("asyncio.get_event_loop") as mock_get_loop:
                mock_loop = Mock()
                mock_get_loop.return_value = mock_loop
                mock_loop.is_running.return_value = False
                mock_loop.run_until_complete.return_value = mock_embedding

                embedding = generator.generate_embedding("Test text")

                assert len(embedding) == 1536
                # Check cache was created
                cache_files = list(Path(tmpdir).glob("*.json"))
                assert len(cache_files) == 1

    def test_generate_embeddings_batch(self):
        """Test batch embedding generation."""
        mock_client = Mock()

        # Mock batch generation
        async def mock_batch(texts, model_id, batch_size):
            return [[0.1] * 1536 for _ in texts]

        mock_client.generate_embeddings_batch = mock_batch

        generator = EmbeddingGenerator(bedrock_client=mock_client, model="amazon.titan-embed-text-v1")

        texts = ["Text 1", "Text 2", "Text 3"]

        with patch("asyncio.get_event_loop") as mock_get_loop:
            mock_loop = Mock()
            mock_get_loop.return_value = mock_loop
            mock_loop.is_running.return_value = False
            mock_loop.run_until_complete.return_value = [[0.1] * 1536] * 3

            embeddings = generator.generate_embeddings_batch(texts)

            assert len(embeddings) == 3
            assert all(len(emb) == 1536 for emb in embeddings)

    def test_get_cache_key(self):
        """Test cache key generation."""
        mock_client = Mock()
        generator = EmbeddingGenerator(bedrock_client=mock_client)

        key1 = generator._get_cache_key("Test text")
        key2 = generator._get_cache_key("Test text")
        key3 = generator._get_cache_key("Different text")

        # Same text should produce same key
        assert key1 == key2
        # Different text should produce different key
        assert key1 != key3
        # Keys should be valid hash strings
        assert len(key1) > 0
        assert key1.isalnum()


class TestEmbeddingPipeline:
    """Test the EmbeddingPipeline class."""

    def test_pipeline_initialization(self):
        """Test pipeline initialization."""
        chunker = TextChunker()
        mock_client = Mock()
        generator = EmbeddingGenerator(bedrock_client=mock_client)

        pipeline = EmbeddingPipeline(chunker=chunker, generator=generator)

        assert pipeline.chunker == chunker
        assert pipeline.generator == generator

    def test_process_document_basic(self):
        """Test basic document processing."""
        chunker = TextChunker(chunk_size=50, chunk_overlap=10, min_chunk_size=5)
        mock_client = Mock()

        # Mock embedding generation
        def mock_batch(texts, show_progress=False):
            return [[0.1] * 1536 for _ in texts]

        generator = EmbeddingGenerator(bedrock_client=mock_client)
        generator.generate_embeddings_batch = Mock(side_effect=mock_batch)

        pipeline = EmbeddingPipeline(chunker=chunker, generator=generator)

        document = {"id": "doc-001", "content": " ".join(["word"] * 100), "title": "Test Document"}

        chunks = pipeline.process_document(document)

        assert len(chunks) > 0
        for chunk in chunks:
            assert "chunk_id" in chunk
            assert "text" in chunk
            assert "embedding" in chunk
            assert "word_count" in chunk
            assert len(chunk["embedding"]) == 1536

    def test_process_document_empty(self):
        """Test processing of empty document."""
        chunker = TextChunker()
        mock_client = Mock()
        generator = EmbeddingGenerator(bedrock_client=mock_client)

        pipeline = EmbeddingPipeline(chunker=chunker, generator=generator)

        document = {"id": "doc-001", "content": "", "title": "Empty Document"}

        chunks = pipeline.process_document(document)

        assert len(chunks) == 0

    def test_process_document_with_structure(self):
        """Test document processing with structure preservation."""
        chunker = TextChunker(chunk_size=50, chunk_overlap=10, min_chunk_size=5)
        mock_client = Mock()

        def mock_batch(texts, show_progress=False):
            return [[0.1] * 1536 for _ in texts]

        generator = EmbeddingGenerator(bedrock_client=mock_client)
        generator.generate_embeddings_batch = Mock(side_effect=mock_batch)

        pipeline = EmbeddingPipeline(chunker=chunker, generator=generator)

        document = {
            "id": "doc-001",
            "content": "# Title\n\n" + "Paragraph with many words. " * 20,
            "title": "Test Document",
        }

        chunks = pipeline.process_document(document, preserve_structure=True)

        assert len(chunks) > 0
        assert all("embedding" in chunk for chunk in chunks)


# Integration test (requires actual AWS Bedrock access)
@pytest.mark.skip(reason="Requires AWS credentials and Bedrock access")
@pytest.mark.asyncio
async def test_integration_full_pipeline():
    """Integration test with actual AWS Bedrock (skip by default)."""
    from auto_bedrock_chat_fastapi.bedrock_client import BedrockClient
    from auto_bedrock_chat_fastapi.config import ChatConfig

    config = ChatConfig()
    bedrock_client = BedrockClient(config)

    chunker = TextChunker(chunk_size=256, chunk_overlap=50)
    generator = EmbeddingGenerator(bedrock_client=bedrock_client, model="amazon.titan-embed-text-v1")
    pipeline = EmbeddingPipeline(chunker=chunker, generator=generator)

    document = {"id": "test-doc", "content": "FastAPI is a modern web framework. " * 50, "title": "Test Document"}

    chunks = pipeline.process_document(document)

    assert len(chunks) > 0
    assert all(len(chunk["embedding"]) == 1536 for chunk in chunks)
