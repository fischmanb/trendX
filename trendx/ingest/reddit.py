"""Reddit trend surface ingestion via IPRoyal proxy."""

import json
import logging
from datetime import datetime

import httpx

from .base import BaseIngestor
from ..proxy import fetch
from ..config import RedditConfig
from ..store.db import Database

logger = logging.getLogger(__name__)


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
            created_at = datetime.utcfromtimestamp(created_utc).isoformat() if created_utc else None
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
                "created_at": datetime.utcfromtimestamp(created_utc).isoformat() if created_utc else None,
                "is_new": True,
            })
        return subs

    async def ingest(self, topics_for_search: list[str] | None = None, **kwargs) -> int:
        """Fetch all Reddit trend surfaces."""
        ingested = 0

        # Primary feeds
        for feed_cfg in self.config.feeds:
            try:
                resp = await fetch(feed_cfg.url_template, self.client)
                self.request_count += 1
                self.bytes_received += len(resp.content)
                if resp.status_code == 200:
                    data = resp.json()
                    signals = self._parse_listing(data, feed_cfg.name)
                    for s in signals:
                        if self.store_signal(s):
                            ingested += 1
                    logger.info(f"Reddit {feed_cfg.name}: {len(signals)} posts, {ingested} new")
                else:
                    logger.warning(f"Reddit {feed_cfg.name}: HTTP {resp.status_code}")
                    self.errors.append(f"Reddit {feed_cfg.name}: HTTP {resp.status_code}")
            except Exception as e:
                logger.error(f"Reddit {feed_cfg.name} error: {e}")
                self.errors.append(f"Reddit {feed_cfg.name}: {e}")

        # New subreddits
        try:
            resp = await fetch(self.config.new_subreddits_url, self.client)
            self.request_count += 1
            self.bytes_received += len(resp.content)
            if resp.status_code == 200:
                subs = self._parse_subreddit_listing(resp.json())
                for sub in subs:
                    self.db.upsert_subreddit(sub)
                logger.info(f"Reddit new subreddits: {len(subs)} tracked")
        except Exception as e:
            logger.error(f"Reddit new subreddits error: {e}")
            self.errors.append(f"Reddit new subreddits: {e}")

        # Subreddit search (using topics from previous high-scoring opportunities)
        if topics_for_search:
            for topic in topics_for_search[:5]:
                try:
                    url = self.config.subreddit_search_template.format(topic=topic)
                    resp = await fetch(url, self.client)
                    self.request_count += 1
                    self.bytes_received += len(resp.content)
                    if resp.status_code == 200:
                        subs = self._parse_subreddit_listing(resp.json())
                        for sub in subs:
                            self.db.upsert_subreddit(sub)
                except Exception as e:
                    logger.error(f"Reddit subreddit search '{topic}' error: {e}")
                    self.errors.append(f"Reddit sub search '{topic}': {e}")
        return ingested

    async def ingest_follow_ups(self, high_signal_posts: list[dict]) -> int:
        """Fetch comment threads for high-intensity posts."""
        ingested = 0
        for post in high_signal_posts[:30]:
            subreddit = post.get("subreddit", "")
            source_id = post.get("source_id", "")
            if not subreddit or not source_id:
                continue
            try:
                url = self.config.comment_template.format(
                    subreddit=subreddit, post_id=source_id
                )
                resp = await fetch(url, self.client)
                self.request_count += 1
                self.bytes_received += len(resp.content)
                if resp.status_code == 200:
                    data = resp.json()
                    # Reddit comment JSON is a list of two listings
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
                                created_at=datetime.utcfromtimestamp(created_utc).isoformat() if created_utc else None,
                                feed="comment",
                                parent_signal_id=post.get("id"),
                            )
                            if self.store_signal(signal):
                                ingested += 1
            except Exception as e:
                logger.error(f"Reddit comments {source_id} error: {e}")
                self.errors.append(f"Reddit comments {source_id}: {e}")
        return ingested
