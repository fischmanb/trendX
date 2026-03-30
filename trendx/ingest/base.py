"""Abstract base ingestor."""

import uuid
from abc import ABC, abstractmethod
from datetime import datetime

from ..store.db import Database


class BaseIngestor(ABC):
    """Base class for all platform ingestors."""

    source_name: str = ""

    def __init__(self, db: Database):
        self.db = db
        self.request_count = 0
        self.bytes_received = 0
        self.errors: list[str] = []

    def make_signal_id(self) -> str:
        return str(uuid.uuid4())

    def build_signal(
        self,
        source_id: str,
        title: str,
        body: str = "",
        url: str = "",
        permalink: str = "",
        score: int = 0,
        comment_count: int = 0,
        subreddit: str | None = None,
        author: str = "",
        created_at: str | None = None,
        feed: str = "",
        parent_signal_id: str | None = None,
        metadata_json: str | None = None,
    ) -> dict:
        return {
            "id": self.make_signal_id(),
            "source": self.source_name,
            "source_id": str(source_id),
            "title": title,
            "body": body,
            "url": url,
            "permalink": permalink,
            "score": score,
            "comment_count": comment_count,
            "subreddit": subreddit,
            "author": author,
            "created_at": created_at or datetime.utcnow().isoformat(),
            "feed": feed,
            "parent_signal_id": parent_signal_id,
            "metadata_json": metadata_json,
        }

    def store_signal(self, signal: dict) -> bool:
        return self.db.insert_raw_signal(signal)

    @abstractmethod
    async def ingest(self, **kwargs) -> int:
        """Run ingestion. Returns number of signals ingested."""
        ...

    async def ingest_follow_ups(self, high_signal_ids: list[dict]) -> int:
        """Fetch follow-up data for high-signal items. Override in subclasses."""
        return 0
