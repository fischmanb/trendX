"""Post-classification pattern detection — convergence, emergence."""

import logging
from ..store.db import Database
from ..config import ClusteringConfig, DetectionConfig

logger = logging.getLogger(__name__)


def detect_convergence(opportunities: list[dict], config: ClusteringConfig) -> list[dict]:
    """Flag opportunities with cross-subreddit or cross-source convergence."""
    updated = []
    for opp in opportunities:
        changed = False

        # Cross-subreddit convergence
        subreddit_count = opp.get("subreddit_count", 1)
        if subreddit_count >= config.convergence_min_subreddits:
            opp["convergence_detected"] = True
            avg_score = opp.get("max_intensity", 1) * 10  # proxy for avg signal score
            opp["convergence_score"] = subreddit_count * avg_score
            changed = True

        # Cross-source convergence
        distinct_sources = opp.get("distinct_source_count", 1)
        if distinct_sources >= 3:
            opp["cross_source_confirmed"] = True
            opp["convergence_score"] = opp.get("convergence_score", 0) * 1.5
            changed = True

        if changed:
            updated.append(opp)

    return updated


def detect_emergence(db: Database, config: DetectionConfig) -> list[dict]:
    """Cross-reference new subreddits with opportunity topics."""
    # This is handled during clustering when new_community signals are found
    # Kept as a hook for future subreddit growth tracking
    return []
