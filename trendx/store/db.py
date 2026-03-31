"""SQLite database operations for TrendX."""

import json
import sqlite3
from datetime import datetime, UTC
from pathlib import Path


SCHEMA = """
CREATE TABLE IF NOT EXISTS raw_signals (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_id TEXT NOT NULL,
    title TEXT,
    body TEXT,
    url TEXT,
    permalink TEXT,
    score INTEGER,
    comment_count INTEGER,
    subreddit TEXT,
    author TEXT,
    created_at TIMESTAMP,
    ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    feed TEXT,
    parent_signal_id TEXT,
    metadata_json TEXT,
    classified BOOLEAN DEFAULT FALSE,
    UNIQUE(source, source_id)
);

CREATE TABLE IF NOT EXISTS classified_signals (
    id TEXT PRIMARY KEY,
    raw_signal_id TEXT REFERENCES raw_signals(id),
    relevant BOOLEAN,
    topic TEXT,
    category TEXT,
    signal_type TEXT,
    intensity INTEGER,
    convergence_likely BOOLEAN DEFAULT FALSE,
    convergence_score INTEGER DEFAULT 0,
    convergence_breadth TEXT,
    unanswered_detected BOOLEAN DEFAULT FALSE,
    unanswered_score INTEGER DEFAULT 0,
    unanswered_evidence TEXT,
    workaround_detected BOOLEAN DEFAULT FALSE,
    workaround_score INTEGER DEFAULT 0,
    workaround_current_method TEXT,
    workaround_pain_point TEXT,
    workaround_ideal_solution TEXT,
    new_community_detected BOOLEAN DEFAULT FALSE,
    new_community_score INTEGER DEFAULT 0,
    new_community_name TEXT,
    is_timely BOOLEAN,
    timely_context TEXT,
    existing_solution TEXT,
    social_hook TEXT,
    content_angle TEXT,
    product_angle TEXT,
    key_quote TEXT,
    classified_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS opportunities (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    category TEXT,
    signal_count INTEGER DEFAULT 1,
    max_intensity INTEGER,
    subreddit_count INTEGER DEFAULT 1,
    subreddits_json TEXT,
    convergence_detected BOOLEAN DEFAULT FALSE,
    convergence_score REAL DEFAULT 0,
    cross_source_confirmed BOOLEAN DEFAULT FALSE,
    distinct_source_count INTEGER DEFAULT 1,
    has_unanswered_demand BOOLEAN DEFAULT FALSE,
    has_manual_workaround BOOLEAN DEFAULT FALSE,
    workaround_descriptions_json TEXT,
    has_new_community BOOLEAN DEFAULT FALSE,
    new_community_names_json TEXT,
    max_convergence_score INTEGER DEFAULT 0,
    max_unanswered_score INTEGER DEFAULT 0,
    max_workaround_score INTEGER DEFAULT 0,
    max_new_community_score INTEGER DEFAULT 0,
    is_timely BOOLEAN,
    timely_context TEXT,
    existing_solution TEXT,
    score_path_a INTEGER DEFAULT 0,
    score_path_b INTEGER DEFAULT 0,
    score_path_c INTEGER DEFAULT 0,
    recommended_path TEXT,
    multi_path_json TEXT,
    delta_type TEXT,
    delta_signal_change INTEGER,
    delta_subreddit_change INTEGER,
    social_hook TEXT,
    content_angle TEXT,
    product_angle TEXT,
    source_urls_json TEXT,
    status TEXT DEFAULT 'new',
    first_seen TIMESTAMP,
    last_seen TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS opportunity_signals (
    opportunity_id TEXT REFERENCES opportunities(id),
    classified_signal_id TEXT REFERENCES classified_signals(id),
    PRIMARY KEY (opportunity_id, classified_signal_id)
);

CREATE TABLE IF NOT EXISTS subreddit_tracker (
    subreddit TEXT PRIMARY KEY,
    first_seen TIMESTAMP,
    subscriber_count INTEGER,
    subscriber_count_previous INTEGER,
    growth_rate_per_day REAL,
    last_checked TIMESTAMP,
    description TEXT,
    is_new BOOLEAN DEFAULT FALSE,
    related_opportunity_id TEXT
);

CREATE TABLE IF NOT EXISTS actions (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT REFERENCES opportunities(id),
    path TEXT,
    action_type TEXT,
    notes TEXT,
    output_url TEXT,
    revenue REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT REFERENCES opportunities(id),
    judgment TEXT NOT NULL,
    deliberation_text TEXT,
    deliberation_cost REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS deliberations (
    opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id),
    assessment_text TEXT,
    cost REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_quality (
    source_key TEXT PRIMARY KEY,
    source_type TEXT,
    signals_seen INTEGER DEFAULT 0,
    signals_relevant INTEGER DEFAULT 0,
    relevance_rate REAL DEFAULT 0,
    last_updated TIMESTAMP
);

CREATE TABLE IF NOT EXISTS topic_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    topic TEXT NOT NULL,
    category TEXT,
    judgment TEXT NOT NULL,
    opportunity_id TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signal_viability (
    raw_signal_id TEXT PRIMARY KEY REFERENCES raw_signals(id),
    viable BOOLEAN NOT NULL,
    reason TEXT,
    opportunity_id TEXT,
    checked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS signal_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    raw_signal_id TEXT REFERENCES raw_signals(id),
    score INTEGER,
    comment_count INTEGER,
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS build_candidates (
    id TEXT PRIMARY KEY,
    opportunity_id TEXT REFERENCES opportunities(id),
    status TEXT DEFAULT 'affirmed',
    vercel_url TEXT,
    source_thread_urls_json TEXT,
    posted_at TIMESTAMP,
    engagement_json TEXT,
    affirmed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    built_at TIMESTAMP,
    deployed_at TIMESTAMP,
    archived_at TIMESTAMP,
    archive_reason TEXT
);

CREATE TABLE IF NOT EXISTS comparative_rankings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ranking_text TEXT,
    candidate_ids_json TEXT,
    cost REAL DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rice_scores (
    opportunity_id TEXT PRIMARY KEY REFERENCES opportunities(id),
    rice_score INTEGER DEFAULT 0,
    reach REAL DEFAULT 0,
    measured_reach REAL DEFAULT 0,
    market_reach REAL DEFAULT 0,
    impact REAL DEFAULT 0,
    confidence REAL DEFAULT 0,
    effort REAL DEFAULT 0,
    buildable BOOLEAN DEFAULT FALSE,
    estimated_campaigns INTEGER DEFAULT 0,
    complexity_reason TEXT,
    market_signals_json TEXT,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scan_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    requests_made INTEGER,
    signals_ingested INTEGER,
    signals_classified INTEGER,
    signals_relevant INTEGER,
    opportunities_created INTEGER,
    opportunities_updated INTEGER,
    deltas_detected INTEGER,
    classification_cost_usd REAL,
    bandwidth_used_bytes INTEGER,
    errors_json TEXT
);

CREATE TABLE IF NOT EXISTS opportunity_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id TEXT REFERENCES opportunities(id),
    snapshot_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    signal_count INTEGER,
    subreddit_count INTEGER,
    score_path_a INTEGER,
    score_path_b INTEGER,
    score_path_c INTEGER
);
"""


class Database:
    """SQLite database manager for TrendX."""

    def __init__(self, db_path: str = "data/trendx.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=30000")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def init_schema(self) -> None:
        if not self.conn:
            self.connect()
        self.conn.executescript(SCHEMA)
        # Migrate: add score columns to existing tables
        for col in ["convergence_score", "unanswered_score", "workaround_score", "new_community_score"]:
            try:
                self.conn.execute(f"ALTER TABLE classified_signals ADD COLUMN {col} INTEGER DEFAULT 0")
            except Exception:
                pass
        for col in ["max_convergence_score", "max_unanswered_score", "max_workaround_score", "max_new_community_score"]:
            try:
                self.conn.execute(f"ALTER TABLE opportunities ADD COLUMN {col} INTEGER DEFAULT 0")
            except Exception:
                pass
        self.conn.commit()

    def __enter__(self):
        self.connect()
        self.init_schema()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    # ── Raw Signals ──

    def insert_raw_signal(self, signal: dict) -> bool:
        """Insert a raw signal. Returns True if inserted, False if duplicate."""
        try:
            self.conn.execute(
                """INSERT INTO raw_signals
                   (id, source, source_id, title, body, url, permalink, score,
                    comment_count, subreddit, author, created_at, feed,
                    parent_signal_id, metadata_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    signal["id"], signal["source"], signal["source_id"],
                    signal.get("title", ""), signal.get("body", ""),
                    signal.get("url", ""), signal.get("permalink", ""),
                    signal.get("score", 0), signal.get("comment_count", 0),
                    signal.get("subreddit"), signal.get("author", ""),
                    signal.get("created_at"), signal.get("feed", ""),
                    signal.get("parent_signal_id"),
                    signal.get("metadata_json"),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def get_unclassified_signals(self, limit: int = 100) -> list[dict]:
        """Get raw signals that haven't been classified yet."""
        rows = self.conn.execute(
            """SELECT * FROM raw_signals WHERE classified = FALSE
               ORDER BY ingested_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def mark_classified(self, signal_id: str) -> None:
        self.conn.execute(
            "UPDATE raw_signals SET classified = TRUE WHERE id = ?",
            (signal_id,),
        )
        self.conn.commit()

    # ── Classified Signals ──

    def insert_classified_signal(self, cs: dict) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO classified_signals
               (id, raw_signal_id, relevant, topic, category, signal_type,
                intensity,
                convergence_likely, convergence_score, convergence_breadth,
                unanswered_detected, unanswered_score, unanswered_evidence,
                workaround_detected, workaround_score, workaround_current_method,
                workaround_pain_point, workaround_ideal_solution,
                new_community_detected, new_community_score, new_community_name,
                is_timely, timely_context, existing_solution,
                social_hook, content_angle, product_angle, key_quote)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cs["id"], cs["raw_signal_id"], cs["relevant"],
                cs.get("topic", ""), cs.get("category", ""),
                cs.get("signal_type", ""), cs.get("intensity", 0),
                cs.get("convergence_likely", False), cs.get("convergence_score", 0),
                cs.get("convergence_breadth", ""),
                cs.get("unanswered_detected", False), cs.get("unanswered_score", 0),
                cs.get("unanswered_evidence", ""),
                cs.get("workaround_detected", False), cs.get("workaround_score", 0),
                cs.get("workaround_current_method", ""),
                cs.get("workaround_pain_point", ""), cs.get("workaround_ideal_solution", ""),
                cs.get("new_community_detected", False), cs.get("new_community_score", 0),
                cs.get("new_community_name", ""),
                cs.get("is_timely", False), cs.get("timely_context", ""),
                cs.get("existing_solution", ""),
                cs.get("social_hook", ""), cs.get("content_angle", ""),
                cs.get("product_angle", ""), cs.get("key_quote", ""),
            ),
        )
        self.conn.commit()

    def get_relevant_signals(self, since: datetime | None = None) -> list[dict]:
        """Get classified signals marked as relevant."""
        if since:
            rows = self.conn.execute(
                """SELECT cs.*, rs.source, rs.subreddit, rs.score as raw_score,
                          rs.permalink, rs.url as raw_url
                   FROM classified_signals cs
                   JOIN raw_signals rs ON cs.raw_signal_id = rs.id
                   WHERE cs.relevant = TRUE AND cs.classified_at >= ?
                   ORDER BY cs.intensity DESC""",
                (since.isoformat(),),
            ).fetchall()
        else:
            rows = self.conn.execute(
                """SELECT cs.*, rs.source, rs.subreddit, rs.score as raw_score,
                          rs.permalink, rs.url as raw_url
                   FROM classified_signals cs
                   JOIN raw_signals rs ON cs.raw_signal_id = rs.id
                   WHERE cs.relevant = TRUE
                   ORDER BY cs.intensity DESC""",
            ).fetchall()
        return [dict(r) for r in rows]

    # ── Opportunities ──

    def upsert_opportunity(self, opp: dict) -> None:
        now = datetime.now(UTC).isoformat()
        existing = self.conn.execute(
            "SELECT id FROM opportunities WHERE id = ?", (opp["id"],)
        ).fetchone()

        if existing:
            self.conn.execute(
                """UPDATE opportunities SET
                   topic=?, category=?, signal_count=?, max_intensity=?,
                   subreddit_count=?, subreddits_json=?,
                   convergence_detected=?, convergence_score=?,
                   cross_source_confirmed=?, distinct_source_count=?,
                   has_unanswered_demand=?, has_manual_workaround=?,
                   workaround_descriptions_json=?,
                   has_new_community=?, new_community_names_json=?,
                   max_convergence_score=?, max_unanswered_score=?,
                   max_workaround_score=?, max_new_community_score=?,
                   is_timely=?, timely_context=?, existing_solution=?,
                   score_path_a=?, score_path_b=?, score_path_c=?,
                   recommended_path=?, multi_path_json=?,
                   delta_type=?, delta_signal_change=?, delta_subreddit_change=?,
                   social_hook=?, content_angle=?, product_angle=?,
                   source_urls_json=?, last_seen=?, updated_at=?
                   WHERE id=?""",
                (
                    opp["topic"], opp.get("category", ""),
                    opp.get("signal_count", 1), opp.get("max_intensity", 0),
                    opp.get("subreddit_count", 1),
                    json.dumps(opp.get("subreddits", [])),
                    opp.get("convergence_detected", False),
                    opp.get("convergence_score", 0),
                    opp.get("cross_source_confirmed", False),
                    opp.get("distinct_source_count", 1),
                    opp.get("has_unanswered_demand", False),
                    opp.get("has_manual_workaround", False),
                    json.dumps(opp.get("workaround_descriptions", [])),
                    opp.get("has_new_community", False),
                    json.dumps(opp.get("new_community_names", [])),
                    opp.get("max_convergence_score", 0), opp.get("max_unanswered_score", 0),
                    opp.get("max_workaround_score", 0), opp.get("max_new_community_score", 0),
                    opp.get("is_timely", False), opp.get("timely_context", ""),
                    opp.get("existing_solution", ""),
                    opp.get("score_path_a", 0), opp.get("score_path_b", 0),
                    opp.get("score_path_c", 0),
                    opp.get("recommended_path", ""),
                    json.dumps(opp.get("multi_path", [])),
                    opp.get("delta_type"), opp.get("delta_signal_change"),
                    opp.get("delta_subreddit_change"),
                    opp.get("social_hook", ""), opp.get("content_angle", ""),
                    opp.get("product_angle", ""),
                    json.dumps(opp.get("source_urls", [])),
                    now, now, opp["id"],
                ),
            )
        else:
            self.conn.execute(
                """INSERT INTO opportunities
                   (id, topic, category, signal_count, max_intensity,
                    subreddit_count, subreddits_json,
                    convergence_detected, convergence_score,
                    cross_source_confirmed, distinct_source_count,
                    has_unanswered_demand, has_manual_workaround,
                    workaround_descriptions_json,
                    has_new_community, new_community_names_json,
                    max_convergence_score, max_unanswered_score,
                    max_workaround_score, max_new_community_score,
                    is_timely, timely_context, existing_solution,
                    score_path_a, score_path_b, score_path_c,
                    recommended_path, multi_path_json,
                    delta_type, delta_signal_change, delta_subreddit_change,
                    social_hook, content_angle, product_angle,
                    source_urls_json, status, first_seen, last_seen,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    opp["id"], opp["topic"], opp.get("category", ""),
                    opp.get("signal_count", 1), opp.get("max_intensity", 0),
                    opp.get("subreddit_count", 1),
                    json.dumps(opp.get("subreddits", [])),
                    opp.get("convergence_detected", False),
                    opp.get("convergence_score", 0),
                    opp.get("cross_source_confirmed", False),
                    opp.get("distinct_source_count", 1),
                    opp.get("has_unanswered_demand", False),
                    opp.get("has_manual_workaround", False),
                    json.dumps(opp.get("workaround_descriptions", [])),
                    opp.get("has_new_community", False),
                    json.dumps(opp.get("new_community_names", [])),
                    opp.get("max_convergence_score", 0), opp.get("max_unanswered_score", 0),
                    opp.get("max_workaround_score", 0), opp.get("max_new_community_score", 0),
                    opp.get("is_timely", False), opp.get("timely_context", ""),
                    opp.get("existing_solution", ""),
                    opp.get("score_path_a", 0), opp.get("score_path_b", 0),
                    opp.get("score_path_c", 0),
                    opp.get("recommended_path", ""),
                    json.dumps(opp.get("multi_path", [])),
                    opp.get("delta_type"), opp.get("delta_signal_change"),
                    opp.get("delta_subreddit_change"),
                    opp.get("social_hook", ""), opp.get("content_angle", ""),
                    opp.get("product_angle", ""),
                    json.dumps(opp.get("source_urls", [])),
                    "new", now, now, now, now,
                ),
            )
        self.conn.commit()

    def link_signal_to_opportunity(self, opportunity_id: str, classified_signal_id: str) -> None:
        try:
            self.conn.execute(
                "INSERT OR IGNORE INTO opportunity_signals VALUES (?, ?)",
                (opportunity_id, classified_signal_id),
            )
            self.conn.commit()
        except sqlite3.IntegrityError:
            pass

    def get_opportunities(
        self,
        limit: int = 50,
        path: str | None = None,
        pattern: str | None = None,
        delta: str | None = None,
        status: str | None = None,
    ) -> list[dict]:
        """Get opportunities with optional filters."""
        query = "SELECT * FROM opportunities WHERE 1=1"
        params: list = []

        if status:
            query += " AND status = ?"
            params.append(status)
        elif status is None:
            query += " AND status != 'dismissed'"

        if pattern == "convergence":
            query += " AND convergence_detected = TRUE"
        elif pattern == "unanswered":
            query += " AND has_unanswered_demand = TRUE"
        elif pattern == "workaround":
            query += " AND has_manual_workaround = TRUE"
        elif pattern == "new_community":
            query += " AND has_new_community = TRUE"

        if delta:
            query += " AND delta_type = ?"
            params.append(delta)

        if path:
            score_col = f"score_path_{path.lower()}"
            query += f" ORDER BY {score_col} DESC"
        else:
            query += " ORDER BY MAX(score_path_a, score_path_b, score_path_c) DESC"

        query += " LIMIT ?"
        params.append(limit)

        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    def get_opportunity(self, opp_id: str) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM opportunities WHERE id = ?", (opp_id,)
        ).fetchone()
        return dict(row) if row else None

    def dismiss_opportunity(self, opp_id: str) -> None:
        self.conn.execute(
            "UPDATE opportunities SET status = 'dismissed', updated_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), opp_id),
        )
        self.conn.commit()

    def act_on_opportunity(self, action: dict) -> None:
        self.conn.execute(
            """INSERT INTO actions (id, opportunity_id, path, action_type, notes, output_url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                action["id"], action["opportunity_id"], action["path"],
                action.get("action_type", ""), action.get("notes", ""),
                action.get("output_url", ""),
            ),
        )
        self.conn.execute(
            "UPDATE opportunities SET status = 'acted_on', updated_at = ? WHERE id = ?",
            (datetime.now(UTC).isoformat(), action["opportunity_id"]),
        )
        self.conn.commit()

    # ── Snapshots ──

    def save_snapshots(self) -> None:
        """Snapshot all non-dismissed opportunities for delta detection."""
        now = datetime.now(UTC).isoformat()
        opps = self.conn.execute(
            "SELECT id, signal_count, subreddit_count, score_path_a, score_path_b, score_path_c FROM opportunities WHERE status != 'dismissed'"
        ).fetchall()
        for opp in opps:
            self.conn.execute(
                """INSERT INTO opportunity_snapshots
                   (opportunity_id, snapshot_at, signal_count, subreddit_count,
                    score_path_a, score_path_b, score_path_c)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (opp["id"], now, opp["signal_count"], opp["subreddit_count"],
                 opp["score_path_a"], opp["score_path_b"], opp["score_path_c"]),
            )
        self.conn.commit()

    def get_previous_snapshots(self) -> dict[str, dict]:
        """Get the most recent snapshot for each opportunity."""
        rows = self.conn.execute(
            """SELECT os.* FROM opportunity_snapshots os
               INNER JOIN (
                   SELECT opportunity_id, MAX(snapshot_at) as max_at
                   FROM opportunity_snapshots
                   GROUP BY opportunity_id
               ) latest ON os.opportunity_id = latest.opportunity_id
                       AND os.snapshot_at = latest.max_at"""
        ).fetchall()
        return {r["opportunity_id"]: dict(r) for r in rows}

    # ── Subreddit Tracker ──

    def upsert_subreddit(self, sub: dict) -> None:
        self.conn.execute(
            """INSERT INTO subreddit_tracker
               (subreddit, first_seen, subscriber_count, subscriber_count_previous,
                growth_rate_per_day, last_checked, description, is_new,
                related_opportunity_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(subreddit) DO UPDATE SET
                subscriber_count_previous = subreddit_tracker.subscriber_count,
                subscriber_count = excluded.subscriber_count,
                growth_rate_per_day = excluded.growth_rate_per_day,
                last_checked = excluded.last_checked,
                description = excluded.description,
                related_opportunity_id = excluded.related_opportunity_id""",
            (
                sub["subreddit"], sub.get("first_seen", datetime.now(UTC).isoformat()),
                sub.get("subscriber_count", 0), sub.get("subscriber_count_previous", 0),
                sub.get("growth_rate_per_day", 0),
                sub.get("last_checked", datetime.now(UTC).isoformat()),
                sub.get("description", ""), sub.get("is_new", False),
                sub.get("related_opportunity_id"),
            ),
        )
        self.conn.commit()

    # ── Scan Log ──

    def log_scan(self, log: dict) -> int:
        cursor = self.conn.execute(
            """INSERT INTO scan_log
               (started_at, completed_at, requests_made, signals_ingested,
                signals_classified, signals_relevant, opportunities_created,
                opportunities_updated, deltas_detected, classification_cost_usd,
                bandwidth_used_bytes, errors_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                log.get("started_at"), log.get("completed_at"),
                log.get("requests_made", 0), log.get("signals_ingested", 0),
                log.get("signals_classified", 0), log.get("signals_relevant", 0),
                log.get("opportunities_created", 0), log.get("opportunities_updated", 0),
                log.get("deltas_detected", 0), log.get("classification_cost_usd", 0),
                log.get("bandwidth_used_bytes", 0),
                json.dumps(log.get("errors", [])),
            ),
        )
        self.conn.commit()
        return cursor.lastrowid

    def get_scan_stats(self) -> dict:
        """Get aggregate scan statistics."""
        row = self.conn.execute(
            """SELECT
                COUNT(*) as total_scans,
                SUM(signals_ingested) as total_signals,
                SUM(signals_classified) as total_classified,
                SUM(signals_relevant) as total_relevant,
                SUM(opportunities_created) as total_opportunities,
                SUM(classification_cost_usd) as total_cost,
                MAX(completed_at) as last_scan
               FROM scan_log"""
        ).fetchone()
        return dict(row) if row else {}

    def get_opportunity_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as c FROM opportunities").fetchone()
        return row["c"] if row else 0

    def get_signal_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) as c FROM raw_signals").fetchone()
        return row["c"] if row else 0

    # ── Reviews ──

    def save_review(self, opportunity_id: str, judgment: str, deliberation_text: str = "", cost: float = 0) -> None:
        self.conn.execute(
            "INSERT INTO reviews (opportunity_id, judgment, deliberation_text, deliberation_cost) VALUES (?, ?, ?, ?)",
            (opportunity_id, judgment, deliberation_text, cost),
        )
        # Log topic feedback for learning
        opp = self.get_opportunity(opportunity_id)
        if opp:
            self.conn.execute(
                "INSERT INTO topic_feedback (topic, category, judgment, opportunity_id) VALUES (?, ?, ?, ?)",
                (opp.get("topic", ""), opp.get("category", ""), judgment, opportunity_id),
            )
        if judgment == "interesting":
            self.conn.execute(
                "UPDATE opportunities SET status = 'watching', updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), opportunity_id),
            )
            # Create build candidate — this opportunity enters the build pipeline
            self.create_build_candidate(opportunity_id)
        elif judgment == "pass":
            self.conn.execute(
                "UPDATE opportunities SET status = 'dismissed', updated_at = ? WHERE id = ?",
                (datetime.now(UTC).isoformat(), opportunity_id),
            )
            # NOTE: dismissed does NOT stop re-crawling the source signals.
            # The signals stay in the system. The opportunity is just hidden from review.
            # If the same topic re-emerges with new signals, a new opportunity will be created.
        self.conn.commit()

    def get_unreviewed_opportunities(self, limit: int = 20) -> list[dict]:
        """Get opportunities that haven't been reviewed yet, ranked by score."""
        rows = self.conn.execute(
            """SELECT o.* FROM opportunities o
               LEFT JOIN reviews r ON o.id = r.opportunity_id
               WHERE r.id IS NULL AND o.status NOT IN ('dismissed', 'acted_on')
               ORDER BY MAX(o.score_path_a, o.score_path_b, o.score_path_c) DESC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_reviews(self, limit: int = 50) -> list[dict]:
        rows = self.conn.execute(
            """SELECT r.*, o.topic, o.category FROM reviews r
               JOIN opportunities o ON r.opportunity_id = o.id
               ORDER BY r.created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Source Quality ──

    def update_source_quality(self) -> None:
        """Recalculate source quality stats from classified signals."""
        self.conn.execute("""
            INSERT OR REPLACE INTO source_quality (source_key, source_type, signals_seen, signals_relevant, relevance_rate, last_updated)
            SELECT 
                CASE 
                    WHEN rs.subreddit IS NOT NULL AND rs.subreddit != '' 
                    THEN rs.source || ':' || rs.subreddit
                    ELSE rs.source || ':' || rs.feed
                END as source_key,
                rs.source as source_type,
                COUNT(*) as signals_seen,
                SUM(CASE WHEN cs.relevant = 1 THEN 1 ELSE 0 END) as signals_relevant,
                CAST(SUM(CASE WHEN cs.relevant = 1 THEN 1 ELSE 0 END) AS REAL) / COUNT(*) as relevance_rate,
                datetime('now') as last_updated
            FROM raw_signals rs
            JOIN classified_signals cs ON cs.raw_signal_id = rs.id
            GROUP BY source_key
        """)
        self.conn.commit()

    def get_low_quality_sources(self, min_seen: int = 20, max_relevance: float = 0.0) -> list[str]:
        """Get source keys that have been seen enough times and never produced relevant signals."""
        rows = self.conn.execute(
            """SELECT source_key FROM source_quality 
               WHERE signals_seen >= ? AND relevance_rate <= ?
               ORDER BY signals_seen DESC""",
            (min_seen, max_relevance),
        ).fetchall()
        return [r["source_key"] for r in rows]

    def get_source_quality_stats(self) -> list[dict]:
        """Get all source quality stats for display."""
        rows = self.conn.execute(
            "SELECT * FROM source_quality ORDER BY signals_seen DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ── Topic Feedback ──

    def get_topic_feedback_summary(self) -> list[dict]:
        """Get all topic feedback grouped by category for display."""
        rows = self.conn.execute("""
            SELECT category,
                   COUNT(*) as total,
                   SUM(CASE WHEN judgment = 'interesting' THEN 1 ELSE 0 END) as approved,
                   SUM(CASE WHEN judgment = 'pass' THEN 1 ELSE 0 END) as rejected
            FROM topic_feedback
            WHERE category IS NOT NULL AND category != ''
            GROUP BY category
            ORDER BY total DESC
        """).fetchall()
        return [dict(r) for r in rows]

    def get_feedback_for_prompt(self) -> str:
        """Build a summary of past judgments for the auto-eval prompt. 
        Not category-level blocking — just context about what the operator has liked/disliked."""
        rows = self.conn.execute("""
            SELECT judgment, topic, category FROM topic_feedback
            ORDER BY created_at DESC LIMIT 30
        """).fetchall()
        if not rows:
            return ""
        
        liked = [r for r in rows if r["judgment"] == "interesting"]
        passed = [r for r in rows if r["judgment"] == "pass"]
        
        lines = ["Recent operator judgments (for context, not hard rules):"]
        if liked:
            lines.append("  Approved: " + ", ".join(f'"{r["topic"][:40]}"' for r in liked[:10]))
        if passed:
            lines.append("  Rejected: " + ", ".join(f'"{r["topic"][:40]}"' for r in passed[:10]))
        lines.append("Use these to calibrate — the operator may still like opportunities in rejected categories if the signal is different.")
        return "\n".join(lines)

    # ── Signal Viability ──

    def mark_signal_nonviable(self, raw_signal_id: str, reason: str = "", opportunity_id: str = "") -> None:
        """Mark a raw signal as non-viable — won't be re-processed or re-checked."""
        self.conn.execute(
            "INSERT OR REPLACE INTO signal_viability (raw_signal_id, viable, reason, opportunity_id) VALUES (?, FALSE, ?, ?)",
            (raw_signal_id, reason, opportunity_id),
        )
        self.conn.commit()

    def mark_signal_viable(self, raw_signal_id: str, opportunity_id: str = "") -> None:
        """Mark a raw signal as viable — candidate for acceleration re-checking."""
        self.conn.execute(
            "INSERT OR REPLACE INTO signal_viability (raw_signal_id, viable, reason, opportunity_id) VALUES (?, TRUE, '', ?)",
            (raw_signal_id, opportunity_id),
        )
        self.conn.commit()

    def mark_opportunity_signals_nonviable(self, opportunity_id: str, reason: str = "opportunity_dismissed") -> None:
        """When an opportunity is dismissed, mark all its source signals as non-viable."""
        signal_ids = self.conn.execute(
            """SELECT cs.raw_signal_id FROM classified_signals cs
               JOIN opportunity_signals os ON cs.id = os.classified_signal_id
               WHERE os.opportunity_id = ?""",
            (opportunity_id,),
        ).fetchall()
        for row in signal_ids:
            self.mark_signal_nonviable(row["raw_signal_id"], reason, opportunity_id)

    def get_viable_signal_ids(self, limit: int = 200) -> list[str]:
        """Get raw signal IDs that are viable for acceleration re-checking."""
        rows = self.conn.execute(
            """SELECT raw_signal_id FROM signal_viability
               WHERE viable = TRUE
               ORDER BY checked_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [r["raw_signal_id"] for r in rows]

    def get_nonviable_source_ids(self) -> set[str]:
        """Get source_ids of non-viable signals for fast pre-filter lookups."""
        rows = self.conn.execute(
            """SELECT rs.source, rs.source_id FROM signal_viability sv
               JOIN raw_signals rs ON sv.raw_signal_id = rs.id
               WHERE sv.viable = FALSE"""
        ).fetchall()
        return {(r["source"], r["source_id"]) for r in rows}

    def is_signal_nonviable(self, raw_signal_id: str) -> bool:
        row = self.conn.execute(
            "SELECT viable FROM signal_viability WHERE raw_signal_id = ?",
            (raw_signal_id,),
        ).fetchone()
        return row is not None and not row["viable"]

    # ── Signal Snapshots (acceleration tracking) ──

    def save_signal_snapshot(self, raw_signal_id: str, score: int, comment_count: int) -> None:
        self.conn.execute(
            "INSERT INTO signal_snapshots (raw_signal_id, score, comment_count) VALUES (?, ?, ?)",
            (raw_signal_id, score, comment_count),
        )
        self.conn.commit()

    def get_signal_snapshots(self, raw_signal_id: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM signal_snapshots WHERE raw_signal_id = ? ORDER BY snapshot_at ASC",
            (raw_signal_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_signals_to_recheck(self, limit: int = 100) -> list[dict]:
        """Get relevant signals that should be re-crawled for acceleration detection.
        
        Returns signals that are:
        - Classified as relevant
        - From opportunities that are NOT archived/dormant/solved
        - From sources we can re-fetch (reddit, hackernews)
        """
        rows = self.conn.execute("""
            SELECT rs.id, rs.source, rs.source_id, rs.subreddit, rs.score, rs.comment_count,
                   rs.permalink, rs.url, cs.topic, o.id as opportunity_id, o.status
            FROM raw_signals rs
            JOIN classified_signals cs ON cs.raw_signal_id = rs.id
            LEFT JOIN opportunity_signals os ON cs.id = os.classified_signal_id
            LEFT JOIN opportunities o ON os.opportunity_id = o.id
            LEFT JOIN build_candidates bc ON o.id = bc.opportunity_id AND bc.archive_reason IS NOT NULL
            WHERE cs.relevant = TRUE
            AND rs.source IN ('reddit', 'hackernews')
            AND bc.id IS NULL
            AND (o.status IS NULL OR o.status NOT IN ('dismissed'))
            ORDER BY rs.score DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


    # ── Build Candidates ──

    def create_build_candidate(self, opportunity_id: str) -> str:
        """Create a build candidate from an affirmed opportunity. Returns candidate ID."""
        import uuid
        candidate_id = f"build_{datetime.now(UTC).strftime('%Y%m%d')}_{uuid.uuid4().hex[:6]}"
        opp = self.get_opportunity(opportunity_id)
        source_urls = opp.get("source_urls_json", "[]") if opp else "[]"
        self.conn.execute(
            """INSERT OR IGNORE INTO build_candidates 
               (id, opportunity_id, status, source_thread_urls_json)
               VALUES (?, ?, 'affirmed', ?)""",
            (candidate_id, opportunity_id, source_urls),
        )
        self.conn.commit()
        return candidate_id

    def update_build_candidate(self, candidate_id: str, **kwargs) -> None:
        """Update build candidate fields."""
        allowed = {"status", "vercel_url", "posted_at", "engagement_json", 
                   "built_at", "deployed_at", "archived_at", "archive_reason"}
        updates = {k: v for k, v in kwargs.items() if k in allowed}
        if not updates:
            return
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        values = list(updates.values()) + [candidate_id]
        self.conn.execute(f"UPDATE build_candidates SET {set_clause} WHERE id = ?", values)
        self.conn.commit()

    def get_build_candidates(self, status: str | None = None) -> list[dict]:
        query = """SELECT bc.*, o.topic, o.category, o.score_path_a, o.score_path_b, o.score_path_c
                   FROM build_candidates bc
                   JOIN opportunities o ON bc.opportunity_id = o.id"""
        if status:
            query += " WHERE bc.status = ?"
            rows = self.conn.execute(query, (status,)).fetchall()
        else:
            query += " WHERE bc.archive_reason IS NULL ORDER BY bc.affirmed_at DESC"
            rows = self.conn.execute(query).fetchall()
        return [dict(r) for r in rows]

    def archive_build_candidate(self, candidate_id: str, reason: str) -> None:
        """Archive a build candidate — trend died, competition arrived, or went dormant."""
        self.conn.execute(
            "UPDATE build_candidates SET archived_at = ?, archive_reason = ?, status = 'archived' WHERE id = ?",
            (datetime.now(UTC).isoformat(), reason, candidate_id),
        )
        self.conn.commit()

    # ── Comparative Rankings ──

    def save_comparative_ranking(self, ranking_text: str, candidate_ids: list[str], cost: float = 0) -> None:
        self.conn.execute(
            "INSERT INTO comparative_rankings (ranking_text, candidate_ids_json, cost) VALUES (?, ?, ?)",
            (ranking_text, json.dumps(candidate_ids), cost),
        )
        self.conn.commit()

    def get_latest_comparative_ranking(self) -> dict | None:
        row = self.conn.execute(
            "SELECT * FROM comparative_rankings ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    # ── Re-deliberation Detection ──

    def needs_redeliberation(self, opportunity_id: str) -> bool:
        """Check if an opportunity's data has changed since its last deliberation."""
        delib = self.conn.execute(
            "SELECT created_at FROM deliberations WHERE opportunity_id = ?",
            (opportunity_id,),
        ).fetchone()
        if not delib:
            return True
        
        delib_time = delib["created_at"]
        
        # Check if opportunity was updated after deliberation
        opp = self.conn.execute(
            "SELECT updated_at, signal_count FROM opportunities WHERE id = ?",
            (opportunity_id,),
        ).fetchone()
        if not opp:
            return False
        
        if opp["updated_at"] > delib_time:
            return True
        
        # Check if new signal snapshots exist after deliberation
        new_snaps = self.conn.execute("""
            SELECT COUNT(*) as c FROM signal_snapshots ss
            JOIN classified_signals cs ON ss.raw_signal_id = cs.raw_signal_id
            JOIN opportunity_signals os ON cs.id = os.classified_signal_id
            WHERE os.opportunity_id = ? AND ss.snapshot_at > ?
        """, (opportunity_id, delib_time)).fetchone()
        
        return (new_snaps["c"] or 0) > 0

    # ── RICE Scores ──

    def save_rice_score(self, opportunity_id: str, rice: dict) -> None:
        self.conn.execute(
            """INSERT OR REPLACE INTO rice_scores 
               (opportunity_id, rice_score, reach, measured_reach, market_reach,
                impact, confidence, effort,
                buildable, estimated_campaigns, complexity_reason, market_signals_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            (opportunity_id, rice.get("rice_score", 0), rice.get("reach", 0),
             rice.get("measured_reach", 0), rice.get("market_reach", 0),
             rice.get("impact", 0), rice.get("confidence", 0), rice.get("effort", 0),
             rice.get("buildable", False), rice.get("estimated_campaigns", 0),
             rice.get("complexity_reason", ""),
             json.dumps(rice.get("market_signals", {}))),
        )
        self.conn.commit()

    def get_rice_rankings(self, buildable_only: bool = True, limit: int = 20) -> list[dict]:
        """Get RICE-ranked opportunities with scores."""
        query = """
            SELECT o.*, r.rice_score, r.reach, r.measured_reach, r.market_reach,
                   r.impact, r.confidence, r.effort,
                   r.buildable, r.estimated_campaigns, r.complexity_reason,
                   r.market_signals_json
            FROM rice_scores r
            JOIN opportunities o ON r.opportunity_id = o.id
            WHERE o.status NOT IN ('dismissed')
        """
        if buildable_only:
            query += " AND r.buildable = TRUE AND r.effort <= 200"
        query += " ORDER BY r.rice_score DESC LIMIT ?"
        rows = self.conn.execute(query, (limit,)).fetchall()
        return [dict(r) for r in rows]
