"""Tests for scoring module."""

import pytest

from trendx.score.scorer import score_path_a, score_path_b, score_path_c, apply_delta_boost, score_opportunity
from trendx.config import PathAWeights, PathBWeights, PathCWeights, DeltaBoostWeights, ScoringConfig

# Default weight instances — match dataclass defaults
WA = PathAWeights()
WB = PathBWeights()
WC = PathCWeights()
WD = DeltaBoostWeights()
SCORING = ScoringConfig()


def make_opp(**kwargs):
    """Create a test opportunity dict."""
    defaults = {
        "signal_count": 5,
        "max_intensity": 3,
        "subreddit_count": 2,
        "is_timely": False,
        "has_unanswered_demand": False,
        "has_manual_workaround": False,
        "has_new_community": False,
        "existing_solution": "some tool",
        "social_hook": "A short hook",
        "product_angle": "not product-shaped",        "delta_type": None,
    }
    defaults.update(kwargs)
    return defaults


class TestPathA:
    def test_base_score(self):
        opp = make_opp()
        score = score_path_a(opp, WA)
        assert 0 <= score <= 100

    def test_timely_boost(self):
        base = score_path_a(make_opp(is_timely=False), WA)
        boosted = score_path_a(make_opp(is_timely=True), WA)
        assert boosted - base == WA.timely_bonus

    def test_unanswered_boost(self):
        base = score_path_a(make_opp(has_unanswered_demand=False), WA)
        boosted = score_path_a(make_opp(has_unanswered_demand=True), WA)
        assert boosted - base == WA.unanswered_bonus

    def test_convergence_boost(self):
        low = score_path_a(make_opp(subreddit_count=1), WA)
        high = score_path_a(make_opp(subreddit_count=5), WA)
        assert high > low

    def test_capped_at_100(self):
        # Use higher weights so the raw sum exceeds 100 — verifies the cap
        big_wa = PathAWeights(            signal_count_weight=10, signal_count_cap=40,
            intensity_weight=20, convergence_weight=10, convergence_cap=30,
            timely_bonus=20, unanswered_bonus=20, no_solution_bonus=15,
        )
        opp = make_opp(
            signal_count=50, max_intensity=100, subreddit_count=10,
            is_timely=True, has_unanswered_demand=True, existing_solution="none",
        )
        assert score_path_a(opp, big_wa) == 100


class TestPathB:
    def test_workaround_boost(self):
        base = score_path_b(make_opp(has_manual_workaround=False), WB)
        boosted = score_path_b(make_opp(has_manual_workaround=True), WB)
        assert boosted - base == WB.workaround_bonus

    def test_evergreen_bonus(self):
        """Non-timely topics get an evergreen bonus for Path B."""
        timely = score_path_b(make_opp(is_timely=True), WB)
        evergreen = score_path_b(make_opp(is_timely=False), WB)
        assert evergreen > timely

    def test_product_shaped_boost(self):
        no_product = score_path_b(make_opp(product_angle="not product-shaped"), WB)
        has_product = score_path_b(make_opp(product_angle="SaaS overtime tracker"), WB)
        assert has_product - no_product == WB.product_shaped_bonus

class TestPathC:
    def test_timeliness_is_king(self):
        base = score_path_c(make_opp(is_timely=False), WC)
        boosted = score_path_c(make_opp(is_timely=True), WC)
        assert boosted - base == WC.timely_bonus

    def test_hook_quality(self):
        short = score_path_c(make_opp(social_hook="short"), WC)
        long = score_path_c(make_opp(social_hook="Your boss might owe you overtime and doesn't want you to know"), WC)
        assert long > short


class TestDeltaBoost:
    def test_new_delta_boosts_c(self):
        scores = {"A": 50, "B": 50, "C": 50}
        boosted = apply_delta_boost(scores, "new", WD)
        assert boosted["C"] == 50 + WD.new_c
        assert boosted["A"] == 50

    def test_spike_delta_boosts_a_and_c(self):
        scores = {"A": 50, "B": 50, "C": 50}
        boosted = apply_delta_boost(scores, "spike", WD)
        assert boosted["A"] == 50 + WD.spike_a
        assert boosted["C"] == 50 + WD.spike_c
    def test_convergence_new_boosts_a_and_c(self):
        scores = {"A": 50, "B": 50, "C": 50}
        boosted = apply_delta_boost(scores, "convergence_new", WD)
        assert boosted["A"] == 50 + WD.convergence_new_a
        assert boosted["C"] == 50 + WD.convergence_new_c

    def test_cap_at_100(self):
        scores = {"A": 95, "B": 95, "C": 95}
        boosted = apply_delta_boost(scores, "convergence_new", WD)
        assert boosted["A"] == 100
        assert boosted["C"] == 100


class TestScoreOpportunity:
    def test_recommends_best_path(self):
        opp = make_opp(
            is_timely=True, has_unanswered_demand=True,
            signal_count=10, subreddit_count=5, max_intensity=80,
        )
        scored = score_opportunity(opp, SCORING)
        assert scored["recommended_path"] in ("A", "B", "C")

    def test_multi_path_threshold(self):
        opp = make_opp(
            is_timely=True, has_unanswered_demand=True,
            signal_count=10, subreddit_count=5, max_intensity=80,
            social_hook="A really compelling hook for social media content",
            existing_solution="none",
        )
        scored = score_opportunity(opp, SCORING)
        # With high signals across the board, multiple paths should qualify
        assert len(scored["multi_path"]) >= 1
