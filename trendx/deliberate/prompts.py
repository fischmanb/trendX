"""Deliberation prompt for opportunity review — single-call analytical assessment."""

DELIBERATION_SYSTEM = """You are evaluating a business opportunity detected by an automated demand scanner.

The scanner found people on the internet expressing unmet needs, frustrations, or manual workarounds. Your job is to help a solo operator decide whether this opportunity is worth pursuing.

The operator builds web applications using an automated pipeline that generates fully tested Next.js apps from text descriptions in about 40 minutes. The apps are typically interactive tools — calculators, dashboards, comparison engines, trackers, visualizers. They deploy for free. No auth, no payments, no complex integrations — just standalone tools that solve a specific problem.

The operator's strategy: find a real need, build a tool that addresses it, share it where the demand was expressed (Reddit threads, forums), and measure whether people actually use it. Speed matters — the whole cycle from signal to deployed tool should be under 24 hours.

Write your analysis as natural prose — a few dense paragraphs. No headers, no bullet points, no numbered lists.

Do not force balance. If this is clearly worth pursuing, say so. If it's clearly not, say so. Don't manufacture objections or enthusiasm.

Do not use the words "landscape" or "ecosystem." Do not say "it's worth noting." Just say the thing."""


DELIBERATION_USER_TEMPLATE = """Here is an opportunity detected by the scanner:

TOPIC: {topic}
CATEGORY: {category}

SIGNALS: {signal_count} mentions found across {subreddit_count} communities and {source_count} platforms
INTENSITY: {intensity}/5
TIMELY: {is_timely} — {timely_context}

PATTERNS DETECTED:
{patterns_summary}

EXISTING SOLUTIONS: {existing_solution}

SOCIAL HOOK (scanner's suggestion): {social_hook}
CONTENT ANGLE (scanner's suggestion): {content_angle}
PRODUCT ANGLE (scanner's suggestion): {product_angle}

KEY QUOTE FROM THE CONVERSATION:
"{key_quote}"

SOURCE THREADS:
{source_urls}

---

Tell me three things:

First — who actually has this problem? Not market segments, but real people. What does a typical person dealing with this look like? What are they doing right now to cope? Break down the audience by type with rough proportions. Include the people who'd never pay or engage deeply — they're part of the picture too.

Second — if the operator built a free interactive tool for this in the next 24 hours and posted it in the source threads above, what happens? Do people use it once and leave? Do they bookmark it? Do they share it? Does it solve their problem or just scratch the surface? Is there a version of this tool that's simple enough to build in 40 minutes but useful enough that someone would send it to a friend?

Third — what's the one thing you'd want to know before committing that you can't determine from the data above? Name the specific uncertainty and say how it changes the decision if resolved one way vs the other."""


def build_deliberation_prompt(opportunity: dict) -> str:
    """Build the user prompt for deliberation from an opportunity dict."""
    import json

    # Build patterns summary
    patterns = []
    if opportunity.get("convergence_detected"):
        subs = json.loads(opportunity.get("subreddits_json", "[]"))
        patterns.append(f"Cross-community convergence: appeared in {len(subs)} communities ({', '.join(subs[:5])})")
    if opportunity.get("cross_source_confirmed"):
        patterns.append(f"Cross-platform confirmation: detected on {opportunity.get('distinct_source_count', 1)} different platforms")
    if opportunity.get("has_unanswered_demand"):
        patterns.append("Unanswered demand: high engagement posts with no substantive answers")
    if opportunity.get("has_manual_workaround"):
        descs = json.loads(opportunity.get("workaround_descriptions_json", "[]"))
        if descs:
            d = descs[0]
            patterns.append(f"Manual workaround detected: people are currently using \"{d.get('method', 'unknown')}\" — pain point: \"{d.get('pain', 'unspecified')}\"")
        else:
            patterns.append("Manual workaround detected")
    if opportunity.get("has_new_community"):
        names = json.loads(opportunity.get("new_community_names_json", "[]"))
        patterns.append(f"New community forming: {', '.join(names) if names else 'unnamed'}")
    if not patterns:
        patterns.append("No strong structural patterns — general interest signal only")

    # Build source URLs
    urls = json.loads(opportunity.get("source_urls_json", "[]"))
    url_str = "\n".join(urls[:8]) if urls else "No source URLs available"

    return DELIBERATION_USER_TEMPLATE.format(
        topic=opportunity.get("topic", "Unknown"),
        category=opportunity.get("category", ""),
        signal_count=opportunity.get("signal_count", 0),
        subreddit_count=opportunity.get("subreddit_count", 0),
        source_count=opportunity.get("distinct_source_count", 1),
        intensity=opportunity.get("max_intensity", 0),
        is_timely="Yes" if opportunity.get("is_timely") else "No",
        timely_context=opportunity.get("timely_context", "not time-sensitive"),
        patterns_summary="\n".join(patterns),
        existing_solution=opportunity.get("existing_solution", "none identified"),
        social_hook=opportunity.get("social_hook", "none"),
        content_angle=opportunity.get("content_angle", "none"),
        product_angle=opportunity.get("product_angle", "none"),
        key_quote=opportunity.get("key_quote", "no key quote extracted"),
        source_urls=url_str,
    )
