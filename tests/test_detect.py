"""Tests for detection modules (patterns + deltas)."""

import pytest

from trendx.config import ClusteringConfig, DeltasConfig
from trendx.detect.patterns import detect_convergence
from trendx.detect.deltas import detect_deltas


@pytest.fixture
def clustering_config():
    return ClusteringConfig(convergence_min_subreddits=3)


@pytest.fixture
def deltas_config():
    return DeltasConfig(
        new_topic_min_signals=3,
        spike_signal_threshold=5,
        spike_subreddit_threshold=2,
    )


class TestConvergence:
    def test_detects_convergence(self, clustering_config):
        opps = [
            {
                "id": "opp1",
                "subreddit_count": 5,
                "max_intensity": 4,
                "distinct_source_count": 1,
                "convergence_detected": False,
                "convergence_score": 0,
            },
        ]
        updated = detect_convergence(opps, clustering_config)
        assert len(updated) == 1
        assert updated[0]["convergence_detected"] is True
        assert updated[0]["convergence_score"] > 0

    def test_no_convergence_below_threshold(self, clustering_config):
        opps = [
            {
                "id": "opp1",
                "subreddit_count": 2,
                "max_intensity": 4,
                "distinct_source_count": 1,
                "convergence_detected": False,
                "convergence_score": 0,
            },
        ]
        updated = detect_convergence(opps, clustering_config)
        assert len(updated) == 0

    def test_cross_source_boost(self, clustering_config):
        opps = [
            {
                "id": "opp1",
                "subreddit_count": 4,
                "max_intensity": 4,
                "distinct_source_count": 3,
                "convergence_detected": False,
                "convergence_score": 0,
            },
        ]
        updated = detect_convergence(opps, clustering_config)
        assert len(updated) == 1
        assert updated[0]["cross_source_confirmed"] is True
        # Score should be boosted by 1.5x
        base_score = 4 * 4 * 10  # subreddit_count * avg_score
        assert updated[0]["convergence_score"] == base_score * 1.5


class TestDeltas:
    def test_detect_new_topic(self, deltas_config):
        current = [
            {"id": "opp_new", "signal_count": 5, "subreddit_count": 3},
        ]
        previous = {}  # Empty — first cycle
        deltas = detect_deltas(current, previous, deltas_config)

        new_deltas = [d for d in deltas if d.get("delta_type") == "new"]
        assert len(new_deltas) == 1
        assert new_deltas[0]["delta_signal_change"] == 5

    def test_detect_spike(self, deltas_config):
        current = [
            {"id": "opp1", "signal_count": 20, "subreddit_count": 5},
        ]
        previous = {
            "opp1": {"signal_count": 10, "subreddit_count": 3},
        }
        deltas = detect_deltas(current, previous, deltas_config)

        spike_deltas = [d for d in deltas if d.get("delta_type") == "spike"]
        assert len(spike_deltas) == 1
        assert spike_deltas[0]["delta_signal_change"] == 10

    def test_detect_dying(self, deltas_config):
        current = []  # No current opportunities
        previous = {
            "opp_old": {"signal_count": 5, "subreddit_count": 2},
        }
        deltas = detect_deltas(current, previous, deltas_config)

        dying = [d for d in deltas if d.get("delta_type") == "dying"]
        assert len(dying) == 1

    def test_no_delta_below_threshold(self, deltas_config):
        current = [
            {"id": "opp1", "signal_count": 12, "subreddit_count": 3},
        ]
        previous = {
            "opp1": {"signal_count": 10, "subreddit_count": 3},
        }
        deltas = detect_deltas(current, previous, deltas_config)
        # Change of 2 is below threshold of 5
        non_dying = [d for d in deltas if d.get("delta_type") != "dying"]
        assert len(non_dying) == 0

    def test_convergence_new_delta(self, deltas_config):
        current = [
            {"id": "opp1", "signal_count": 15, "subreddit_count": 4},
        ]
        previous = {
            "opp1": {"signal_count": 10, "subreddit_count": 2},
        }
        deltas = detect_deltas(current, previous, deltas_config)
        conv_new = [d for d in deltas if d.get("delta_type") == "convergence_new"]
        assert len(conv_new) == 1
