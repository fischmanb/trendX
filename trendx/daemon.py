"""TrendX continuous daemon — runs the full pipeline 24/7 within budget."""

import asyncio
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import load_config, AnthropicConfig
from .store.db import Database
from .classify.classifier import Classifier
from .cluster.clusterer import cluster_signals
from .detect.patterns import detect_convergence
from .detect.deltas import detect_deltas
from .score.scorer import score_all
from .deliberate.auto_eval import AutoEvaluator
from .deliberate.deliberator import Deliberator

logger = logging.getLogger(__name__)

# Suppress noisy per-request logs — batch-level timing is more useful
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("anthropic").setLevel(logging.WARNING)
logging.getLogger("urllib3").setLevel(logging.WARNING)

ET = ZoneInfo("America/New_York")

def _now_et() -> datetime:
    return datetime.now(ET)

def _fmt_time(dt: datetime) -> str:
    """Format datetime as '7:08 PM ET'."""
    return dt.astimezone(ET).strftime("%-I:%M %p ET")

STATE_FILE = Path(__file__).parent.parent / "data" / "daemon_state.json"


def _read_state() -> dict:
    try:
        if STATE_FILE.exists():
            return json.loads(STATE_FILE.read_text())
    except Exception:
        pass
    return {}


def _write_state(state: dict):
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


class CostTracker:
    """Track daily API spend across all cycles."""
    
    def __init__(self, daily_budget: float = 20.0):
        self.daily_budget = daily_budget
        self.daily_spend = 0.0
        self.day_start = datetime.utcnow().date()
        self.cycle_count = 0
    
    def add(self, amount: float):
        today = datetime.utcnow().date()
        if today != self.day_start:
            logger.info(f"Day rolled over. Yesterday spent ${self.daily_spend:.4f} across {self.cycle_count} cycles")
            self.daily_spend = 0.0
            self.day_start = today
            self.cycle_count = 0
        self.daily_spend += amount
    
    def can_afford(self, estimated_cost: float) -> bool:
        return (self.daily_spend + estimated_cost) < self.daily_budget
    
    @property
    def remaining(self) -> float:
        return max(0, self.daily_budget - self.daily_spend)


class Pipeline:
    """Full autonomous TrendX pipeline."""

    def __init__(self, config_path: str | None = None, daily_budget: float = 20.0):
        self.config = load_config(config_path)
        self.cost = CostTracker(daily_budget)
        self.db_path = self.config.storage.db_path

    def _get_db(self) -> Database:
        db_path = Path(self.db_path)
        if not db_path.is_absolute():
            db_path = Path(__file__).parent.parent / db_path
        db = Database(str(db_path))
        db.connect()
        db.init_schema()
        return db

    def run_cycle(self) -> dict:
        """Run one complete pipeline cycle. Returns stats dict."""
        cycle_start = time.time()
        stats = {
            "started_at": datetime.utcnow().isoformat(),
            "signals_ingested": 0,
            "signals_classified": 0,
            "signals_relevant": 0,
            "opportunities_created": 0,
            "opportunities_updated": 0,
            "auto_eval_selected": 0,
            "deliberations_run": 0,
            "cycle_cost": 0.0,
            "errors": [],
        }

        # Budget check
        estimated_cycle_cost = 1.50  # Conservative estimate
        if not self.cost.can_afford(estimated_cycle_cost):
            logger.warning(f"Daily budget nearly exhausted (${self.cost.daily_spend:.2f}/${self.cost.daily_budget:.2f}). Skipping cycle.")
            stats["errors"].append("budget_exhausted")
            return stats

        db = self._get_db()
        try:
            # ── Step 1: INGEST ──
            t0 = time.time()
            logger.info("Step 1/10: INGEST")
            from .cli import run_ingest
            ingest_stats = asyncio.run(run_ingest(self.config, db))
            stats["signals_ingested"] = ingest_stats["signals"]
            stats["errors"].extend(ingest_stats["errors"])
            logger.info(f"  Ingested {ingest_stats['signals']} signals ({time.time()-t0:.1f}s)")


            # ── Step 2: PRE-FILTER ──
            logger.info("Step 2/10: PRE-FILTER")
            # Only hard filter: score outside 1-1000 range
            pre_filtered = db.conn.execute("""
                UPDATE raw_signals SET classified = TRUE
                WHERE classified = FALSE
                AND (score < 1 OR score > 1000)
            """).rowcount

            # Skip signals from sources that have NEVER produced relevant results
            # Only after 20+ observations — gives new sources a fair chance
            noise_sources = db.get_low_quality_sources(min_seen=20, max_relevance=0.0)
            noise_filtered = 0
            for src_key in noise_sources:
                parts = src_key.split(":", 1)
                if len(parts) == 2:
                    source, sub_or_feed = parts
                    # Match by subreddit if it looks like one, otherwise by feed
                    count = db.conn.execute("""
                        UPDATE raw_signals SET classified = TRUE
                        WHERE classified = FALSE
                        AND source = ?
                        AND (subreddit = ? OR feed = ?)
                    """, (source, sub_or_feed, sub_or_feed)).rowcount
                    noise_filtered += count

            db.conn.commit()
            logger.info(f"  Pre-filtered {pre_filtered} out-of-range + {noise_filtered} from learned noise sources")

            # ── Step 3: CLASSIFY ──
            t0 = time.time()
            logger.info("Step 3/10: CLASSIFY")
            classifier = Classifier(db, self.config.anthropic)
            classified, relevant = classifier.classify_all()
            stats["signals_classified"] = classified
            stats["signals_relevant"] = relevant
            stats["cycle_cost"] += classifier.total_cost
            logger.info(f"  Classified {classified}, {relevant} relevant (${classifier.total_cost:.4f}, {time.time()-t0:.1f}s)")

            # Update source quality stats for future pre-filtering
            db.update_source_quality()
            noise_sources_now = db.get_low_quality_sources(min_seen=20, max_relevance=0.0)
            if noise_sources_now:
                logger.info(f"  Learned noise sources: {len(noise_sources_now)} (will skip in future cycles)")

            # ── Step 4: CLUSTER + SCORE ──
            logger.info("Step 4/10: CLUSTER + SCORE")
            created, updated = cluster_signals(db, self.config.clustering)
            stats["opportunities_created"] = created
            stats["opportunities_updated"] = updated

            # Detect patterns and deltas
            opps = db.get_opportunities(limit=10000, status=None)
            opp_list = [dict(o) for o in opps]
            detect_convergence(opp_list, self.config.clustering)
            prev = db.get_previous_snapshots()
            deltas = detect_deltas(opp_list, prev, self.config.deltas)
            for opp in opp_list:
                db.upsert_opportunity(opp)
            db.save_snapshots()

            scored = score_all(db, self.config.scoring)
            logger.info(f"  {created} created, {updated} updated, {scored} scored")

            # ── Step 5: VELOCITY RECHECK ──
            logger.info("Step 5/10: VELOCITY RECHECK")
            from .velocity.tracker import VelocityTracker
            velocity_tracker = VelocityTracker(db, self.config.proxy)
            velocity_stats = asyncio.run(velocity_tracker.recheck_signals(limit=50))
            logger.info(f"  Rechecked {velocity_stats['rechecked']} signals: {velocity_stats['accelerating']} accelerating, {velocity_stats['decelerating']} decelerating")

            # Assess velocity for build candidates — store data, don't prescribe actions
            candidates = db.get_build_candidates(status="affirmed")
            for cand in candidates:
                vel = velocity_tracker.assess_opportunity_velocity(cand["opportunity_id"])
                logger.info(f"  Candidate '{cand.get('topic', '')[:40]}': velocity={vel['avg_velocity']} pts/hr, competition={'yes' if vel.get('has_competition') else 'no'}, snapshots={vel['snapshots_total']}")

            # ── Step 6: AUTO-EVALUATE ──
            logger.info("Step 6/10: AUTO-EVALUATE")
            # Get unreviewed opportunities that DON'T already have deliberations
            unreviewed = db.conn.execute("""
                SELECT o.* FROM opportunities o
                LEFT JOIN reviews r ON o.id = r.opportunity_id
                LEFT JOIN deliberations d ON o.id = d.opportunity_id
                WHERE r.id IS NULL AND d.opportunity_id IS NULL
                AND o.status NOT IN ('dismissed', 'acted_on')
                ORDER BY MAX(o.score_path_a, o.score_path_b, o.score_path_c) DESC
                LIMIT 30
            """).fetchall()
            unreviewed = [dict(o) for o in unreviewed]
            if unreviewed:
                logger.info(f"  Found {len(unreviewed)} new unreviewed opportunities (excluding already-deliberated)")
                feedback_context = db.get_feedback_for_prompt()
                evaluator = AutoEvaluator(self.config.anthropic)
                selected_ids, non_feasible = evaluator.evaluate([dict(o) for o in unreviewed], feedback_context)
                stats["auto_eval_selected"] = len(selected_ids)
                stats["cycle_cost"] += evaluator.total_cost
                
                # Non-feasible opportunities: dismiss from review but do NOT mark signals non-viable
                # (signals keep crawling — the topic might evolve or the feasibility assessment might be wrong)
                for nf in non_feasible:
                    nf_id = nf.get("id", "") if isinstance(nf, dict) else str(nf)
                    nf_reason = nf.get("reason", "auto_eval_non_feasible") if isinstance(nf, dict) else "auto_eval_non_feasible"
                    if nf_id:
                        db.conn.execute(
                            "UPDATE opportunities SET status = 'dismissed', updated_at = ? WHERE id = ?",
                            (datetime.utcnow().isoformat(), nf_id),
                        )
                        db.conn.commit()
                
                logger.info(f"  Auto-eval: {len(selected_ids)} selected, {len(non_feasible)} non-feasible (dismissed)")
            else:
                selected_ids = []
                logger.info("  No unreviewed opportunities to evaluate")

            # ── Step 7: DELIBERATE ──
            logger.info("Step 7/10: DELIBERATE")
            if selected_ids and self.cost.can_afford(len(selected_ids) * 0.02):
                deliberator = Deliberator(self.config.anthropic)
                for opp_id in selected_ids:
                    opp = db.get_opportunity(opp_id)
                    if not opp:
                        continue
                    opp = dict(opp)
                    assessment = deliberator.deliberate(opp)
                    if assessment:
                        db.conn.execute(
                            """INSERT OR REPLACE INTO deliberations
                               (opportunity_id, assessment_text, cost, created_at)
                               VALUES (?, ?, ?, ?)""",
                            (opp_id, assessment, deliberator.total_cost, datetime.utcnow().isoformat()),
                        )
                        db.conn.commit()
                        stats["deliberations_run"] += 1
                stats["cycle_cost"] += deliberator.total_cost
                logger.info(f"  Deliberated {stats['deliberations_run']} new opportunities (${deliberator.total_cost:.4f})")
            else:
                logger.info("  Skipping deliberation (no candidates or budget)")

            # ── Step 8: RE-DELIBERATE + COMPARE ──
            logger.info("Step 8/10: RE-DELIBERATE + COMPARE")
            candidates = db.get_build_candidates(status="affirmed")
            if candidates and self.cost.can_afford(len(candidates) * 0.02 + 0.05):
                deliberator_re = Deliberator(self.config.anthropic)
                
                # Re-deliberate candidates whose data changed
                redelib_count = 0
                for cand in candidates:
                    if db.needs_redeliberation(cand["opportunity_id"]):
                        opp = db.get_opportunity(cand["opportunity_id"])
                        if opp:
                            assessment = deliberator_re.deliberate(dict(opp))
                            if assessment:
                                db.conn.execute(
                                    """INSERT OR REPLACE INTO deliberations
                                       (opportunity_id, assessment_text, cost, created_at)
                                       VALUES (?, ?, ?, ?)""",
                                    (cand["opportunity_id"], assessment, deliberator_re.total_cost, datetime.utcnow().isoformat()),
                                )
                                db.conn.commit()
                                redelib_count += 1
                
                # Comparative ranking across all affirmed candidates
                if len(candidates) >= 2:
                    # Gather velocity data for comparison
                    vel_data = {}
                    for cand in candidates:
                        vel = velocity_tracker.assess_opportunity_velocity(cand["opportunity_id"])
                        vel_data[cand["opportunity_id"]] = vel
                    
                    # Enrich candidates with deliberation text
                    enriched = []
                    for cand in candidates:
                        c = dict(cand)
                        delib = db.conn.execute(
                            "SELECT assessment_text FROM deliberations WHERE opportunity_id = ?",
                            (cand["opportunity_id"],),
                        ).fetchone()
                        c["assessment_text"] = delib["assessment_text"] if delib else ""
                        enriched.append(c)
                    
                    ranking = deliberator_re.compare_candidates(enriched, vel_data)
                    if ranking:
                        candidate_ids = [c["opportunity_id"] for c in candidates]
                        db.save_comparative_ranking(ranking, candidate_ids, deliberator_re.total_cost)
                        logger.info(f"  Comparative ranking generated for {len(candidates)} candidates")
                
                stats["cycle_cost"] += deliberator_re.total_cost
                logger.info(f"  Re-deliberated {redelib_count} candidates (${deliberator_re.total_cost:.4f})")
            else:
                logger.info("  No affirmed candidates to re-assess")


            # ── Step 9: RICE RANK ──
            logger.info("Step 9/10: RICE RANK")
            from .score.rice import RiceRanker
            from .score.market import MarketSizer
            # Get all deliberated opportunities (they passed auto-eval)
            deliberated_opps = db.conn.execute("""
                SELECT o.* FROM opportunities o
                JOIN deliberations d ON o.id = d.opportunity_id
                WHERE o.status NOT IN ('dismissed')
            """).fetchall()
            deliberated_list = [dict(o) for o in deliberated_opps]
            if deliberated_list and self.cost.can_afford(0.05):
                # Collect market sizing signals (parallel, LLM-generated queries)
                market_sizer = MarketSizer(db, self.config.anthropic)
                
                # Generate search queries for all topics in one LLM call
                topics = [o.get("topic", "") for o in deliberated_list]
                query_map = market_sizer.generate_search_queries(topics)
                
                async def gather_market_data(opps):
                    results = {}
                    tasks = []
                    for opp in opps:
                        queries = query_map.get(opp.get("topic", ""), [opp.get("topic", "")[:30]])
                        async def size_one(o=opp, q=queries):
                            try:
                                return o["id"], await market_sizer.size_opportunity(o, q)
                            except Exception as e:
                                logger.debug(f"  Market sizing error for {o['id']}: {e}")
                                return o["id"], {}
                        tasks.append(size_one())
                    done = await asyncio.gather(*tasks, return_exceptions=True)
                    for item in done:
                        if isinstance(item, tuple):
                            results[item[0]] = item[1]
                    return results

                market_data = asyncio.run(gather_market_data(deliberated_list))
                stats["cycle_cost"] += market_sizer.total_cost

                ranker = RiceRanker(self.config.anthropic)
                ranked = ranker.rank(deliberated_list, market_data)
                for entry in ranked:
                    db.save_rice_score(entry["id"], entry["rice"])
                stats["cycle_cost"] += ranker.total_cost
                buildable = [e for e in ranked if e["rice"]["buildable"]]
                logger.info(f"  RICE ranked {len(ranked)} opportunities, {len(buildable)} buildable under $200")
                if buildable:
                    top = buildable[0]
                    r = top["rice"]
                    logger.info(f"  #1: {top.get('topic', '')[:50]} — RICE={r['rice_score']} (R:{r['reach']:.0f} [measured:{r['measured_reach']:.0f} market:{r['market_reach']:.0f}] I:{r['impact']:.0f} C:{r['confidence']:.0f} E:${r['effort']:.0f})")
            else:
                logger.info("  No deliberated opportunities to rank")

            # ── Step 10: LOG ──
            logger.info("Step 10/10: LOG")
            stats["completed_at"] = datetime.utcnow().isoformat()
            self.cost.add(stats["cycle_cost"])
            self.cost.cycle_count += 1
            
            # Log to DB
            db.log_scan({
                "started_at": stats["started_at"],
                "completed_at": stats["completed_at"],
                "requests_made": ingest_stats.get("requests", 0),
                "signals_ingested": stats["signals_ingested"],
                "signals_classified": stats["signals_classified"],
                "signals_relevant": stats["signals_relevant"],
                "opportunities_created": stats["opportunities_created"],
                "opportunities_updated": stats["opportunities_updated"],
                "deltas_detected": len(deltas),
                "classification_cost_usd": stats["cycle_cost"],
                "bandwidth_used_bytes": ingest_stats.get("bytes", 0),
                "errors": stats["errors"],
            })

            elapsed = time.time() - cycle_start
            logger.info(
                f"Cycle complete in {elapsed:.0f}s — "
                f"{stats['signals_ingested']} ingested, {stats['signals_classified']} classified, "
                f"{stats['signals_relevant']} relevant, {stats['auto_eval_selected']} evaluated, "
                f"{stats['deliberations_run']} deliberated — "
                f"${stats['cycle_cost']:.4f} (daily: ${self.cost.daily_spend:.2f}/${self.cost.daily_budget:.2f})"
            )

        except Exception as e:
            logger.exception(f"Cycle error: {e}")
            stats["errors"].append(str(e))
        finally:
            db.close()

        return stats


    def run_forever(self, interval_minutes: int = 96):
        """Run continuous pipeline cycles."""
        logger.info(f"TrendX daemon starting — cycle every {interval_minutes}m, budget ${self.cost.daily_budget:.0f}/day")
        
        _write_state({
            "status": "running",
            "pid": __import__("os").getpid(),
            "interval_minutes": interval_minutes,
            "started_at": datetime.utcnow().isoformat(),
            "daily_budget": self.cost.daily_budget,
        })

        while True:
            # Check for stop signal before each cycle
            state = _read_state()
            if state.get("command") == "stop":
                logger.info("Stop signal received from GUI")
                _write_state({"status": "stopped", "stopped_at": datetime.utcnow().isoformat()})
                break

            # Check for interval change
            if state.get("command") == "set_interval":
                new_interval = state.get("new_interval", interval_minutes)
                logger.info(f"Interval changed from {interval_minutes}m to {new_interval}m")
                interval_minutes = new_interval

            try:
                # Update state: running cycle
                _write_state({
                    "status": "running_cycle",
                    "pid": __import__("os").getpid(),
                    "cycle_started": datetime.utcnow().isoformat(),
                    "interval_minutes": interval_minutes,
                    "cycle_number": self.cost.cycle_count + 1,
                    "daily_spend": self.cost.daily_spend,
                    "daily_budget": self.cost.daily_budget,
                })

                stats = self.run_cycle()
                
                # Adaptive interval: if budget is getting tight, slow down
                remaining = self.cost.remaining
                if remaining < 2.0:
                    actual_interval = interval_minutes * 4
                    logger.warning(f"Budget nearly exhausted (${remaining:.2f}) — 4x interval")
                elif remaining < 5.0:
                    actual_interval = interval_minutes * 2
                    logger.info(f"Budget tight (${remaining:.2f} remaining) — doubling interval to {actual_interval}m")
                else:
                    actual_interval = interval_minutes
                
                next_cycle = _now_et() + timedelta(minutes=actual_interval)
                
                # Update state: sleeping
                _write_state({
                    "status": "sleeping",
                    "pid": __import__("os").getpid(),
                    "interval_minutes": interval_minutes,
                    "actual_interval": actual_interval,
                    "next_cycle_at": next_cycle.isoformat(),
                    "last_cycle_completed": datetime.utcnow().isoformat(),
                    "last_cycle_signals": stats.get("signals_ingested", 0),
                    "last_cycle_classified": stats.get("signals_classified", 0),
                    "last_cycle_cost": stats.get("cycle_cost", 0),
                    "cycle_number": self.cost.cycle_count,
                    "daily_spend": self.cost.daily_spend,
                    "daily_budget": self.cost.daily_budget,
                })

                logger.info(f"Next cycle at {_fmt_time(next_cycle)} ({actual_interval}m)")
                
                # Sleep in 10-second increments so we can check for stop signals
                sleep_end = time.time() + (actual_interval * 60)
                while time.time() < sleep_end:
                    time.sleep(10)
                    state = _read_state()
                    if state.get("command") in ("stop", "run_now"):
                        break
                
                if state.get("command") == "stop":
                    logger.info("Stop signal received during sleep")
                    _write_state({"status": "stopped", "stopped_at": datetime.utcnow().isoformat()})
                    break
                elif state.get("command") == "run_now":
                    logger.info("Run-now signal received — starting next cycle immediately")
                    # Clear the command
                    _write_state({**state, "command": None})

            except KeyboardInterrupt:
                logger.info("Daemon stopped by user")
                _write_state({"status": "stopped", "stopped_at": datetime.utcnow().isoformat()})
                break
            except Exception as e:
                logger.exception(f"Daemon error: {e}")
                _write_state({"status": "error", "error": str(e), "at": datetime.utcnow().isoformat()})
                logger.info("Recovering in 5 minutes...")
                time.sleep(300)
