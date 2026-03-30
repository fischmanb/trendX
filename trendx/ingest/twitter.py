"""X/Twitter ingestion via Nitter instances + proxy."""

import json
import logging
import random
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from .base import BaseIngestor
from ..proxy import fetch
from ..config import TwitterConfig
from ..store.db import Database

logger = logging.getLogger(__name__)


class TwitterIngestor(BaseIngestor):
    source_name = "twitter"

    def __init__(self, db: Database, config: TwitterConfig, proxy_client: httpx.AsyncClient):
        super().__init__(db)
        self.config = config
        self.client = proxy_client

    def _pick_instance(self) -> str:
        return random.choice(self.config.nitter_instances)

    def _parse_nitter_page(self, html: str, feed: str) -> list[dict]:
        """Parse Nitter search/trending HTML into signals."""
        signals = []
        soup = BeautifulSoup(html, "html.parser")

        timeline_items = soup.select(".timeline-item")
        for item in timeline_items:
            try:
                # Tweet content
                content_el = item.select_one(".tweet-content")
                if not content_el:
                    continue
                body = content_el.get_text(strip=True)

                # Tweet link for ID
                link_el = item.select_one(".tweet-link")
                permalink = ""
                source_id = ""
                if link_el:
                    href = link_el.get("href", "")
                    permalink = href
                    # Extract tweet ID from URL like /user/status/123456
                    id_match = re.search(r"/status/(\d+)", href)
                    if id_match:
                        source_id = id_match.group(1)

                if not source_id:
                    continue

                # Stats
                stats = item.select(".tweet-stat .tweet-stat-value")
                reply_count = 0
                retweet_count = 0
                like_count = 0
                if len(stats) >= 3:
                    reply_count = self._parse_stat(stats[0].get_text(strip=True))
                    retweet_count = self._parse_stat(stats[1].get_text(strip=True))
                    like_count = self._parse_stat(stats[2].get_text(strip=True))

                # Author
                author_el = item.select_one(".username")
                author = author_el.get_text(strip=True) if author_el else ""

                # Date
                date_el = item.select_one(".tweet-date a")
                created_at = None
                if date_el:
                    title = date_el.get("title", "")
                    if title:
                        try:
                            created_at = datetime.strptime(title, "%b %d, %Y · %I:%M %p %Z").isoformat()
                        except ValueError:
                            pass

                signal = self.build_signal(
                    source_id=source_id,
                    title="",
                    body=body,
                    permalink=permalink,
                    score=like_count,
                    comment_count=reply_count,
                    author=author,
                    created_at=created_at,
                    feed=feed,
                    metadata_json=json.dumps({
                        "retweet_count": retweet_count,
                        "like_count": like_count,
                        "reply_count": reply_count,
                    }),
                )
                signals.append(signal)
            except Exception as e:
                logger.debug(f"Twitter parse error: {e}")
                continue
        return signals

    def _parse_stat(self, text: str) -> int:
        """Parse stat like '1.2K' into integer."""
        text = text.strip().replace(",", "")
        if not text:
            return 0
        try:
            if text.endswith("K"):
                return int(float(text[:-1]) * 1000)
            elif text.endswith("M"):
                return int(float(text[:-1]) * 1000000)
            return int(text)
        except ValueError:
            return 0

    async def ingest(self, topics_for_search: list[str] | None = None, **kwargs) -> int:
        ingested = 0
        instance = self._pick_instance()

        # Trending / recent search
        try:
            url = f"{instance}/search?f=tweets&q=*"
            resp = await fetch(url, self.client, accept="text/html")
            self.request_count += 1
            self.bytes_received += len(resp.content)
            if resp.status_code == 200:
                signals = self._parse_nitter_page(resp.text, "trending")
                for s in signals:
                    if self.store_signal(s):
                        ingested += 1
                logger.info(f"Twitter trending: {len(signals)} tweets")
            else:
                logger.warning(f"Twitter trending: HTTP {resp.status_code} from {instance}")
                self.errors.append(f"Twitter trending: HTTP {resp.status_code}")
        except Exception as e:
            logger.error(f"Twitter trending error: {e}")
            self.errors.append(f"Twitter trending: {e}")

        # Topic searches for cross-source confirmation
        if topics_for_search:
            for topic in topics_for_search[:10]:
                try:
                    instance = self._pick_instance()
                    url = f"{instance}/search?q={topic}&f=tweets"
                    resp = await fetch(url, self.client, accept="text/html")
                    self.request_count += 1
                    self.bytes_received += len(resp.content)
                    if resp.status_code == 200:
                        signals = self._parse_nitter_page(resp.text, "search")
                        for s in signals:
                            if self.store_signal(s):
                                ingested += 1
                except Exception as e:
                    logger.error(f"Twitter search '{topic}' error: {e}")
                    self.errors.append(f"Twitter search '{topic}': {e}")
        return ingested
