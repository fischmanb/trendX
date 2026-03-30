"""LLM-based four-pattern signal classifier."""

import json
import logging
import uuid
from datetime import datetime

import anthropic

from ..config import AnthropicConfig
from ..store.db import Database
from .prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)


class Classifier:
    """Classifies raw signals using Claude for four-pattern detection."""

    def __init__(self, db: Database, config: AnthropicConfig):
        self.db = db
        self.config = config
        self.client = anthropic.Anthropic(api_key=config.api_key) if config.api_key else None
        self.total_cost = 0.0
        self.classified_count = 0
        self.relevant_count = 0
        self.errors: list[str] = []

    def classify_signal(self, signal: dict) -> dict | None:
        """Classify a single raw signal. Returns classified signal dict or None on error."""
        if not self.client:
            logger.error("Anthropic API key not configured")
            return None

        user_prompt = build_user_prompt(signal)
        try:
            response = self.client.messages.create(
                model=self.config.model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw_text = response.content[0].text.strip()

            # Estimate cost (Sonnet pricing: ~$3/M input, ~$15/M output)
            input_tokens = response.usage.input_tokens
            output_tokens = response.usage.output_tokens
            cost = (input_tokens * 3.0 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)
            self.total_cost += cost

            # Parse JSON response
            result = json.loads(raw_text)

            classified = {
                "id": str(uuid.uuid4()),
                "raw_signal_id": signal["id"],
                "relevant": result.get("relevant", False),
                "topic": result.get("topic", ""),
                "category": result.get("category", "other"),
                "signal_type": result.get("signal_type", ""),
                "intensity": result.get("intensity", 1),
                "convergence_likely": result.get("patterns", {}).get("convergence", {}).get("likely", False),
                "convergence_breadth": result.get("patterns", {}).get("convergence", {}).get("breadth", "niche"),
                "unanswered_detected": result.get("patterns", {}).get("unanswered", {}).get("detected", False),
                "unanswered_evidence": result.get("patterns", {}).get("unanswered", {}).get("evidence", ""),
                "workaround_detected": result.get("patterns", {}).get("workaround", {}).get("detected", False),
                "workaround_current_method": result.get("patterns", {}).get("workaround", {}).get("current_method", ""),
                "workaround_pain_point": result.get("patterns", {}).get("workaround", {}).get("pain_point", ""),
                "workaround_ideal_solution": result.get("patterns", {}).get("workaround", {}).get("ideal_solution", ""),
                "new_community_detected": result.get("patterns", {}).get("new_community", {}).get("detected", False),
                "new_community_name": result.get("patterns", {}).get("new_community", {}).get("community_name", ""),
                "is_timely": result.get("is_timely", False),
                "timely_context": result.get("timely_context", ""),
                "existing_solution": result.get("existing_solution", ""),
                "social_hook": result.get("social_hook", ""),
                "content_angle": result.get("content_angle", ""),
                "product_angle": result.get("product_angle", ""),
                "key_quote": result.get("key_quote", ""),
            }
            return classified

        except json.JSONDecodeError as e:
            logger.error(f"Classification JSON parse error for {signal['id']}: {e}")
            self.errors.append(f"JSON parse: {signal['id']}")
            return None
        except anthropic.APIError as e:
            logger.error(f"Anthropic API error for {signal['id']}: {e}")
            self.errors.append(f"API error: {signal['id']}: {e}")
            return None
        except Exception as e:
            logger.error(f"Classification error for {signal['id']}: {e}")
            self.errors.append(f"Classification: {signal['id']}: {e}")
            return None

    def classify_batch(self, batch_size: int = 20) -> tuple[int, int]:
        """Classify a batch of unclassified signals. Returns (classified, relevant)."""
        signals = self.db.get_unclassified_signals(limit=batch_size)
        classified = 0
        relevant = 0

        for signal in signals:
            result = self.classify_signal(signal)
            if result:
                self.db.insert_classified_signal(result)
                self.db.mark_classified(signal["id"])
                classified += 1
                if result["relevant"]:
                    relevant += 1

        self.classified_count += classified
        self.relevant_count += relevant
        return classified, relevant

    def classify_all(self, batch_size: int = 20) -> tuple[int, int]:
        """Classify all unclassified signals in batches."""
        total_classified = 0
        total_relevant = 0

        while True:
            c, r = self.classify_batch(batch_size)
            total_classified += c
            total_relevant += r
            if c < batch_size:
                break
            logger.info(f"Classified batch: {c} signals, {r} relevant (total: {total_classified})")

        logger.info(f"Classification complete: {total_classified} classified, {total_relevant} relevant, cost=${self.total_cost:.4f}")
        return total_classified, total_relevant
