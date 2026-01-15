# Web Crawler Setup and Usage Guide

**Module**: `auto_bedrock_chat_fastapi/content_crawler.py`
**Purpose**: Crawl web content and local files for knowledge base population
**Last Updated**: January 8, 2026

---

## 📋 Overview

The content crawler provides tools to:

- **Crawl websites** recursively with configurable depth
- **Parse HTML** to markdown with metadata extraction
- **Load local files** (Markdown with frontmatter support)
- **Rate limit** requests to be respectful to servers
- **Filter URLs** by domain and patterns (e.g., skip translations)
- **Handle proxies** automatically from environment variables

---

## 🚀 Quick Start

### Installation

Dependencies are already in `pyproject.toml`:

```bash
poetry install
```

Required packages:

- `beautifulsoup4` - HTML parsing
- `html2text` - HTML to Markdown conversion
- `aiohttp` - Async HTTP client
- `lxml` - XML/sitemap parsing

### Basic Usage

```python
import asyncio
from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler

async def main():
    crawler = ContentCrawler()

    # Single URL
    documents = await crawler.crawl_url(
        url="https://docs.python.org/3/tutorial/index.html",
        source="python-docs",
        topic="tutorial"
    )

    for doc in documents:
        print(f"Title: {doc['title']}")
        print(f"Content: {doc['content'][:200]}...")

asyncio.run(main())
```

---

## 🔧 API Reference

### ContentCrawler Class

#### Constructor

```python
ContentCrawler(
    max_concurrent: int = 5,
    rate_limit_delay: float = 1.0,
    user_agent: str = "KnowledgeBaseCrawler/1.0",
    timeout: int = 30,
    proxy: Optional[str] = None
)
```

**Parameters**:

- `max_concurrent`: Maximum concurrent HTTP requests
- `rate_limit_delay`: Delay between requests in seconds (be respectful!)
- `user_agent`: User-Agent header for requests
- `timeout`: Request timeout in seconds
- `proxy`: Proxy URL (auto-detected from `HTTP_PROXY`/`HTTPS_PROXY` env vars)

#### crawl_url()

```python
async def crawl_url(
    url: str,
    source: str = "web",
    topic: Optional[str] = None,
    recursive: bool = False,
    max_depth: int = 2,
    allowed_domains: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None
) -> List[Dict[str, Any]]
```

**Parameters**:

- `url`: Starting URL to crawl
- `source`: Source identifier (e.g., "docs", "blog")
- `topic`: Topic/category for content
- `recursive`: Follow links recursively
- `max_depth`: Maximum crawl depth (stops early if no new URLs)
- `allowed_domains`: Only crawl URLs from these domains
- `exclude_patterns`: Skip URLs containing these patterns (e.g., `["/de/", "/es/"]`)

**Returns**: List of document dictionaries with structure:

```python
{
    'id': str,              # Unique document ID (hash of URL)
    'url': str,             # Original URL
    'title': str,           # Extracted page title
    'content': str,         # Markdown content (cleaned)
    'description': str,     # Meta description
    'source': str,          # Source identifier
    'topic': str,           # Topic/category
    'date_published': str,  # Publication date (YYYY-MM-DD)
    'author': str,          # Author name
    'word_count': int,      # Word count
    'raw_html': str,        # Full HTML (for link extraction)
    'crawled_at': str       # ISO timestamp
}
```

---

## 📚 Usage Examples

### Example 1: Crawl Single URL

```python
import asyncio
from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler

async def main():
    crawler = ContentCrawler(rate_limit_delay=1.0)

    documents = await crawler.crawl_url(
        url="https://docs.python.org/3/tutorial/index.html",
        source="python-docs",
        topic="tutorial"
    )

    print(f"Crawled {len(documents)} page")
    print(f"Title: {documents[0]['title']}")
    print(f"Words: {documents[0]['word_count']}")

asyncio.run(main())
```

### Example 2: Recursive Crawl with Filters

```python
import asyncio
from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler

async def main():
    crawler = ContentCrawler(
        max_concurrent=3,
        rate_limit_delay=2.0  # Be respectful!
    )

    # Crawl English docs only, skip translations
    documents = await crawler.crawl_url(
        url="https://fastapi.tiangolo.com/tutorial/",
        source="fastapi-docs",
        topic="web-framework",
        recursive=True,
        max_depth=30,  # Will stop early when no new URLs found
        allowed_domains=["fastapi.tiangolo.com"],
        exclude_patterns=["/de/", "/es/", "/pt/", "/ru/"]  # Skip translations
    )

    print(f"Crawled {len(documents)} pages")
    for doc in documents[:5]:
        print(f"- {doc['title']} ({doc['url']})")

asyncio.run(main())
```

**Output**:

```
13:50:49 - INFO - ✓ Crawled (depth 0): https://fastapi.tiangolo.com/tutorial...
13:50:49 - INFO -   → Found 143 new URLs at depth 0
13:50:49 - INFO - ✓ Crawled (depth 1): https://fastapi.tiangolo.com/tutorial/first-steps...
...
📊 Crawl Summary:
  Total pages crawled: 149
  Unique URLs visited: 155
  Duplicate URLs skipped: 0
  Max depth reached: 2 (limit: 30)
  ✓ Stopped early - no new URLs found
```

### Example 3: Load Local Markdown Files

```python
from auto_bedrock_chat_fastapi.content_crawler import LocalContentLoader

loader = LocalContentLoader()

# Load single file
doc = loader.load_markdown_file(
    file_path="docs/AUTHENTICATION.md",
    source="docs",
    topic="authentication"
)

print(f"Loaded: {doc['title']}")
print(f"Words: {doc['word_count']}")

# Load entire directory
documents = loader.load_directory(
    dir_path="docs",
    source="docs",
    pattern="**/*.md"
)

print(f"Loaded {len(documents)} documents")
```

### Example 4: Populate Knowledge Base

```python
import asyncio
from auto_bedrock_chat_fastapi.content_crawler import ContentCrawler
from auto_bedrock_chat_fastapi.vector_db import VectorDB

async def main():
    # Initialize
    db = VectorDB("knowledge_base.db")
    crawler = ContentCrawler()

    # Crawl content
    documents = await crawler.crawl_url(
        url="https://docs.python.org/3/tutorial/",
        source="python-docs",
        topic="tutorial",
        recursive=True,
        max_depth=2,
        allowed_domains=["docs.python.org"]
    )

    # Add to database
    for doc in documents:
        db.add_document(
            doc_id=doc['id'],
            content=doc['content'],
            title=doc['title'],
            source=doc['source'],
            source_url=doc['url'],
            topic=doc['topic'],
            date_published=doc.get('date_published'),
            metadata={
                'author': doc.get('author'),
                'description': doc.get('description'),
                'word_count': doc['word_count'],
                'crawled_at': doc['crawled_at']
            }
        )

    print(f"Added {len(documents)} documents to knowledge base")
    db.close()

asyncio.run(main())
```

---

## ⚙️ Configuration

### Proxy Configuration

The crawler automatically detects proxy settings from environment variables:

```bash
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080
```

Or pass explicitly:

```python
crawler = ContentCrawler(proxy="http://proxy.example.com:8080")
```

### Logging Configuration

The crawler uses Python's logging module:

```python
import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
```

Log levels:

- `INFO`: Crawl progress, found URLs, statistics
- `ERROR`: Failed requests, timeouts, HTTP errors
- `DEBUG`: Detailed link extraction (verbose)

---

## 🎯 Features

### URL Handling

**Normalization**:

- Removes fragment identifiers (`#section`) - same content
- Preserves query parameters (`?version=2`) - different content
- Removes trailing slashes for deduplication (but keeps for link resolution)

**Example**:

```
https://example.com/docs/           → https://example.com/docs
https://example.com/docs#intro      → https://example.com/docs
https://example.com/api?v=1         → https://example.com/api?v=1 (preserved)
https://example.com/api?v=2         → https://example.com/api?v=2 (different URL)
```

### Crawl Optimization

**Early Termination**:

- Stops when no new URLs found, even if `max_depth` not reached
- Prevents unnecessary crawling with high `max_depth` values

**Deduplication**:

- Tracks visited URLs to avoid duplicate requests
- Tracks queued URLs to avoid adding duplicates to queue
- Reports: `Duplicate URLs skipped: 0` means perfect deduplication

**Example**:

```
Max depth: 30
Actual depth reached: 2
Reason: No new unique URLs found after depth 2
```

### Content Extraction

**Metadata Extraction**:

- `<title>` tag or `<meta property="og:title">`
- `<meta name="description">` or `<meta property="og:description">`
- `<meta property="article:published_time">` for dates
- `<meta name="author">` for authorship

**Content Cleaning**:

- Removes `<script>`, `<style>`, `<nav>`, `<footer>`, `<header>`, `<aside>`
- Extracts main content from `<main>`, `<article>`, or `<body>`
- Converts HTML to clean Markdown
- Removes excessive newlines and empty links

**Link Extraction**:

- Extracts from **full HTML** (includes navigation)
- Converts relative to absolute URLs
- Filters by allowed domains
- Excludes non-HTTP links (mailto:, javascript:, etc.)

---

## 🧪 Testing

Run tests:

```bash
poetry run pytest tests/test_content_crawler.py -v
```

Coverage:

```bash
poetry run pytest tests/test_content_crawler.py --cov=auto_bedrock_chat_fastapi.content_crawler
```

Test results:

- **16 tests** covering all major functionality
- **66% coverage** on content_crawler.py

---

## 🐛 Common Issues

### Issue: "Failed to fetch: HTTP 404"

**Cause**: URL doesn't exist or relative URL resolved incorrectly

**Solution**: Ensure base URL has trailing slash for directory-like paths:

```python
# ❌ Wrong: https://example.com/docs (no trailing slash)
# Relative link "guide" → https://example.com/guide (wrong)

# ✅ Correct: https://example.com/docs/ (with trailing slash)
# Relative link "guide" → https://example.com/docs/guide (correct)
```

### Issue: "Timeout fetching URL"

**Cause**: Server slow or proxy issues

**Solution**:

- Increase timeout: `ContentCrawler(timeout=60)`
- Check proxy settings
- Reduce concurrent requests: `ContentCrawler(max_concurrent=2)`

### Issue: Crawling translations unnecessarily

**Cause**: Not excluding language paths

**Solution**: Use `exclude_patterns`:

```python
exclude_patterns=["/de/", "/es/", "/pt/", "/ru/", "/fr/", "/ja/", "/zh/"]
```

---

## 🚀 Best Practices

### Rate Limiting

**Be respectful to servers**:

```python
crawler = ContentCrawler(
    rate_limit_delay=2.0,  # 2 seconds between requests
    max_concurrent=3       # Max 3 concurrent requests
)
```

### URL Filtering

**Save bandwidth by excluding unnecessary content**:

```python
documents = await crawler.crawl_url(
    url="https://example.com/docs/",
    allowed_domains=["example.com"],  # Stay on same domain
    exclude_patterns=[
        "/de/", "/es/", "/fr/",  # Skip translations
        "/archive/",              # Skip old content
        "/admin/"                 # Skip admin pages
    ]
)
```

### Error Handling

**Handle failures gracefully**:

```python
documents = await crawler.crawl_url(...)

successful = [doc for doc in documents if doc is not None]
print(f"Successfully crawled: {len(successful)}")
```

---

## 📈 Performance

### Benchmarks

Real-world test (FastAPI docs, English only):

- **149 pages** crawled successfully
- **155 URLs** visited (6 failed)
- **0 duplicates** (perfect deduplication)
- **~6 minutes** total time (2 sec/request)
- **Depth 2** reached (stopped early from max_depth=30)

### Optimization Tips

1. **Adjust concurrency** based on server capacity
2. **Use exclude_patterns** to skip unnecessary pages
3. **Set reasonable max_depth** (2-3 is usually enough)
4. **Enable caching** for repeated crawls (future feature)

---

## 🔮 Future Enhancements

Potential improvements:

- [ ] Respect `robots.txt` directives
- [ ] Add JavaScript rendering support (Playwright)
- [ ] Implement incremental updates (only crawl changed pages)
- [ ] Add content quality scoring
- [ ] Support for authentication (login required sites)
- [ ] Parallel sitemap processing
- [ ] Custom extraction rules per domain

---

## 📚 Related Documentation

- [VECTOR_DB_SETUP.md](VECTOR_DB_SETUP.md) - Vector database setup
- [HYBRID_KB_IMPLEMENTATION_TRACKER.md](HYBRID_KB_IMPLEMENTATION_TRACKER.md) - Project tracker
- [KNOWLEDGE_BASE_ARCHITECTURE.md](KNOWLEDGE_BASE_ARCHITECTURE.md) - Architecture overview

---

## 🆘 Support

For issues or questions:

1. Check test suite: `tests/test_content_crawler.py`
2. Review examples: `examples/crawler_examples.py`
3. Enable DEBUG logging for detailed output
4. Check proxy/network connectivity

---

**Last Updated**: January 8, 2026
**Module Version**: 1.0.0
**Test Coverage**: 66%
