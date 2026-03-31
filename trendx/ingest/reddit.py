"""Reddit trend surface ingestion via IPRoyal proxy — parallel."""

import asyncio
import json
import logging
from datetime import datetime, UTC

import httpx

from .base import BaseIngestor
from ..proxy import fetch
from ..config import RedditConfig
from ..store.db import Database

logger = logging.getLogger(__name__)

MAX_CONCURRENT_REDDIT = 10  # Parallel subreddit fetches (safe with IP rotation)


class RedditIngestor(BaseIngestor):
    source_name = "reddit"

    def __init__(self, db: Database, config: RedditConfig, proxy_client: httpx.AsyncClient):
        super().__init__(db)
        self.config = config
        self.client = proxy_client

    def _parse_listing(self, data: dict, feed: str) -> list[dict]:
        """Parse a Reddit listing JSON response into raw signals."""
        signals = []
        children = data.get("data", {}).get("children", [])
        for child in children:
            post = child.get("data", {})
            if not post.get("id"):
                continue
            created_utc = post.get("created_utc", 0)
            created_at = datetime.fromtimestamp(created_utc, UTC).isoformat() if created_utc else None
            signal = self.build_signal(
                source_id=post["id"],
                title=post.get("title", ""),
                body=post.get("selftext", ""),
                url=post.get("url", ""),
                permalink=f"https://www.reddit.com{post.get('permalink', '')}",
                score=post.get("score", 0),
                comment_count=post.get("num_comments", 0),
                subreddit=post.get("subreddit", ""),
                author=post.get("author", ""),
                created_at=created_at,
                feed=feed,
            )
            signals.append(signal)
        return signals

    def _parse_subreddit_listing(self, data: dict) -> list[dict]:
        """Parse subreddit listing for new/search results."""
        subs = []
        children = data.get("data", {}).get("children", [])
        for child in children:
            sub = child.get("data", {})
            if not sub.get("display_name"):
                continue
            created_utc = sub.get("created_utc", 0)
            subs.append({
                "subreddit": sub["display_name"],
                "subscriber_count": sub.get("subscribers", 0),
                "description": sub.get("public_description", ""),
                "created_at": datetime.fromtimestamp(created_utc, UTC).isoformat() if created_utc else None,
                "is_new": True,
            })
        return subs

    async def ingest(self, topics_for_search: list[str] | None = None, **kwargs) -> int:
        """Fetch all Reddit trend surfaces in parallel."""
        import time as _time
        t0 = _time.time()
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REDDIT)
        results = []

        async def fetch_feed(feed_cfg):
            async with semaphore:
                try:
                    resp = await fetch(feed_cfg.url_template, self.client)
                    self.request_count += 1
                    self.bytes_received += len(resp.content)
                    if resp.status_code == 200:
                        signals = self._parse_listing(resp.json(), feed_cfg.name)
                        count = sum(1 for s in signals if self.store_signal(s))
                        logger.debug(f"  Reddit {feed_cfg.name}: {len(signals)} posts, {count} new")
                        return count
                    else:
                        self.errors.append(f"Reddit {feed_cfg.name}: HTTP {resp.status_code}")
                        return 0
                except Exception as e:
                    self.errors.append(f"Reddit {feed_cfg.name}: {e}")
                    return 0

        async def fetch_new_subs():
            async with semaphore:
                try:
                    resp = await fetch(self.config.new_subreddits_url, self.client)
                    self.request_count += 1
                    self.bytes_received += len(resp.content)
                    if resp.status_code == 200:
                        subs = self._parse_subreddit_listing(resp.json())
                        for sub in subs:
                            self.db.upsert_subreddit(sub)
                        return 0
                except Exception as e:
                    self.errors.append(f"Reddit new subreddits: {e}")
                    return 0

        async def search_subreddit(topic):
            async with semaphore:
                try:
                    url = self.config.subreddit_search_template.format(topic=topic)
                    resp = await fetch(url, self.client)
                    self.request_count += 1
                    self.bytes_received += len(resp.content)
                    if resp.status_code == 200:
                        subs = self._parse_subreddit_listing(resp.json())
                        for sub in subs:
                            self.db.upsert_subreddit(sub)
                    return 0
                except Exception as e:
                    self.errors.append(f"Reddit sub search '{topic}': {e}")
                    return 0

        # Build all tasks
        tasks = [fetch_feed(f) for f in self.config.feeds]
        tasks.append(fetch_new_subs())
        if topics_for_search:
            tasks.extend(search_subreddit(t) for t in topics_for_search[:5])

        # Fire all at once
        results = await asyncio.gather(*tasks, return_exceptions=True)
        ingested = sum(r for r in results if isinstance(r, int))

        elapsed = _time.time() - t0
        logger.info(f"Reddit: {ingested} new signals from {len(tasks)} requests in {elapsed:.1f}s (parallel)")

        # Refresh subscriber counts for subreddits we've seen but don't have counts for
        await self._refresh_subscriber_counts(semaphore)

        return ingested

    async def _refresh_subscriber_counts(self, semaphore: asyncio.Semaphore):
        """Fetch real subscriber counts for subreddits missing them."""
        # First, ensure all subreddits from signals are in the tracker
        self.db.conn.execute("""
            INSERT OR IGNORE INTO subreddit_tracker (subreddit, subscriber_count, first_seen)
            SELECT DISTINCT subreddit, 0, datetime('now')
            FROM raw_signals WHERE subreddit != '' AND source = 'reddit'
        """)
        self.db.conn.commit()

        # Now fetch counts for those missing them
        missing = self.db.conn.execute(
            "SELECT DISTINCT subreddit FROM subreddit_tracker WHERE subscriber_count <= 1"
        ).fetchall()
        if not missing:
            return

        subs_to_check = [r["subreddit"] for r in missing]  # All of them — IP rotation handles rate limits

        async def fetch_about(sub_name):
            async with semaphore:
                try:
                    url = f"https://www.reddit.com/r/{sub_name}/about.json"
                    resp = await fetch(url, self.client)
                    self.request_count += 1
                    if resp.status_code == 200:
                        data = resp.json().get("data", {})
                        count = data.get("subscribers", 0)
                        if count > 0:
                            self.db.conn.execute(
                                "UPDATE subreddit_tracker SET subscriber_count = ? WHERE subreddit = ?",
                                (count, sub_name),
                            )
                            self.db.conn.commit()
                            return count
                    return 0
                except Exception:
                    return 0

        results = await asyncio.gather(*[fetch_about(s) for s in subs_to_check], return_exceptions=True)
        updated = sum(1 for r in results if isinstance(r, int) and r > 0)
        if updated:
            logger.info(f"  Updated subscriber counts for {updated}/{len(subs_to_check)} subreddits")

    async def ingest_follow_ups(self, high_signal_posts: list[dict]) -> int:
        """Fetch comment threads for high-intensity posts — parallel."""
        semaphore = asyncio.Semaphore(MAX_CONCURRENT_REDDIT)

        async def fetch_comments(post):
            async with semaphore:
                subreddit = post.get("subreddit", "")
                source_id = post.get("source_id", "")
                if not subreddit or not source_id:
                    return 0
                try:
                    url = self.config.comment_template.format(
                        subreddit=subreddit, post_id=source_id
                    )
                    resp = await fetch(url, self.client)
                    self.request_count += 1
                    self.bytes_received += len(resp.content)
                    count = 0
                    if resp.status_code == 200:
                        data = resp.json()
                        if isinstance(data, list) and len(data) > 1:
                            comments = data[1].get("data", {}).get("children", [])
                            for comment in comments[:10]:
                                cdata = comment.get("data", {})
                                if not cdata.get("id") or cdata.get("body") in (None, "[deleted]", "[removed]"):
                                    continue
                                created_utc = cdata.get("created_utc", 0)
                                signal = self.build_signal(
                                    source_id=cdata["id"],
                                    title="",
                                    body=cdata.get("body", ""),
                                    permalink=f"https://www.reddit.com{cdata.get('permalink', '')}",
                                    score=cdata.get("score", 0),
                                    subreddit=subreddit,
                                    author=cdata.get("author", ""),
                                    created_at=datetime.fromtimestamp(created_utc, UTC).isoformat() if created_utc else None,
                                    feed="comment",
                                    parent_signal_id=post.get("id"),
                                )
                                if self.store_signal(signal):
                                    count += 1
                    return count
                except Exception as e:
                    self.errors.append(f"Reddit comments {source_id}: {e}")
                    return 0

        tasks = [fetch_comments(p) for p in high_signal_posts[:30]]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(r for r in results if isinstance(r, int))
