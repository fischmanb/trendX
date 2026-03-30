"""Temporal delta detection — new topics, spikes, dying trends."""

import logging
from ..config import DeltasConfig

logger = logging.getLogger(__name__)


def detect_deltas(
    current_opps: list[dict],
    previous_snapshots: dict[str, dict],
    config: DeltasConfig,
) -> list[dict]:
    """Compare current opportunities to previous cycle snapshots.

    Returns list of opportunities with delta_type set.
    """
    deltas = []

    current_by_id = {opp["id"]: opp for opp in current_opps}

    for opp_id, opp in current_by_id.items():
        prev = previous_snapshots.get(opp_id)

        if prev is None:
            # Brand new topic
            if opp.get("signal_count", 0) >= config.new_topic_min_signals:
                opp["delta_type"] = "new"
                opp["delta_signal_change"] = opp.get("signal_count", 0)
                opp["delta_subreddit_change"] = opp.get("subreddit_count", 0)
                deltas.append(opp)
        else:
            signal_change = opp.get("signal_count", 0) - prev.get("signal_count", 0)
            sub_change = opp.get("subreddit_count", 0) - prev.get("subreddit_count", 0)

            # Check for convergence_new first (more specific): just crossed the 3-subreddit threshold
            prev_subs = prev.get("subreddit_count", 0)
            curr_subs = opp.get("subreddit_count", 0)
            if prev_subs < 3 <= curr_subs:
                opp["delta_type"] = "convergence_new"
                opp["delta_signal_change"] = signal_change
                opp["delta_subreddit_change"] = sub_change
                deltas.append(opp)
            elif signal_change >= config.spike_signal_threshold or sub_change >= config.spike_subreddit_threshold:
                opp["delta_type"] = "spike"
                opp["delta_signal_change"] = signal_change
                opp["delta_subreddit_change"] = sub_change
                deltas.append(opp)

    # Detect dying topics
    for prev_id, prev in previous_snapshots.items():
        if prev_id not in current_by_id:
            deltas.append({
                "id": prev_id,
                "delta_type": "dying",
                "delta_signal_change": -(prev.get("signal_count", 0)),
                "delta_subreddit_change": -(prev.get("subreddit_count", 0)),
            })

    logger.info(f"Delta detection: {len(deltas)} deltas found")
    return deltas
