"""CLI entry point for TrendX Demand Scanner."""

import asyncio
import json
import logging
import sys
import time
import uuid
from datetime import datetime, UTC
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from .config import load_config
from .store.db import Database
from .store.export import export_opportunities
from .classify.classifier import Classifier
from .cluster.clusterer import cluster_signals
from .detect.patterns import detect_convergence
from .detect.deltas import detect_deltas
from .score.scorer import score_all, score_opportunity

console = Console()
logger = logging.getLogger("trendx")


def setup_logging(verbose: bool = False):
    import time as _time
    level = logging.DEBUG if verbose else logging.INFO
    
    # Use Eastern time in log timestamps
    class EasternFormatter(logging.Formatter):
        def formatTime(self, record, datefmt=None):
            from zoneinfo import ZoneInfo
            dt = datetime.fromtimestamp(record.created, tz=ZoneInfo("America/New_York"))
            return dt.strftime("%-I:%M:%S %p")
    
    handler = logging.StreamHandler()
    handler.setFormatter(EasternFormatter("%(asctime)s [%(name)s] %(levelname)s: %(message)s"))
    logging.root.handlers = [handler]
    logging.root.setLevel(level)


def get_db(config=None):
    if config is None:
        config = load_config()
    db_path = Path(config.storage.db_path)
    if not db_path.is_absolute():
        db_path = Path(__file__).parent.parent / db_path
    return Database(str(db_path))


async def run_ingest(config, db) -> dict:
    """Run all ingestors IN PARALLEL and return stats."""
    import time as _time
    from .proxy import make_proxy_client, make_direct_client

    stats = {
        "requests": 0,
        "signals": 0,
        "bytes": 0,
        "errors": [],
    }

    # Get topics from existing high-scoring opportunities for dynamic search
    topics = []
    try:
        opps = db.get_opportunities(limit=5, path="A")
        topics = [o["topic"] for o in opps if o.get("topic")]
    except Exception:
        pass

    proxy_client = None
    direct_client = None

    try:
        if config.proxy.user and config.proxy.password:
            proxy_client = make_proxy_client(config.proxy.user, config.proxy.password)
        direct_client = make_direct_client()

        # Build list of concurrent ingest tasks
        tasks = []

        async def ingest_reddit():
            if not proxy_client:
                return {"signals": 0, "requests": 0, "bytes": 0, "errors": ["no proxy"]}
            from .ingest.reddit import RedditIngestor
            r = RedditIngestor(db, config.reddit, proxy_client)
            count = await r.ingest(topics_for_search=topics)
            # Follow-up on high-intensity posts (comment threads)
            high_posts = db.get_unclassified_signals(limit=30)
            high_posts = [s for s in high_posts if s.get("source") == "reddit" and s.get("score", 0) > 50]
            fu_count = 0
            if high_posts:
                fu_count = await r.ingest_follow_ups(high_posts)
            logger.info(f"  Reddit: {count} signals + {fu_count} follow-ups")
            return {"signals": count + fu_count, "requests": r.request_count, "bytes": r.bytes_received, "errors": r.errors}

        async def ingest_hackernews():
            from .ingest.hackernews import HackerNewsIngestor
            hn = HackerNewsIngestor(db, config.hackernews, direct_client)
            count = await hn.ingest()
            logger.info(f"  HackerNews: {count} signals")
            return {"signals": count, "requests": hn.request_count, "bytes": hn.bytes_received, "errors": hn.errors}

        async def ingest_twitter():
            if not proxy_client:
                return {"signals": 0, "requests": 0, "bytes": 0, "errors": []}
            from .ingest.twitter import TwitterIngestor
            t = TwitterIngestor(db, config.twitter, proxy_client)
            count = await t.ingest(topics_for_search=topics)
            logger.info(f"  Twitter: {count} signals")
            return {"signals": count, "requests": t.request_count, "bytes": t.bytes_received, "errors": t.errors}

        async def ingest_google_trends():
            from .ingest.google_trends import GoogleTrendsIngestor
            gt = GoogleTrendsIngestor(db, config.google_trends)
            count = await gt.ingest()
            logger.info(f"  Google Trends: {count} signals")
            return {"signals": count, "requests": gt.request_count, "bytes": 0, "errors": gt.errors}

        async def ingest_youtube():
            if not config.youtube.api_key:
                return {"signals": 0, "requests": 0, "bytes": 0, "errors": []}
            from .ingest.youtube import YouTubeIngestor
            yt = YouTubeIngestor(db, config.youtube, direct_client)
            count = await yt.ingest(topics_for_search=topics)
            logger.info(f"  YouTube: {count} signals")
            return {"signals": count, "requests": yt.request_count, "bytes": yt.bytes_received, "errors": yt.errors}

        async def ingest_quora():
            if not proxy_client or not topics:
                return {"signals": 0, "requests": 0, "bytes": 0, "errors": []}
            from .ingest.quora import QuoraIngestor
            q = QuoraIngestor(db, config.quora, proxy_client)
            count = await q.ingest(topics_for_search=topics)
            logger.info(f"  Quora: {count} signals")
            return {"signals": count, "requests": q.request_count, "bytes": q.bytes_received, "errors": q.errors}

        async def ingest_producthunt():
            if not config.producthunt.api_token:
                return {"signals": 0, "requests": 0, "bytes": 0, "errors": []}
            from .ingest.producthunt import ProductHuntIngestor
            ph = ProductHuntIngestor(db, config.producthunt)
            count = await ph.ingest()
            logger.info(f"  Product Hunt: {count} signals")
            return {"signals": count, "requests": ph.request_count, "bytes": 0, "errors": ph.errors}

        # Run all sources concurrently
        t_start = _time.time()
        results = await asyncio.gather(
            ingest_reddit(),
            ingest_hackernews(),
            ingest_twitter(),
            ingest_google_trends(),
            ingest_youtube(),
            ingest_quora(),
            ingest_producthunt(),
            return_exceptions=True,
        )

        for r in results:
            if isinstance(r, Exception):
                stats["errors"].append(str(r))
                logger.error(f"Ingest source error: {r}")
            elif isinstance(r, dict):
                stats["signals"] += r.get("signals", 0)
                stats["requests"] += r.get("requests", 0)
                stats["bytes"] += r.get("bytes", 0)
                stats["errors"].extend(r.get("errors", []))

        elapsed = _time.time() - t_start
        logger.info(f"  All sources completed in {elapsed:.1f}s (parallel)")

    finally:
        if proxy_client:
            await proxy_client.aclose()
        if direct_client:
            await direct_client.aclose()

    return stats


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--config", "-c", "config_path", default=None, help="Path to config YAML")
@click.pass_context
def cli(ctx, verbose, config_path):
    """TrendX Demand Scanner — detect unmet demand before anyone else."""
    setup_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["config"] = load_config(config_path)


@cli.command()
@click.pass_context
def scan(ctx):
    """Run a full scan cycle: ingest → classify → cluster → detect → score → export."""
    config = ctx.obj["config"]
    started_at = datetime.now(UTC).isoformat()

    with get_db(config) as db:
        scan_stats = {
            "started_at": started_at,
            "requests_made": 0,
            "signals_ingested": 0,
            "signals_classified": 0,
            "signals_relevant": 0,
            "opportunities_created": 0,
            "opportunities_updated": 0,
            "deltas_detected": 0,
            "classification_cost_usd": 0,
            "bandwidth_used_bytes": 0,
            "errors": [],
        }

        # Step 1: INGEST
        console.print("\n[bold cyan]Step 1/6: INGEST[/bold cyan]")
        ingest_stats = asyncio.run(run_ingest(config, db))
        scan_stats["requests_made"] = ingest_stats["requests"]
        scan_stats["signals_ingested"] = ingest_stats["signals"]
        scan_stats["bandwidth_used_bytes"] = ingest_stats["bytes"]
        scan_stats["errors"].extend(ingest_stats["errors"])
        console.print(f"  Ingested {ingest_stats['signals']} signals via {ingest_stats['requests']} requests")

        # Step 2: CLASSIFY
        console.print("\n[bold cyan]Step 2/6: CLASSIFY[/bold cyan]")
        classifier = Classifier(db, config.anthropic)
        classified, relevant = classifier.classify_all()
        scan_stats["signals_classified"] = classified
        scan_stats["signals_relevant"] = relevant
        scan_stats["classification_cost_usd"] = classifier.total_cost
        scan_stats["errors"].extend(classifier.errors)
        console.print(f"  Classified {classified} signals, {relevant} relevant (${classifier.total_cost:.4f})")

        # Step 3: CLUSTER
        console.print("\n[bold cyan]Step 3/6: CLUSTER[/bold cyan]")
        created, updated = cluster_signals(db, config.clustering)
        scan_stats["opportunities_created"] = created
        scan_stats["opportunities_updated"] = updated
        console.print(f"  {created} new opportunities, {updated} updated")

        # Step 4: DETECT
        console.print("\n[bold cyan]Step 4/6: DETECT[/bold cyan]")
        opps = db.get_opportunities(limit=10000, status=None)
        detect_convergence(opps, config.clustering)
        previous = db.get_previous_snapshots()
        deltas = detect_deltas(opps, previous, config.deltas)
        scan_stats["deltas_detected"] = len(deltas)
        # Apply deltas back to opportunities
        delta_map = {d["id"]: d for d in deltas if "topic" not in d or d.get("delta_type") != "dying"}
        for opp in opps:
            if opp["id"] in delta_map:
                d = delta_map[opp["id"]]
                opp["delta_type"] = d.get("delta_type")
                opp["delta_signal_change"] = d.get("delta_signal_change")
                opp["delta_subreddit_change"] = d.get("delta_subreddit_change")
                db.upsert_opportunity(opp)
        console.print(f"  {len(deltas)} deltas detected")

        # Step 5: SCORE
        console.print("\n[bold cyan]Step 5/6: SCORE[/bold cyan]")
        scored = score_all(db, config.scoring)
        console.print(f"  Scored {scored} opportunities")

        # Save snapshots for next cycle
        db.save_snapshots()

        # Step 6: EXPORT
        console.print("\n[bold cyan]Step 6/6: EXPORT[/bold cyan]")
        export_path = Path(config.storage.export_path)
        if not export_path.is_absolute():
            export_path = Path(__file__).parent.parent / export_path
        out = export_opportunities(db, str(export_path), config.storage.export_top_n)
        console.print(f"  Exported to {out}")

        # Log scan
        scan_stats["completed_at"] = datetime.now(UTC).isoformat()
        db.log_scan(scan_stats)

        # Summary
        console.print(Panel(
            f"Signals: {scan_stats['signals_ingested']} ingested, "
            f"{scan_stats['signals_classified']} classified, "
            f"{scan_stats['signals_relevant']} relevant\n"
            f"Opportunities: {scan_stats['opportunities_created']} new, "
            f"{scan_stats['opportunities_updated']} updated\n"
            f"Deltas: {scan_stats['deltas_detected']} detected\n"
            f"Cost: ${scan_stats['classification_cost_usd']:.4f}\n"
            f"Errors: {len(scan_stats['errors'])}",
            title="[bold green]Scan Complete[/bold green]",
        ))


@cli.command()
@click.pass_context
def ingest(ctx):
    """Run ingestion only."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        stats = asyncio.run(run_ingest(config, db))
        console.print(f"Ingested {stats['signals']} signals via {stats['requests']} requests")
        if stats["errors"]:
            console.print(f"[yellow]Errors: {len(stats['errors'])}[/yellow]")


@cli.command()
@click.pass_context
def classify(ctx):
    """Run classification only."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        classifier = Classifier(db, config.anthropic)
        classified, relevant = classifier.classify_all()
        console.print(f"Classified {classified} signals, {relevant} relevant (${classifier.total_cost:.4f})")


@cli.command()
@click.pass_context
def rescore(ctx):
    """Re-score all opportunities."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        # Re-cluster first
        cluster_signals(db, config.clustering)
        scored = score_all(db, config.scoring)
        console.print(f"Re-scored {scored} opportunities")


@cli.command()
@click.pass_context
def export(ctx):
    """Export top opportunities to JSON."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        export_path = Path(config.storage.export_path)
        if not export_path.is_absolute():
            export_path = Path(__file__).parent.parent / export_path
        out = export_opportunities(db, str(export_path), config.storage.export_top_n)
        console.print(f"Exported to {out}")


@cli.command()
@click.option("--limit", "-n", default=20, help="Number of results")
@click.option("--path", "-p", type=click.Choice(["A", "B", "C"]), help="Filter by path")
@click.option("--pattern", type=click.Choice(["convergence", "unanswered", "workaround", "new_community"]))
@click.option("--delta", type=click.Choice(["new", "spike", "convergence_new", "dying"]))
@click.pass_context
def top(ctx, limit, path, pattern, delta):
    """View top-ranked opportunities."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        opps = db.get_opportunities(limit=limit, path=path, pattern=pattern, delta=delta)
        if not opps:
            console.print("[yellow]No opportunities found.[/yellow]")
            return

        table = Table(title=f"Top {len(opps)} Opportunities")
        table.add_column("#", style="dim", width=3)
        table.add_column("Topic", style="bold", max_width=35)
        table.add_column("Cat", width=8)
        table.add_column("Sigs", justify="right", width=4)
        table.add_column("Int", justify="right", width=3)
        table.add_column("A", justify="right", width=3, style="blue")
        table.add_column("B", justify="right", width=3, style="green")
        table.add_column("C", justify="right", width=3, style="magenta")
        table.add_column("Path", width=4)
        table.add_column("Patterns", max_width=20)
        table.add_column("Delta", width=8)

        for i, opp in enumerate(opps, 1):
            patterns = []
            if opp.get("convergence_detected"):
                patterns.append(f"conv({opp.get('subreddit_count', 0)})")
            if opp.get("has_unanswered_demand"):
                patterns.append("unans")
            if opp.get("has_manual_workaround"):
                patterns.append("wrk")
            if opp.get("has_new_community"):
                patterns.append("new")
            if opp.get("cross_source_confirmed"):
                patterns.append("xsrc")

            delta_str = opp.get("delta_type", "") or ""
            if delta_str and opp.get("delta_signal_change"):
                delta_str += f"(+{opp['delta_signal_change']})"

            table.add_row(
                str(i),
                opp.get("topic", "")[:35],
                opp.get("category", "")[:8],
                str(opp.get("signal_count", 0)),
                str(opp.get("max_intensity", 0)),
                str(opp.get("score_path_a", 0)),
                str(opp.get("score_path_b", 0)),
                str(opp.get("score_path_c", 0)),
                opp.get("recommended_path", ""),
                ", ".join(patterns),
                delta_str,
            )

        console.print(table)


@cli.command()
@click.argument("opportunity_id")
@click.pass_context
def show(ctx, opportunity_id):
    """Show detailed view of a specific opportunity."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        opp = db.get_opportunity(opportunity_id)
        if not opp:
            console.print(f"[red]Opportunity '{opportunity_id}' not found[/red]")
            return

        console.print(Panel(
            f"[bold]{opp['topic']}[/bold]\n"
            f"Category: {opp['category']} | Status: {opp['status']}\n"
            f"Signals: {opp['signal_count']} | Max Intensity: {opp['max_intensity']}\n"
            f"Subreddits: {opp['subreddit_count']} — {opp.get('subreddits_json', '[]')}\n\n"
            f"[blue]Path A (Content):[/blue] {opp['score_path_a']}\n"
            f"[green]Path B (Product):[/green] {opp['score_path_b']}\n"
            f"[magenta]Path C (Social):[/magenta] {opp['score_path_c']}\n"
            f"Recommended: {opp['recommended_path']}\n\n"
            f"Convergence: {'Yes' if opp.get('convergence_detected') else 'No'} "
            f"(score: {opp.get('convergence_score', 0):.0f})\n"
            f"Cross-source: {'Yes' if opp.get('cross_source_confirmed') else 'No'}\n"
            f"Unanswered: {'Yes' if opp.get('has_unanswered_demand') else 'No'}\n"
            f"Workaround: {'Yes' if opp.get('has_manual_workaround') else 'No'}\n"
            f"New Community: {'Yes' if opp.get('has_new_community') else 'No'}\n\n"
            f"Delta: {opp.get('delta_type', 'none')} "
            f"(signals: {opp.get('delta_signal_change', 0)}, subs: {opp.get('delta_subreddit_change', 0)})\n\n"
            f"Timely: {'Yes' if opp.get('is_timely') else 'No'}\n"
            f"Context: {opp.get('timely_context', '')}\n"
            f"Existing Solution: {opp.get('existing_solution', 'none')}\n\n"
            f"[bold]Social Hook:[/bold] {opp.get('social_hook', '')}\n"
            f"[bold]Content Angle:[/bold] {opp.get('content_angle', '')}\n"
            f"[bold]Product Angle:[/bold] {opp.get('product_angle', '')}\n\n"
            f"First seen: {opp.get('first_seen', '')}\n"
            f"Last seen: {opp.get('last_seen', '')}",
            title=f"Opportunity {opp['id']}",
        ))

        # Show source URLs
        urls = json.loads(opp.get("source_urls_json", "[]"))
        if urls:
            console.print("\n[bold]Source URLs:[/bold]")
            for url in urls[:10]:
                console.print(f"  {url}")


@cli.command()
@click.argument("opportunity_id")
@click.pass_context
def deliberate(ctx, opportunity_id):
    """Generate analytical assessment for an opportunity before review."""
    config = ctx.obj["config"]
    from .deliberate.deliberator import Deliberator

    with get_db(config) as db:
        opp = db.get_opportunity(opportunity_id)
        if not opp:
            console.print(f"[red]Opportunity '{opportunity_id}' not found[/red]")
            return

        opp = dict(opp)
        console.print(f"\n[bold]Deliberating: {opp['topic']}[/bold]")
        console.print(f"[dim]Scores: A={opp['score_path_a']} B={opp['score_path_b']} C={opp['score_path_c']}[/dim]\n")

        with console.status("[bold cyan]Thinking...[/bold cyan]"):
            deliberator = Deliberator(config.anthropic)
            assessment = deliberator.deliberate(opp)

        if assessment:
            console.print(Panel(assessment, title="Assessment", padding=(1, 2)))
            console.print(f"\n[dim]Cost: ${deliberator.total_cost:.4f}[/dim]")
        else:
            console.print("[red]Deliberation failed[/red]")


@cli.command()
@click.option("--interval", "-i", default=30, help="Minutes between scan cycles")
@click.pass_context
def watch(ctx, interval):
    """Run continuous scan cycles."""
    config = ctx.obj["config"]
    console.print(f"[bold]TrendX Watch Mode[/bold] — scanning every {interval} minutes")
    console.print("Press Ctrl+C to stop\n")

    cycle = 0
    while True:
        cycle += 1
        console.print(f"\n[bold cyan]═══ Cycle {cycle} ═══[/bold cyan]")
        try:
            ctx.invoke(scan)
        except Exception as e:
            console.print(f"[red]Cycle {cycle} error: {e}[/red]")
            logger.exception(f"Cycle {cycle} failed")

        console.print(f"\n[dim]Next scan in {interval} minutes...[/dim]")
        try:
            time.sleep(interval * 60)
        except KeyboardInterrupt:
            console.print("\n[yellow]Watch mode stopped.[/yellow]")
            break


@cli.command()
@click.option("--interval", "-i", default=96, help="Minutes between cycles")
@click.option("--budget", "-b", default=20.0, help="Daily budget in USD")
@click.pass_context
def daemon(ctx, interval, budget):
    """Run full autonomous pipeline 24/7: ingest → classify → score → auto-eval → deliberate."""
    from .daemon import Pipeline
    config_path = ctx.obj.get("config_path")
    pipeline = Pipeline(config_path=config_path, daily_budget=budget)
    console.print(f"[bold]TrendX Daemon[/bold] — cycle every {interval}m, budget ${budget:.0f}/day")
    console.print("Press Ctrl+C to stop\n")
    pipeline.run_forever(interval_minutes=interval)


@cli.command()
@click.pass_context
def stats(ctx):
    """Show scan statistics."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        scan_stats = db.get_scan_stats()
        opp_count = db.get_opportunity_count()
        signal_count = db.get_signal_count()

        console.print(Panel(
            f"Total scans: {scan_stats.get('total_scans', 0)}\n"
            f"Total signals ingested: {scan_stats.get('total_signals', 0) or 0}\n"
            f"Total classified: {scan_stats.get('total_classified', 0) or 0}\n"
            f"Total relevant: {scan_stats.get('total_relevant', 0) or 0}\n"
            f"Active opportunities: {opp_count}\n"
            f"Raw signals in DB: {signal_count}\n"
            f"Total classification cost: ${(scan_stats.get('total_cost') or 0):.4f}\n"
            f"Last scan: {scan_stats.get('last_scan', 'never')}",
            title="[bold]TrendX Statistics[/bold]",
        ))


@cli.command("track-sub")
@click.argument("subreddit_name")
@click.pass_context
def track_sub(ctx, subreddit_name):
    """Add a subreddit to emergence tracking."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        db.upsert_subreddit({
            "subreddit": subreddit_name,
            "first_seen": datetime.now(UTC).isoformat(),
            "subscriber_count": 0,
            "is_new": True,
        })
        console.print(f"Tracking subreddit: r/{subreddit_name}")


@cli.command()
@click.argument("opportunity_id")
@click.pass_context
def dismiss(ctx, opportunity_id):
    """Mark an opportunity as not interesting."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        opp = db.get_opportunity(opportunity_id)
        if not opp:
            console.print(f"[red]Opportunity '{opportunity_id}' not found[/red]")
            return
        db.dismiss_opportunity(opportunity_id)
        console.print(f"Dismissed: {opp['topic']}")


@cli.command()
@click.argument("opportunity_id")
@click.option("--path", "-p", required=True, type=click.Choice(["A", "B", "C"]))
@click.option("--notes", "-n", default="", help="Action notes")
@click.pass_context
def act(ctx, opportunity_id, path, notes):
    """Record an action taken on an opportunity."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        opp = db.get_opportunity(opportunity_id)
        if not opp:
            console.print(f"[red]Opportunity '{opportunity_id}' not found[/red]")
            return
        db.act_on_opportunity({
            "id": str(uuid.uuid4()),
            "opportunity_id": opportunity_id,
            "path": path,
            "action_type": f"path_{path.lower()}",
            "notes": notes,
        })
        console.print(f"Recorded action on: {opp['topic']} (Path {path})")


@cli.command()
@click.pass_context
def init_db(ctx):
    """Initialize the database schema."""
    config = ctx.obj["config"]
    with get_db(config) as db:
        console.print("[green]Database initialized.[/green]")


if __name__ == "__main__":
    cli()
