"""Product Hunt GraphQL API ingestion (direct, no proxy)."""

import json
import logging
from datetime import datetime, timezone

import httpx

from .base import BaseIngestor
from ..config import ProductHuntConfig
from ..store.db import Database

logger = logging.getLogger(__name__)

PH_API_URL = "https://api.producthunt.com/v2/api/graphql"

PH_QUERY = """
{
  posts(order: RANKING, postedAfter: "%s") {
    edges {
      node {
        id
        name
        tagline
        votesCount
        commentsCount
        url
        website
        topics {
          edges {
            node {
              name
            }
          }
        }
        comments(first: 20) {
          edges {
            node {
              id
              body
              votesCount
            }
          }
        }
      }
    }
  }
}
"""


class ProductHuntIngestor(BaseIngestor):
    source_name = "producthunt"

    def __init__(self, db: Database, config: ProductHuntConfig):
        super().__init__(db)
        self.config = config

    async def ingest(self, **kwargs) -> int:
        if not self.config.api_token:
            logger.warning("Product Hunt API token not set, skipping")
            return 0

        ingested = 0
        today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
        query = PH_QUERY % today

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(
                    PH_API_URL,
                    json={"query": query},
                    headers={
                        "Authorization": f"Bearer {self.config.api_token}",
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                    },
                )
                self.request_count += 1
                self.bytes_received += len(resp.content)

                if resp.status_code != 200:
                    logger.warning(f"Product Hunt: HTTP {resp.status_code}")
                    self.errors.append(f"Product Hunt: HTTP {resp.status_code}")
                    return 0

                data = resp.json()
                posts = data.get("data", {}).get("posts", {}).get("edges", [])

                for edge in posts:
                    node = edge.get("node", {})
                    if not node.get("id"):
                        continue

                    # Extract topic names
                    topics = [
                        t["node"]["name"]
                        for t in node.get("topics", {}).get("edges", [])
                        if t.get("node", {}).get("name")
                    ]

                    # Store the product as a signal
                    signal = self.build_signal(
                        source_id=f"ph_{node['id']}",
                        title=node.get("name", ""),
                        body=node.get("tagline", ""),
                        url=node.get("website", ""),
                        permalink=node.get("url", ""),
                        score=node.get("votesCount", 0),
                        comment_count=node.get("commentsCount", 0),
                        feed="daily_ranking",
                        metadata_json=json.dumps({"topics": topics}),
                    )
                    if self.store_signal(signal):
                        ingested += 1

                    # Store comments as separate signals
                    comments = node.get("comments", {}).get("edges", [])
                    for cedge in comments:
                        cnode = cedge.get("node", {})
                        if not cnode.get("body"):
                            continue
                        csignal = self.build_signal(
                            source_id=f"ph_comment_{cnode.get('id', '')}",
                            title="",
                            body=cnode["body"],
                            permalink=node.get("url", ""),
                            score=cnode.get("votesCount", 0),
                            feed="comment",
                            parent_signal_id=signal["id"],
                            metadata_json=json.dumps({
                                "product_name": node.get("name", ""),
                                "topics": topics,
                            }),
                        )
                        if self.store_signal(csignal):
                            ingested += 1

                logger.info(f"Product Hunt: {len(posts)} products, {ingested} signals")

        except Exception as e:
            logger.error(f"Product Hunt error: {e}")
            self.errors.append(f"Product Hunt: {e}")

        return ingested
