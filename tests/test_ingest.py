"""Tests for ingestion modules."""

import json
import pytest
import pytest_asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from trendx.store.db import Database
from trendx.config import RedditConfig, RedditFeed, HackerNewsConfig
from trendx.ingest.reddit import RedditIngestor
from trendx.ingest.hackernews import HackerNewsIngestor

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    database.connect()
    database.init_schema()
    yield database
    database.close()


@pytest.fixture
def reddit_config():
    return RedditConfig(
        feeds=[RedditFeed(url_template="https://www.reddit.com/r/all/rising.json?limit=100", name="rising")],
        follow_up_intensity_threshold=4,
    )


@pytest.fixture
def hn_config():
    return HackerNewsConfig(
        max_stories_per_feed=3,
        min_score=10,
        story_types=["topstories"],
    )


class TestRedditIngestor:
    def test_parse_listing(self, db, reddit_config):
        with open(FIXTURES / "reddit_rising.json") as f:
            data = json.load(f)

        mock_client = MagicMock()
        ingestor = RedditIngestor(db, reddit_config, mock_client)
        signals = ingestor._parse_listing(data, "rising")

        assert len(signals) == 4
        assert signals[0]["source"] == "reddit"
        assert signals[0]["title"] == "New overtime rules 2026 - am I eligible?"
        assert signals[0]["subreddit"] == "personalfinance"
        assert signals[0]["score"] == 1247
        assert signals[0]["feed"] == "rising"

    def test_parse_listing_stores_unique(self, db, reddit_config):
        with open(FIXTURES / "reddit_rising.json") as f:
            data = json.load(f)

        mock_client = MagicMock()
        ingestor = RedditIngestor(db, reddit_config, mock_client)
        signals = ingestor._parse_listing(data, "rising")

        # Store all signals
        stored = sum(1 for s in signals if ingestor.store_signal(s))
        assert stored == 4

        # Try storing again — should be deduplicated
        signals2 = ingestor._parse_listing(data, "rising")
        stored2 = sum(1 for s in signals2 if ingestor.store_signal(s))
        assert stored2 == 0  # All duplicates

    def test_parse_comments(self, db, reddit_config):
        with open(FIXTURES / "reddit_comments.json") as f:
            data = json.load(f)

        mock_client = MagicMock()
        ingestor = RedditIngestor(db, reddit_config, mock_client)

        # Parse comment listing (second element of the JSON array)
        comments = data[1]["data"]["children"]
        assert len(comments) == 3
        assert "Following" in comments[0]["data"]["body"]


class TestHackerNewsIngestor:
    @pytest.mark.asyncio
    async def test_ingest_with_mock(self, db, hn_config):
        mock_client = AsyncMock()

        # Mock story list response
        stories_response = MagicMock()
        stories_response.status_code = 200
        stories_response.content = b"[41000001]"
        stories_response.json.return_value = [41000001]

        # Mock item response
        with open(FIXTURES / "hn_item_41000001.json") as f:
            item_data = json.load(f)
        item_response = MagicMock()
        item_response.status_code = 200
        item_response.content = json.dumps(item_data).encode()
        item_response.json.return_value = item_data

        mock_client.get = AsyncMock(side_effect=[stories_response, item_response])

        ingestor = HackerNewsIngestor(db, hn_config, mock_client)
        with patch("trendx.ingest.hackernews.fetch_direct", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.side_effect = [stories_response, item_response]
            count = await ingestor.ingest()

        assert count == 1
        signals = db.get_unclassified_signals()
        assert len(signals) == 1
        assert signals[0]["title"] == "Show HN: Open-source alternative to Notion with offline support"
        assert signals[0]["source"] == "hackernews"
