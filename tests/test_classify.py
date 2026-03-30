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
    """Classifier should parse a valid LLM JSON response."""
    # Insert a raw signal
    signal = {
        "id": "test-signal-1",
        "source": "reddit",
        "source_id": "abc123",
        "title": "Need help tracking overtime",
        "body": "I use a spreadsheet to track hours",
        "score": 100,
        "comment_count": 50,
        "subreddit": "personalfinance",
        "feed": "rising",
        "created_at": "2024-03-24T20:00:00",
    }
    db.insert_raw_signal(signal)

    mock_response = {
        "relevant": True,
        "topic": "overtime tracking tools",
        "category": "finance",
        "patterns": {
            "convergence": {"likely": True, "breadth": "broad"},
            "unanswered": {"detected": True, "evidence": "Top comments are 'following'"},
            "workaround": {
                "detected": True,
                "current_method": "spreadsheet",
                "pain_point": "manual hour tracking",
                "ideal_solution": "automated overtime calculator",
            },
            "new_community": {"detected": False, "community_name": ""},
        },
        "signal_type": "workaround",
        "intensity": 4,
        "is_timely": True,
        "timely_context": "New DOL overtime threshold April 2026",
        "existing_solution": "none",
        "social_hook": "Your boss might owe you overtime",
        "content_angle": "Overtime eligibility calculator",
        "product_angle": "Automated overtime tracking SaaS",
        "key_quote": "I use a spreadsheet to track hours",
    }

    # Mock the Anthropic client
    mock_content = MagicMock()
    mock_content.text = json.dumps(mock_response)
    mock_api_response = MagicMock()
    mock_api_response.content = [mock_content]
    mock_api_response.usage = MagicMock(input_tokens=500, output_tokens=200)

    with patch("anthropic.Anthropic") as MockAnthropic:
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_api_response
        MockAnthropic.return_value = mock_client

        classifier = Classifier(db, config)
        classifier.client = mock_client
        result = classifier.classify_signal(signal)

    assert result is not None
    assert result["relevant"] is True
    assert result["topic"] == "overtime tracking tools"
    assert result["workaround_detected"] is True
    assert result["intensity"] == 4
    assert result["convergence_likely"] is True


def test_classify_irrelevant_signal(db, config):
    """Classifier should handle irrelevant signals."""
    signal = {
        "id": "test-meme-1",
        "source": "reddit",
        "source_id": "meme001",
        "title": "When your code works on the first try",
        "body": "",
        "score": 15000,
        "comment_count": 500,
        "subreddit": "funny",
        "feed": "hot",
        "created_at": "2024-03-24T20:00:00",
    }

    mock_response = {
        "relevant": False,
        "topic": "",
        "category": "other",
        "patterns": {
            "convergence": {"likely": False, "breadth": "niche"},
            "unanswered": {"detected": False, "evidence": ""},
            "workaround": {"detected": False, "current_method": "", "pain_point": "", "ideal_solution": ""},
            "new_community": {"detected": False, "community_name": ""},
        },
        "signal_type": "discussion",
        "intensity": 1,
        "is_timely": False,
        "timely_context": "",
        "existing_solution": "",
        "social_hook": "",
        "content_angle": "",
        "product_angle": "",
        "key_quote": "",
    }

    mock_content = MagicMock()
    mock_content.text = json.dumps(mock_response)
    mock_api_response = MagicMock()
    mock_api_response.content = [mock_content]
    mock_api_response.usage = MagicMock(input_tokens=300, output_tokens=100)

    with patch("anthropic.Anthropic"):
        classifier = Classifier(db, config)
        mock_client = MagicMock()
        mock_client.messages.create.return_value = mock_api_response
        classifier.client = mock_client
        result = classifier.classify_signal(signal)

    assert result is not None
    assert result["relevant"] is False
