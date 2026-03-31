# TrendX Changelog

All major work and architectural decisions, newest first.

---

## 2026-03-30 — Session 1 (cont): Evidence-Based Classification + Calibration + Fixes

### Classification rewritten: evidence scores replace booleans + intensity
The classifier no longer uses binary booleans (workaround_detected: true/false) or a single overall intensity (0-100). Each of the four demand patterns now gets its own evidence score on a fixed scale:

**Evidence scale (per pattern):**
- 0 = absent — no evidence
- 25 = low support — a hint or passing mention
- 50 = moderate support — clear evidence but limited detail
- 75 = advanced support — strong evidence with multiple indicators
- 100 = full support — exemplary, comprehensive, meets all criteria

Each level is explicitly defined per pattern in the prompt (e.g., workaround 50 = "describes a specific manual process with 2-3 steps", workaround 100 = "comprehensive documentation of a tedious manual process with time estimates, specific pain points, and explicit wish for automation").

**What this fixes:**
- A 15-step workaround description no longer gets the same `workaround_detected: true` as "I just do it manually"
- No single intensity number trying to represent four independent signals
- The LLM makes categorical judgments (which it's reliable at) instead of inventing precise numbers (which it's not)
- Fixed scale means no calibration drift — "75" means the same thing today and next month

**Backward compatibility:**
- Boolean fields still populated (convergence_likely = score ≥ 25) for downstream code
- max_intensity = max of all four pattern scores
- New score columns added to DB with ALTER TABLE migration for existing databases

**Terminology:** "signals" renamed to "pages" in prompts and documentation. A page is what we actually ingest — a web page, post, or comment. "Signal" implied intelligence that doesn't exist until classification.

### RICE Impact now uses per-pattern evidence scores (implemented)
Replaced flat boolean bonuses (workaround_detected → +20, unanswered_detected → +15) with actual per-pattern evidence scores aggregated from classified pages.

**How it works:**
- Clusterer now tracks max per-pattern scores on each opportunity: max_convergence_score, max_unanswered_score, max_workaround_score, max_new_community_score
- Each is the highest evidence score (0/25/50/75/100) seen across all pages clustered into that opportunity
- RICE Impact = average of the two strongest pattern scores + no-solution bonus (15 if no existing solution)
- Using top-2 average means having two strong signals is better than one — an opportunity with workaround=75 AND unanswered=50 scores higher than workaround=75 alone

**What this fixes:**
- A casual "I do it manually" (workaround_score=25) no longer gets the same +20 bonus as a detailed 15-step documentation (workaround_score=100)
- Impact is proportional to evidence strength, not just pattern presence
- Two weak signals (25+25 → Impact=40) correctly score lower than one strong signal (75 → Impact=90)

**Migration note:** Existing classified pages don't have per-pattern scores (they're 0). Opportunities from old classifications will score low on Impact until re-classified. Wipe classified_signals and reset raw_signals.classified to re-run with the evidence scale.

### Fixed: Auto-eval intensity scale still said "/5"
`build_auto_eval_prompt` was formatting intensity as `{max_intensity}/5` from the old 1-5 scale. Changed to `/100`.

### Fixed: Auto-eval re-evaluated already-deliberated opportunities (caused "0 evaluated")
`get_unreviewed_opportunities` returned ALL opportunities without reviews, including ones that already had deliberations from the previous cycle. The auto-eval kept sending the same batch to the LLM, which either selected the same ones (already deliberated → "0 deliberated") or returned nothing. Fixed: the query now excludes opportunities that already have deliberations. Only genuinely new, never-evaluated opportunities get sent to auto-eval. Added logging: "Found N new unreviewed opportunities (excluding already-deliberated)".

### Fixed: Timestamps mixed Eastern and UTC
Log timestamps showed Eastern time (from the machine's locale) but the "next cycle at" time used `datetime.utcnow()` which showed UTC. At 7:08 PM Eastern (= 11:08 PM UTC), adding 96 minutes gave 12:44 AM UTC, logged as "00:44:07" — correct math but looked broken next to the Eastern log timestamp. Fixed: all times now use Eastern via `zoneinfo.ZoneInfo("America/New_York")`. Log format changed to AM/PM (e.g., "7:08 PM"). Next cycle shows "8:44 PM ET".

### LLM-generated search queries for market sizing (implemented)
Replaced regex-based query extraction with an LLM call. One API call per cycle generates 3 search queries per topic — what a real person experiencing the problem would actually type into Google. Includes both problem-focused ("how to fix X") and solution-focused ("X calculator") queries.

Multiple variants tested concurrently via `asyncio.gather()`. Google Trends and autocomplete run for each variant in parallel. System takes the BEST score across variants — if "MCA rate calculator" scores 80 on Trends but "merchant cash advance rates" scores 40, the opportunity gets 80. Prevents good opportunities from being penalized by poor phrasing.

All queries tried and their individual scores stored in `market_signals.search_queries` for transparency.

### Honesty audit — fabricated and invented elements identified
Systematic review of all code for assumptions presented as facts:

**Caught and fixed this session:**
- Multi-campaign builds (fabricated concept — Auto-SDD has no such mechanism)
- $1.50 build cost estimate (real: $50-150)
- Category-level rejection (too coarse — "I may like one car thing and not another")
- Three stop conditions reduced to one (only competition arriving is valid)
- Hardcoded velocity thresholds (avg_v > 10 = "build_now" — fully unsolved)
- Inferred reach weight of .33 (arbitrary)
- Reddit follow-ups dropped during parallelization refactor
- Auto-eval non-feasible marking contradicted "No doesn't stop crawling"
- signal_viability table became dead code
- Auto-eval re-evaluating already-deliberated opportunities
- Intensity scale "/5" leftover from old range
- Market sizing queries too long for search APIs
- Subscriber counts never actually fetched (all showed 1)
- Autocomplete assumed max 10 (actual max 15)
- Subscriber normalization ceiling of 7 (actual data max 5.9)

**Documented as invented, cannot fix without outcome data:**
- Engagement ratio ×5000 constant
- 50/50 measured/market split
- Source quality threshold (20 observations, 0% relevance)
- RICE component formula (R×I×C/E) and all bonus weights
- Autocomplete normalization (low variance signal — barely differentiates)

### Fixed: Market sizing queries were too long (data not differentiating)
Google Trends and autocomplete were receiving the full verbose topic string (e.g., "AI coding assistant frustration: incomplete implementations, yes-man behavior, and dependency chaos") — way too long for search APIs. Google Trends returned 100 for almost everything (matching broadly), autocomplete returned 0 for most topics (no one types 15-word queries).

Fix: MarketSizer now extracts a short searchable query from the topic: strips parenthetical expansions, splits on delimiters (/, :, —), caps at 4 words, strips trailing filler words. Results now differentiate: "Meshtastic" → Trends 100, "AI coding assistant frustration" → Trends 0.

### Fixed: Autocomplete normalization was wrong
Google returns 0-15 suggestions (not 0-10 as assumed). Most topics get 9-15 (low variance). Changed constant from ×10 to /15×100 (based on actual max). Note: autocomplete count barely differentiates — almost everything gets 9-15. The signal's value is presence vs absence, not count.

### Fixed: Subscriber count cap of 30 was arbitrary
Changed from 30 per cycle to all missing subreddits. IP rotation handles rate limiting — no reason to cap. Also ensured all subreddits from signals are auto-added to the tracker table.

### Fixed: Subscriber counts were broken — now auto-fetched from Reddit
The subreddit_tracker had 494 entries but almost all with subscriber_count = 1 (default). The ingestor discovered subreddits but never fetched their actual subscriber counts. Fixed:
1. All subreddits from signals are now auto-added to the tracker
2. Each ingest cycle fetches real subscriber counts from Reddit's about.json endpoint for subreddits missing them (up to 30 per cycle, parallel via semaphore)
3. This enables the engagement ratio calculation in market sizing

### Partially calibrated: Subscriber normalization constant
Changed from log10(subs)/7 to log10(subs)/6. Reason: scraped data showed max real subscriber count is ~780K (log10 = 5.9). Previous ceiling of 7 (10M) was a guess. New ceiling of 6 (1M) is grounded in the data we actually have. Will recalibrate once subscriber fetching runs at scale across more subreddits.

### CANNOT CALIBRATE YET (documented in code):
- **Engagement ratio ×5000**: Couldn't validate because subscriber counts were broken. Now that fetching is fixed, will be recalibrated after enough cycles with real subscriber data. Marked UNCALIBRATED in code.
- **Autocomplete ×10**: Google returns max 10 suggestions so ×10 maps to 0-100. No scraped autocomplete data exists yet to validate whether 10 suggestions actually equals the same demand level as a Google Trends score of 100. Marked UNCALIBRATED in code.
- **RICE component weights (workaround_bonus=20, etc.)**: Need build outcome data. 
- **50/50 measured/market split**: Arbitrary, needs outcome data.
- **Source quality threshold (20 obs, 0% relevance)**: Needs outcome data.

### Validated from scraped data:
- **Score 1-1000 pre-filter**: Relevant signals P95 = 678, irrelevant P95 = 16,257. Filter correctly keeps high-yield range.
- **Comment-to-score ratio**: Relevant signals have 3x the comment-to-score ratio vs irrelevant (0.77 vs 0.25). Confirms discussion depth is a real signal. Not yet integrated into market sizing (it measures signal quality, not market size).
- **Intensity distribution**: Relevant signals cluster 52-78 on 0-100 scale. Classifier calibration looks reasonable.

### Fixed: Reddit follow-ups regression
When `run_ingest` was parallelized, the follow-up comment fetching step (fetching comments on high-intensity Reddit posts) was dropped. Restored — Reddit ingest now fetches main feeds + follow-up comments on posts with score > 50.

### Fixed: Auto-eval non-feasible contradicted "No doesn't stop crawling"
The auto-evaluator was marking non-feasible opportunities' signals as non-viable, which would prevent them from being re-crawled. This contradicted the agreed principle that only competition arriving stops crawling. Fixed: non-feasible opportunities are dismissed from review but their signals are NOT marked non-viable. The underlying data keeps flowing.

### Fixed: signal_viability is dead code
After removing non-viable marking from both `save_review` and auto-eval, nothing writes non-viable entries to `signal_viability`. The pre-filter was querying it every cycle for an always-empty set. Removed the dead pre-filter step and the dead `mark_signal_viable` call from the daemon. The table and methods remain in db.py as unused infrastructure — not harmful, just not active.

### Fixed: Multi-campaign builds were fabricated
Auto-SDD has no multi-campaign mechanism. Each run produces a complete app from scratch. Removed "estimated_campaigns" from RICE feasibility prompt, scoring, GUI columns, and vision prompt generation. The feasibility estimator now just says: "this costs ~$X and is/isn't buildable."

### CANNOT FIX (need outcome data):

**Market sizing normalization constants are invented.** The `×5000` for engagement ratio, `×10` for autocomplete count, `log10(subs)/7 × 100` for subscribers — I picked these to make the scales roughly comparable but they're not validated. The engagement ratio ceiling (0.02% = score 100) has no empirical basis. These need to be validated against actual build outcome data — does a high engagement ratio score correlate with high tool usage after deployment? Unknown until we have builds.

**The 50/50 measured/market split is arbitrary.** We agreed to test it but there's no principled reason for 50/50 vs 70/30 vs 30/70. Only outcome data can tell us. Currently: if 2+ market signals available, 50/50 split. If <2, measured scales to full range (no penalty). The split itself is a guess.

**Source quality "20 observations, 0% relevance" threshold is invented.** Why 20? Why not 10 or 50? Why exactly 0%? What about a subreddit that produced 1 relevant signal out of 100 (1%)? These thresholds need to be tuned against data showing which sources actually produced opportunities that led to successful builds. We don't have that data yet.

**RICE component weights are structural assumptions.** The formula (R×I×C/E) weights all three numerator components equally via multiplication. There's no basis for this vs (R+I+C)/E or (R×I)/E or any other combination. The individual component calculations (workaround_bonus = 20, unanswered_bonus = 15, etc.) are also invented numbers. All of these are bootstrapping scaffolding that should be replaced with learned weights once enough outcome data exists.

**Velocity tracker thresholds (none) — correctly unsolved.** We agreed "when to build" is unsolved and must be learned from outcomes. The velocity tracker stores raw measurements with no interpretation. This is intentionally unfinished, not a bug.

---

## 2026-03-30 — Session 1 (cont): Parallelization + RICE + Market Sizing + Build Button

### Parallelized ingestion (implemented)
All 7 data sources now run concurrently via `asyncio.gather()`. Previously sequential (each source waited for the previous one to finish). Sources are independent — Reddit, HackerNews, Twitter, Google Trends, YouTube, Quora, Product Hunt all fire at once. Ingestion time drops from ~60s (sum of all sources) to ~15-20s (bounded by slowest source).

### Parallelized WITHIN HackerNews (implemented)
HN was fetching story types sequentially, then individual items within each type sequentially. With 5 story types × 30 items = 150 individual Firebase API calls done one-by-one.

Now two-phase parallel:
1. All story type ID lists fetched concurrently (5 requests at once)
2. IDs deduplicated across feeds, then all items fetched concurrently (20 at a time via semaphore)
3. Follow-up comments also parallelized — all posts' comments fetched concurrently

Firebase API has no meaningful rate limit, so 20 concurrent requests is safe. Expected improvement: ~30s sequential → ~3-5s parallel.

### Parallelized WITHIN Reddit (implemented)
Reddit was the worst offender — it had 3 sequential loops: primary feeds, subreddit discovery, and topic search. Now all Reddit requests fire concurrently with a semaphore (10 concurrent). If there are 8 feeds + 1 new subs + 5 topic searches = 14 requests, they all launch together (10 at a time). Same for follow-up comment fetching — 30 comment threads fetched in parallel instead of one-by-one.

IPRoyal proxy rotates IPs per request, so Reddit sees each request from a different US residential IP. No single IP gets rate-limited even with 10 concurrent requests.

### Parallelized market sizing (implemented)
Google Trends, autocomplete, and subscriber lookups for all deliberated opportunities now run concurrently instead of sequentially.

### What's already parallel
- Classification: 20 concurrent async API calls per batch (implemented earlier)
- Ingestion: all 7 sources concurrent (implemented now)
- Market sizing: all opportunities concurrent (implemented now)

### What's NOT parallelized (and why)
- Clustering/scoring: operates on full opportunity set in memory, single-threaded is appropriate
- Auto-evaluation: single LLM call for the whole batch, can't split
- Velocity re-checks: sequential with rate-limit delays between Reddit requests. Could batch more aggressively via proxy but risk is rate limiting.
- Deliberation: could parallelize multiple calls, but each is a large context window. Anthropic rate limits are generous (4000 req/min) but parallel deliberations eat token budget faster. Probably fine for 5-6 calls — consider parallelizing later.

### Daemon control via GUI (implemented)
Daemon writes state to `data/daemon_state.json` every 10 seconds. GUI reads it to show:
- Status: 🟢 sleeping (with next cycle time), ⏳ running cycle, 🔴 stopped
- Daily spend vs budget
- Cycle number

GUI controls:
- **⏹ Stop** — sends stop signal, daemon exits within 10 seconds
- **▶ Run Now** — triggers next cycle immediately (skips remaining sleep)
- Re-score and Refresh buttons unchanged

Daemon sleeps in 10-second increments checking for commands, so stop/run-now signals are picked up quickly. Adaptive interval still applies: budget tight → auto-doubles interval, budget nearly exhausted → 4x interval.

### Daemon cycle interval: 96 minutes (default, rationale documented)
At $20/day budget with ~$3-5/cycle when new signals exist:
- 96 min = 15 cycles/day, ~$1.33/cycle budget, catches Reddit feed turnover, gives velocity snapshots meaningful time deltas
- After first few cycles, dedup catches most signals. Typical new signal count drops from 1000 (first run) to 50-100/cycle. At 50 new signals, classification costs ~$0.20 — budget is not the bottleneck.
- If new signals per cycle is consistently near zero, interval is too short. If signals are hours old by review time, too long.
- Future: adaptive intervals based on new signal count (high yield → shorter interval, low yield → longer). Not yet implemented.

### Daemon logging cleaned up
Suppressed httpx, anthropic, and urllib3 per-request logs. Step-level timing added to INGEST and CLASSIFY steps. Classification batch timing shows actual parallelism: "Batch of 100 completed in 12.3s (0.12s/signal, 8.1 signals/s)".

### Market sizing integrated into RICE (implemented)
RICE Reach is now split between measured and market signals — but ONLY when sufficient market data exists.

**Sufficient data (2+ market signals available):** 50/50 split between measured reach and market signal.
**Insufficient data (<2 signals):** measured reach scales to full 0-100. No penalty for missing market data. Opportunities without Google Trends or subscriber data compete on measured reach alone.

Market signals collected per opportunity:
- Google Trends interest score (0-100, via pytrends)
- Subreddit subscriber counts (log-normalized to 0-100)
- Google autocomplete suggestion count (×10, 10 suggestions = 100)
- Engagement ratio: best post score / total subscribers (×5000, 0.02% = 100)
Averaged across whichever signals are available — missing data excluded from average.

### "Build with Auto-SDD V2" button (implemented)
RICE stack rank entries now have a 🚀 Build button. Clicking it:
1. Generates a vision prompt from all TrendX analysis (topic, product angle, workaround descriptions, audience context from deliberation, feasibility constraints, source thread context)
2. Saves prompt to `data/build_prompts/{opp_id}.md`
3. Shows the generated prompt for review
4. "Launch Pre-Build" button triggers Auto-SDD's pre-build pipeline (vision → systems → design → personas → patterns → roadmap → specs)
5. Manual command shown as fallback
6. Build candidate status updated to "building"

The vision prompt synthesizes everything TrendX knows: what to build, who it's for, what people are currently doing manually, what constraints Auto-SDD operates under (no auth, no payments, no runtime APIs, Vercel free tier), and how many campaigns it should take.

### RICE stack ranking with Auto-SDD feasibility gate
Every daemon cycle, all deliberated opportunities get RICE-scored and stack-ranked.

**RICE = (Reach × Impact × Confidence) / Effort**
- Reach: signal_count × subreddit_count × cross-source multiplier
- Impact: intensity + workaround bonus + unanswered bonus + no-solution bonus
- Confidence: convergence + cross-source + signal volume + baseline
- Effort: estimated Auto-SDD build cost from LLM feasibility assessment

**Open questions (discussed, not yet resolved):**
- Inferred reach: LLM could estimate total addressable audience beyond what we crawl. Decision: do NOT weight it in the RICE formula. Store as a separate advisory field. Display alongside measured reach. Correlation between inferred reach and actual build outcomes will determine how much to trust it — but we need outcome data first. A fixed weight like .33 is another invented number. The trust level will likely differ by category and topic type. Until we have data, inferred reach is context for human judgment, not a formula input.

**Market sizing approach (decision: start with free signals):**
Free signals to implement:
1. Google Trends interest_over_time per opportunity (already in stack via pytrends)
2. Subreddit subscriber counts (already tracked)
3. Post engagement ratio: upvotes / subscriber count (computable from existing data)
4. Google autocomplete suggestions count (free, no API key)
5. Stack Overflow / GitHub search counts (free APIs, for dev-tool opportunities)

All stored as advisory fields alongside measured reach. NOT in the RICE formula yet. Displayed in GUI and included in deliberation prompt. Weighted into RICE only after enough build outcomes exist to learn the correlation. Paid alternatives (SimilarWeb, SEMrush, $100-400/month) deferred — revisit after enough builds to know whether better sizing data changes decisions.
- Competition discovery: AUTOMATE in the daemon. Each cycle, web search for existing tools/OSS/commercial products for every deliberated opportunity. Three outcomes: none_found (bullish), abandoned_oss (validates need, no active competitor — potentially replicate), active_competitor (lower Impact unless their product is bad). Updates opportunity data, triggers re-deliberation, affects RICE ranking. The replicate vs give up decision is not automatable — daemon surfaces what it finds, operator decides.

**Auto-SDD feasibility estimation:** One LLM call evaluates all deliberated opportunities. Estimates cost per single build run ($50-150) and buildability. Anything over $200 or requiring auth/payments/real-time APIs is filtered out. There is NO multi-campaign mechanism in Auto-SDD — each run produces a complete app from scratch. "Estimated campaigns" was a fabricated concept and has been removed.

**Hard cap: $200 per build.** RICE ranking only shows opportunities buildable by Auto-SDD under $200.

**10-step daemon pipeline:** ingest → pre-filter → classify → cluster/score → velocity recheck → auto-evaluate → deliberate new → re-deliberate + compare → RICE rank → log

**GUI:** RICE stack rank table at bottom shows top buildable opportunities. Each review card shows RICE score and buildability alongside path scores.

---

## 2026-03-30 — Session 1 (cont): Review Flow Clarified

### GUI shows only deliberated opportunities
Fixed the review queue query to JOIN on deliberations table. Only opportunities that passed auto-evaluation AND have a pre-computed deliberation assessment appear in the review UI. Metrics bar updated to match — "Unreviewed" count reflects deliberated-but-unreviewed, not all opportunities.

### Continuous comparison of affirmed candidates (implemented)
Build candidates are now a living ranked portfolio, not static cards. Each daemon cycle:

**Re-deliberation:** When an affirmed candidate's data changes (new signals, velocity shift, opportunity updated), the daemon re-runs the deliberation assessment. Stale assessments are replaced automatically.

**Comparative ranking:** When 2+ affirmed candidates exist, the daemon generates a single comparative analysis across all of them — "given budget and time, which is the best to build next?" One LLM call sees all candidates with their velocity data, previous assessments, and product angles, then ranks them relative to each other. Stored in `comparative_rankings` table.

**GUI portfolio view:** Below the review cards, affirmed build candidates are shown with the latest comparative ranking text, their status (watching/building/deployed), and Vercel URL if deployed.

**9-step daemon pipeline:** ingest → pre-filter → classify → cluster/score → velocity recheck → auto-evaluate → deliberate new → re-deliberate + compare existing → log

### Feasibility gate
Auto-SDD runs ONE build from a vision prompt. No multi-campaign mechanism exists — each run produces a complete app from scratch. If the output isn't right, you refine the prompt and run again. The feasibility estimator now reflects this: it estimates the cost of a single run ($50-150) and whether the opportunity is buildable at all.

Auto-SDD's bounds are NOT limited to calculators and dashboards — it can build anything the enforcement gates can validate and the test scaffolding can cover: CRUD apps, multi-page routing, API-consuming tools, data viz, interactive educational content, comparison engines, form workflows. The constraint is "can it be described in a vision prompt and tested," not "is it a simple tool."

Key cost distinction: zero runtime cost (static deploy, free forever) vs ongoing API spend (needs traffic to justify). The feasibility assessment flags which category a build falls into.

### ROI framing (corrected costs)
Real Auto-SDD build costs: $50-150 per run. Some builds have cost $100+. This means the deliberation and velocity tracking are essential gates — at $100/build, you need confidence before committing.

### Only deliberated opportunities reach the operator
The GUI review queue shows ONLY opportunities that passed both auto-evaluation AND have a pre-computed deliberation assessment. No "Generate Assessment" button — if the daemon hasn't deliberated it, it doesn't appear. The auto-evaluator is the gate, the deliberation is the analysis, the operator just judges.

### Yes = build candidate
Creates a build_candidate record with source thread URLs saved. Lifecycle: affirmed → building → deployed → monitoring. Vercel URL stored when deployed. Source threads tracked for later posting. Engagement data tracked as outcome. This outcome data (velocity curve at build time + post-deployment engagement) is the training data for eventually learning "when to build."

### No = dismissed from review, NOT from crawling
Opportunity leaves the review queue. Underlying signals continue to be re-crawled and velocity-tracked. Only competition arriving (existing_solution changes) stops crawling. Dismissed topics can re-emerge as new opportunities if fresh signals appear.

---

## 2026-03-30 — Session 1 (cont): Velocity Tracking + Build Pipeline Correction

### Three stop conditions revised — only one is valid
Original three stop conditions for re-crawling were: (1) counter-acceleration (conversation dying), (2) solved (competition arrived), (3) dormant (no new signals). 

Correction: only #2 (solved/competition arrived) is a valid stop condition. A dying or dormant conversation does NOT mean the opportunity is bad — it might mean nobody built the solution yet, which is exactly the gap we're looking for. Stale demand is still demand. Auto-archiving on declining velocity or dormancy was wrong.

### "Build now" is fully unsolved
The velocity tracker collects time-series data (score/comment snapshots over time) but does NOT interpret it. No thresholds, no state labels, no recommendations. The "when to build" signal must emerge from correlating velocity curves with actual build outcomes once enough builds have been attempted. Until then, the system measures and presents — the operator decides.

### What's unsolved
These questions require outcome data that doesn't exist yet:
- When to build (needs build outcome data to learn from)
- Whether a stale topic is still worth building for (can't know without trying)
- How to distinguish "stale but real need" from "genuinely dead topic"  
- What velocity curve shape predicts successful vs unsuccessful builds
- How to detect competition arrival automatically (currently manual via existing_solution field)

---

## 2026-03-30 — Session 1 (cont): Three-Layer Learning + 0-100 Scoring

### Three-layer feedback learning
The system learns what to skip at three levels, each from different signals:

**Layer 1 — URL level:** Specific URLs can be blocked. Stored in `blocked_urls` table. Pre-filter skips them before spending API money on classification. Currently manual (operator blocks a URL); future: auto-block URLs that produce dismissed opportunities.

**Layer 2 — Source level (subreddit/feed):** `source_quality` table tracks relevance rate per subreddit and feed. After 20+ classified signals with 0% relevance, that source is auto-skipped in future cycles. Entirely learned, no manual intervention needed.

**Layer 3 — Topic/category level:** `topic_feedback` table logs every Yes/No judgment with the opportunity's topic and category. When a category accumulates 2+ rejections with 0 approvals, the auto-evaluator's prompt is injected with: "The operator has rejected ALL opportunities in [category]. Exclude unless exceptionally strong." This doesn't block classification — it prevents wasting deliberation cost on topics the operator has consistently rejected.

### Intensity scale unified to 0-100
Classifier prompt updated to request intensity 0-100. Scorer intensity_weight now means "max points intensity contributes" (e.g., 20 = intensity 100 adds 20 pts). Clamping enforced in classifier. Config YAML updated with new weights.

---

## 2026-03-30 — Session 1: Foundation Build + First Scan + Review Pipeline

### Intensity Scale Fix
- Changed classifier intensity from 1-5 to 0-100 to match scoring scale
- All scores now on unified 0-100 scale — no hidden multipliers
- Classifier prompt updated with explicit range guidance
- Clamping added in classifier to enforce range regardless of LLM output
- Scorer updated: `intensity_weight` now means "max points intensity contributes"
  - e.g., intensity_weight: 20 means intensity=100 adds 20 points to score

### Autonomous Daemon (`trendx daemon`)
- Full 24/7 pipeline: ingest → pre-filter → classify → cluster/score → auto-evaluate → deliberate → log
- CostTracker monitors daily spend, adapts cycle interval when budget gets tight
- Adaptive interval: doubles wait at <$5 remaining, quadruples at <$2
- Budget: ~$1.28/cycle × 15 cycles/day = ~$19.20/day at $20 budget
- Pre-filter step: drops signals with score < 2 or body < 20 chars (only hard filters)

### Auto-Evaluation Layer (`trendx/deliberate/auto_eval.py`)
- Single LLM call that evaluates all unreviewed opportunities
- Selects which ones deserve full deliberation (replaces human eyeballing the ranked list)
- Criteria: real workaround patterns, tool-shaped product angles, genuine demand signals
- Filters out: broad macro trends, entertainment, already-solved problems, infrastructure-heavy ideas
- Cost: ~$0.03 per batch of 30 opportunities

### Deliberation System (`trendx/deliberate/`)
- Rich analytical prompt replaces multi-agent debate architecture
- Research finding (NeurIPS 2025 spotlight, Choi et al.): majority voting accounts for most MAD performance gains; debate is a martingale that doesn't improve expected correctness without targeted interventions
- Decision: single high-quality Sonnet call beats simulated multi-agent debate
- Prompt asks three open questions: who has this problem (with audience proportions), what happens if you build and post a tool, and what's the key uncertainty
- No prescribed personas, no voting, no forced balance
- Deliberations pre-computed by daemon, stored in DB, shown instantly in GUI
- Cost: ~$0.02 per opportunity deliberated

### Review UI (`app.py` — Streamlit)
- Complete redesign from tabbed layout to single-page dashboard
- Top metrics bar: Active Opportunities, Unreviewed, Affirmed, Total Signals, Relevant, Total Cost
- System status panel: source health, classification progress, last scan time
- Card-based review interface with large readable text, colored pattern badges
- Two-button decision: "Yes — keep tracking" or "No — stop tracking" (plus Skip)
- Pre-computed deliberations from daemon show instantly; manual generation available as fallback
- Review history tracking — all judgments stored for future learning

### Parallel Classification (`trendx/classify/classifier.py`)
- Rewrote from sequential to async parallel using `anthropic.AsyncAnthropic`
- 20 concurrent API calls via `asyncio.Semaphore` (safe under 4000 req/min limit)
- Batch size increased from 20 to 100
- JSON recovery: strips markdown fences, closes truncated braces
- Failed signals marked as processed to prevent infinite retry loops
- ~800 signals classified in ~2 minutes instead of ~40 minutes

### Config-Driven Scoring (`trendx/score/scorer.py` + `config/default.yaml`)
- All scoring weights moved from hardcoded Python to YAML config
- New dataclasses: PathAWeights, PathBWeights, PathCWeights, DeltaBoostWeights, ScoringConfig
- `trendx rescore` re-scores all opportunities with current weights — no code changes needed
- GUI tuning panel: sliders for every weight, writes to YAML, triggers rescore

### Classification System (`trendx/classify/`)
- Four-pattern classifier: convergence, unanswered demand, manual workaround, new community
- max_tokens bumped from 600 to 1200 to prevent truncated JSON responses
- Pre-filter: only two hard rules (score < 2, body < 20 chars) — everything else goes to LLM
- No hardcoded subreddit lists, no keyword heuristics, no age cutoffs

### All Seven Ingestors (fully implemented, no stubs)
- `reddit.py` — Reddit .json endpoints via IPRoyal residential proxy
- `hackernews.py` — HN Firebase API, direct connection
- `twitter.py` — Nitter HTML parsing via proxy, handles stat parsing (K/M suffixes)
- `google_trends.py` — pytrends library, trending + realtime searches
- `youtube.py` — YouTube Data API v3, search + comment mining + competition check
- `quora.py` — BeautifulSoup scraping via proxy
- `producthunt.py` — GraphQL API with comment extraction

### Database Schema (`trendx/store/db.py`)
- Tables: raw_signals, classified_signals, opportunities, opportunity_signals, subreddit_tracker, actions, scan_log, opportunity_snapshots, reviews, deliberations
- SQLite with WAL mode, 30-second busy timeout, foreign keys enabled
- Review and deliberation tables added for the review pipeline

### Proxy Setup
- IPRoyal residential proxies, US-only, random IP rotation per request
- Gateway: geo.iproyal.com:12321
- Credentials via env vars: IPROYAL_USER, IPROYAL_PASS

### Layer 2 + 3 Prompts Written
- `docs/layer2-dashboard-prompt.md` — Auto-SDD vision prompt for Next.js dashboard (6 screens)
- `docs/layer3-content-factory-prompt.md` — Claude Code prompt for social content pipeline (Path C)

---

## Key Architectural Decisions

### No hardcoded heuristics
The LLM classifier decides what's relevant. Only one hard filter exists: score must be between 1 and 1000. Score=0 (no engagement at all) and score>1000 (overwhelmingly entertainment/news with 1.5% relevance) are filtered. No text length filter, no subreddit blocklists, no keyword filters, no age cutoffs. Everything else is the LLM's judgment.

Data-driven decision: analyzed 999 classified signals. Score 2-10 had highest relevance rate (14.3%) — niche early signals. Score 1K+ had lowest (1.5%). Score=1 had 2.3% with 5 real opportunities from niche subreddits. Text <20 chars had 0% but text 20-49 had 1.8% — dropped the filter entirely to avoid losing edge cases.

### Single-call deliberation over multi-agent debate
Research (NeurIPS 2025) proved that multi-agent debate is a martingale — expected correctness doesn't improve with more debate rounds. Majority voting alone captures most gains. Consilium (open-source multi-model CLI) implements research-backed interventions but adds complexity. For TrendX's use case (subjective opportunity assessment, not factual QA), one well-crafted analytical prompt to the best available model outperforms simulated multi-agent debate.

### Scoring as bootstrapping scaffolding
Current config-driven weights are a starting point, not ground truth. Once enough review data exists (30+ judgments), the system can learn which features predict the operator's "yes" vs "no" decisions and replace the manual weights with learned ones.

### Explore/exploit on subreddit allocation (designed, not yet implemented)
UCB (Upper Confidence Bound) scoring for which subreddits get deep-dive workers. 80% exploit (known-good subs), 20% explore (unknown/new). Subreddits with less data get an uncertainty bonus that decays with more observations. Tracked in subreddit_signal_quality table.

### Parallel Reddit swarm (designed, not yet implemented)
Four-phase architecture: Discovery (6 workers on r/all feeds) → Deep-dive (~20 workers on discovered subreddits) → Comments (~15 workers on high-signal posts) → Subreddit search (~10 workers on top topics). Each worker gets its own IPRoyal proxy connection. Reduces ingest time from ~5 minutes to ~30 seconds.

### Signal velocity / acceleration tracking (designed, not yet implemented)
Re-fetch previously relevant posts to detect score velocity, volume acceleration, depth acceleration, community acceleration, and counter-acceleration (dying trends). Five types of acceleration identified but NOT prescriptively categorized — raw measurements stored, patterns to be learned from data.

### Tightest feedback loop identified
TrendX finds signal → Auto-SDD builds tool in ~40 min → deploy to Vercel free → drop link in source Reddit thread → measure engagement. 24-hour cycle. Path C (social content) deferred — operator not ready to post on TikTok yet.

---

## First Scan Results (2026-03-30)
- 581 signals ingested from Reddit + HackerNews (Twitter/Quora/YouTube/PH had API key or parsing issues)
- 368 classified (remainder pre-filtered or errored)
- 11 marked relevant (1.2% relevance rate — expected for broad r/all feeds)
- 15 opportunities created after clustering
- Top opportunities identified: AI agent API cost tracking, freelancer rate negotiation, Rate My Professor overlay, Facebook Ads compliance checker
- Scores were inflated (100/100/100 on top 4) due to intensity scale bug — now fixed

---

## Environment
- Machine: Mac Studio (development) + Mac laptop (portable)
- Python: 3.13, virtualenv at .venv/
- API: Anthropic (Claude Sonnet 4.6 for classification + deliberation)
- Proxy: IPRoyal residential, US, random rotation
- DB: SQLite with WAL
- GUI: Streamlit
- Version control: GitHub (fischmanb/trendx), push from Terminal due to Claude GitHub App 403 bug
