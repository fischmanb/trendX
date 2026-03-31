"""YouTube Data API v3 ingestion (direct, no proxy)."""

import json
import logging
from datetime import datetime, timedelta, UTC

import httpx

from .base import BaseIngestor
from ..proxy import fetch_direct
from ..config import YouTubeConfig
from ..store.db import Database

logger = logging.getLogger(__name__)

YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"
YT_VIDEOS_URL = "https://www.googleapis.com/youtube/v3/videos"
YT_COMMENTS_URL = "https://www.googleapis.com/youtube/v3/commentThreads"


class YouTubeIngestor(BaseIngestor):
    source_name = "youtube"

    def __init__(self, db: Database, config: YouTubeConfig, client: httpx.AsyncClient):
        super().__init__(db)
        self.config = config
        self.client = client
        self._searches_used = 0
        self._comment_fetches_used = 0

    async def ingest(self, topics_for_search: list[str] | None = None, **kwargs) -> int:
        """Search YouTube for topics and mine comments for demand signals."""
        if not self.config.api_key:
            logger.warning("YouTube API key not set, skipping")
            return 0

        ingested = 0
        if not topics_for_search:
            return 0

        for topic in topics_for_search:
            if self._searches_used >= self.config.max_searches_per_cycle:
                break
            try:
                ingested += await self._search_topic(topic)
            except Exception as e:
                logger.error(f"YouTube search '{topic}' error: {e}")
                self.errors.append(f"YouTube search '{topic}': {e}")
        return ingested

    async def _search_topic(self, topic: str) -> int:
        """Search for recent videos on a topic and fetch their comments."""
        ingested = 0
        since = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

        url = (
            f"{YT_SEARCH_URL}?part=snippet&q={topic}&type=video"
            f"&order=date&publishedAfter={since}&maxResults=10"
            f"&key={self.config.api_key}"
        )
        resp = await fetch_direct(url, self.client)
        self.request_count += 1
        self._searches_used += 1
        self.bytes_received += len(resp.content)

        if resp.status_code != 200:
            logger.warning(f"YouTube search: HTTP {resp.status_code}")
            return 0

        data = resp.json()
        items = data.get("items", [])

        # Store search results as signals (competition check data)
        video_ids = []
        for item in items:
            vid_id = item.get("id", {}).get("videoId", "")
            if not vid_id:
                continue
            video_ids.append(vid_id)
            snippet = item.get("snippet", {})
            signal = self.build_signal(
                source_id=vid_id,
                title=snippet.get("title", ""),
                body=snippet.get("description", ""),
                url=f"https://www.youtube.com/watch?v={vid_id}",
                permalink=f"https://www.youtube.com/watch?v={vid_id}",
                author=snippet.get("channelTitle", ""),
                created_at=snippet.get("publishedAt", ""),
                feed="search",
                metadata_json=json.dumps({"search_topic": topic}),
            )
            if self.store_signal(signal):
                ingested += 1

        # Fetch comments on these videos
        for vid_id in video_ids[:3]:
            if self._comment_fetches_used >= self.config.max_comment_fetches_per_cycle:
                break
            try:
                ingested += await self._fetch_comments(vid_id, topic)
            except Exception as e:
                logger.error(f"YouTube comments {vid_id} error: {e}")

        logger.info(f"YouTube '{topic}': {len(items)} videos, {ingested} signals")
        return ingested

    async def _fetch_comments(self, video_id: str, topic: str) -> int:
        """Fetch top comments on a video."""
        ingested = 0
        url = (
            f"{YT_COMMENTS_URL}?part=snippet&videoId={video_id}"
            f"&maxResults=50&order=relevance&key={self.config.api_key}"
        )
        resp = await fetch_direct(url, self.client)
        self.request_count += 1
        self._comment_fetches_used += 1
        self.bytes_received += len(resp.content)

        if resp.status_code != 200:
            return 0

        data = resp.json()
        for item in data.get("items", []):
            snippet = item.get("snippet", {}).get("topLevelComment", {}).get("snippet", {})
            if not snippet:
                continue
            signal = self.build_signal(
                source_id=item.get("id", ""),
                title="",
                body=snippet.get("textDisplay", ""),
                url=f"https://www.youtube.com/watch?v={video_id}",
                permalink=f"https://www.youtube.com/watch?v={video_id}",
                score=snippet.get("likeCount", 0),
                author=snippet.get("authorDisplayName", ""),
                created_at=snippet.get("publishedAt", ""),
                feed="comment",
                metadata_json=json.dumps({
                    "video_id": video_id,
                    "search_topic": topic,
                }),
            )
            if self.store_signal(signal):
                ingested += 1
        return ingested

    async def check_competition(self, topic: str) -> dict:
        """Check how many recent videos exist on a topic (content gap analysis)."""
        if not self.config.api_key:
            return {"topic": topic, "recent_videos": -1, "gap": "unknown"}

        since = (datetime.now(UTC) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")
        url = (
            f"{YT_SEARCH_URL}?part=snippet&q={topic}&type=video"
            f"&order=date&publishedAfter={since}&maxResults=1"
            f"&key={self.config.api_key}"
        )
        resp = await fetch_direct(url, self.client)
        self.request_count += 1
        self._searches_used += 1

        if resp.status_code != 200:
            return {"topic": topic, "recent_videos": -1, "gap": "unknown"}

        data = resp.json()
        total = data.get("pageInfo", {}).get("totalResults", 0)
        gap = "open" if total < 5 else "moderate" if total < 20 else "saturated"
        return {"topic": topic, "recent_videos": total, "gap": gap}
