"""LLM-based four-pattern signal classifier — parallel via async."""

import asyncio
import json
import logging
import uuid
from datetime import datetime

import anthropic

from ..config import AnthropicConfig
from ..store.db import Database
from .prompts import SYSTEM_PROMPT, build_user_prompt

logger = logging.getLogger(__name__)

MAX_CONCURRENT = 20  # Parallel API calls (safe under 4000 req/min limit)


class Classifier:
    """Classifies raw signals using Claude — parallel async API calls."""

    def __init__(self, db: Database, config: AnthropicConfig):
        self.db = db
        self.config = config
        self.async_client = anthropic.AsyncAnthropic(api_key=config.api_key) if config.api_key else None
        self.sync_client = anthropic.Anthropic(api_key=config.api_key) if config.api_key else None
        self.total_cost = 0.0
        self.classified_count = 0
        self.relevant_count = 0
        self.errors: list[str] = []
        self._cost_lock = asyncio.Lock() if config.api_key else None

    def _parse_json_response(self, raw_text: str) -> dict | None:
        """Parse LLM response as JSON with recovery for common issues."""
        text = raw_text.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            lines = lines[1:]
            if lines and lines[-1].strip().startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()
        first = text.find("{")
        last = text.rfind("}")
        if first == -1:
            return None
        if last == -1 or last <= first:
            text = text[first:]
            opens = text.count("{") - text.count("}")
            text += '""}' * max(opens, 0)
        else:
            text = text[first:last + 1]
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            patched = text.rstrip().rstrip(",")
            opens = patched.count("{") - patched.count("}")
            patched += "}" * max(opens, 0)
            try:
                return json.loads(patched)
            except json.JSONDecodeError:
                return None


    def _extract_classified(self, result: dict, signal_id: str) -> dict:
        """Extract classified signal dict from parsed JSON result."""
        patterns = result.get("patterns", {})
        conv = patterns.get("convergence", {})
        unans = patterns.get("unanswered", {})
        work = patterns.get("workaround", {})
        newc = patterns.get("new_community", {})

        # Evidence scores (0, 25, 50, 75, 100)
        conv_score = max(0, min(100, int(conv.get("score", 0))))
        unans_score = max(0, min(100, int(unans.get("score", 0))))
        work_score = max(0, min(100, int(work.get("score", 0))))
        newc_score = max(0, min(100, int(newc.get("score", 0))))

        # Intensity = max of all pattern scores (strongest evidence)
        intensity = max(conv_score, unans_score, work_score, newc_score)

        return {
            "id": str(uuid.uuid4()),
            "raw_signal_id": signal_id,
            "relevant": result.get("relevant", False),
            "topic": result.get("topic", ""),
            "category": result.get("category", "other"),
            "signal_type": "",
            "intensity": intensity,
            "convergence_likely": conv_score >= 25,
            "convergence_score": conv_score,
            "convergence_breadth": conv.get("evidence", ""),
            "unanswered_detected": unans_score >= 25,
            "unanswered_score": unans_score,
            "unanswered_evidence": unans.get("evidence", ""),
            "workaround_detected": work_score >= 25,
            "workaround_score": work_score,
            "workaround_current_method": work.get("current_method", ""),
            "workaround_pain_point": work.get("pain_point", ""),
            "workaround_ideal_solution": work.get("ideal_solution", ""),
            "new_community_detected": newc_score >= 25,
            "new_community_score": newc_score,
            "new_community_name": newc.get("evidence", ""),
            "is_timely": result.get("is_timely", False),
            "timely_context": result.get("timely_context", ""),
            "existing_solution": result.get("existing_solution", ""),
            "social_hook": "",
            "content_angle": "",
            "product_angle": result.get("product_angle", ""),
            "key_quote": result.get("key_quote", ""),
        }


    async def _classify_one_async(self, signal: dict, semaphore: asyncio.Semaphore) -> tuple[dict, dict | None]:
        """Classify a single signal asynchronously. Returns (signal, classified_result_or_None)."""
        async with semaphore:
            user_prompt = build_user_prompt(signal)
            try:
                response = await self.async_client.messages.create(
                    model=self.config.model,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                    system=SYSTEM_PROMPT,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                raw_text = response.content[0].text.strip()

                input_tokens = response.usage.input_tokens
                output_tokens = response.usage.output_tokens
                cost = (input_tokens * 3.0 / 1_000_000) + (output_tokens * 15.0 / 1_000_000)
                async with self._cost_lock:
                    self.total_cost += cost

                result = self._parse_json_response(raw_text)
                if result is None:
                    logger.warning(f"Could not parse response for {signal['id']}: {raw_text[:200]}")
                    return signal, None

                return signal, self._extract_classified(result, signal["id"])

            except anthropic.APIError as e:
                logger.error(f"API error for {signal['id']}: {e}")
                self.errors.append(f"API error: {signal['id']}: {e}")
                return signal, None
            except Exception as e:
                logger.error(f"Classification error for {signal['id']}: {e}")
                self.errors.append(f"Classification: {signal['id']}: {e}")
                return signal, None


    async def classify_batch_async(self, batch_size: int = 100) -> tuple[int, int, int]:
        """Classify a batch of signals in parallel. Returns (classified, relevant, attempted)."""
        signals = self.db.get_unclassified_signals(limit=batch_size)
        attempted = len(signals)
        if attempted == 0:
            return 0, 0, 0

        import time
        start = time.time()
        logger.info(f"Launching {attempted} classifications with {MAX_CONCURRENT} concurrent workers...")

        semaphore = asyncio.Semaphore(MAX_CONCURRENT)
        tasks = [self._classify_one_async(s, semaphore) for s in signals]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.time() - start
        logger.info(f"Batch of {attempted} completed in {elapsed:.1f}s ({elapsed/max(attempted,1):.1f}s/signal, {attempted/max(elapsed,0.1):.1f} signals/s)")

        classified = 0
        relevant = 0
        for item in results:
            if isinstance(item, Exception):
                logger.error(f"Unexpected error in parallel classify: {item}")
                continue
            signal, result = item
            if result:
                self.db.insert_classified_signal(result)
                self.db.mark_classified(signal["id"])
                classified += 1
                if result["relevant"]:
                    relevant += 1
            else:
                self.db.mark_classified(signal["id"])

        self.classified_count += classified
        self.relevant_count += relevant
        return classified, relevant, attempted


    async def classify_all_async(self, batch_size: int = 100) -> tuple[int, int]:
        """Classify all unclassified signals using parallel async batches."""
        if not self.async_client:
            logger.error("Anthropic API key not configured")
            return 0, 0

        self._cost_lock = asyncio.Lock()
        total_classified = 0
        total_relevant = 0

        while True:
            c, r, attempted = await self.classify_batch_async(batch_size)
            total_classified += c
            total_relevant += r
            if attempted == 0:
                break
            logger.info(
                f"Classified batch: {c}/{attempted} signals, {r} relevant "
                f"(total: {total_classified}, cost=${self.total_cost:.4f})"
            )

        logger.info(
            f"Classification complete: {total_classified} classified, "
            f"{total_relevant} relevant, cost=${self.total_cost:.4f}"
        )
        return total_classified, total_relevant

    def classify_all(self, batch_size: int = 100) -> tuple[int, int]:
        """Sync wrapper — runs parallel async classification."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            # Already in an async context — can't use asyncio.run
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, self.classify_all_async(batch_size))
                return future.result()
        else:
            return asyncio.run(self.classify_all_async(batch_size))
