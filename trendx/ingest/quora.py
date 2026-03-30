"""Quora question ingestion via proxy scraping."""

import json
import logging
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from .base import BaseIngestor
from ..proxy import fetch
from ..config import QuoraConfig
from ..store.db import Database

logger = logging.getLogger(__name__)


class QuoraIngestor(BaseIngestor):
    source_name = "quora"

    def __init__(self, db: Database, config: QuoraConfig, proxy_client: httpx.AsyncClient):
        super().__init__(db)
        self.config = config
        self.client = proxy_client

    def _parse_quora_page(self, html: str, feed: str) -> list[dict]:
        """Parse Quora topic/search page HTML into signals."""
        signals = []
        soup = BeautifulSoup(html, "html.parser")

        # Quora renders client-side, but search results may have question links
        # Look for question-like patterns in the HTML
        question_links = soup.find_all("a", href=True)
        seen_ids = set()
        for link in question_links:
            href = link.get("href", "")
            text = link.get_text(strip=True)
            if not text or len(text) < 15:
                continue
            # Quora questions typically end with ?
            if "?" not in text and not href.startswith("/"):
                continue
            # Skip navigation / non-question links
            if href in ("#", "/", "") or "login" in href.lower():
                continue

            # Derive a stable ID from the URL path
            source_id = re.sub(r"[^a-zA-Z0-9]", "_", href)[:100]
            if source_id in seen_ids:
                continue
            seen_ids.add(source_id)

            signal = self.build_signal(
                source_id=f"quora_{source_id}",
                title=text,
                body="",
                url=f"https://www.quora.com{href}" if href.startswith("/") else href,
                permalink=f"https://www.quora.com{href}" if href.startswith("/") else href,
                feed=feed,
            )
            signals.append(signal)

        return signals

    async def ingest(self, topics_for_search: list[str] | None = None, **kwargs) -> int:
        ingested = 0
        if not topics_for_search:
            return 0

        for topic in topics_for_search[:10]:
            try:
                url = f"https://www.quora.com/search?q={topic}"
                resp = await fetch(url, self.client, accept="text/html")
                self.request_count += 1
                self.bytes_received += len(resp.content)
                if resp.status_code == 200:
                    signals = self._parse_quora_page(resp.text, "search")
                    for s in signals:
                        if self.store_signal(s):
                            ingested += 1
                    logger.info(f"Quora '{topic}': {len(signals)} questions")
                else:
                    logger.warning(f"Quora '{topic}': HTTP {resp.status_code}")
            except Exception as e:
                logger.error(f"Quora '{topic}' error: {e}")
                self.errors.append(f"Quora '{topic}': {e}")
        return ingested
