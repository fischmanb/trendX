"""Google Trends ingestion via pytrends (direct, no proxy)."""

import json
import logging
import time
from datetime import datetime

from .base import BaseIngestor
from ..config import GoogleTrendsConfig
from ..store.db import Database

logger = logging.getLogger(__name__)


class GoogleTrendsIngestor(BaseIngestor):
    source_name = "google_trends"

    def __init__(self, db: Database, config: GoogleTrendsConfig):
        super().__init__(db)
        self.config = config

    async def ingest(self, **kwargs) -> int:
        """Pull trending searches from Google Trends."""
        ingested = 0
        try:
            from pytrends.request import TrendReq
            pytrends = TrendReq(hl="en-US", tz=self.config.timezone)

            # Daily trending searches
            try:
                trending = pytrends.trending_searches(pn=self.config.trending_searches_pn)
                for idx, row in trending.iterrows():
                    topic = row[0] if len(row) > 0 else str(row)
                    signal = self.build_signal(
                        source_id=f"gt_trending_{topic}_{datetime.utcnow().strftime('%Y%m%d')}",
                        title=topic,
                        body="",
                        feed="trending_searches",
                        score=100 - idx,  # Higher rank = higher score
                    )
                    if self.store_signal(signal):
                        ingested += 1
                logger.info(f"Google Trends trending: {len(trending)} topics")
                time.sleep(1.5)
            except Exception as e:
                logger.error(f"Google Trends trending error: {e}")
                self.errors.append(f"Google Trends trending: {e}")

            # Real-time trending
            try:
                realtime = pytrends.realtime_trending_searches(pn="US")
                for idx, row in realtime.iterrows():
                    title = row.get("title", "") if hasattr(row, "get") else str(row)
                    signal = self.build_signal(
                        source_id=f"gt_realtime_{idx}_{datetime.utcnow().strftime('%Y%m%d%H')}",
                        title=title,
                        body=row.get("entityNames", "") if hasattr(row, "get") else "",
                        feed="realtime_trending",
                        score=50,
                    )
                    if self.store_signal(signal):
                        ingested += 1
                    if idx >= 30:
                        break
                logger.info(f"Google Trends realtime: processed")
                time.sleep(1.5)
            except Exception as e:
                logger.error(f"Google Trends realtime error: {e}")
                self.errors.append(f"Google Trends realtime: {e}")

        except ImportError:
            logger.warning("pytrends not installed, skipping Google Trends")
            self.errors.append("pytrends not installed")

        return ingested

    async def validate_topic(self, topic: str) -> dict | None:
        """Check Google Trends interest for a specific topic. Returns interest data."""
        try:
            from pytrends.request import TrendReq
            pytrends = TrendReq(hl="en-US", tz=self.config.timezone)
            pytrends.build_payload([topic], timeframe="now 7-d", geo=self.config.geo)
            interest = pytrends.interest_over_time()
            time.sleep(1.5)

            if interest.empty:
                return None

            values = interest[topic].tolist()
            return {
                "topic": topic,
                "current": values[-1] if values else 0,
                "average": sum(values) / len(values) if values else 0,
                "max": max(values) if values else 0,
                "trend": "rising" if len(values) >= 2 and values[-1] > values[0] else "flat",
            }
        except Exception as e:
            logger.error(f"Google Trends validation for '{topic}' error: {e}")
            return None
