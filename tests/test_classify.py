"""Tests for classification module."""

import json
import pytest
from unittest.mock import MagicMock, patch

from trendx.store.db import Database
from trendx.config import AnthropicConfig
from trendx.classify.classifier import Classifier
from trendx.classify.prompts import build_user_prompt, SYSTEM_PROMPT


@pytest.fixture
def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    database.connect()
    database.init_schema()
    yield database
    database.close()


@pytest.fixture
def config():
    return AnthropicConfig(api_key="test-key", model="claude-sonnet-4-6")


def test_system_prompt_contains_patterns():
    """System prompt should describe all four patterns."""
    assert "PATTERN 1" in SYSTEM_PROMPT
    assert "PATTERN 2" in SYSTEM_PROMPT
    assert "PATTERN 3" in SYSTEM_PROMPT
    assert "PATTERN 4" in SYSTEM_PROMPT
    assert "CONVERGENCE" in SYSTEM_PROMPT
    assert "UNANSWERED" in SYSTEM_PROMPT
    assert "WORKAROUND" in SYSTEM_PROMPT
    assert "NEW COMMUNITY" in SYSTEM_PROMPT


def test_build_user_prompt():
    signal = {
        "source": "reddit",
        "subreddit": "personalfinance",
        "feed": "rising",
        "score": 1247,
        "comment_count": 342,
        "created_at": "2024-03-24T20:00:00",
        "title": "New overtime rules 2026",
        "body": "Am I eligible for overtime?",
    }
    prompt = build_user_prompt(signal)
    assert "reddit (personalfinance)" in prompt
    assert "rising" in prompt
    assert "1247" in prompt
    assert "New overtime rules 2026" in prompt

def test_classify_signal_parses_response(db, config):
    """Classifier should parse a valid LLM JSON response and extract classified fields."""
    mock_response = {
        "relevant": True,
        "topic": "overtime tracking tools",
        "category": "finance",
        "patterns": {
            "convergence": {"score": 50, "evidence": "broad discussion"},
            "unanswered": {"score": 75, "evidence": "Top comments are 'following'"},
            "workaround": {
                "score": 75,
                "current_method": "spreadsheet",
                "pain_point": "manual hour tracking",
                "ideal_solution": "automated overtime calculator",
            },
            "new_community": {"score": 0, "evidence": ""},
        },
        "is_timely": True,
        "timely_context": "New DOL overtime threshold April 2026",
        "existing_solution": "none",
        "product_angle": "Automated overtime tracking SaaS",
        "key_quote": "I use a spreadsheet to track hours",
    }

    with patch("anthropic.Anthropic"):
        classifier = Classifier(db, config)
    # Test JSON parsing
    raw_text = json.dumps(mock_response)
    parsed = classifier._parse_json_response(raw_text)
    assert parsed is not None
    assert parsed["relevant"] is True

    # Test extraction
    result = classifier._extract_classified(parsed, "test-signal-1")
    assert result["relevant"] is True
    assert result["topic"] == "overtime tracking tools"
    assert result["workaround_detected"] is True  # score 75 >= 25
    assert result["workaround_score"] == 75
    assert result["convergence_likely"] is True  # score 50 >= 25
    assert result["convergence_score"] == 50
    assert result["unanswered_detected"] is True
    assert result["unanswered_score"] == 75
    assert result["new_community_detected"] is False  # score 0 < 25
    assert result["intensity"] == 75  # max of all pattern scores

def test_classify_irrelevant_signal(db, config):
    """Classifier should handle irrelevant signals."""
    mock_response = {
        "relevant": False,
        "topic": "",
        "category": "other",
        "patterns": {
            "convergence": {"score": 0, "evidence": ""},
            "unanswered": {"score": 0, "evidence": ""},
            "workaround": {"score": 0, "current_method": "", "pain_point": "", "ideal_solution": ""},
            "new_community": {"score": 0, "evidence": ""},
        },
        "is_timely": False,
        "timely_context": "",
        "existing_solution": "",
        "product_angle": "",
        "key_quote": "",
    }

    with patch("anthropic.Anthropic"):
        classifier = Classifier(db, config)

    parsed = classifier._parse_json_response(json.dumps(mock_response))
    result = classifier._extract_classified(parsed, "test-meme-1")

    assert result is not None
    assert result["relevant"] is False
    assert result["intensity"] == 0
    assert result["workaround_detected"] is False
    assert result["convergence_likely"] is False
