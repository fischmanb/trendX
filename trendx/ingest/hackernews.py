"""HackerNews Firebase API ingestion (direct, no proxy) — parallel."""

import asyncio
import logging
from datetime import datetime, UTC

import httpx

from .base import BaseIngestor
from ..proxy import fetch_direct
from ..config import HackerNewsConfig
from ..store.db import Database

logger = logging.getLogger(__name__)

MAX_CONCURRENT_HN = 20  # Firebase API is fast and generous


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
        import time as _time
        t0 = _time.time()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_HN)

        # Phase 1: Fetch all story ID lists concurrently
        async def fetch_story_ids(story_type):
            async with semaphore:
                try:
                    url = f"{self.config.base_url}/{story_type}.json"
                    resp = await fetch_direct(url, self.client)
                    self.request_count += 1
                    self.bytes_received += len(resp.content)
                    if resp.status_code == 200:
                        return story_type, resp.json()[:self.config.max_stories_per_feed]
                except Exception as e:
                    self.errors.append(f"HN {story_type}: {e}")
                return story_type, []

        type_results = await asyncio.gather(*[fetch_story_ids(st) for st in self.config.story_types])

        # Collect all unique item IDs across all feeds
        all_items = []  # (story_type, item_id)
        seen_ids = set()
        for story_type, ids in type_results:
            for sid in ids:
                if sid not in seen_ids:
                    seen_ids.add(sid)
                    all_items.append((story_type, sid))

        # Phase 2: Fetch all items concurrently
        async def fetch_and_store(story_type, item_id):
            async with semaphore:
                item = await self._fetch_item(item_id)
                if not item or item.get("type") not in ("story", "job"):
                    return 0
                if item.get("score", 0) < self.config.min_score:
                    return 0
                created_at = datetime.fromtimestamp(item.get("time", 0), UTC).isoformat()
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
                return 1 if self.store_signal(signal) else 0

        results = await asyncio.gather(*[fetch_and_store(st, sid) for st, sid in all_items], return_exceptions=True)
        ingested = sum(r for r in results if isinstance(r, int))

        elapsed = _time.time() - t0
        logger.info(f"HN: {ingested} new signals from {len(all_items)} items in {elapsed:.1f}s (parallel)")
        return ingested

    async def ingest_follow_ups(self, high_signal_posts: list[dict]) -> int:
        """Fetch top-level comments on high-score HN items — parallel."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_HN)

        async def fetch_comments_for_post(post):
            async with semaphore:
                source_id = post.get("source_id", "")
                count = 0
                try:
                    item = await self._fetch_item(int(source_id))
                    if not item or "kids" not in item:
                        return 0

                    async def fetch_one_comment(kid_id):
                        async with semaphore:
                            comment = await self._fetch_item(kid_id)
                            if not comment or comment.get("type") != "comment":
                                return 0
                            if comment.get("text") in (None, "[dead]", "[flagged]"):
                                return 0
                            created_at = datetime.fromtimestamp(comment.get("time", 0), UTC).isoformat()
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
                            return 1 if self.store_signal(signal) else 0

                    results = await asyncio.gather(*[fetch_one_comment(kid) for kid in item["kids"][:10]], return_exceptions=True)
                    return sum(r for r in results if isinstance(r, int))
                except Exception as e:
                    self.errors.append(f"HN follow-up {source_id}: {e}")
                    return 0

        results = await asyncio.gather(*[fetch_comments_for_post(p) for p in high_signal_posts], return_exceptions=True)
        return sum(r for r in results if isinstance(r, int))
