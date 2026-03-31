"""JSON export for TrendX opportunities."""

import json
from datetime import datetime, UTC
from pathlib import Path

from .db import Database


def export_opportunities(db: Database, output_path: str, top_n: int = 50) -> str:
    """Export top opportunities to JSON file."""
    opps = db.get_opportunities(limit=top_n)

    export_data = {
        "exported_at": datetime.now(UTC).isoformat() + "Z",
        "count": len(opps),
        "opportunities": [],
    }

    for opp in opps:
        entry = {
            "id": opp["id"],
            "topic": opp["topic"],
            "category": opp["category"],
            "signal_count": opp["signal_count"],
            "max_intensity": opp["max_intensity"],
            "delta": {
                "type": opp["delta_type"],
                "signal_count_change": opp["delta_signal_change"],
                "subreddit_count_change": opp["delta_subreddit_change"],
            } if opp["delta_type"] else None,
            "patterns": {
                "convergence": {
                    "detected": bool(opp["convergence_detected"]),
                    "subreddit_count": opp["subreddit_count"],
                    "subreddits": json.loads(opp["subreddits_json"] or "[]"),
                    "score": opp["convergence_score"],
                },
                "unanswered": {
                    "detected": bool(opp["has_unanswered_demand"]),
                },
                "workaround": {
                    "detected": bool(opp["has_manual_workaround"]),
                    "descriptions": json.loads(opp["workaround_descriptions_json"] or "[]"),
                },
                "new_community": {
                    "detected": bool(opp["has_new_community"]),
                    "communities": json.loads(opp["new_community_names_json"] or "[]"),
                },
            },
            "scores": {
                "path_a": opp["score_path_a"],
                "path_b": opp["score_path_b"],
                "path_c": opp["score_path_c"],
            },
            "recommended_path": opp["recommended_path"],
            "multi_path": json.loads(opp["multi_path_json"] or "[]"),
            "is_timely": bool(opp["is_timely"]),
            "timely_context": opp["timely_context"],
            "existing_solution": opp["existing_solution"],
            "social_hook": opp["social_hook"],
            "content_angle": opp["content_angle"],
            "product_angle": opp["product_angle"],
            "source_urls": json.loads(opp["source_urls_json"] or "[]"),
            "first_seen": opp["first_seen"],
            "last_seen": opp["last_seen"],
            "status": opp["status"],
        }
        export_data["opportunities"].append(entry)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(export_data, f, indent=2)

    return str(output_path)
