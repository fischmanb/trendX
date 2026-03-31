"""Auto-evaluation — LLM selects which opportunities deserve deliberation."""

AUTO_EVAL_SYSTEM = """You are a filter for a demand scanner. You will see a batch of detected opportunities, each with a topic, category, scores, patterns, and product angle.

Your job: pick the ones that are genuinely worth deeper analysis. Not everything the scanner finds is real. Most are noise, obvious ideas, or things that already exist.

An opportunity is worth deeper analysis if:
- Someone described a real manual process they're doing (workaround pattern)
- Multiple unrelated communities are discussing the same unmet need (convergence)
- The product angle describes something that could be built as a standalone web tool — a calculator, tracker, comparison engine, dashboard, or visualizer
- The demand seems real (not just people complaining for entertainment)

An opportunity is NOT worth deeper analysis if:
- It's a broad macro trend with no specific tool shape (e.g., "inflation concerns")
- The product angle requires complex infrastructure (auth, payments, real-time APIs, user accounts)
- It's already well-served by existing products
- It's entertainment/cultural commentary, not a solvable problem
- The signal is too vague to build anything specific from

Return a JSON object with two arrays:
- "selected": opportunity IDs worth deeper analysis
- "non_feasible": opportunity IDs that are clearly not buildable as a standalone web tool, with brief reason

Example: {"selected": ["opp_123", "opp_456"], "non_feasible": [{"id": "opp_789", "reason": "requires payment integration"}, {"id": "opp_012", "reason": "existing tools already cover this"}]}

If none are worth it, return: {"selected": [], "non_feasible": []}"""


def build_auto_eval_prompt(opportunities: list[dict]) -> str:
    """Build the prompt showing all opportunities for auto-evaluation."""
    import json
    
    lines = []
    for opp in opportunities:
        patterns = []
        if opp.get("has_manual_workaround"):
            wk = json.loads(opp.get("workaround_descriptions_json", "[]") or "[]")
            wk_detail = ""
            if wk:
                wk_detail = f" ({wk[0].get('method', '')[:50]})"
                patterns.append(f"workaround{wk_detail}")
        if opp.get("has_unanswered_demand"):
            patterns.append("unanswered")
        if opp.get("convergence_detected"):
            patterns.append(f"convergence({opp.get('subreddit_count', 0)} subs)")
        if opp.get("has_new_community"):
            patterns.append("new_community")
        if opp.get("cross_source_confirmed"):
            patterns.append("cross_source")
        
        lines.append(f"""ID: {opp['id']}
Topic: {opp.get('topic', '')}
Category: {opp.get('category', '')}
Scores: A={opp.get('score_path_a', 0)} B={opp.get('score_path_b', 0)} C={opp.get('score_path_c', 0)}
Signals: {opp.get('signal_count', 0)} | Intensity: {opp.get('max_intensity', 0)}/100
Patterns: {', '.join(patterns) if patterns else 'none'}
Product angle: {opp.get('product_angle', 'none')}
Existing solution: {opp.get('existing_solution', 'none')}
---""")
    
    return "\n".join(lines)

import json
import logging

import anthropic

from ..config import AnthropicConfig

logger = logging.getLogger(__name__)


class AutoEvaluator:
    """Selects which opportunities deserve deliberation using a single LLM call."""

    def __init__(self, config: AnthropicConfig):
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.api_key) if config.api_key else None
        self.total_cost = 0.0

    def evaluate(self, opportunities: list[dict], feedback_context: str = "") -> tuple[list[str], list[dict]]:
        """Returns (selected_ids, non_feasible_list). Non-feasible items have 'id' and 'reason'."""
        if not self.client or not opportunities:
            return [], []

        from .auto_eval import AUTO_EVAL_SYSTEM, build_auto_eval_prompt

        user_prompt = build_auto_eval_prompt(opportunities)
        if feedback_context:
            user_prompt = feedback_context + "\n\n---\n\n" + user_prompt

        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=4000,
                temperature=0.1,
                system=AUTO_EVAL_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip()

            cost = (response.usage.input_tokens * 3.0 / 1_000_000) + (response.usage.output_tokens * 15.0 / 1_000_000)
            self.total_cost += cost

            # Strip markdown fences
            if raw.startswith("```"):
                lines = raw.splitlines()
                raw = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
            
            parsed = None
            text = raw.strip()
            # Try direct parse first
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                # Recover truncated JSON: find the JSON object and close it
                first = text.find("{")
                if first >= 0:
                    text = text[first:]
                    # Try closing unclosed braces/brackets
                    for suffix in ["", '"}', '"]}', '"]}}', "}", "]}", "]}}"]:
                        try:
                            parsed = json.loads(text + suffix)
                            break
                        except json.JSONDecodeError:
                            continue
            
            if parsed is None:
                logger.warning(f"Auto-eval: could not parse response ({len(raw)} chars)")
                return [], []
            
            if isinstance(parsed, dict):
                selected = [str(i) for i in parsed.get("selected", [])]
                non_feasible = parsed.get("non_feasible", [])
                if not isinstance(non_feasible, list):
                    non_feasible = []
                logger.info(f"Auto-eval: {len(selected)} selected, {len(non_feasible)} non-feasible (${cost:.4f})")
                return selected, non_feasible
            elif isinstance(parsed, list):
                # Backward compat: old format was just an array of IDs
                return [str(i) for i in parsed], []
            return [], []

        except Exception as e:
            logger.error(f"Auto-eval error: {e}", exc_info=True)
            return [], []
