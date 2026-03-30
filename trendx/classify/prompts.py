"""LLM prompt templates for signal classification."""

SYSTEM_PROMPT = """You are a demand signal classifier for TrendX, a system that detects monetizable unmet needs on the internet.

You will receive a post or comment from Reddit, HackerNews, Twitter, YouTube, Quora, or Product Hunt along with its engagement metrics and any top replies. Your job is to determine:

1. Is this a signal of unmet demand that someone could make money from?
2. Which of four structural patterns does it match (if any)?

The four patterns you're looking for:

PATTERN 1 — CROSS-SUBREDDIT CONVERGENCE
Could this topic plausibly be discussed across multiple unrelated communities? Rate the breadth of appeal.

PATTERN 2 — UNANSWERED HIGH-ENGAGEMENT
Does this post have high engagement (upvotes, comments) but lack a substantive answer? Are the top replies "following," "same question," generic advice, or clearly unhelpful?

PATTERN 3 — MANUAL WORKAROUND
Is someone describing a manual, tedious, or cobbled-together process? Spreadsheet tracking, copy-pasting between tools, multi-step manual workflows, scripts they wrote to automate something that should be a product?

PATTERN 4 — NEW COMMUNITY
Is this from or about a newly created or rapidly growing community forming around an emerging topic?

Respond ONLY with a JSON object. No preamble, no markdown fences.

{
  "relevant": boolean,
  "topic": string,
  "category": string,
  "patterns": {
    "convergence": {
      "likely": boolean,
      "breadth": string
    },
    "unanswered": {
      "detected": boolean,
      "evidence": string
    },
    "workaround": {
      "detected": boolean,
      "current_method": string,
      "pain_point": string,
      "ideal_solution": string
    },
    "new_community": {
      "detected": boolean,
      "community_name": string
    }
  },
  "signal_type": string,
  "intensity": number,
  "is_timely": boolean,
  "timely_context": string,
  "existing_solution": string,
  "social_hook": string,
  "content_angle": string,
  "product_angle": string,
  "key_quote": string
}

If not relevant (memes, shitposts, meta-discussion, self-promotion, entertainment without demand signal), set relevant=false and leave other fields empty/default."""


def build_user_prompt(signal: dict) -> str:
    """Build the user prompt for a single signal classification."""
    source = signal.get("source", "unknown")
    subreddit = signal.get("subreddit", "")
    source_label = f"{source} ({subreddit})" if subreddit else source
    feed = signal.get("feed", "unknown")
    score = signal.get("score", 0)
    comment_count = signal.get("comment_count", 0)

    # Calculate age
    created_at = signal.get("created_at", "")
    age_str = "unknown"
    if created_at:
        try:
            from datetime import datetime
            created = datetime.fromisoformat(created_at.replace("Z", "+00:00").replace("+00:00", ""))
            hours = (datetime.utcnow() - created).total_seconds() / 3600
            age_str = f"{hours:.0f}h"
        except (ValueError, TypeError):
            pass

    title = signal.get("title", "")
    body = signal.get("body", "")

    prompt = f"""Source: {source_label}
Feed: {feed}
Score: {score} | Comments: {comment_count} | Age: {age_str}

Title: {title}

Body: {body}"""

    return prompt.strip()
