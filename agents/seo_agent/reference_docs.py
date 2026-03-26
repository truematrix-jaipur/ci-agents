"""
CI SEO Agent — Google Search documentation crawler/trainer.

Fetches Google Search Central / Rich Results related docs and stores them in
ChromaDB so CLI, IDE, and local agents can query the guidance through MCP/FastAPI.
"""
from __future__ import annotations

import logging
import re
from collections import deque
from html import unescape
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests

from agents.seo_agent.vector_store import vector_store

logger = logging.getLogger("ci.reference_docs")


class ReferenceDocsTrainer:
    USER_AGENT = (
        "Mozilla/5.0 (compatible; CI-SEO-Agent/2.0; "
        "+https://indogenmed.org)"
    )
    DEFAULT_ALLOWED_PREFIXES = (
        "https://developers.google.com/search/docs",
        "https://developers.google.com/search/blog",
        "https://developers.google.com/search/apis",
        "https://support.google.com/webmasters/",
        "https://search.google.com/test/rich-results",
    )
    DEFAULT_SEED_URLS = (
        "https://developers.google.com/search/docs?hl=en",
        "https://developers.google.com/search/docs/fundamentals/seo-starter-guide?hl=en",
        "https://developers.google.com/search/docs/appearance/structured-data/intro-structured-data?hl=en",
        "https://developers.google.com/search/docs/appearance/structured-data/test?hl=en",
        "https://developers.google.com/search/docs/crawling-indexing/sitemaps/overview?hl=en",
        "https://developers.google.com/search/docs/crawling-indexing/sitemaps/build-sitemap?hl=en",
        "https://developers.google.com/search/docs/crawling-indexing/robots/intro?hl=en",
        "https://developers.google.com/search/docs/monitor-debug/search-console/get-started?hl=en",
        "https://support.google.com/webmasters/answer/7451001?hl=en",
        "https://search.google.com/test/rich-results",
    )

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": self.USER_AGENT})

    def train_google_search_docs(
        self,
        max_pages: int = 120,
        max_depth: int = 3,
    ) -> dict:
        """Crawl and ingest Google Search-related documentation."""
        vector_store.init()

        queue = deque((seed, 0) for seed in self.DEFAULT_SEED_URLS)
        seen: set[str] = set()
        indexed = 0
        skipped = 0
        errors: list[dict] = []

        while queue and indexed < max_pages:
            raw_url, depth = queue.popleft()
            url = self._normalize_url(raw_url)
            if not url or url in seen:
                continue
            seen.add(url)

            if not self._allowed(url):
                skipped += 1
                continue

            try:
                response = self._session.get(url, timeout=25)
                content_type = response.headers.get("content-type", "")
                if response.status_code != 200 or "text/html" not in content_type:
                    skipped += 1
                    continue

                html = response.text
                title = self._extract_title(html)
                text = self._extract_text(html)
                if len(text) < 400:
                    skipped += 1
                    continue

                vector_store.upsert_reference_doc(
                    url=response.url,
                    title=title,
                    content=text,
                    metadata={
                        "source": self._source_for_url(response.url),
                        "type": "reference_doc",
                        "depth": depth,
                        "fetched_from": url,
                    },
                )
                indexed += 1

                if depth >= max_depth:
                    continue

                for link in self._extract_links(response.url, html):
                    normalized = self._normalize_url(link)
                    if normalized and normalized not in seen and self._allowed(normalized):
                        queue.append((normalized, depth + 1))
            except Exception as exc:
                logger.warning("Reference doc crawl failed for %s: %s", url, exc)
                errors.append({"url": url, "error": str(exc)[:200]})

        result = {
            "indexed": indexed,
            "seen": len(seen),
            "skipped": skipped,
            "errors": errors[:25],
            "sources": vector_store.list_reference_doc_sources(),
        }
        logger.info("Reference doc training complete: %s", result)
        return result

    def _allowed(self, url: str) -> bool:
        return any(url.startswith(prefix) for prefix in self.DEFAULT_ALLOWED_PREFIXES)

    def _normalize_url(self, url: str) -> str:
        if not url:
            return ""
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"}:
            return ""

        query_items = []
        for key, value in parse_qsl(parsed.query, keep_blank_values=False):
            if parsed.netloc.endswith("developers.google.com") and key == "hl":
                query_items.append((key, "en"))
            elif parsed.netloc.endswith("support.google.com") and key == "hl":
                query_items.append((key, "en"))
            elif key not in {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content"}:
                query_items.append((key, value))

        if parsed.netloc.endswith("developers.google.com") and not any(key == "hl" for key, _ in query_items):
            query_items.append(("hl", "en"))

        normalized = parsed._replace(
            fragment="",
            query=urlencode(sorted(query_items)),
        )
        return urlunparse(normalized)

    def _extract_title(self, html: str) -> str:
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
        return self._clean_fragment(match.group(1)) if match else ""

    def _extract_links(self, base_url: str, html: str) -> list[str]:
        links = []
        for href in re.findall(r'href=["\']([^"\']+)["\']', html, re.I):
            if href.startswith(("mailto:", "javascript:", "#")):
                continue
            links.append(urljoin(base_url, unescape(href)))
        return links

    def _extract_text(self, html: str) -> str:
        cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
        cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
        cleaned = re.sub(r"(?is)<noscript.*?>.*?</noscript>", " ", cleaned)
        cleaned = re.sub(r"(?is)<svg.*?>.*?</svg>", " ", cleaned)
        cleaned = re.sub(r"(?is)<[^>]+>", " ", cleaned)
        cleaned = unescape(cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        return cleaned[:20000]

    def _clean_fragment(self, fragment: str) -> str:
        fragment = re.sub(r"(?is)<[^>]+>", " ", fragment)
        fragment = unescape(fragment)
        return re.sub(r"\s+", " ", fragment).strip()

    def _source_for_url(self, url: str) -> str:
        if url.startswith("https://support.google.com/webmasters/"):
            return "google_search_console_help"
        if url.startswith("https://search.google.com/test/rich-results"):
            return "google_rich_results_test"
        if url.startswith("https://developers.google.com/search/"):
            return "google_search_central"
        return "google_reference"


reference_docs_trainer = ReferenceDocsTrainer()
