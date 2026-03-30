"""Signal → opportunity clustering by topic similarity."""

import json
import logging
import re
import uuid
from datetime import datetime

from rapidfuzz import fuzz

from ..config import ClusteringConfig
from ..store.db import Database

logger = logging.getLogger(__name__)


def normalize_topic(topic: str) -> str:
    """Normalize a topic string for matching."""
    topic = topic.lower().strip()
    # Strip leading articles
    topic = re.sub(r"^(the|a|an)\s+", "", topic)
    # Collapse whitespace
    topic = re.sub(r"\s+", " ", topic)
    return topic


def find_matching_opportunity(
    topic: str,
    category: str,
    existing: dict[str, dict],
    threshold: float,
) -> str | None:
    """Find an existing opportunity that matches this topic."""
    norm = normalize_topic(topic)

    # Exact match first
    if norm in existing:
        return norm

    # Fuzzy match within same category
    best_match = None
    best_score = 0.0
    for key, opp in existing.items():
        if opp.get("category", "") != category:
            continue
        score = fuzz.ratio(norm, key) / 100.0
        if score > threshold and score > best_score:
            best_score = score
            best_match = key

    return best_match


def cluster_signals(db: Database, config: ClusteringConfig) -> tuple[int, int]:
    """Group classified relevant signals into opportunities.

    Returns (created, updated) counts.
    """
    signals = db.get_relevant_signals()
    if not signals:
        return 0, 0

    # Build opportunity map from existing DB opportunities
    existing_opps = db.get_opportunities(limit=10000, status=None)
    opp_map: dict[str, dict] = {}
    for opp in existing_opps:
        key = normalize_topic(opp["topic"])
        opp_map[key] = opp

    # Track which signals are already linked
    created = 0
    updated = 0

    for signal in signals:
        topic = signal.get("topic", "")
        category = signal.get("category", "other")
        if not topic:
            continue

        norm_topic = normalize_topic(topic)
        match_key = find_matching_opportunity(topic, category, opp_map, config.topic_similarity_threshold)

        if match_key and match_key in opp_map:
            # Merge into existing opportunity
            opp = opp_map[match_key]
            _merge_signal_into_opportunity(opp, signal)
            db.upsert_opportunity(opp)
            db.link_signal_to_opportunity(opp["id"], signal["id"])
            updated += 1
        else:
            # Create new opportunity
            opp = _create_opportunity_from_signal(signal)
            opp_map[norm_topic] = opp
            db.upsert_opportunity(opp)
            db.link_signal_to_opportunity(opp["id"], signal["id"])
            created += 1

    logger.info(f"Clustering: {created} created, {updated} updated from {len(signals)} signals")
    return created, updated


def _create_opportunity_from_signal(signal: dict) -> dict:
    """Create a new opportunity dict from a classified signal."""
    now = datetime.utcnow().isoformat()
    subreddits = [signal["subreddit"]] if signal.get("subreddit") else []
    sources = {signal.get("source", "unknown")}
    source_urls = [signal["permalink"]] if signal.get("permalink") else []

    workaround_descriptions = []
    if signal.get("workaround_detected"):
        workaround_descriptions.append({
            "method": signal.get("workaround_current_method", ""),
            "pain": signal.get("workaround_pain_point", ""),
            "ideal": signal.get("workaround_ideal_solution", ""),
        })

    new_community_names = []
    if signal.get("new_community_detected") and signal.get("new_community_name"):
        new_community_names.append(signal["new_community_name"])

    return {
        "id": f"opp_{datetime.utcnow().strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}",
        "topic": signal.get("topic", ""),
        "category": signal.get("category", "other"),
        "signal_count": 1,
        "max_intensity": signal.get("intensity", 1),
        "subreddit_count": len(subreddits),
        "subreddits": subreddits,
        "convergence_detected": False,
        "convergence_score": 0,
        "cross_source_confirmed": False,
        "distinct_source_count": len(sources),
        "has_unanswered_demand": bool(signal.get("unanswered_detected")),
        "has_manual_workaround": bool(signal.get("workaround_detected")),
        "workaround_descriptions": workaround_descriptions,
        "has_new_community": bool(signal.get("new_community_detected")),
        "new_community_names": new_community_names,
        "is_timely": bool(signal.get("is_timely")),
        "timely_context": signal.get("timely_context", ""),
        "existing_solution": signal.get("existing_solution", ""),
        "score_path_a": 0,
        "score_path_b": 0,
        "score_path_c": 0,
        "recommended_path": "",
        "multi_path": [],
        "delta_type": None,
        "delta_signal_change": None,
        "delta_subreddit_change": None,
        "social_hook": signal.get("social_hook", ""),
        "content_angle": signal.get("content_angle", ""),
        "product_angle": signal.get("product_angle", ""),
        "source_urls": source_urls,
        "status": "new",
        "first_seen": now,
        "last_seen": now,
    }


def _merge_signal_into_opportunity(opp: dict, signal: dict) -> None:
    """Merge a classified signal into an existing opportunity."""
    opp["signal_count"] = opp.get("signal_count", 1) + 1
    opp["max_intensity"] = max(opp.get("max_intensity", 0), signal.get("intensity", 0))
    opp["last_seen"] = datetime.utcnow().isoformat()

    # Merge subreddits
    subreddits = json.loads(opp.get("subreddits_json", "[]")) if isinstance(opp.get("subreddits_json"), str) else opp.get("subreddits", [])
    if signal.get("subreddit") and signal["subreddit"] not in subreddits:
        subreddits.append(signal["subreddit"])
    opp["subreddits"] = subreddits
    opp["subreddit_count"] = len(subreddits)

    # Track distinct sources
    # We approximate since we don't have full source tracking in the opp
    opp["distinct_source_count"] = opp.get("distinct_source_count", 1)
    # A rough heuristic: if source differs from what we've seen
    # In practice we'd track the set of sources, but for now just increment
    source_urls = json.loads(opp.get("source_urls_json", "[]")) if isinstance(opp.get("source_urls_json"), str) else opp.get("source_urls", [])
    if signal.get("permalink") and signal["permalink"] not in source_urls:
        source_urls.append(signal["permalink"])
    opp["source_urls"] = source_urls

    # Merge pattern flags (OR semantics)
    if signal.get("unanswered_detected"):
        opp["has_unanswered_demand"] = True
    if signal.get("workaround_detected"):
        opp["has_manual_workaround"] = True
        descs = json.loads(opp.get("workaround_descriptions_json", "[]")) if isinstance(opp.get("workaround_descriptions_json"), str) else opp.get("workaround_descriptions", [])
        descs.append({
            "method": signal.get("workaround_current_method", ""),
            "pain": signal.get("workaround_pain_point", ""),
            "ideal": signal.get("workaround_ideal_solution", ""),
        })
        opp["workaround_descriptions"] = descs
    if signal.get("new_community_detected"):
        opp["has_new_community"] = True
        names = json.loads(opp.get("new_community_names_json", "[]")) if isinstance(opp.get("new_community_names_json"), str) else opp.get("new_community_names", [])
        if signal.get("new_community_name") and signal["new_community_name"] not in names:
            names.append(signal["new_community_name"])
        opp["new_community_names"] = names

    if signal.get("is_timely"):
        opp["is_timely"] = True
        opp["timely_context"] = signal.get("timely_context", opp.get("timely_context", ""))

    # Use best angles from highest intensity signal
    if signal.get("intensity", 0) >= opp.get("max_intensity", 0):
        opp["social_hook"] = signal.get("social_hook", "") or opp.get("social_hook", "")
        opp["content_angle"] = signal.get("content_angle", "") or opp.get("content_angle", "")
        opp["product_angle"] = signal.get("product_angle", "") or opp.get("product_angle", "")
        opp["existing_solution"] = signal.get("existing_solution", "") or opp.get("existing_solution", "")
