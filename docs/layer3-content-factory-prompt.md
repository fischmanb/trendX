# TrendX Layer 3 — Social Content Factory (Claude Code Prompt)

Build the Path C automation pipeline for TrendX. When Layer 1 identifies a high-scoring Path C opportunity (timely, broad appeal, strong social hook), Layer 3 produces ready-to-post social content automatically.

## What this does

Takes a TrendX opportunity and produces:
1. Short-form video scripts (TikTok, YouTube Shorts, Instagram Reels)
2. Instagram/LinkedIn carousel slide content
3. Twitter/X thread copy
4. Thumbnail text/concept descriptions

The human reviews and posts. Layer 3 does not auto-post (yet).

## Architecture

```
[TrendX DB] → [Opportunity Selector] → [Content Generator (LLM)] → [Media Producer] → [Output Queue]
```

## Pipeline Steps

### Step 1: Opportunity Selection

Pull from Layer 1's SQLite database. Select opportunities where:
- `score_path_c >= 70`
- `status = 'new'` or `status = 'watching'`
- `is_timely = true` (prioritize) OR has strong social_hook
- Not already in the content queue (dedup)

Sort by `score_path_c` descending. Process top N per cycle (configurable, default 5).

### Step 2: Content Generation (LLM)

For each selected opportunity, call Claude with the opportunity data and generate multiple content formats in a single pass.

#### System prompt:

```
You are a viral content strategist for social media. You receive a trending
topic with context about why people care about it right now.

Your job: produce content that educates and engages. Not clickbait — genuine
value that makes people share because they learned something useful.

For each topic, produce ALL of the following:

1. SHORT VIDEO SCRIPT (30-60 seconds, for TikTok/Reels/Shorts)
   - Hook (first 3 seconds — must stop the scroll)
   - Body (key insight, explained simply)
   - CTA (what to do next, or "follow for more")
   - Include visual direction notes in brackets: [show X on screen]
   - Write in spoken language, not essay language

2. CAROUSEL (5-7 slides, for Instagram/LinkedIn)
   - Slide 1: Bold hook statement
   - Slides 2-6: One clear point per slide, short sentences
   - Final slide: Summary + CTA
   - Each slide: max 30 words

3. TWITTER/X THREAD (4-6 tweets)
   - Tweet 1: Hook with the core insight
   - Tweets 2-5: Supporting points, one per tweet
   - Final tweet: Summary + CTA
   - Each tweet: max 280 chars

4. THUMBNAIL CONCEPT
   - Text overlay (max 5 words, high contrast)
   - Visual description (what the background image/graphic should show)
   - Emotion to convey (curiosity, urgency, surprise, etc.)

Respond as JSON:
{
  "video_script": {
    "hook": string,
    "body": string,
    "cta": string,
    "visual_notes": [string],
    "duration_estimate_seconds": number
  },
  "carousel": {
    "slides": [{"text": string, "visual_note": string}]
  },
  "twitter_thread": {
    "tweets": [string]
  },
  "thumbnail": {
    "text_overlay": string,
    "visual_description": string,
    "emotion": string
  },
  "hashtags": [string],
  "best_posting_time": string,
  "platform_priority": [string]
}
```

#### User prompt (per opportunity):

```
Topic: {opportunity.topic}
Category: {opportunity.category}
Why it's trending: {opportunity.timely_context}
Signal count: {opportunity.signal_count} mentions across {opportunity.subreddit_count} communities
Social hook from analysis: {opportunity.social_hook}
Content angle: {opportunity.content_angle}
Key quote from the conversation: {opportunity.key_quote}
Source examples: {opportunity.source_urls[:3]}
Patterns detected: {convergence/unanswered/workaround/new_community}
```

### Step 3: Media Production (optional, phase 2)

For MVP, Layer 3 outputs text content only — scripts, slides, thread copy. The human records/designs the actual media.

Future automation:
- **Video:** Use Remotion (React-based video) or ffmpeg to combine:
  - Text-to-speech via ElevenLabs API for voiceover
  - Stock footage/motion graphics background
  - Text overlays matching the script's visual notes
- **Carousels:** Use `sharp` or `canvas` to render slide images from templates
- **Thumbnails:** Use `canvas` to render text-on-background images

### Step 4: Output Queue

Store generated content in SQLite alongside the opportunity:

```sql
CREATE TABLE content_queue (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT REFERENCES opportunities(id),
    content_type TEXT,          -- 'video_script', 'carousel', 'twitter_thread', 'thumbnail'
    content_json TEXT,          -- Full generated content as JSON
    platform TEXT,              -- 'tiktok', 'youtube_shorts', 'instagram', 'twitter', 'linkedin'
    status TEXT DEFAULT 'draft', -- draft, reviewed, posted, skipped
    posted_url TEXT,            -- URL after posting (manual entry)
    performance_json TEXT,      -- Views, likes, shares (manual or API entry)
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    posted_at TIMESTAMP
);
```

## Project Structure (additions to existing trendx/)

```
trendx/
├── trendx/
│   ├── content/                    # NEW — Layer 3
│   │   ├── __init__.py
│   │   ├── selector.py            # Pick top Path C opportunities from DB
│   │   ├── generator.py           # LLM content generation (scripts, carousels, threads)
│   │   ├── prompts.py             # System + user prompts for content generation
│   │   ├── queue.py               # Content queue DB operations
│   │   └── media.py               # Future: video/image generation (Remotion, sharp, ElevenLabs)
```

## CLI Commands (add to existing cli.py)

```bash
# Generate content for top Path C opportunities
python -m trendx generate --limit 5

# View content queue
python -m trendx queue
python -m trendx queue --status draft
python -m trendx queue --platform tiktok

# View a specific content item
python -m trendx content {content_id}

# Mark content as reviewed/posted
python -m trendx post {content_id} --url "https://tiktok.com/..."

# Log performance metrics
python -m trendx perf {content_id} --views 15000 --likes 800 --shares 120

# Full pipeline: scan + classify + score + generate content
python -m trendx full-cycle
```

## Configuration (add to config/default.yaml)

```yaml
content:
  min_path_c_score: 70          # Minimum Path C score to generate content
  max_per_cycle: 5              # Max opportunities to generate content for per cycle
  platforms:                     # Which platforms to generate for
    - tiktok
    - youtube_shorts
    - instagram
    - twitter
  model: "claude-sonnet-4-6"    # Can use a different model for content vs classification
  max_tokens: 2000              # Content generation needs more tokens than classification
  temperature: 0.7              # Higher creativity for content than classification (0.2)
```

## Dependencies (add to pyproject.toml)

No new dependencies for MVP — content generation uses the same Anthropic SDK already installed.

Future (media production):
- `elevenlabs` — text-to-speech for video voiceovers
- `Pillow` — image generation for carousels/thumbnails
- `remotion` — React-based video generation (npm, not pip)

## Performance Feedback Loop

When you log performance data (`trendx perf`), the system tracks which topics, patterns, and content styles get the most engagement. Over time:

- Topics from certain categories may consistently outperform → boost those categories in Path C scoring
- Certain hook styles may work better → refine the content generation prompt
- Posting time patterns may emerge → adjust `best_posting_time` recommendations
- Platform-specific performance differences → adjust `platform_priority` output

This data lives in `content_queue.performance_json` and can be analyzed with `trendx stats --content`.

## Success Criteria

1. `trendx generate` produces content for top Path C opportunities
2. Each content item includes video script, carousel, thread, and thumbnail concept
3. Content is stored in the queue with draft status
4. `trendx queue` shows pending content ready for review
5. Performance tracking enables data-driven content strategy refinement
6. Full cycle (`scan → classify → score → generate`) runs end-to-end
