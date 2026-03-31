"""TrendX — Demand Scanner GUI"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).parent))
from trendx.config import load_config
from trendx.store.db import Database

CONFIG_PATH = Path(__file__).parent / "config" / "default.yaml"


def get_db():
    config = load_config(CONFIG_PATH)
    db_path = Path(config.storage.db_path)
    if not db_path.is_absolute():
        db_path = Path(__file__).parent / db_path
    db = Database(str(db_path))
    db.connect()
    db.init_schema()
    return db


st.set_page_config(page_title="TrendX", page_icon="📡", layout="wide", initial_sidebar_state="collapsed")

# ── Custom CSS ──
st.markdown("""
<style>
    /* Kill Streamlit padding */
    .block-container { padding-top: 1rem; padding-bottom: 0; max-width: 1400px; }
    
    /* Larger base font */
    html, body, [class*="css"] { font-size: 16px; }
    
    /* Card styling */
    .opp-card {
        background: #1a1a2e;
        border: 1px solid #2a2a4a;
        border-radius: 12px;
        padding: 24px;
        margin-bottom: 16px;
    }
    .opp-title {
        font-size: 1.5rem;
        font-weight: 700;
        color: #e0e0e0;
        margin-bottom: 8px;
        line-height: 1.3;
    }
    .opp-meta {
        font-size: 0.95rem;
        color: #888;
        margin-bottom: 12px;
    }
    .opp-body {
        font-size: 1.05rem;
        color: #ccc;
        line-height: 1.6;
        margin-bottom: 8px;
    }
    .badge {
        display: inline-block;
        padding: 4px 10px;
        border-radius: 6px;
        font-size: 0.85rem;
        font-weight: 600;
        margin-right: 6px;
        margin-bottom: 4px;
    }
    .badge-workaround { background: #2d1b4e; color: #c084fc; }
    .badge-unanswered { background: #1b3a4e; color: #67e8f9; }
    .badge-convergence { background: #1b4e2d; color: #86efac; }
    .badge-new { background: #4e3a1b; color: #fcd34d; }
    .badge-timely { background: #4e1b1b; color: #fca5a5; }
    .badge-crosssrc { background: #1b2e4e; color: #93c5fd; }
    
    .score-box {
        text-align: center;
        padding: 12px;
        border-radius: 8px;
        background: #16213e;
    }
    .score-label { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: 1px; }
    .score-value { font-size: 2rem; font-weight: 800; }
    .score-a { color: #60a5fa; }
    .score-b { color: #34d399; }
    .score-c { color: #f472b6; }
    
    .stat-card {
        background: #16213e;
        border-radius: 10px;
        padding: 16px 20px;
        text-align: center;
    }
    .stat-value { font-size: 2.2rem; font-weight: 800; color: #e0e0e0; }
    .stat-label { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: 1px; }
    
    .system-status {
        background: #0d1117;
        border: 1px solid #21262d;
        border-radius: 10px;
        padding: 16px;
    }
    .status-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 6px 0;
        border-bottom: 1px solid #21262d;
        font-size: 0.9rem;
    }
    .status-row:last-child { border-bottom: none; }
    .status-ok { color: #3fb950; }
    .status-warn { color: #d29922; }
    .status-off { color: #666; }
    
    .assessment-text {
        font-size: 1.1rem;
        line-height: 1.8;
        color: #d0d0d0;
        background: #0d1117;
        border: 1px solid #21262d;
        border-radius: 10px;
        padding: 24px;
    }
    
    /* Hide streamlit menu & footer */
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Button overrides */
    .stButton > button {
        font-size: 1.1rem;
        padding: 12px 24px;
        border-radius: 8px;
        font-weight: 600;
    }
</style>
""", unsafe_allow_html=True)


# ── Header ──
st.markdown("# 📡 TrendX")

# ── Top metrics bar + system status ──
try:
    db = get_db()
    scan_stats = db.get_scan_stats()
    
    # Count opportunities by status
    all_opps = db.conn.execute("SELECT COUNT(*) as c FROM opportunities WHERE status != 'dismissed'").fetchone()
    unreviewed = db.conn.execute("""
        SELECT COUNT(*) as c FROM opportunities o 
        JOIN deliberations d ON o.id = d.opportunity_id
        LEFT JOIN reviews r ON o.id = r.opportunity_id 
        WHERE r.id IS NULL AND o.status NOT IN ('dismissed', 'acted_on')
    """).fetchone()
    affirmed = db.conn.execute("SELECT COUNT(*) as c FROM opportunities WHERE status = 'watching'").fetchone()
    dismissed = db.conn.execute("SELECT COUNT(*) as c FROM opportunities WHERE status = 'dismissed'").fetchone()
    total_signals = db.get_signal_count()
    total_classified = scan_stats.get("total_classified", 0) or 0
    total_relevant = scan_stats.get("total_relevant", 0) or 0
    total_cost = scan_stats.get("total_cost", 0) or 0
    last_scan = scan_stats.get("last_scan", "never")
    
    # Metrics row
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    with m1:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{all_opps["c"]}</div><div class="stat-label">Active Opportunities</div></div>', unsafe_allow_html=True)
    with m2:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{unreviewed["c"]}</div><div class="stat-label">Ready for Review</div></div>', unsafe_allow_html=True)
    with m3:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{affirmed["c"]}</div><div class="stat-label">Build Candidates</div></div>', unsafe_allow_html=True)
    with m4:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{total_signals}</div><div class="stat-label">Total Signals</div></div>', unsafe_allow_html=True)
    with m5:
        st.markdown(f'<div class="stat-card"><div class="stat-value">{total_relevant}</div><div class="stat-label">Relevant</div></div>', unsafe_allow_html=True)
    with m6:
        st.markdown(f'<div class="stat-card"><div class="stat-value">${total_cost:.2f}</div><div class="stat-label">Total Cost</div></div>', unsafe_allow_html=True)


    st.markdown("")
    
    # System status + controls row
    status_col, controls_col = st.columns([3, 1])
    
    with status_col:
        # Check source health
        last_scan_str = str(last_scan)[:19] if last_scan != "never" else "never"
        total_scans = scan_stats.get("total_scans", 0) or 0
        
        # Check which sources produced signals
        source_counts = db.conn.execute("SELECT source, COUNT(*) as c FROM raw_signals GROUP BY source").fetchall()
        source_map = {r["source"]: r["c"] for r in source_counts}
        
        sources_html = ""
        for src in ["reddit", "hackernews", "twitter", "google_trends", "youtube", "quora", "producthunt"]:
            count = source_map.get(src, 0)
            if count > 0:
                sources_html += f'<div class="status-row"><span>{src}</span><span class="status-ok">● {count} signals</span></div>'
            else:
                sources_html += f'<div class="status-row"><span>{src}</span><span class="status-off">○ no data</span></div>'
        
        st.markdown(f"""
        <div class="system-status">
            <div style="font-weight:700; margin-bottom:8px; color:#e0e0e0;">System Status</div>
            <div class="status-row"><span>Last scan</span><span class="{"status-ok" if last_scan != "never" else "status-off"}">{last_scan_str}</span></div>
            <div class="status-row"><span>Total scans</span><span>{total_scans}</span></div>
            <div class="status-row"><span>Classified</span><span>{total_classified} / {total_signals} signals</span></div>
            {sources_html}
        </div>
        """, unsafe_allow_html=True)
    
    with controls_col:
        # Daemon status + controls
        daemon_state_path = Path(__file__).parent / "data" / "daemon_state.json"
        daemon_state = {}
        if daemon_state_path.exists():
            try:
                daemon_state = json.loads(daemon_state_path.read_text())
            except Exception:
                pass
        
        d_status = daemon_state.get("status", "not running")
        if d_status == "sleeping":
            next_at = daemon_state.get("next_cycle_at", "")[:19].replace("T", " ")
            spend = daemon_state.get("daily_spend", 0)
            budget = daemon_state.get("daily_budget", 20)
            st.caption(f"🟢 Daemon sleeping · next: {next_at}")
            st.caption(f"${spend:.2f}/${budget:.0f} today · cycle #{daemon_state.get('cycle_number', '?')}")
        elif d_status == "running_cycle":
            st.caption(f"⏳ Daemon running cycle #{daemon_state.get('cycle_number', '?')}...")
        elif d_status == "stopped":
            st.caption("🔴 Daemon stopped")
        else:
            st.caption(f"⚪ Daemon: {d_status}")
        
        dc1, dc2 = st.columns(2)
        with dc1:
            if d_status in ("sleeping", "running_cycle"):
                if st.button("⏹ Stop", key="stop_daemon", use_container_width=True):
                    daemon_state_path.write_text(json.dumps({"command": "stop"}))
                    st.success("Stop signal sent")
                    st.rerun()
            else:
                st.caption("Run `trendx daemon --budget 20`")
        with dc2:
            if d_status == "sleeping":
                if st.button("▶ Run Now", key="run_now", use_container_width=True):
                    daemon_state["command"] = "run_now"
                    daemon_state_path.write_text(json.dumps(daemon_state))
                    st.success("Running next cycle now")
                    st.rerun()
        
        if st.button("📊 Re-score", use_container_width=True):
            with st.spinner("Scoring..."):
                result = subprocess.run(
                    [sys.executable, "-m", "trendx", "rescore"],
                    capture_output=True, text=True, cwd=str(Path(__file__).parent),
                )
                st.rerun()
        if st.button("🔄 Refresh", use_container_width=True):
            st.rerun()


    # ═══════════════════════════════════════════
    # MAIN REVIEW INTERFACE
    # ═══════════════════════════════════════════
    st.markdown("---")
    st.markdown("## Review Opportunities")
    
    unreviewed_opps = db.conn.execute("""
            SELECT o.* FROM opportunities o
            JOIN deliberations d ON o.id = d.opportunity_id
            LEFT JOIN reviews r ON o.id = r.opportunity_id
            WHERE r.id IS NULL AND o.status NOT IN ('dismissed', 'acted_on')
            ORDER BY MAX(o.score_path_a, o.score_path_b, o.score_path_c) DESC
            LIMIT 50
        """).fetchall()
    unreviewed_opps = [dict(r) for r in unreviewed_opps]
    
    if not unreviewed_opps:
        st.markdown('<div class="opp-card"><div class="opp-title">All caught up.</div><div class="opp-body">No unreviewed opportunities. Run a scan to find more.</div></div>', unsafe_allow_html=True)
    else:
        if "review_idx" not in st.session_state:
            st.session_state.review_idx = 0
        if "deliberation_cache" not in st.session_state:
            st.session_state.deliberation_cache = {}
        
        idx = st.session_state.review_idx
        if idx >= len(unreviewed_opps):
            st.success(f"Reviewed all {len(unreviewed_opps)} opportunities!")
            if st.button("Start over"):
                st.session_state.review_idx = 0
                st.rerun()
        else:
            opp = dict(unreviewed_opps[idx])
            opp_id = opp["id"]
            
            # Progress
            st.caption(f"Card {idx + 1} of {len(unreviewed_opps)}")
            st.progress((idx + 1) / len(unreviewed_opps))


            # ── Opportunity card ──
            # Badges
            badges_html = ""
            if opp.get("has_manual_workaround"):
                badges_html += '<span class="badge badge-workaround">🔧 Workaround</span>'
            if opp.get("has_unanswered_demand"):
                badges_html += '<span class="badge badge-unanswered">❓ Unanswered</span>'
            if opp.get("convergence_detected"):
                badges_html += f'<span class="badge badge-convergence">🔀 {opp.get("subreddit_count", 0)} communities</span>'
            if opp.get("has_new_community"):
                badges_html += '<span class="badge badge-new">🆕 New community</span>'
            if opp.get("is_timely"):
                badges_html += '<span class="badge badge-timely">⏰ Timely</span>'
            if opp.get("cross_source_confirmed"):
                badges_html += '<span class="badge badge-crosssrc">🌐 Cross-source</span>'
            
            st.markdown(f"""
            <div class="opp-card">
                <div class="opp-title">{opp.get("topic", "Unknown")}</div>
                <div class="opp-meta">{opp.get("category", "")} · {opp.get("signal_count", 0)} signals · intensity {opp.get("max_intensity", 0)}/5</div>
                <div style="margin-bottom: 16px;">{badges_html}</div>
            </div>
            """, unsafe_allow_html=True)
            
            # Scores + RICE
            s1, s2, s3, s4 = st.columns(4)
            with s1:
                st.markdown(f'<div class="score-box"><div class="score-label">Path A · Content</div><div class="score-value score-a">{opp.get("score_path_a", 0)}</div></div>', unsafe_allow_html=True)
            with s2:
                st.markdown(f'<div class="score-box"><div class="score-label">Path B · Product</div><div class="score-value score-b">{opp.get("score_path_b", 0)}</div></div>', unsafe_allow_html=True)
            with s3:
                st.markdown(f'<div class="score-box"><div class="score-label">Path C · Social</div><div class="score-value score-c">{opp.get("score_path_c", 0)}</div></div>', unsafe_allow_html=True)
            with s4:
                # Show RICE if available
                try:
                    rice_row = db.conn.execute(
                        "SELECT * FROM rice_scores WHERE opportunity_id = ?", (opp_id,)
                    ).fetchone()
                except Exception:
                    rice_row = None
                if rice_row and rice_row["buildable"]:
                    try:
                        ms = json.loads(rice_row.get("market_signals_json", "{}") or "{}")
                        sig_used = ms.get("signals_used", 0) if isinstance(ms, dict) else 0
                    except Exception:
                        sig_used = 0
                    mkt_label = "mkt✅" if sig_used >= 2 else "mkt⚠️" if sig_used == 1 else "mkt❌"
                    st.markdown(f'<div class="score-box"><div class="score-label">RICE · Est. ${rice_row["effort"]:.0f} · {mkt_label}</div><div class="score-value" style="color:#fbbf24;">{rice_row["rice_score"]:,}</div></div>', unsafe_allow_html=True)
                elif rice_row:
                    st.markdown(f'<div class="score-box"><div class="score-label">Auto-SDD</div><div class="score-value" style="color:#ef4444; font-size:1.2rem;">Not buildable</div></div>', unsafe_allow_html=True)
                else:
                    st.markdown(f'<div class="score-box"><div class="score-label">RICE</div><div class="score-value" style="color:#666;">pending</div></div>', unsafe_allow_html=True)


            # Details
            st.markdown("")
            
            if opp.get("product_angle"):
                st.markdown(f'<div class="opp-body">🛠️ <strong>Product angle:</strong> {opp["product_angle"]}</div>', unsafe_allow_html=True)
            
            if opp.get("social_hook"):
                st.markdown(f'<div class="opp-body">🪝 <strong>Hook:</strong> {opp["social_hook"]}</div>', unsafe_allow_html=True)
            
            wk = json.loads(opp.get("workaround_descriptions_json", "[]") or "[]")
            if wk:
                d = wk[0]
                st.markdown(f'<div class="opp-body">🔧 <strong>Current workaround:</strong> {d.get("method", "")} → <strong>Pain:</strong> {d.get("pain", "")}</div>', unsafe_allow_html=True)
            
            if opp.get("existing_solution") and opp["existing_solution"] not in ("none", "", "none identified"):
                st.markdown(f'<div class="opp-body">⚠️ <strong>Existing solution:</strong> {opp["existing_solution"]}</div>', unsafe_allow_html=True)
            
            if opp.get("is_timely") and opp.get("timely_context"):
                st.markdown(f'<div class="opp-body">⏰ <strong>Why now:</strong> {opp["timely_context"]}</div>', unsafe_allow_html=True)
            
            # Source links
            urls = json.loads(opp.get("source_urls_json", "[]") or "[]")
            if urls:
                with st.expander(f"📎 {len(urls)} source threads"):
                    for url in urls[:8]:
                        st.markdown(f"[{url}]({url})")


            # ── Assessment ──
            st.markdown("---")
            
            # Check for pre-computed deliberation from daemon
            precomputed = None
            try:
                row = db.conn.execute(
                    "SELECT assessment_text, cost FROM deliberations WHERE opportunity_id = ?",
                    (opp_id,),
                ).fetchone()
                if row:
                    precomputed = (row["assessment_text"], row["cost"])
            except Exception:
                pass
            
            if opp_id in st.session_state.deliberation_cache:
                assessment, cost = st.session_state.deliberation_cache[opp_id]
                st.markdown(f'<div class="assessment-text">{assessment}</div>', unsafe_allow_html=True)
                st.caption(f"Assessment cost: ${cost:.4f}")
            elif precomputed:
                assessment, cost = precomputed
                st.session_state.deliberation_cache[opp_id] = precomputed
                st.markdown(f'<div class="assessment-text">{assessment}</div>', unsafe_allow_html=True)
                st.caption(f"Assessment cost: ${cost:.4f} (pre-computed by daemon)")
            else:
                if st.button("🧠 Generate Assessment", use_container_width=True, type="primary"):
                    with st.spinner("Analyzing opportunity..."):
                        from trendx.deliberate.deliberator import Deliberator
                        config = load_config(CONFIG_PATH)
                        deliberator = Deliberator(config.anthropic)
                        assessment = deliberator.deliberate(opp)
                        if assessment:
                            st.session_state.deliberation_cache[opp_id] = (assessment, deliberator.total_cost)
                            st.rerun()
                        else:
                            st.error("Assessment generation failed")
            
            # ── Decision buttons ──
            st.markdown("---")
            b1, b2, b3 = st.columns([2, 2, 1])
            with b1:
                if st.button("✅  Yes — keep tracking", use_container_width=True, type="primary"):
                    delib = st.session_state.deliberation_cache.get(opp_id, ("", 0))
                    db.save_review(opp_id, "interesting", delib[0], delib[1])
                    st.session_state.review_idx += 1
                    st.rerun()
            with b2:
                if st.button("❌  No — stop tracking", use_container_width=True):
                    delib = st.session_state.deliberation_cache.get(opp_id, ("", 0))
                    db.save_review(opp_id, "pass", delib[0], delib[1])
                    st.session_state.review_idx += 1
                    st.rerun()
            with b3:
                if st.button("⏭️  Skip", use_container_width=True):
                    st.session_state.review_idx += 1
                    st.rerun()


    # ── Review history ──
    st.markdown("---")
    
    # ── Build Candidate Portfolio ──
    candidates = db.get_build_candidates(status="affirmed")
    
    # RICE Rankings — the primary view of what to build
    try:
        rice_ranked = db.get_rice_rankings(buildable_only=True, limit=10)
    except Exception:
        rice_ranked = []
    if rice_ranked:
        st.markdown("### 🏆 RICE Stack Rank")
        st.caption("Buildable by Auto-SDD for under $200 — ranked by (Reach × Impact × Confidence) / Effort")
        
        for rank, r in enumerate(rice_ranked, 1):
            with st.container(border=True):
                rc1, rc2, rc3, rc4, rc5, rc6 = st.columns([0.5, 3, 1, 1, 1.5, 1.5])
                with rc1:
                    st.markdown(f"**#{rank}**")
                with rc2:
                    st.markdown(f"**{r.get('topic', '')[:60]}**")
                    st.caption(f"{r.get('category', '')} · {r.get('signal_count', 0)} signals")
                with rc3:
                    st.metric("RICE", f"{r.get('rice_score', 0):,}")
                with rc4:
                    st.metric("Est. Cost", f"${r.get('effort', 0):.0f}")
                with rc5:
                    reason = r.get('complexity_reason', '')[:50]
                    mr = r.get('measured_reach', 0) or 0
                    mkr = r.get('market_reach', 0) or 0
                    market_json = r.get('market_signals_json', '{}') or '{}'
                    try:
                        ms = json.loads(market_json) if isinstance(market_json, str) else market_json
                        signals_used = ms.get('signals_used', 0) if isinstance(ms, dict) else 0
                    except Exception:
                        signals_used = 0
                    market_tag = f"✅ {signals_used} signals" if signals_used >= 2 else "⚠️ limited" if signals_used == 1 else "❌ none"
                    st.caption(f"R:{r.get('reach', 0):.0f} (m:{mr:.0f}+mkt:{mkr:.0f} [{market_tag}])")
                    st.caption(f"I:{r.get('impact', 0):.0f} C:{r.get('confidence', 0):.0f} · {reason}")
                with rc6:
                    if st.button("🚀 Build", key=f"build_{r.get('id', rank)}", use_container_width=True, type="primary"):
                        st.session_state[f"build_target_{r.get('id')}"] = True
                        st.rerun()
            
            # Build modal — show generated prompt and trigger
            if st.session_state.get(f"build_target_{r.get('id')}"):
                opp_id = r.get("id", "")
                from trendx.build.vision_prompt import generate_vision_prompt
                
                # Get deliberation text
                delib_row = db.conn.execute(
                    "SELECT assessment_text FROM deliberations WHERE opportunity_id = ?",
                    (opp_id,),
                ).fetchone()
                delib_text = delib_row["assessment_text"] if delib_row else ""
                
                rice_data = {
                    "estimated_cost_usd": r.get("effort", 100),
                }
                
                opp_dict = dict(r)
                prompt = generate_vision_prompt(opp_dict, delib_text, rice_data)
                
                st.markdown("---")
                st.markdown("### 🚀 Auto-SDD Vision Prompt")
                st.code(prompt, language="markdown")
                
                # Save prompt to file
                prompt_path = Path(__file__).parent / "data" / "build_prompts" / f"{opp_id}.md"
                prompt_path.parent.mkdir(parents=True, exist_ok=True)
                prompt_path.write_text(prompt)
                st.caption(f"Saved to: {prompt_path}")
                
                bc1, bc2, bc3 = st.columns(3)
                with bc1:
                    if st.button("🔨 Launch Pre-Build", key=f"launch_{opp_id}", use_container_width=True, type="primary"):
                        # Update build candidate status
                        cand = db.conn.execute(
                            "SELECT id FROM build_candidates WHERE opportunity_id = ?",
                            (opp_id,),
                        ).fetchone()
                        if cand:
                            db.update_build_candidate(cand["id"], status="building", built_at=datetime.utcnow().isoformat())
                        else:
                            db.create_build_candidate(opp_id)
                        
                        # Launch Auto-SDD pre-build
                        result = subprocess.run(
                            [sys.executable, "-m", "auto_sdd", "pre-build",
                             "--input", str(prompt_path),
                             "--model-config", "config/models/claude-sonnet.yaml"],
                            capture_output=True, text=True,
                            cwd="/Users/brianfischman/Auto_SDD_v2",
                        )
                        if result.returncode == 0:
                            st.success("Pre-build complete!")
                        else:
                            st.error(f"Pre-build failed:\n{result.stderr[-500:]}")
                        st.text(result.stdout[-500:] if result.stdout else "")
                with bc2:
                    st.code(f"cd ~/Auto_SDD_v2 && python -m auto_sdd pre-build --input {prompt_path}", language="bash")
                with bc3:
                    if st.button("Cancel", key=f"cancel_{opp_id}"):
                        del st.session_state[f"build_target_{r.get('id')}"]
                        st.rerun()
        st.markdown("")

    if candidates:
        st.markdown("### 🏗️ Build Candidates")
        st.caption(f"{len(candidates)} affirmed — ranked by comparative analysis each cycle")
        
        # Show latest comparative ranking
        ranking = db.get_latest_comparative_ranking()
        if ranking:
            st.markdown(f'<div class="assessment-text">{ranking["ranking_text"]}</div>', unsafe_allow_html=True)
            st.caption(f"Last ranked: {ranking['created_at'][:16]} · Cost: ${ranking.get('cost', 0):.4f}")
        
        st.markdown("")
        for cand in candidates:
            with st.container(border=True):
                cc1, cc2 = st.columns([3, 1])
                with cc1:
                    st.markdown(f"**{cand.get('topic', 'Unknown')}**")
                    st.caption(f"{cand.get('category', '')} · A:{cand.get('score_path_a', 0)} B:{cand.get('score_path_b', 0)} C:{cand.get('score_path_c', 0)} · Affirmed: {str(cand.get('affirmed_at', ''))[:10]}")
                    if cand.get("vercel_url"):
                        st.markdown(f"🔗 [{cand['vercel_url']}]({cand['vercel_url']})")
                with cc2:
                    status = cand.get("status", "affirmed")
                    if status == "affirmed":
                        st.markdown("⏳ Watching")
                    elif status == "building":
                        st.markdown("🔨 Building")
                    elif status == "deployed":
                        st.markdown("🚀 Live")
        
        st.markdown("")
    
    # Recent review decisions
    reviews = db.get_reviews(limit=10)
    if reviews:
        with st.expander(f"Recent decisions ({len(reviews)})"):
            for rev in reviews:
                emoji = "✅" if rev["judgment"] == "interesting" else "❌"
                st.markdown(f'{emoji} **{rev.get("topic", "")}** — {rev.get("category", "")} — {rev["created_at"][:16]}')
    
    db.close()

except Exception as e:
    st.error(f"Error: {e}")
    import traceback
    st.code(traceback.format_exc())
