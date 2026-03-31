"""Three-path scoring + delta boost for opportunities — config-driven weights."""

import json
import logging

from ..config import ScoringConfig, PathAWeights, PathBWeights, PathCWeights, DeltaBoostWeights
from ..store.db import Database

logger = logging.getLogger(__name__)


def score_path_a(opp: dict, w: PathAWeights) -> int:
    """Content/tool. Rewards breadth, timeliness, unanswered questions."""
    s = 0
    s += min(opp.get("signal_count", 0) * w.signal_count_weight, w.signal_count_cap)
    s += int(opp.get("max_intensity", 0) * (w.intensity_weight / 100))  # intensity is 0-100, weight scales it
    s += min(opp.get("subreddit_count", 0) * w.convergence_weight, w.convergence_cap)
    s += w.timely_bonus if opp.get("is_timely") else 0
    s += w.unanswered_bonus if opp.get("has_unanswered_demand") else 0
    s += w.no_solution_bonus if opp.get("existing_solution") in ("none", "", None) else 0
    return min(s, 100)


def score_path_b(opp: dict, w: PathBWeights) -> int:
    """Product/SaaS. Rewards workarounds, recurring pain, depth."""
    s = 0
    s += int(opp.get("max_intensity", 0) * (w.intensity_weight / 100))
    s += w.workaround_bonus if opp.get("has_manual_workaround") else 0
    s += w.new_community_bonus if opp.get("has_new_community") else 0
    s += w.no_solution_bonus if opp.get("existing_solution") in ("none", "", None) else w.no_solution_fallback
    s += w.evergreen_bonus if not opp.get("is_timely") else 0
    product_angle = opp.get("product_angle", "")
    s += w.product_shaped_bonus if product_angle and product_angle != "not product-shaped" else 0
    s += min(opp.get("signal_count", 0) * w.signal_count_weight, w.signal_count_cap)
    return min(s, 100)


def score_path_c(opp: dict, w: PathCWeights) -> int:
    """Social content. Rewards timeliness, breadth, hookability."""
    s = 0
    s += w.timely_bonus if opp.get("is_timely") else 0
    s += min(opp.get("subreddit_count", 0) * w.convergence_weight, w.convergence_cap)
    s += int(opp.get("max_intensity", 0) * (w.intensity_weight / 100))
    s += min(opp.get("signal_count", 0) * w.signal_count_weight, w.signal_count_cap)
    s += w.unanswered_bonus if opp.get("has_unanswered_demand") else 0
    hook = opp.get("social_hook", "")
    s += w.hook_quality_bonus if hook and len(hook) > 20 else w.hook_quality_fallback
    return min(s, 100)


def apply_delta_boost(scores: dict[str, int], delta_type: str | None, w: DeltaBoostWeights) -> dict[str, int]:
    """Apply delta-based score boosts."""
    if delta_type == "new":
        scores["C"] = scores.get("C", 0) + w.new_c
    elif delta_type == "spike":
        scores["C"] = scores.get("C", 0) + w.spike_c
        scores["A"] = scores.get("A", 0) + w.spike_a
    elif delta_type == "convergence_new":
        scores["A"] = scores.get("A", 0) + w.convergence_new_a
        scores["C"] = scores.get("C", 0) + w.convergence_new_c
    return {k: min(v, 100) for k, v in scores.items()}


def score_opportunity(opp: dict, scoring: ScoringConfig) -> dict:
    """Score an opportunity across all three paths with config-driven weights."""
    scores = {
        "A": score_path_a(opp, scoring.path_a),
        "B": score_path_b(opp, scoring.path_b),
        "C": score_path_c(opp, scoring.path_c),
    }
    scores = apply_delta_boost(scores, opp.get("delta_type"), scoring.delta_boost)
    opp["score_path_a"] = scores["A"]
    opp["score_path_b"] = scores["B"]
    opp["score_path_c"] = scores["C"]
    opp["recommended_path"] = max(scores, key=scores.get)
    opp["multi_path"] = [k for k, v in scores.items() if v >= 60]
    return opp


def score_all(db: Database, scoring: ScoringConfig | None = None) -> int:
    """Score all non-dismissed opportunities. Returns count scored."""
    if scoring is None:
        scoring = ScoringConfig()
    opps = db.get_opportunities(limit=10000, status=None)
    scored = 0
    for opp in opps:
        if opp.get("status") == "dismissed":
            continue
        opp_dict = dict(opp)
        score_opportunity(opp_dict, scoring)
        db.upsert_opportunity(opp_dict)
        scored += 1
    logger.info(f"Scored {scored} opportunities")
    return scored
