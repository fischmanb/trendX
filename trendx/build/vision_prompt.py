"""Generate Auto-SDD vision prompts from TrendX opportunity data."""

import json
import logging

logger = logging.getLogger(__name__)


def generate_vision_prompt(opportunity: dict, deliberation: str = "", rice: dict | None = None) -> str:
    """Synthesize a vision prompt for Auto-SDD from all TrendX analysis.
    
    Combines: topic, product angle, audience analysis from deliberation,
    workaround descriptions, source context, and feasibility data.
    """
    topic = opportunity.get("topic", "Unknown")
    category = opportunity.get("category", "")
    product_angle = opportunity.get("product_angle", "")
    existing_solution = opportunity.get("existing_solution", "")

    # Workaround detail
    workaround_section = ""
    wk = json.loads(opportunity.get("workaround_descriptions_json", "[]") or "[]")
    if wk:
        d = wk[0]
        workaround_section = (
            f"\nWhat people are currently doing manually: {d.get('method', '')}\n"
            f"Their pain point: {d.get('pain', '')}\n"
            f"What they wish existed: {d.get('ideal', '')}\n"
        )

    # Source threads for context
    urls = json.loads(opportunity.get("source_urls_json", "[]") or "[]")
    source_context = ""
    if urls:
        source_context = f"\nThis demand was detected in {len(urls)} online threads where people are actively discussing this problem."

    # Timely context
    timely = ""
    if opportunity.get("is_timely") and opportunity.get("timely_context"):
        timely = f"\nTimeliness: {opportunity['timely_context']}"

    # Build constraints from RICE
    constraint = ""
    if rice:
        est_cost = rice.get("estimated_cost_usd", 100)
        if est_cost > 120:
            constraint = "\nConstraint: This is a complex build. Keep scope tight — focus on the core value proposition. Cut features that aren't essential to solving the primary problem."
        else:
            constraint = "\nConstraint: This should be buildable as a single standalone tool. Keep scope focused."

    prompt = f"""Build a free, standalone interactive web tool for: {topic}

Category: {category}

Problem: {product_angle if product_angle else f'People need a tool for {topic} but nothing good exists.'}
{workaround_section}
{f'Existing solutions: {existing_solution}' if existing_solution and existing_solution not in ('none', '', 'none identified') else 'No existing solution addresses this well.'}
{source_context}
{timely}

Requirements:
- Standalone Next.js 14 app (App Router)
- No authentication, no user accounts, no payment processing
- No external API calls at runtime (use seed data or client-side computation)
- Must work immediately on first visit — no setup required
- Mobile-friendly responsive design
- Dark mode default
- Deploy target: Vercel free tier
{constraint}

{f'Audience context from analysis: ' + deliberation[:500] if deliberation else ''}
"""
    return prompt.strip()
