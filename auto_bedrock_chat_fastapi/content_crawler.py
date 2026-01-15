"""
Web content crawler for knowledge base population.

This module provides tools to crawl websites, extract content, and prepare
it for embedding and storage in the knowledge base.
"""

import asyncio
import hashlib
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urljoin

import aiohttp
import html2text
from bs4 import BeautifulSoup

# Module logger
logger = logging.getLogger(__name__)


class ContentCrawler:
    """Asynchronous web crawler for documentation and articles."""

    def __init__(
        self,
        max_concurrent: int = 5,
        rate_limit_delay: float = 1.0,
        user_agent: str = "KnowledgeBaseCrawler/1.0",
        timeout: int = 30,
        proxy: Optional[str] = None,
        visited_urls: Optional[Set[str]] = None,
    ):
        """
        Initialize content crawler.

        Args:
            max_concurrent: Maximum concurrent requests
            rate_limit_delay: Delay between requests (seconds)
            user_agent: User agent string for requests
            timeout: Request timeout in seconds
            proxy: Proxy URL (or auto-detected from HTTP_PROXY/HTTPS_PROXY env vars)
            visited_urls: Optional shared set of visited URLs (for cross-source deduplication)
        """
        self.max_concurrent = max_concurrent
        self.rate_limit_delay = rate_limit_delay
        self.user_agent = user_agent
        self.timeout = timeout

        # Auto-detect proxy from environment variables if not provided
        self.proxy = proxy or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if self.proxy:
            logger.info(f"Using proxy: {self.proxy}")

        # Use shared visited_urls set if provided, otherwise create new one
        self.visited_urls: Set[str] = visited_urls if visited_urls is not None else set()
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.body_width = 0  # Don't wrap lines

    def _should_exclude_url(self, url: str, exclude_patterns: List[str]) -> bool:
        """
        Check if URL should be excluded based on patterns.

        Args:
            url: URL to check
            exclude_patterns: List of patterns to match

        Returns:
            True if URL should be excluded, False otherwise
        """
        if not exclude_patterns:
            return False

        # DEBUG: Log the first few calls
        # logger.debug(f"Checking exclusion for: {url[:80]}... (patterns: {len(exclude_patterns)})")

        for pattern in exclude_patterns:
            # If pattern ends with /, match anywhere in URL
            if pattern.endswith("/"):
                if pattern in url:
                    return True
            # If pattern doesn't end with /, match as path segment
            else:
                try:
                    from urllib.parse import urlparse as parse_url

                    path = parse_url(url).path
                    # Match if path ends with pattern or contains pattern as directory
                    # /de matches: /de, /de/, /de/anything
                    if path == pattern or path.startswith(f"{pattern}/"):
                        return True
                except Exception:
                    # Fallback to simple substring match
                    if pattern in url:
                        return True
        return False

    async def crawl_url(
        self,
        url: str,
        source: str = "web",
        topic: Optional[str] = None,
        recursive: bool = False,
        max_depth: int = 2,
        allowed_domains: Optional[List[str]] = None,
        exclude_patterns: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Crawl a URL and extract content.

        Args:
            url: URL to crawl
            source: Source identifier (e.g., "docs", "blog")
            topic: Topic/category for the content
            recursive: Whether to follow links recursively
            max_depth: Maximum crawl depth for recursive crawling
            allowed_domains: List of allowed domains for recursive crawling
            exclude_patterns: URL patterns to exclude (e.g., ['/de/', '/es/'] for translations)

        Returns:
            List of extracted documents with metadata
        """
        documents = []

        if recursive:
            documents = await self._crawl_recursive(
                url, source, topic, max_depth, allowed_domains or [], exclude_patterns or []
            )
        else:
            doc = await self._fetch_and_parse(url, source, topic)
            if doc:
                documents.append(doc)

        return documents

    async def crawl_sitemap(
        self, sitemap_url: str, source: str = "docs", topic: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Crawl all URLs from a sitemap.

        Args:
            sitemap_url: URL of the sitemap.xml
            source: Source identifier
            topic: Topic/category

        Returns:
            List of extracted documents
        """
        urls = await self._parse_sitemap(sitemap_url)
        documents = []

        # Process URLs with rate limiting
        semaphore = asyncio.Semaphore(self.max_concurrent)

        async def fetch_with_limit(url: str):
            async with semaphore:
                await asyncio.sleep(self.rate_limit_delay)
                return await self._fetch_and_parse(url, source, topic)

        tasks = [fetch_with_limit(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter out errors and None results
        documents = [doc for doc in results if doc and not isinstance(doc, Exception)]

        return documents

    async def _crawl_recursive(
        self,
        start_url: str,
        source: str,
        topic: Optional[str],
        max_depth: int,
        allowed_domains: List[str],
        exclude_patterns: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Recursively crawl URLs following links.

        Features:
        - Tracks visited URLs to avoid duplicates (includes query params)
        - Ignores URL fragments (#) as they don't change content
        - Normalizes trailing slashes for consistency
        - Stops early if no new URLs found, even before max_depth
        - Provides crawl statistics
        - Can exclude URL patterns (e.g., translations)
        """
        # Log exclusion patterns for debugging
        logger.info(f"🔍 Crawl starting with {len(exclude_patterns) if exclude_patterns else 0} exclusion patterns")
        if exclude_patterns:
            logger.info(f"   Patterns: {exclude_patterns[:10]}...")  # Show first 10

        documents = []

        # Add start URL to queue (keep original form with trailing slash)
        to_crawl = [(start_url, 0)]  # (url, depth)
        queued_urls = {self._normalize_url(start_url)}  # Track normalized version
        skipped_count = 0
        max_depth_reached = 0

        while to_crawl:
            url, depth = to_crawl.pop(0)

            # Normalize URL for comparison only
            normalized_url = self._normalize_url(url)

            # Skip if excluded by patterns (check before visiting)
            if self._should_exclude_url(url, exclude_patterns):
                logger.info(f"  ⊘ Skipping excluded URL: {normalized_url[:80]}...")
                queued_urls.discard(normalized_url)
                continue

            # Skip if already visited
            if normalized_url in self.visited_urls:
                skipped_count += 1
                logger.debug(f"  ↷ Already visited: {normalized_url[:80]}...")
                # Remove from queued_urls since we're processing it
                queued_urls.discard(normalized_url)
                continue

            # Skip if exceeding max depth
            if depth > max_depth:
                continue

            # Track maximum depth actually reached
            max_depth_reached = max(max_depth_reached, depth)

            # Mark as visited before crawling to avoid race conditions
            self.visited_urls.add(normalized_url)
            # Remove from queued set
            queued_urls.discard(normalized_url)

            # Fetch and parse using ORIGINAL URL (preserves trailing slash for link resolution)
            doc = await self._fetch_and_parse(url, source, topic)
            if doc:
                documents.append(doc)
                logger.info(f"✓ Crawled (depth {depth}): {normalized_url[:80]}...")

                # Extract links if not at max depth
                if depth < max_depth:
                    # Use original URL from document for link extraction (preserves trailing slash)
                    # This is critical for correct relative URL resolution
                    base_url_for_links = doc.get("url", url)
                    links = self._extract_links(doc.get("raw_html", ""), base_url_for_links)

                    # Filter links by allowed domains
                    if allowed_domains:
                        links = [link for link in links if any(domain in link for domain in allowed_domains)]

                    # Filter out excluded patterns (e.g., translations)
                    if exclude_patterns:
                        links = [link for link in links if not self._should_exclude_url(link, exclude_patterns)]

                    # Deduplicate links by normalized form, but keep original URLs
                    # This preserves trailing slashes for correct relative URL resolution
                    link_map = {}  # normalized -> original
                    for link in links:
                        normalized_link = self._normalize_url(link)
                        if normalized_link not in link_map:
                            link_map[normalized_link] = link  # Keep first occurrence with original form

                    # Track new URLs found
                    new_links = 0
                    added_samples = []

                    for normalized_link, original_link in link_map.items():
                        if normalized_link not in self.visited_urls and normalized_link not in queued_urls:
                            # Add ORIGINAL link to queue (preserves trailing slash)
                            to_crawl.append((original_link, depth + 1))
                            queued_urls.add(normalized_link)  # Track in persistent set
                            new_links += 1
                            if len(added_samples) < 5:
                                added_samples.append(normalized_link)

                    if new_links > 0:
                        logger.info(f"  → Found {new_links} new URLs at depth {depth}")
                        if added_samples:
                            logger.debug(f"    Sample: {added_samples}")
                    elif depth < max_depth:
                        logger.info(f"  ℹ No new URLs found at depth {depth}")
            else:
                logger.error(f"✗ Failed to crawl: {normalized_url[:80]}...")

            # Rate limiting
            await asyncio.sleep(self.rate_limit_delay)

        # Print summary
        logger.info("\n📊 Crawl Summary:")
        logger.info(f"  Total pages crawled: {len(documents)}")
        logger.info(f"  Unique URLs visited: {len(self.visited_urls)}")
        logger.info(f"  Duplicate URLs skipped: {skipped_count}")
        logger.info(f"  Max depth reached: {max_depth_reached} (limit: {max_depth})")
        if max_depth_reached < max_depth:
            logger.info("  ✓ Stopped early - no new URLs found")

        return documents

    async def _fetch_and_parse(self, url: str, source: str, topic: Optional[str]) -> Optional[Dict[str, Any]]:
        """
        Fetch URL and parse content.

        Returns:
            Document dict or None if failed
        """
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"User-Agent": self.user_agent}
                async with session.get(
                    url,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=self.timeout),
                    proxy=self.proxy,  # Use proxy if configured
                ) as response:
                    if response.status != 200:
                        logger.error(f"Failed to fetch {url}: HTTP {response.status}")
                        return None

                    html_content = await response.text()
                    content_type = response.headers.get("Content-Type", "")

                    # Only process HTML content
                    if "text/html" not in content_type:
                        return None

                    return self._parse_html(html_content, url, source, topic)

        except asyncio.TimeoutError:
            logger.error(f"Timeout fetching {url}")
            return None
        except Exception as e:
            logger.error(f"Error fetching {url}: {e}")
            return None

    def _parse_html(self, html_content: str, url: str, source: str, topic: Optional[str]) -> Dict[str, Any]:
        """Parse HTML content and extract metadata."""
        soup = BeautifulSoup(html_content, "html.parser")

        # Keep raw HTML for link extraction (before removing nav)
        raw_html_for_links = str(soup)

        # Remove unwanted elements for content extraction
        for element in soup(["script", "style", "nav", "footer", "header", "aside"]):
            element.decompose()

        # Extract metadata
        title = self._extract_title(soup)
        description = self._extract_description(soup)
        date_published = self._extract_date(soup)
        author = self._extract_author(soup)

        # Extract main content
        main_content = self._extract_main_content(soup)

        # Convert to markdown
        markdown_content = self.html_converter.handle(str(main_content))

        # Clean up markdown
        markdown_content = self._clean_markdown(markdown_content)

        # Generate document ID from URL
        doc_id = self._generate_doc_id(url)

        return {
            "id": doc_id,
            "url": url,
            "title": title,
            "content": markdown_content,
            "description": description,
            "source": source,
            "topic": topic,
            "date_published": date_published,
            "author": author,
            "word_count": len(markdown_content.split()),
            "raw_html": raw_html_for_links,  # Keep FULL HTML with nav for link extraction
            "crawled_at": datetime.now().isoformat(),
        }

    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract page title."""
        # Try multiple methods
        title = None

        # 1. Try <title> tag
        if soup.title:
            title = soup.title.string

        # 2. Try og:title meta tag
        if not title:
            og_title = soup.find("meta", property="og:title")
            if og_title:
                title = og_title.get("content")

        # 3. Try first <h1> tag
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text()

        return (title or "Untitled").strip()

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract page description."""
        # Try meta description
        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc:
            return meta_desc.get("content", "").strip()

        # Try og:description
        og_desc = soup.find("meta", property="og:description")
        if og_desc:
            return og_desc.get("content", "").strip()

        return None

    def _extract_date(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract publication date."""
        # Try various meta tags
        date_tags = [
            ("meta", {"property": "article:published_time"}),
            ("meta", {"name": "date"}),
            ("meta", {"name": "publish_date"}),
            ("time", {"datetime": True}),
        ]

        for tag_name, attrs in date_tags:
            tag = soup.find(tag_name, attrs)
            if tag:
                date_str = tag.get("content") or tag.get("datetime")
                if date_str:
                    # Try to parse and format as ISO date
                    try:
                        # Simple extraction of YYYY-MM-DD
                        match = re.search(r"\d{4}-\d{2}-\d{2}", date_str)
                        if match:
                            return match.group(0)
                    except Exception:
                        pass

        return None

    def _extract_author(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract author name."""
        # Try meta tags
        author_tags = [("meta", {"name": "author"}), ("meta", {"property": "article:author"})]

        for tag_name, attrs in author_tags:
            tag = soup.find(tag_name, attrs)
            if tag:
                return tag.get("content", "").strip()

        return None

    def _extract_main_content(self, soup: BeautifulSoup) -> BeautifulSoup:
        """Extract main content area from page."""
        # Try to find main content container
        main_selectors = ["main", "article", '[role="main"]', ".content", ".main-content", "#content", "#main-content"]

        for selector in main_selectors:
            main = soup.select_one(selector)
            if main:
                return main

        # Fallback to body
        return soup.find("body") or soup

    def _clean_markdown(self, markdown: str) -> str:
        """Clean and normalize markdown content."""
        # Remove excessive newlines (use iterative replacement for thorough cleaning)
        while "\\n\\n\\n" in markdown:
            markdown = markdown.replace("\\n\\n\\n", "\\n\\n")

        # Remove leading/trailing whitespace
        markdown = markdown.strip()

        # Remove empty links
        markdown = re.sub(r"\[\]\([^)]*\)", "", markdown)

        # Normalize whitespace
        markdown = re.sub(r" +", " ", markdown)

        return markdown

    def _normalize_url(self, url: str) -> str:
        """
        Normalize URL for consistent comparison.

        - Removes fragments (#)
        - Removes trailing slashes
        - Preserves query parameters
        """
        # Remove fragment
        if "#" in url:
            url = url.split("#")[0]

        # Remove trailing slash (but keep single slash for domain root)
        if url.endswith("/") and url.count("/") > 2:
            url = url.rstrip("/")

        return url

    def _generate_doc_id(self, url: str) -> str:
        """Generate a unique document ID from URL."""
        return hashlib.sha256(url.encode()).hexdigest()[:16]

    def _extract_links(self, html: str, base_url: str) -> List[str]:
        """
        Extract all links from HTML content.

        URL handling:
        - Converts relative URLs to absolute
        - Only includes http/https URLs
        - Returns raw URLs (normalization done by caller)

        Examples:
        - /docs/guide → https://example.com/docs/guide ✓
        - /api?version=2 → https://example.com/api?version=2 ✓ (keeps query)
        - /page#section → https://example.com/page#section (caller removes #)
        - mailto:test@example.com → excluded ✗
        """
        soup = BeautifulSoup(html, "html.parser")
        links = []

        for a_tag in soup.find_all("a", href=True):
            href = a_tag["href"]

            # Convert relative URLs to absolute
            absolute_url = urljoin(base_url, href)

            # Only include http/https URLs (excludes mailto:, javascript:, etc.)
            if absolute_url.startswith(("http://", "https://")) and absolute_url:
                links.append(absolute_url)

        # Return deduplicated list while preserving order
        seen = set()
        unique_links = []
        for link in links:
            if link not in seen:
                seen.add(link)
                unique_links.append(link)

        return unique_links

    async def _parse_sitemap(self, sitemap_url: str) -> List[str]:
        """Parse sitemap XML and extract URLs."""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {"User-Agent": self.user_agent}
                async with session.get(
                    sitemap_url, headers=headers, timeout=aiohttp.ClientTimeout(total=self.timeout)
                ) as response:
                    if response.status != 200:
                        logger.error(f"Failed to fetch sitemap: HTTP {response.status}")
                        return []

                    xml_content = await response.text()
                    soup = BeautifulSoup(xml_content, "xml")

                    # Extract all <loc> tags
                    urls = [loc.text for loc in soup.find_all("loc")]
                    return urls

        except Exception as e:
            logger.error(f"Error parsing sitemap: {e}")
            return []


class LocalContentLoader:
    """Load content from local files (Markdown, text, etc.)."""

    def __init__(self):
        """Initialize local content loader."""
        self.html_converter = html2text.HTML2Text()
        self.html_converter.ignore_links = False
        self.html_converter.ignore_images = True
        self.html_converter.body_width = 0

    def load_markdown_file(self, file_path: str, source: str = "local", topic: Optional[str] = None) -> Dict[str, Any]:
        """
        Load content from a markdown file.

        Args:
            file_path: Path to markdown file
            source: Source identifier
            topic: Topic/category

        Returns:
            Document dict with metadata
        """
        path = Path(file_path)

        if not path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")

        content = path.read_text(encoding="utf-8")

        # Extract frontmatter if present
        metadata = self._parse_frontmatter(content)
        content = self._remove_frontmatter(content)

        # Generate document ID
        doc_id = hashlib.sha256(str(path.absolute()).encode()).hexdigest()[:16]

        return {
            "id": doc_id,
            "url": f"file://{path.absolute()}",
            "title": metadata.get("title", path.stem),
            "content": content.strip(),
            "description": metadata.get("description"),
            "source": source,
            "topic": topic or metadata.get("topic"),
            "date_published": metadata.get("date"),
            "author": metadata.get("author"),
            "word_count": len(content.split()),
            "crawled_at": datetime.now().isoformat(),
        }

    def load_directory(
        self, dir_path: str, source: str = "local", topic: Optional[str] = None, pattern: str = "**/*.md"
    ) -> List[Dict[str, Any]]:
        """
        Load all markdown files from a directory.

        Args:
            dir_path: Directory path
            source: Source identifier
            topic: Topic/category
            pattern: Glob pattern for files

        Returns:
            List of document dicts
        """
        path = Path(dir_path)

        if not path.exists():
            raise FileNotFoundError(f"Directory not found: {dir_path}")

        documents = []

        for file_path in path.glob(pattern):
            if file_path.is_file():
                try:
                    doc = self.load_markdown_file(str(file_path), source, topic)
                    documents.append(doc)
                except Exception as e:
                    logger.error(f"Error loading {file_path}: {e}")

        return documents

    def _parse_frontmatter(self, content: str) -> Dict[str, Any]:
        """Parse YAML frontmatter from markdown."""
        metadata = {}

        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter = parts[1].strip()

                # Simple parsing (key: value format)
                for line in frontmatter.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        metadata[key.strip()] = value.strip()

        return metadata

    def _remove_frontmatter(self, content: str) -> str:
        """Remove YAML frontmatter from markdown."""
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                return parts[2].strip()

        return content
