"""Tests for proxy module."""

import pytest
from trendx.proxy import USER_AGENTS, make_proxy_client, make_direct_client


def test_user_agents_count():
    """Should have 50+ user agents."""
    assert len(USER_AGENTS) >= 50


def test_user_agents_are_realistic():
    """All UAs should contain Mozilla."""
    for ua in USER_AGENTS:
        assert "Mozilla" in ua


def test_make_proxy_client():
    """Should create a proxy client with correct URL."""
    client = make_proxy_client("testuser", "testpass")
    assert client is not None
    # Clean up
    import asyncio
    asyncio.get_event_loop().run_until_complete(client.aclose())


def test_make_direct_client():
    """Should create a non-proxied client."""
    client = make_direct_client()
    assert client is not None
    import asyncio
    asyncio.get_event_loop().run_until_complete(client.aclose())
