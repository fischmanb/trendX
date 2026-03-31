"""Market sizing via free signals — Google Trends, subreddit size, autocomplete, engagement ratio."""

import asyncio
import json
import logging
import math
from datetime import datetime

import anthropic
import httpx

logger = logging.getLogger(__name__)


SEARCH_QUERY_SYSTEM = """Given product opportunity topics, generate 3 search queries PER TOPIC that a real person experiencing this problem would actually type into Google.

Rules:
- Each query should be 2-5 words — what someone would actually type
- Include at least one problem-focused query ("how to fix X") and one solution-focused query ("X calculator")
- Think about what the person would search BEFORE they know a solution exists
- No quotes, no search operators, just natural search terms

Respond ONLY with a JSON object mapping each topic to an array of 3 queries. No preamble.
Example: {"MCA rate benchmarking for small business owners": ["MCA rate calculator", "merchant cash advance rates", "compare MCA offers"]}"""


async def get_google_trends_score(topic: str) -> int | None:
    """Get Google Trends interest score (0-100) for a topic. Returns None on error."""
    try:
        from pytrends.request import TrendReq
        import time
        pytrends = TrendReq(hl="en-US", tz=360)
        pytrends.build_payload([topic[:100]], timeframe="now 7-d", geo="US")
        interest = pytrends.interest_over_time()
        time.sleep(1.5)
        if interest.empty:
            return 0
        values = interest[topic[:100]].tolist()
        return max(values) if values else 0
    except Exception as e:
        logger.debug(f"Google Trends error for '{topic}': {e}")
        return None


async def get_autocomplete_count(topic: str) -> int | None:
    """Get number of Google autocomplete suggestions for a topic."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            url = f"https://suggestqueries.google.com/complete/search?q={topic}&client=chrome"
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            if resp.status_code == 200:
                data = resp.json()
                suggestions = data[1] if len(data) > 1 else []
                return len(suggestions)
        return 0
    except Exception as e:
        logger.debug(f"Autocomplete error for '{topic}': {e}")
        return None


def compute_engagement_ratio(score: int, subscriber_count: int) -> float | None:
    """Compute engagement ratio: upvotes / subscribers. Returns 0-1 ratio."""
    if not subscriber_count or subscriber_count < 1:
        return None
    return score / subscriber_count


def compute_subscriber_signal(total_subscribers: int) -> float:
    """Normalize subscriber count to 0-100 using log scale.
    Calibrated from scraped data: max observed ~780K (log10=5.9). Ceiling=6 (1M=100).
    """
    if total_subscribers < 1:
        return 0
    return min(100, (math.log10(total_subscribers) / 6) * 100)


def compute_market_signal(
    google_trends: int | None = None,
    total_subscribers: int = 0,
    autocomplete_count: int | None = None,
    engagement_ratio: float | None = None,
) -> dict:
    """Combine available free signals into a market sizing score (0-100).
    Only averages signals we actually have — missing data doesn't pull score down.
    """
    signals = {}
    available = []

    if google_trends is not None:
        signals["google_trends"] = google_trends
        available.append(google_trends)

    if total_subscribers > 0:
        sub_score = compute_subscriber_signal(total_subscribers)
        signals["subscriber_score"] = round(sub_score, 1)
        available.append(sub_score)

    if autocomplete_count is not None:
        # Google returns 0-15 suggestions. Normalize to 0-100 using max of 15.
        auto_score = min(100, round((autocomplete_count / 15) * 100))
        signals["autocomplete_score"] = auto_score
        available.append(auto_score)

    if engagement_ratio is not None and engagement_ratio > 0:
        # UNCALIBRATED: ×5000 invented. Will recalibrate once subscriber data flows.
        eng_score = min(100, engagement_ratio * 5000)
        signals["engagement_score"] = round(eng_score, 1)
        available.append(eng_score)

    combined = round(sum(available) / len(available), 1) if available else 0
    signals["combined"] = combined
    signals["signals_used"] = len(available)

    return signals


class MarketSizer:
    """Collect free market sizing signals for opportunities using LLM-generated search queries."""

    def __init__(self, db, anthropic_config=None):
        self.db = db
        self.anthropic_config = anthropic_config
        self.client = None
        self.total_cost = 0.0
        if anthropic_config and anthropic_config.api_key:
            self.client = anthropic.Anthropic(api_key=anthropic_config.api_key)

    def generate_search_queries(self, topics: list[str]) -> dict[str, list[str]]:
        """Use LLM to generate realistic search queries for a batch of topics.
        One API call for the whole batch. Returns {topic: [query1, query2, query3]}.
        """
        if not self.client or not topics:
            # Fallback: first 4 words of topic
            return {t: [' '.join(t.split()[:4])] for t in topics}

        prompt = "\n".join(f"- {t}" for t in topics)

        try:
            response = self.client.messages.create(
                model=self.anthropic_config.model,
                max_tokens=1500,
                temperature=0.3,
                system=SEARCH_QUERY_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            cost = (response.usage.input_tokens * 3.0 / 1_000_000) + (response.usage.output_tokens * 15.0 / 1_000_000)
            self.total_cost += cost

            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

            parsed = json.loads(raw.strip())
            if isinstance(parsed, dict):
                result = {}
                for t in topics:
                    # Try exact match, then partial match
                    if t in parsed:
                        result[t] = parsed[t]
                    else:
                        for key, val in parsed.items():
                            if key in t or t in key:
                                result[t] = val
                                break
                        if t not in result:
                            result[t] = [' '.join(t.split()[:4])]
                logger.info(f"Generated search queries for {len(topics)} topics (${cost:.4f})")
                return result

            return {t: [' '.join(t.split()[:4])] for t in topics}
        except Exception as e:
            logger.debug(f"Search query generation error: {e}")
            return {t: [' '.join(t.split()[:4])] for t in topics}

    async def size_opportunity(self, opportunity: dict, search_queries: list[str] | None = None) -> dict:
        """Gather all free market sizing signals for one opportunity.
        
        Runs Google Trends and autocomplete across multiple query variants (from LLM),
        takes the best score from each to avoid penalizing good opportunities 
        that just need the right search phrasing.
        """
        topic = opportunity.get("topic", "")
        queries = search_queries or [' '.join(topic.split()[:4])]

        # Run Google Trends + autocomplete for each query variant concurrently
        best_gt = None
        best_auto = None
        all_queries_tried = []

        async def try_query(q):
            gt = await get_google_trends_score(q)
            ac = await get_autocomplete_count(q)
            return q, gt, ac

        results = await asyncio.gather(*[try_query(q) for q in queries], return_exceptions=True)

        for item in results:
            if isinstance(item, Exception):
                continue
            q, gt, ac = item
            all_queries_tried.append({"query": q, "trends": gt, "autocomplete": ac})
            # Take the BEST score across variants — if any phrasing shows demand, it's real
            if gt is not None and (best_gt is None or gt > best_gt):
                best_gt = gt
            if ac is not None and (best_auto is None or ac > best_auto):
                best_auto = ac

        # Total subscribers across all subreddits for this opportunity
        subs_json = opportunity.get("subreddits_json", "[]")
        subreddits = json.loads(subs_json) if isinstance(subs_json, str) else (subs_json or [])
        total_subs = 0
        for sub in subreddits:
            row = self.db.conn.execute(
                "SELECT subscriber_count FROM subreddit_tracker WHERE subreddit = ?", (sub,)
            ).fetchone()
            if row and row["subscriber_count"] and row["subscriber_count"] > 1:
                total_subs += row["subscriber_count"]

        # Engagement ratio: best post score / total subscribers
        raw_scores = self.db.conn.execute("""
            SELECT MAX(rs.score) as max_score FROM raw_signals rs
            JOIN classified_signals cs ON cs.raw_signal_id = rs.id
            JOIN opportunity_signals os ON cs.id = os.classified_signal_id
            WHERE os.opportunity_id = ?
        """, (opportunity.get("id", ""),)).fetchone()
        best_raw_score = raw_scores["max_score"] if raw_scores and raw_scores["max_score"] else 0
        eng_ratio = compute_engagement_ratio(best_raw_score, total_subs) if total_subs > 0 else None

        market = compute_market_signal(
            google_trends=best_gt,
            total_subscribers=total_subs,
            autocomplete_count=best_auto,
            engagement_ratio=eng_ratio,
        )
        market["topic"] = topic
        market["search_queries"] = all_queries_tried
        market["total_subscribers"] = total_subs
        market["google_trends_raw"] = best_gt
        market["autocomplete_raw"] = best_auto
        market["engagement_ratio_raw"] = eng_ratio
        market["best_raw_score"] = best_raw_score

        return market
