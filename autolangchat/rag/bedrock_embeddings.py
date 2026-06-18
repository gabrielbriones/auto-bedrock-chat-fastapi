"""Bedrock embedding client — async AWS Bedrock embedding generation.

Extracted from ``bedrock_client.py`` (which handled both LLM chat and
embeddings). This module is embedding-only; LLM chat calls are handled
by ``autolangchat/graph/nodes/llm_call.py`` via ``ChatBedrockConverse``.

Supported models
----------------
- ``amazon.titan-embed-text-v1``   — 1536 dimensions
- ``amazon.titan-embed-text-v2:0`` — configurable dimensions
- ``cohere.embed-english-v3``
- ``cohere.embed-multilingual-v3``
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any, List

import boto3

from ..exceptions import BedrockClientError

logger = logging.getLogger(__name__)


class BedrockEmbeddingClient:
    """AWS Bedrock client for embedding generation only.

    Provides ``generate_embedding`` (single text) and
    ``generate_embeddings_batch`` (concurrent batch) using the
    ``bedrock-runtime`` boto3 client.

    Args:
        config: ``ChatConfig`` instance used for AWS region, credentials,
            timeout, and rate-limit settings.
    """

    def __init__(self, config: Any):
        self.config = config
        self._client = None
        self._last_request_time: float = 0

        self._initialize_client()

    def _initialize_client(self) -> None:
        """Initialise the boto3 bedrock-runtime client."""
        try:
            from botocore.config import Config

            session = boto3.Session(**self.config.get_aws_config())
            client_config = Config(
                read_timeout=max(120, self.config.timeout),
                connect_timeout=30,
                retries={"max_attempts": 3},
            )
            self._client = session.client(
                "bedrock-runtime",
                region_name=self.config.aws_region,
                config=client_config,
            )
            logger.info("Bedrock embedding client initialized for region: %s", self.config.aws_region)
        except Exception as exc:
            raise BedrockClientError(f"Failed to initialize Bedrock embedding client: {exc}") from exc

    async def _handle_rate_limiting(self) -> None:
        """Simple rate limiter — 100 ms minimum between requests."""
        current_time = time.time()
        elapsed = current_time - self._last_request_time
        min_interval = 0.1
        if elapsed < min_interval:
            await asyncio.sleep(min_interval - elapsed)
        self._last_request_time = time.time()

    async def generate_embedding(
        self,
        text: str,
        model_id: str = "amazon.titan-embed-text-v1",
    ) -> List[float]:
        """Generate an embedding vector for a single text.

        Args:
            text: Input text to embed.
            model_id: Bedrock embedding model identifier.

        Returns:
            Embedding as a list of floats.

        Raises:
            BedrockClientError: If the API call fails or returns no embedding.
        """
        try:
            if model_id.startswith("amazon.titan-embed"):
                body = json.dumps({"inputText": text})
            elif model_id.startswith("cohere.embed"):
                body = json.dumps({"texts": [text], "input_type": "search_document"})
            else:
                raise BedrockClientError(f"Unsupported embedding model: {model_id}")

            await self._handle_rate_limiting()

            response = self._client.invoke_model(
                modelId=model_id,
                body=body,
                contentType="application/json",
                accept="application/json",
            )
            response_body = json.loads(response["body"].read())

            if model_id.startswith("amazon.titan-embed"):
                embedding = response_body.get("embedding")
            elif model_id.startswith("cohere.embed"):
                embedding = response_body.get("embeddings", [None])[0]
            else:
                raise BedrockClientError(f"Unknown response format for model: {model_id}")

            if not embedding:
                raise BedrockClientError("No embedding returned from model")

            logger.debug("Generated embedding: %d dimensions", len(embedding))
            return embedding

        except BedrockClientError:
            raise
        except Exception as exc:
            logger.error("Failed to generate embedding: %s", exc)
            raise BedrockClientError(f"Embedding generation failed: {exc}") from exc

    async def generate_embeddings_batch(
        self,
        texts: List[str],
        model_id: str = "amazon.titan-embed-text-v1",
        batch_size: int = 25,
    ) -> List[List[float]]:
        """Generate embeddings for multiple texts with concurrent batching.

        Args:
            texts: Input texts.
            model_id: Bedrock embedding model identifier.
            batch_size: Number of texts to embed concurrently per batch.

        Returns:
            List of embedding vectors in the same order as ``texts``.

        Note:
            AWS Bedrock has rate limits. The default ``batch_size=25`` is
            conservative; increase it only if your quota allows.
        """
        embeddings: List[List[float]] = []

        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            batch_num = i // batch_size + 1
            total_batches = (len(texts) - 1) // batch_size + 1
            logger.info("Processing embedding batch %d/%d", batch_num, total_batches)

            tasks = [self.generate_embedding(text, model_id) for text in batch]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for j, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error("Failed to embed text %d: %s", i + j, result)
                    embeddings.append([0.0] * 1536)  # Titan v1 fallback dimension
                else:
                    embeddings.append(result)

        logger.info("Generated %d embeddings total", len(embeddings))
        return embeddings
