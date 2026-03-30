"""Three-path scoring + delta boost for opportunities."""

import json
import logging

from ..store.db import Database

logger = logging.getLogger(__name__)


def score_path_a(opp: dict) -> int:
    """Content/tool. Rewards breadth, timeliness, unanswered questions."""
    s = 0
    s += min(opp.get("signal_count", 0) * 4, 20)
    s += opp.get("max_intensity", 0) * 4                    # max 20
    s += min(opp.get("subreddit_count", 0) * 8, 24)         # convergence: max 24
    s += 15 if opp.get("is_timely") else 0
    s += 12 if opp.get("has_unanswered_demand") else 0
    s += 9 if opp.get("existing_solution") in ("none", "", None) else 0
    return min(s, 100)


def score_path_b(opp: dict) -> int:
    """Product/SaaS. Rewards workarounds, recurring pain, depth."""
    s = 0
    s += opp.get("max_intensity", 0) * 4                    # max 20
    s += 25 if opp.get("has_manual_workaround") else 0
    s += 15 if opp.get("has_new_community") else 0
    s += 15 if opp.get("existing_solution") in ("none", "", None) else 3
    s += 10 if not opp.get("is_timely") else 0              # evergreen bonus
    product_angle = opp.get("product_angle", "")
    s += 10 if product_angle and product_angle != "not product-shaped" else 0
    s += min(opp.get("signal_count", 0) * 2, 10)
    return min(s, 100)


def score_path_c(opp: dict) -> int:
    """Social content. Rewards timeliness, breadth, hookability."""
    s = 0
    s += 25 if opp.get("is_timely") else 0                  # timeliness is king
    s += min(opp.get("subreddit_count", 0) * 7, 21)         # convergence
    s += opp.get("max_intensity", 0) * 3                    # max 15
    s += min(opp.get("signal_count", 0) * 3, 15)
    s += 12 if opp.get("has_unanswered_demand") else 0
    hook = opp.get("social_hook", "")
    s += 12 if hook and len(hook) > 20 else 4
    return min(s, 100)


def apply_delta_boost(scores: dict[str, int], delta_type: str | None) -> dict[str, int]:
    """Apply delta-based score boosts."""
    if delta_type == "new":
        scores["C"] = scores.get("C", 0) + 15
    elif delta_type == "spike":
        scores["C"] = scores.get("C", 0) + 10
        scores["A"] = scores.get("A", 0) + 10
    elif delta_type == "convergence_new":
        scores["A"] = scores.get("A", 0) + 15
        scores["C"] = scores.get("C", 0) + 10

    # Re-cap at 100
    return {k: min(v, 100) for k, v in scores.items()}


def score_opportunity(opp: dict) -> dict:
    """Score an opportunity across all three paths with delta boost."""
    scores = {
        "A": score_path_a(opp),
        "B": score_path_b(opp),
        "C": score_path_c(opp),
    }

    scores = apply_delta_boost(scores, opp.get("delta_type"))

    opp["score_path_a"] = scores["A"]
    opp["score_path_b"] = scores["B"]
    opp["score_path_c"] = scores["C"]
    opp["recommended_path"] = max(scores, key=scores.get)
    opp["multi_path"] = [k for k, v in scores.items() if v >= 60]

    return opp


def score_all(db: Database) -> int:
    """Score all non-dismissed opportunities. Returns count scored."""
    opps = db.get_opportunities(limit=10000, status=None)
    scored = 0
    for opp in opps:
        if opp.get("status") == "dismissed":
            continue
        # Convert JSON fields for scoring
        opp_dict = dict(opp)
        score_opportunity(opp_dict)
        db.upsert_opportunity(opp_dict)
        scored += 1

    logger.info(f"Scored {scored} opportunities")
    return scored
