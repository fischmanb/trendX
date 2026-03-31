"""Deliberation engine — generates analytical assessments for opportunities."""

import json
import logging

import anthropic

from ..config import AnthropicConfig
from .prompts import DELIBERATION_SYSTEM, build_deliberation_prompt

logger = logging.getLogger(__name__)


COMPARATIVE_SYSTEM = """You are ranking a portfolio of build candidates for a solo operator who builds web tools using an automated pipeline.

Each candidate has:
- A topic and the demand signal behind it
- Velocity data (how the signal is changing over time)
- A previous assessment (which may be outdated)
- Cost to build: $50-150 in API costs + 1-2 hours of operator time

Your job: rank these candidates from most to least worth building RIGHT NOW, given everything you know. For each, write 2-3 sentences explaining what changed since the last assessment (if anything) and why it ranks where it does relative to the others.

Do not force a build recommendation. Some portfolios have nothing worth building yet — say so if true.

Write as natural prose — a few paragraphs comparing the candidates to each other. No bullet points, no numbered lists, no headers. End with one sentence naming the single strongest candidate and why, or say none are ready."""


class Deliberator:
    """Generates assessments for individual opportunities and comparative rankings."""

    def __init__(self, config: AnthropicConfig):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.api_key) if config.api_key else None
        self.total_cost = 0.0

    def deliberate(self, opportunity: dict) -> str | None:
        """Generate analytical assessment for a single opportunity."""
        if not self.client:
            logger.error("Anthropic API key not configured")
            return None

        user_prompt = build_deliberation_prompt(opportunity)

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=2000,
                temperature=0.4,
                system=DELIBERATION_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            )
            text = response.content[0].text.strip()
            cost = (response.usage.input_tokens * 3.0 / 1_000_000) + (response.usage.output_tokens * 15.0 / 1_000_000)
            self.total_cost += cost
            logger.info(f"Deliberation for '{opportunity.get('topic', '?')}': ${cost:.4f}")
            return text
        except Exception as e:
            logger.error(f"Deliberation error: {e}")
            return None


    def compare_candidates(self, candidates: list[dict], velocity_data: dict[str, dict]) -> str | None:
        """Generate comparative ranking across all affirmed build candidates.
        
        candidates: list of opportunity dicts (with build candidate data)
        velocity_data: {opportunity_id: velocity assessment dict}
        Returns ranking prose, or None on error.
        """
        if not self.client or not candidates:
            return None

        lines = []
        for c in candidates:
            opp_id = c.get("opportunity_id", c.get("id", ""))
            vel = velocity_data.get(opp_id, {})
            
            # Get previous deliberation if exists
            prev_assessment = c.get("assessment_text", "No previous assessment.")
            if len(prev_assessment) > 300:
                prev_assessment = prev_assessment[:300] + "..."

            lines.append(f"""CANDIDATE: {c.get('topic', 'Unknown')}
Category: {c.get('category', '')}
Scores: A={c.get('score_path_a', 0)} B={c.get('score_path_b', 0)} C={c.get('score_path_c', 0)}
Signals: {c.get('signal_count', 0)} | Subreddits: {c.get('subreddit_count', 0)}
Velocity: {vel.get('avg_velocity', 'unknown')} pts/hr | Snapshots: {vel.get('snapshots_total', 0)}
Competition detected: {'yes' if vel.get('has_competition') else 'no'}
Product angle: {c.get('product_angle', 'none')}
Previous assessment: {prev_assessment}
Affirmed at: {c.get('affirmed_at', 'unknown')}
---""")

        prompt = "\n".join(lines)

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=2000,
                temperature=0.4,
                system=COMPARATIVE_SYSTEM,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            cost = (response.usage.input_tokens * 3.0 / 1_000_000) + (response.usage.output_tokens * 15.0 / 1_000_000)
            self.total_cost += cost
            logger.info(f"Comparative ranking of {len(candidates)} candidates: ${cost:.4f}")
            return text
        except Exception as e:
            logger.error(f"Comparative ranking error: {e}")
            return None
