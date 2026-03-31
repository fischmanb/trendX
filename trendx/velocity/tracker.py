"""Re-crawl signals and compute velocity metrics."""

import asyncio
import logging
from datetime import datetime

import httpx

from ..proxy import make_proxy_client, fetch, fetch_direct
from ..config import ProxyConfig
from ..store.db import Database

logger = logging.getLogger(__name__)


class VelocityTracker:
    """Re-fetches known signals to detect acceleration."""

    def __init__(self, db: Database, proxy_config: ProxyConfig):
        self.db = db
        self.proxy_config = proxy_config
        self.rechecked = 0
        self.errors = 0

    async def recheck_signals(self, limit: int = 100) -> dict:
        """Re-fetch relevant signals and store new snapshots.
        
        Returns stats dict with counts and velocity data.
        """
        signals = self.db.get_signals_to_recheck(limit=limit)
        if not signals:
            return {"rechecked": 0, "accelerating": 0, "decelerating": 0, "dormant": 0}

        stats = {"rechecked": 0, "accelerating": 0, "decelerating": 0, "dormant": 0}


        # Group by source for efficient fetching
        reddit_signals = [s for s in signals if s["source"] == "reddit"]
        hn_signals = [s for s in signals if s["source"] == "hackernews"]

        # Re-fetch Reddit posts
        if reddit_signals:
            proxy_client = make_proxy_client(self.proxy_config.user, self.proxy_config.password)
            try:
                for sig in reddit_signals:
                    try:
                        url = sig.get("permalink") or sig.get("url", "")
                        if not url:
                            continue
                        # Reddit JSON endpoint
                        if "reddit.com" not in url:
                            url = f"https://www.reddit.com{url}"
                        json_url = url.rstrip("/") + ".json"

                        resp = await fetch(json_url, proxy_client)
                        if resp.status_code == 200:
                            data = resp.json()
                            if isinstance(data, list) and len(data) > 0:
                                post = data[0].get("data", {}).get("children", [{}])[0].get("data", {})
                                new_score = post.get("score", 0)
                                new_comments = post.get("num_comments", 0)

                                self.db.save_signal_snapshot(sig["id"], new_score, new_comments)
                                velocity = self._compute_velocity(sig["id"], sig["score"], new_score)
                                if velocity > 0:
                                    stats["accelerating"] += 1
                                elif velocity < 0:
                                    stats["decelerating"] += 1
                                stats["rechecked"] += 1
                    except Exception as e:
                        logger.debug(f"Reddit recheck error for {sig['id']}: {e}")
                        self.errors += 1
                    await asyncio.sleep(2)  # Don't hammer Reddit
            finally:
                await proxy_client.aclose()


        # Re-fetch HackerNews items
        if hn_signals:
            async with httpx.AsyncClient(timeout=15.0) as client:
                for sig in hn_signals:
                    try:
                        hn_id = sig["source_id"]
                        url = f"https://hacker-news.firebaseio.com/v0/item/{hn_id}.json"
                        resp = await client.get(url)
                        if resp.status_code == 200:
                            item = resp.json()
                            new_score = item.get("score", 0)
                            new_comments = len(item.get("kids", []))

                            self.db.save_signal_snapshot(sig["id"], new_score, new_comments)
                            velocity = self._compute_velocity(sig["id"], sig["score"], new_score)
                            if velocity > 0:
                                stats["accelerating"] += 1
                            elif velocity < 0:
                                stats["decelerating"] += 1
                            stats["rechecked"] += 1
                    except Exception as e:
                        logger.debug(f"HN recheck error for {sig['id']}: {e}")
                        self.errors += 1

        self.rechecked = stats["rechecked"]
        logger.info(f"Velocity recheck: {stats['rechecked']} signals — {stats['accelerating']} accelerating, {stats['decelerating']} decelerating")
        return stats

    def _compute_velocity(self, signal_id: str, old_score: int, new_score: int) -> float:
        """Compute score velocity. Positive = accelerating, negative = decelerating."""
        snapshots = self.db.get_signal_snapshots(signal_id)
        if len(snapshots) < 2:
            return float(new_score - (old_score or 0))

        # Compare last two snapshots
        prev = snapshots[-2]
        curr = snapshots[-1]
        try:
            prev_time = datetime.fromisoformat(prev["snapshot_at"])
            curr_time = datetime.fromisoformat(curr["snapshot_at"])
            hours = (curr_time - prev_time).total_seconds() / 3600
            if hours < 0.1:
                return 0
            return (curr["score"] - prev["score"]) / hours
        except (ValueError, TypeError):
            return 0


    def assess_opportunity_velocity(self, opportunity_id: str) -> dict:
        """Assess velocity state for an opportunity across all its signals.
        
        Returns dict with:
            state: 'accelerating' | 'plateau' | 'decelerating' | 'dormant' | 'insufficient_data'
            avg_velocity: float (points/hour across signals)
            max_velocity: float
            signals_checked: int
            snapshots_total: int
            recommendation: 'build_now' | 'wait' | 'archive' | 'monitor'
            reason: str
        """
        # Get all signals for this opportunity
        signal_rows = self.db.conn.execute("""
            SELECT rs.id, rs.score, rs.comment_count
            FROM raw_signals rs
            JOIN classified_signals cs ON cs.raw_signal_id = rs.id
            JOIN opportunity_signals os ON cs.id = os.classified_signal_id
            WHERE os.opportunity_id = ?
        """, (opportunity_id,)).fetchall()

        if not signal_rows:
            return {"state": "insufficient_data", "avg_velocity": 0, "max_velocity": 0,
                    "signals_checked": 0, "snapshots_total": 0,
                    "recommendation": "monitor", "reason": "no signals linked"}

        velocities = []
        total_snapshots = 0
        for sig in signal_rows:
            snaps = self.db.get_signal_snapshots(sig["id"])
            total_snapshots += len(snaps)
            if len(snaps) >= 2:
                prev = snaps[-2]
                curr = snaps[-1]
                try:
                    prev_time = datetime.fromisoformat(prev["snapshot_at"])
                    curr_time = datetime.fromisoformat(curr["snapshot_at"])
                    hours = (curr_time - prev_time).total_seconds() / 3600
                    if hours >= 0.1:
                        v = (curr["score"] - prev["score"]) / hours
                        velocities.append(v)
                except (ValueError, TypeError):
                    pass

        if not velocities:
            return {"state": "insufficient_data", "avg_velocity": 0, "max_velocity": 0,
                    "signals_checked": len(signal_rows), "snapshots_total": total_snapshots,
                    "recommendation": "monitor", "reason": f"need more snapshots ({total_snapshots} so far)"}

        avg_v = sum(velocities) / len(velocities)
        max_v = max(velocities)

        # Check for competition (existing_solution changed)
        opp = self.db.get_opportunity(opportunity_id)
        has_competition = (opp and opp.get("existing_solution") 
                          and opp["existing_solution"] not in ("none", "", "none identified"))

        return {
            "avg_velocity": round(avg_v, 2),
            "max_velocity": round(max_v, 2),
            "signals_checked": len(signal_rows),
            "snapshots_total": total_snapshots,
            "has_competition": has_competition,
        }
