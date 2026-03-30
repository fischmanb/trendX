"""Tests for clustering module."""

import pytest

from trendx.store.db import Database
from trendx.config import ClusteringConfig
from trendx.cluster.clusterer import normalize_topic, find_matching_opportunity, cluster_signals


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    database.connect()
    database.init_schema()
    yield database
    database.close()


@pytest.fixture
def config():
    return ClusteringConfig(topic_similarity_threshold=0.7)


def test_normalize_topic():
    assert normalize_topic("The Overtime Rules") == "overtime rules"
    assert normalize_topic("  A  New  Topic  ") == "new topic"
    assert normalize_topic("an interesting thing") == "interesting thing"


def test_find_matching_opportunity_exact():
    existing = {
        "overtime rules": {"category": "finance"},
        "figma to react": {"category": "tech"},
    }
    assert find_matching_opportunity("overtime rules", "finance", existing, 0.7) == "overtime rules"


def test_find_matching_opportunity_fuzzy():
    existing = {
        "overtime pay rules": {"category": "finance"},
    }
    # "overtime rules" should fuzzy match "overtime pay rules"
    result = find_matching_opportunity("overtime rules 2026", "finance", existing, 0.6)
    assert result == "overtime pay rules"


def test_find_matching_opportunity_no_match():
    existing = {
        "figma to react": {"category": "tech"},
    }
    result = find_matching_opportunity("overtime rules", "finance", existing, 0.7)
    assert result is None


def test_cluster_signals_creates_opportunities(db, config):
    """Clustering should create opportunities from classified signals."""
    # Insert raw signals
    for i in range(3):
        db.insert_raw_signal({
            "id": f"raw_{i}",
            "source": "reddit",
            "source_id": f"post_{i}",
            "title": f"Overtime question {i}",
            "body": "body",
            "subreddit": ["personalfinance", "smallbusiness", "legaladvice"][i],
            "score": 100,
            "comment_count": 50,
            "feed": "rising",
        })

    # Insert classified signals with same topic
    for i in range(3):
        db.insert_classified_signal({
            "id": f"cls_{i}",
            "raw_signal_id": f"raw_{i}",
            "relevant": True,
            "topic": "overtime pay rules 2026",
            "category": "finance",
            "signal_type": "question",
            "intensity": 4,
            "is_timely": True,
            "timely_context": "New DOL threshold",
            "existing_solution": "none",
            "social_hook": "Your boss owes you overtime",
            "content_angle": "Overtime calculator",
            "product_angle": "not product-shaped",
        })

    created, updated = cluster_signals(db, config)
    assert created + updated == 3

    # Check opportunity was created
    opps = db.get_opportunities(limit=10)
    assert len(opps) >= 1

    # Find our opportunity
    opp = next(o for o in opps if "overtime" in o["topic"].lower())
    assert opp["signal_count"] >= 2  # At least 2 signals merged
