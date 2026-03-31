# TrendX Layer 2 — Dashboard (Auto-SDD Vision Prompt)

Build a Next.js 14 dashboard for TrendX, a demand detection system that scans the internet for monetizable trends. The dashboard reads from a SQLite database and JSON export produced by a Python backend scanner (Layer 1). The dashboard is the command center where the operator reviews detected opportunities, sees why each one scored high, and decides which monetization path to pursue.

## Overview

TrendX scans Reddit, Twitter, HackerNews, Google Trends, YouTube, Quora, and Product Hunt for unmet demand. It detects four structural patterns that indicate real opportunities, scores each one across three monetization paths, and tracks how signals change over time. The dashboard visualizes all of this.

## Target Users

Solo operator running TrendX part-time alongside a job. Needs to open the dashboard, immediately see what's hot, understand why, and decide what to act on — all in under 5 minutes.

## Tech Stack

- Next.js 14 (App Router)
- TypeScript
- Tailwind CSS
- SQLite (read-only — Layer 1 writes, dashboard only reads)
- Recharts for data visualization
- No auth needed (local tool, single user)

## Key Screens

### 1. Opportunity Feed (Home)

The main screen. A ranked list of detected opportunities, most actionable first.

Each opportunity card shows:
- Topic name and category badge
- Three path scores as horizontal bars (Path A / B / C)
- Recommended path highlighted
- Pattern badges: convergence, unanswered, workaround, new community — only show detected ones
- Delta indicator: NEW, SPIKE, or nothing if stable
- Signal count, subreddit/source count, intensity
- Timeliness badge if timely, with context on hover
- Time since first seen / last seen
- Status: new, watching, acted_on, dismissed

Filters at top:
- By recommended path (A / B / C / all)
- By pattern (convergence / unanswered / workaround / new_community)
- By delta type (new / spike / all)
- By status (new / watching / all)
- By category
- Sort: by highest score, by most recent, by most signals, by biggest spike

### 2. Opportunity Detail

Click an opportunity card to see full detail:
- All three path scores with breakdown (what contributed to each score — show weight names and values from config)
- Pattern details:
  - Convergence: list of subreddits/sources, post counts per sub
  - Unanswered: specific posts with high engagement and no good answers, with key quotes
  - Workaround: extracted descriptions of manual processes, pain points, ideal solutions
  - New community: subreddit names, subscriber counts, growth rates
- Timeline chart showing signal count and score over time (from opportunity_snapshots table)
- Source list: clickable links to every Reddit post, HN item, tweet, etc.
- Social hook, content angle, product angle — displayed prominently
- Action buttons: "Mark as watching", "Act on this (Path A/B/C)", "Dismiss"
- Action log: history of actions taken on this opportunity

### 3. Trends Overview

High-level view of the system:
- Number of opportunities by status (new, watching, acted_on, dismissed)
- Top 5 categories by opportunity count
- Top emerging topics (delta type = new) as a card row
- Top spiking topics (delta type = spike)
- Cross-source confirmations (topics on 3+ platforms)
- Chart: opportunities created per day over last 30 days
- Chart: distribution of recommended paths (A vs B vs C)

### 4. Tuning Panel

Controls for adjusting scoring weights without editing YAML:
- Sliders for each scoring weight (path A, B, C weights)
- Current values loaded from config/default.yaml
- "Apply & Rescore" button that writes updated weights to YAML and triggers rescore
- Classification stats: relevance rate, pattern detection rates, top categories
- Subreddit quality table: which subs produce good signals vs noise

### 5. Scan Log

Operational view:
- Table of recent scan cycles: start time, duration, requests made, signals ingested, classified, relevant, opportunities created/updated, classification cost, bandwidth, errors
- Current scan status if watch mode is active

### 6. Sources Health

Status of each data source:
- Last successful fetch timestamp
- Error rate over last 24 hours
- Signals ingested from each source in last cycle
- Green/yellow/red indicator per source

## Design Principles

1. Information density over whitespace — data tool, not a marketing site
2. Scannable — know what's important within 3 seconds
3. Pattern badges are the most important visual element — they tell you WHY something scored high
4. Delta indicators (NEW, SPIKE) should be visually loud — time-sensitive signals
5. Dark mode default
6. Scores use color ramps: green (high), amber (medium), red (low)
7. Read-only from database except action logging

## Data Access

The dashboard reads from:
- `data/trendx.db` (SQLite — tables: opportunities, classified_signals, raw_signals, opportunity_signals, subreddit_tracker, subreddit_signal_quality, actions, scan_log, opportunity_snapshots)
- `data/opportunities.json` (top N export, denormalized)
- `config/default.yaml` (scoring weights — read for display, write for tuning panel)

Key tables:
- `opportunities` — main table, one row per detected opportunity
- `opportunity_snapshots` — historical scores/counts per opportunity per cycle (for timeline charts)
- `classified_signals` — individual posts/comments with pattern detection results
- `subreddit_signal_quality` — which subreddits produce good signals (for tuning panel)
- `scan_log` — operational stats per scan cycle
- `actions` — what the operator did with each opportunity

## Out of Scope

- User auth (single user, local tool)
- Running scans (Layer 1 handles this independently)
- Mobile optimization (desktop only)
- Writing to database except action logging + tuning weight updates
