"""Configuration loader — YAML + environment variable interpolation."""

import os
import re
from pathlib import Path
from dataclasses import dataclass, field

import yaml


def _interpolate_env(value: str) -> str:
    """Replace ${ENV_VAR} with its value from environment."""
    def replacer(match: re.Match) -> str:
        var_name = match.group(1)
        return os.environ.get(var_name, "")
    return re.sub(r"\$\{(\w+)\}", replacer, value)


def _walk_and_interpolate(obj):
    """Recursively interpolate env vars in all string values."""
    if isinstance(obj, str):
        return _interpolate_env(obj)
    if isinstance(obj, dict):
        return {k: _walk_and_interpolate(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_walk_and_interpolate(item) for item in obj]
    return obj


@dataclass
class ProxyConfig:
    provider: str = "iproyal"
    gateway: str = "geo.iproyal.com"
    port: int = 12321
    user: str = ""
    password: str = ""
    country: str = "US"


@dataclass
class AnthropicConfig:
    api_key: str = ""
    model: str = "claude-sonnet-4-6"
    max_tokens: int = 600
    temperature: float = 0.2


@dataclass
class RedditFeed:
    url_template: str = ""
    name: str = ""


@dataclass
class RedditConfig:
    feeds: list[RedditFeed] = field(default_factory=list)
    new_subreddits_url: str = "https://www.reddit.com/subreddits/new.json?limit=100"
    subreddit_search_template: str = "https://www.reddit.com/subreddits/search.json?q={topic}&limit=25"
    comment_template: str = "https://www.reddit.com/r/{subreddit}/comments/{post_id}.json"
    follow_up_intensity_threshold: int = 4
    poll_interval_minutes: int = 30


@dataclass
class HackerNewsConfig:
    base_url: str = "https://hacker-news.firebaseio.com/v0"
    poll_interval_minutes: int = 15
    min_score: int = 10
    follow_up_score_threshold: int = 50
    max_stories_per_feed: int = 50
    story_types: list[str] = field(default_factory=lambda: ["topstories", "newstories", "askstories", "showstories"])


@dataclass
class TwitterConfig:
    nitter_instances: list[str] = field(default_factory=lambda: ["https://nitter.net"])
    poll_interval_minutes: int = 30
    via_proxy: bool = True


@dataclass
class GoogleTrendsConfig:
    geo: str = "US"
    timezone: int = 360
    trending_searches_pn: str = "united_states"
    max_validation_calls_per_cycle: int = 20
    poll_interval_minutes: int = 30


@dataclass
class YouTubeConfig:
    api_key: str = ""
    max_searches_per_cycle: int = 50
    max_comment_fetches_per_cycle: int = 30
    poll_interval_minutes: int = 60


@dataclass
class QuoraConfig:
    poll_interval_minutes: int = 60
    via_proxy: bool = True


@dataclass
class ProductHuntConfig:
    api_token: str = ""
    poll_interval_minutes: int = 60


@dataclass
class ClusteringConfig:
    topic_similarity_threshold: float = 0.7
    convergence_min_subreddits: int = 3
    convergence_window_hours: int = 48


@dataclass
class DeltasConfig:
    new_topic_min_signals: int = 3
    spike_signal_threshold: int = 5
    spike_subreddit_threshold: int = 2


@dataclass
class DetectionConfig:
    new_subreddit_max_age_days: int = 30
    new_subreddit_growth_threshold: int = 50


@dataclass
class StorageConfig:
    db_path: str = "data/trendx.db"
    export_path: str = "data/opportunities.json"
    export_top_n: int = 50


@dataclass
class Config:
    proxy: ProxyConfig = field(default_factory=ProxyConfig)
    anthropic: AnthropicConfig = field(default_factory=AnthropicConfig)
    reddit: RedditConfig = field(default_factory=RedditConfig)
    hackernews: HackerNewsConfig = field(default_factory=HackerNewsConfig)
    twitter: TwitterConfig = field(default_factory=TwitterConfig)
    google_trends: GoogleTrendsConfig = field(default_factory=GoogleTrendsConfig)
    youtube: YouTubeConfig = field(default_factory=YouTubeConfig)
    quora: QuoraConfig = field(default_factory=QuoraConfig)
    producthunt: ProductHuntConfig = field(default_factory=ProductHuntConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)
    deltas: DeltasConfig = field(default_factory=DeltasConfig)
    detection: DetectionConfig = field(default_factory=DetectionConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)


def _build_config_obj(raw: dict) -> Config:
    """Build a Config from a raw dict."""
    cfg = Config()

    if "proxy" in raw:
        p = raw["proxy"]
        cfg.proxy = ProxyConfig(
            provider=p.get("provider", "iproyal"),
            gateway=p.get("gateway", "geo.iproyal.com"),
            port=p.get("port", 12321),
            user=os.environ.get("IPROYAL_USER", ""),
            password=os.environ.get("IPROYAL_PASS", ""),
            country=p.get("country", "US"),
        )

    if "anthropic" in raw:
        a = raw["anthropic"]
        cfg.anthropic = AnthropicConfig(
            api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
            model=a.get("model", "claude-sonnet-4-6"),
            max_tokens=a.get("max_tokens", 600),
            temperature=a.get("temperature", 0.2),
        )

    if "reddit" in raw:
        r = raw["reddit"]
        feeds = [RedditFeed(url_template=f["url_template"], name=f["name"]) for f in r.get("feeds", [])]
        cfg.reddit = RedditConfig(
            feeds=feeds,
            new_subreddits_url=r.get("new_subreddits_url", cfg.reddit.new_subreddits_url),
            subreddit_search_template=r.get("subreddit_search_template", cfg.reddit.subreddit_search_template),
            comment_template=r.get("comment_template", cfg.reddit.comment_template),
            follow_up_intensity_threshold=r.get("follow_up_intensity_threshold", 4),
            poll_interval_minutes=r.get("poll_interval_minutes", 30),
        )

    if "hackernews" in raw:
        h = raw["hackernews"]
        cfg.hackernews = HackerNewsConfig(
            base_url=h.get("base_url", cfg.hackernews.base_url),
            poll_interval_minutes=h.get("poll_interval_minutes", 15),
            min_score=h.get("min_score", 10),
            follow_up_score_threshold=h.get("follow_up_score_threshold", 50),
            max_stories_per_feed=h.get("max_stories_per_feed", 50),
            story_types=h.get("story_types", cfg.hackernews.story_types),
        )

    if "twitter" in raw:
        t = raw["twitter"]
        cfg.twitter = TwitterConfig(
            nitter_instances=t.get("nitter_instances", cfg.twitter.nitter_instances),
            poll_interval_minutes=t.get("poll_interval_minutes", 30),
            via_proxy=t.get("via_proxy", True),
        )

    if "google_trends" in raw:
        g = raw["google_trends"]
        cfg.google_trends = GoogleTrendsConfig(
            geo=g.get("geo", "US"),
            timezone=g.get("timezone", 360),
            trending_searches_pn=g.get("trending_searches_pn", "united_states"),
            max_validation_calls_per_cycle=g.get("max_validation_calls_per_cycle", 20),
            poll_interval_minutes=g.get("poll_interval_minutes", 30),
        )

    if "youtube" in raw:
        y = raw["youtube"]
        cfg.youtube = YouTubeConfig(
            api_key=os.environ.get("YOUTUBE_API_KEY", ""),
            max_searches_per_cycle=y.get("max_searches_per_cycle", 50),
            max_comment_fetches_per_cycle=y.get("max_comment_fetches_per_cycle", 30),
            poll_interval_minutes=y.get("poll_interval_minutes", 60),
        )

    if "quora" in raw:
        q = raw["quora"]
        cfg.quora = QuoraConfig(
            poll_interval_minutes=q.get("poll_interval_minutes", 60),
            via_proxy=q.get("via_proxy", True),
        )

    if "producthunt" in raw:
        ph = raw["producthunt"]
        cfg.producthunt = ProductHuntConfig(
            api_token=os.environ.get("PRODUCTHUNT_API_TOKEN", ""),
            poll_interval_minutes=ph.get("poll_interval_minutes", 60),
        )

    if "clustering" in raw:
        c = raw["clustering"]
        cfg.clustering = ClusteringConfig(
            topic_similarity_threshold=c.get("topic_similarity_threshold", 0.7),
            convergence_min_subreddits=c.get("convergence_min_subreddits", 3),
            convergence_window_hours=c.get("convergence_window_hours", 48),
        )

    if "deltas" in raw:
        d = raw["deltas"]
        cfg.deltas = DeltasConfig(
            new_topic_min_signals=d.get("new_topic_min_signals", 3),
            spike_signal_threshold=d.get("spike_signal_threshold", 5),
            spike_subreddit_threshold=d.get("spike_subreddit_threshold", 2),
        )

    if "detection" in raw:
        dt = raw["detection"]
        cfg.detection = DetectionConfig(
            new_subreddit_max_age_days=dt.get("new_subreddit_max_age_days", 30),
            new_subreddit_growth_threshold=dt.get("new_subreddit_growth_threshold", 50),
        )

    if "storage" in raw:
        s = raw["storage"]
        cfg.storage = StorageConfig(
            db_path=s.get("db_path", "data/trendx.db"),
            export_path=s.get("export_path", "data/opportunities.json"),
            export_top_n=s.get("export_top_n", 50),
        )

    return cfg


def load_config(config_path: str | Path | None = None) -> Config:
    """Load configuration from YAML file with env var interpolation."""
    if config_path is None:
        # Look relative to the trendx package root
        config_path = Path(__file__).parent.parent / "config" / "default.yaml"

    config_path = Path(config_path)
    if not config_path.exists():
        return _build_config_obj({})

    with open(config_path) as f:
        raw = yaml.safe_load(f) or {}

    raw = _walk_and_interpolate(raw)
    return _build_config_obj(raw)
