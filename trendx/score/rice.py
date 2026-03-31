"""RICE scoring + Auto-SDD feasibility for stack-ranking build candidates."""

import json
import logging

import anthropic

from ..config import AnthropicConfig

logger = logging.getLogger(__name__)

MAX_BUILD_COST = 200  # USD — hard cap for Auto-SDD feasibility

RICE_FEASIBILITY_SYSTEM = """You are estimating build effort for a solo developer's automated pipeline.

The pipeline (Auto-SDD) generates fully tested Next.js web applications from a single text prompt in one run (~40 minutes). There is no multi-run or incremental build mechanism — each run produces a complete application from scratch. A single run costs $50-150 in API calls depending on the complexity of what's described in the prompt.

The pipeline can build:
- Standalone interactive tools (calculators, trackers, comparisons, visualizers): $50-80
- Multi-feature dashboards or data-rich apps: $80-120
- Complex multi-page apps with routing, state management, and seed data: $100-150
- Apps requiring auth, payments, real-time external APIs, or complex backends: NOT BUILDABLE

For each opportunity, estimate:
1. estimated_cost_usd: total estimated API cost for ONE build run
2. buildable: true/false — can Auto-SDD produce this in a single run?
3. complexity_reason: one sentence explaining the estimate

Respond ONLY with a JSON array. No preamble. Each element:
{"id": "opp_xxx", "estimated_cost_usd": N, "buildable": bool, "complexity_reason": "..."}
"""


def compute_rice(opp: dict, effort_estimate: dict, market_signals: dict | None = None) -> dict:
    """Compute RICE score for an opportunity.
    
    Reach = 50% measured (signal density) + 50% market (free sizing signals)
    Impact × Confidence / Effort complete the formula.
    All components normalized to 0-100.
    """
    # Reach: split between measured and market ONLY if sufficient market data
    signal_count = opp.get("signal_count", 1)
    subreddit_count = opp.get("subreddit_count", 1)
    cross_source = 1.5 if opp.get("cross_source_confirmed") else 1.0
    raw_measured = signal_count * subreddit_count * cross_source

    market_signals_used = 0
    market_combined = 0
    if market_signals:
        market_signals_used = market_signals.get("signals_used", 0)
        market_combined = market_signals.get("combined", 0)

    if market_signals_used >= 2:
        # Sufficient market data — 50/50 split
        measured_reach = min(50, raw_measured)
        market_reach = min(50, market_combined / 2)
        reach = measured_reach + market_reach
    else:
        # Insufficient market data — use measured only, scaled to full 0-100
        measured_reach = min(100, raw_measured * 2)  # scale up since it's the only signal
        market_reach = 0
        reach = measured_reach

    # Impact: how painful is it — driven by per-pattern evidence scores
    # Use the two strongest pattern scores (averaged) as the base impact
    pattern_scores = [
        opp.get("max_convergence_score", 0),
        opp.get("max_unanswered_score", 0),
        opp.get("max_workaround_score", 0),
        opp.get("max_new_community_score", 0),
    ]
    pattern_scores.sort(reverse=True)
    # Average of top 2 patterns — having two strong signals is better than one
    top_patterns = [s for s in pattern_scores[:2] if s > 0]
    pattern_impact = sum(top_patterns) / len(top_patterns) if top_patterns else 0
    
    # No-solution bonus still applies — it's binary external data, not a pattern score
    no_solution_bonus = 15 if opp.get("existing_solution") in ("none", "", None, "none identified") else 0
    impact = min(100, pattern_impact + no_solution_bonus)

    # Confidence: how sure are we this is real
    # Convergence score directly (0-100 already on the evidence scale)
    convergence_confidence = min(30, opp.get("max_convergence_score", 0) * 0.3)
    has_cross_source = 20 if opp.get("cross_source_confirmed") else 0
    signal_confidence = min(30, signal_count * 3)
    base_confidence = 20
    confidence = min(100, base_confidence + convergence_confidence + has_cross_source + signal_confidence)

    # Effort: estimated cost (lower cost = less effort = higher RICE)
    est_cost = effort_estimate.get("estimated_cost_usd", 200)
    buildable = effort_estimate.get("buildable", False)
    if not buildable or est_cost > MAX_BUILD_COST:
        return {"rice_score": 0, "reach": reach, "impact": impact, 
                "confidence": confidence, "effort": est_cost, "buildable": False,
                "measured_reach": round(measured_reach, 1), "market_reach": round(market_reach, 1),
                "market_signals": market_signals or {},
                "complexity_reason": effort_estimate.get("complexity_reason", "not buildable")}
    
    # Normalize effort: $50 = effort 25, $100 = effort 50, $200 = effort 100
    effort = max(1, (est_cost / MAX_BUILD_COST) * 100)

    rice_score = round((reach * impact * confidence) / effort)

    return {
        "rice_score": rice_score,
        "reach": round(reach, 1),
        "measured_reach": round(measured_reach, 1),
        "market_reach": round(market_reach, 1),
        "impact": round(impact, 1),
        "confidence": round(confidence, 1),
        "effort": est_cost,
        "buildable": buildable,
        "complexity_reason": effort_estimate.get("complexity_reason", ""),
        "market_signals": market_signals or {},
    }


class RiceRanker:
    """RICE scoring + Auto-SDD feasibility estimation."""

    def __init__(self, config: AnthropicConfig):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.api_key) if config.api_key else None
        self.total_cost = 0.0

    def estimate_effort(self, opportunities: list[dict]) -> dict[str, dict]:
        """Estimate Auto-SDD build effort for a batch of opportunities.
        Returns {opportunity_id: effort_estimate_dict}.
        """
        if not self.client or not opportunities:
            return {}

        lines = []
        for opp in opportunities:
            lines.append(f"""ID: {opp.get('id', '')}
Topic: {opp.get('topic', '')}
Product angle: {opp.get('product_angle', 'none')}
Existing solution: {opp.get('existing_solution', 'none')}
---""")

        prompt = "\n".join(lines)

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=1500,
                temperature=0.1,
                system=RICE_FEASIBILITY_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip()
            cost = (response.usage.input_tokens * 3.0 / 1_000_000) + (response.usage.output_tokens * 15.0 / 1_000_000)
            self.total_cost += cost

            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])

            estimates = json.loads(raw.strip())
            result = {}
            for est in estimates:
                opp_id = est.get("id", "")
                if opp_id:
                    result[opp_id] = est
            
            logger.info(f"Effort estimation for {len(opportunities)} opportunities: ${cost:.4f}")
            return result

        except Exception as e:
            logger.error(f"Effort estimation error: {e}")
            return {}

    def rank(self, opportunities: list[dict], market_data: dict[str, dict] | None = None) -> list[dict]:
        """RICE-rank a list of opportunities. Returns sorted list with rice_data attached.
        
        market_data: {opportunity_id: market_signals_dict} — if None, market reach is 0.
        """
        if not opportunities:
            return []

        effort_estimates = self.estimate_effort(opportunities)

        ranked = []
        for opp in opportunities:
            opp_id = opp.get("id", "")
            effort = effort_estimates.get(opp_id, {"buildable": False, "estimated_cost_usd": 999, "complexity_reason": "no estimate"})
            market = market_data.get(opp_id, {}) if market_data else {}
            rice = compute_rice(opp, effort, market)
            entry = dict(opp)
            entry["rice"] = rice
            ranked.append(entry)

        ranked.sort(key=lambda x: x["rice"]["rice_score"], reverse=True)
        return ranked
