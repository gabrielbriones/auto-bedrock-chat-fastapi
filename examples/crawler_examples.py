"""Example script showing how to use the content crawler."""

import asyncio
import logging

from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler, LocalContentLoader

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", datefmt="%H:%M:%S"
)


async def example_crawl_single_url():
    """Example: Crawl a single URL."""
    crawler = ContentCrawler(rate_limit_delay=1.0)

    # Crawl a single page
    documents = await crawler.crawl_url(
        url="https://docs.python.org/3/tutorial/index.html", source="python-docs", topic="tutorial"
    )

    for doc in documents:
        print(f"Title: {doc['title']}")
        print(f"URL: {doc['url']}")
        print(f"Content length: {doc['word_count']} words")
        print(f"Content preview: {doc['content'][:200]}...")
        print("-" * 80)


async def example_crawl_recursive():
    """Example: Recursively crawl a documentation site."""
    crawler = ContentCrawler(max_concurrent=3, rate_limit_delay=2.0)  # Be respectful

    # Crawl recursively with real-world URL (English only)
    documents = await crawler.crawl_url(
        url="https://fastapi.tiangolo.com/tutorial/",
        source="fastapi-docs",
        topic="web-framework",
        recursive=True,
        max_depth=30,  # Will stop early if no new URLs found
        allowed_domains=["fastapi.tiangolo.com"],
        exclude_patterns=["/de/", "/es/", "/pt/", "/ru/", "/fr/", "/ja/", "/zh/"],  # Skip translations
    )

    print(f"\nCrawled {len(documents)} pages (English only)")
    print("\nFirst 5 pages:")
    for doc in documents[:5]:
        print(f"- {doc['title']} ({doc['url']})")


def example_load_local_files():
    """Example: Load local markdown files."""
    loader = LocalContentLoader()

    # Load single file
    doc = loader.load_markdown_file(file_path="docs/AUTHENTICATION.md", source="docs", topic="authentication")

    print(f"Loaded: {doc['title']}")
    print(f"Content length: {doc['word_count']} words")

    # Load entire directory
    documents = loader.load_directory(dir_path="docs", source="docs", pattern="**/*.md")

    print(f"\nLoaded {len(documents)} documents from docs/")
    for doc in documents:
        print(f"- {doc['title']}")


async def example_populate_knowledge_base():
    """Example: Crawl content and add to vector database."""
    from auto_bedrock_chat_fastapi.vector_db import VectorDB

    # Initialize database
    db = VectorDB("knowledge_base.db")

    # Crawl content
    crawler = ContentCrawler()
    documents = await crawler.crawl_url(
        url="https://docs.python.org/3/tutorial/introduction.html", source="python-docs", topic="tutorial"
    )

    # Add to database
    for doc in documents:
        db.add_document(
            doc_id=doc["id"],
            content=doc["content"],
            title=doc["title"],
            source=doc["source"],
            source_url=doc["url"],
            topic=doc["topic"],
            date_published=doc.get("date_published"),
            metadata={
                "author": doc.get("author"),
                "description": doc.get("description"),
                "word_count": doc["word_count"],
                "crawled_at": doc["crawled_at"],
            },
        )

    print(f"Added {len(documents)} documents to knowledge base")

    # Show stats
    stats = db.get_stats()
    print(f"Database stats: {stats}")

    db.close()


if __name__ == "__main__":
    print("Content Crawler Examples")
    print("=" * 80)

    # Uncomment the example you want to run:

    # asyncio.run(example_crawl_single_url())
    asyncio.run(example_crawl_recursive())  # Real-world FastAPI docs crawl
    # example_load_local_files()  # <-- Start with this one!
    # asyncio.run(example_populate_knowledge_base())

    print("\nTo run an example, uncomment it in the script!")
    print("\nRecommended: Start with example_load_local_files() to index docs/ folder")
