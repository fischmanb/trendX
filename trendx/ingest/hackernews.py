"""HackerNews Firebase API ingestion (direct, no proxy)."""

import logging
from datetime import datetime

import httpx

from .base import BaseIngestor
from ..proxy import fetch_direct
from ..config import HackerNewsConfig
from ..store.db import Database

logger = logging.getLogger(__name__)


class HackerNewsIngestor(BaseIngestor):
    source_name = "hackernews"

    def __init__(self, db: Database, config: HackerNewsConfig, client: httpx.AsyncClient):
        super().__init__(db)
        self.config = config
        self.client = client

    async def _fetch_item(self, item_id: int) -> dict | None:
        try:
            url = f"{self.config.base_url}/item/{item_id}.json"
            resp = await fetch_direct(url, self.client)
            self.request_count += 1
            self.bytes_received += len(resp.content)
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.error(f"HN item {item_id} error: {e}")
            self.errors.append(f"HN item {item_id}: {e}")
        return None

    async def ingest(self, **kwargs) -> int:
        ingested = 0
        for story_type in self.config.story_types:
            try:
                url = f"{self.config.base_url}/{story_type}.json"
                resp = await fetch_direct(url, self.client)
                self.request_count += 1
                self.bytes_received += len(resp.content)
                if resp.status_code != 200:
                    logger.warning(f"HN {story_type}: HTTP {resp.status_code}")
                    continue

                story_ids = resp.json()[:self.config.max_stories_per_feed]
                for sid in story_ids:
                    item = await self._fetch_item(sid)
                    if not item or item.get("type") not in ("story", "job"):
                        continue
                    if item.get("score", 0) < self.config.min_score:
                        continue

                    created_at = datetime.utcfromtimestamp(item.get("time", 0)).isoformat()
                    signal = self.build_signal(
                        source_id=str(item["id"]),
                        title=item.get("title", ""),
                        body=item.get("text", ""),
                        url=item.get("url", ""),
                        permalink=f"https://news.ycombinator.com/item?id={item['id']}",
                        score=item.get("score", 0),
                        comment_count=item.get("descendants", 0),
                        author=item.get("by", ""),
                        created_at=created_at,
                        feed=story_type,
                    )
                    if self.store_signal(signal):
                        ingested += 1

                logger.info(f"HN {story_type}: processed {len(story_ids)} stories")
            except Exception as e:
                logger.error(f"HN {story_type} error: {e}")
                self.errors.append(f"HN {story_type}: {e}")
        return ingested

    async def ingest_follow_ups(self, high_signal_posts: list[dict]) -> int:
        """Fetch top-level comments on high-score HN items."""
        ingested = 0
        for post in high_signal_posts:
            source_id = post.get("source_id", "")
            try:
                item = await self._fetch_item(int(source_id))
                if not item or "kids" not in item:
                    continue
                for kid_id in item["kids"][:10]:
                    comment = await self._fetch_item(kid_id)
                    if not comment or comment.get("type") != "comment":
                        continue
                    if comment.get("text") in (None, "[dead]", "[flagged]"):
                        continue
                    created_at = datetime.utcfromtimestamp(comment.get("time", 0)).isoformat()
                    signal = self.build_signal(
                        source_id=str(comment["id"]),
                        title="",
                        body=comment.get("text", ""),
                        permalink=f"https://news.ycombinator.com/item?id={comment['id']}",
                        score=0,
                        author=comment.get("by", ""),
                        created_at=created_at,
                        feed="comment",
                        parent_signal_id=post.get("id"),
                    )
                    if self.store_signal(signal):
                        ingested += 1
            except Exception as e:
                logger.error(f"HN follow-up {source_id} error: {e}")
                self.errors.append(f"HN follow-up {source_id}: {e}")
        return ingested
