"""SQLite database operations for TrendX."""

import json
import sqlite3
from datetime import datetime
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
    convergence_breadth TEXT,
    unanswered_detected BOOLEAN DEFAULT FALSE,
    unanswered_evidence TEXT,
    workaround_detected BOOLEAN DEFAULT FALSE,
    workaround_current_method TEXT,
    workaround_pain_point TEXT,
    workaround_ideal_solution TEXT,
    new_community_detected BOOLEAN DEFAULT FALSE,
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
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def close(self) -> None:
        if self.conn:
            self.conn.close()
            self.conn = None

    def init_schema(self) -> None:
        if not self.conn:
            self.connect()
        self.conn.executescript(SCHEMA)
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
                intensity, convergence_likely, convergence_breadth,
                unanswered_detected, unanswered_evidence,
                workaround_detected, workaround_current_method,
                workaround_pain_point, workaround_ideal_solution,
                new_community_detected, new_community_name,
                is_timely, timely_context, existing_solution,
                social_hook, content_angle, product_angle, key_quote)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                cs["id"], cs["raw_signal_id"], cs["relevant"],
                cs.get("topic", ""), cs.get("category", ""),
                cs.get("signal_type", ""), cs.get("intensity", 0),
                cs.get("convergence_likely", False), cs.get("convergence_breadth", ""),
                cs.get("unanswered_detected", False), cs.get("unanswered_evidence", ""),
                cs.get("workaround_detected", False), cs.get("workaround_current_method", ""),
                cs.get("workaround_pain_point", ""), cs.get("workaround_ideal_solution", ""),
                cs.get("new_community_detected", False), cs.get("new_community_name", ""),
                cs.get("is_timely", False), cs.get("timely_context", ""),
                cs.get("existing_solution", ""), cs.get("social_hook", ""),
                cs.get("content_angle", ""), cs.get("product_angle", ""),
                cs.get("key_quote", ""),
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
        now = datetime.utcnow().isoformat()
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
                    is_timely, timely_context, existing_solution,
                    score_path_a, score_path_b, score_path_c,
                    recommended_path, multi_path_json,
                    delta_type, delta_signal_change, delta_subreddit_change,
                    social_hook, content_angle, product_angle,
                    source_urls_json, status, first_seen, last_seen,
                    created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            (datetime.utcnow().isoformat(), opp_id),
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
            (datetime.utcnow().isoformat(), action["opportunity_id"]),
        )
        self.conn.commit()

    # ── Snapshots ──

    def save_snapshots(self) -> None:
        """Snapshot all non-dismissed opportunities for delta detection."""
        now = datetime.utcnow().isoformat()
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
                sub["subreddit"], sub.get("first_seen", datetime.utcnow().isoformat()),
                sub.get("subscriber_count", 0), sub.get("subscriber_count_previous", 0),
                sub.get("growth_rate_per_day", 0),
                sub.get("last_checked", datetime.utcnow().isoformat()),
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
