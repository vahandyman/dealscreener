"""Streamlit dashboard over the screening pipeline.

Layout: upload lives in the sidebar; dropping .md packages screens them
live (LLM extraction -> deterministic rules -> one-line narration). The
queue is a list of expandable rows — the narration sits inside each row,
"See more" opens the full audit detail below the queue.

No business logic lives here: rules, ranking, and persistence are the
pipeline's. The dashboard only invokes it and renders what it wrote.

Run:  streamlit run app.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.narrate import narrate_trace
from src.pipeline import rank_all, rescreen_all
from src.rules import evaluate, load_lending_box
from src.rank import load_ranking_config
from src.store import Store

APP_DIR = Path(__file__).resolve().parent
CONFIG_DIR = APP_DIR / "config"
DEALS_DIR = APP_DIR / "data" / "deals"
RUNS_DIR = APP_DIR / "data" / "runs"

st.set_page_config(page_title="Deal Screening Queue", layout="wide")
store = Store()
st.session_state.setdefault("processed_files", set())
st.session_state.setdefault("detail_deal", None)

STATUS_ICON = {"pass": "✓", "fail": "✗", "not_evaluable": "?", "skipped": "·"}
OUTCOME_BADGE = {"advance": "🟢 advance", "needs_review": "🟡 needs review", "decline": "🔴 decline"}
WEIGHT_LABELS = {
    "distance_from_box": ("Distance from box", "#4c78a8"),
    "resolvability": ("Resolvability", "#72b7b2"),
    "discrepancy": ("Discrepancy", "#e45756"),
    "time_in_queue": ("Time in queue", "#f2a057"),
    "loan_size": ("Loan size", "#b279a2"),
}


def field_val(group: dict, name: str):
    f = group.get(name) or {}
    return f.get("value")


# --- Sidebar: upload & screen, lending box, reset --------------------------

with st.sidebar:
    st.header("Add packages")
    do_narrate = st.toggle("Narrate rows (✱ LLM)", value=True,
                           help="One-line narration written from the rule trace only.")
    uploaded = st.file_uploader(
        "Drop package files (.md) — screening runs on upload",
        type=["md"], accept_multiple_files=True,
    )
    new_files = [u for u in (uploaded or []) if u.name not in st.session_state.processed_files]

    box_text = (CONFIG_DIR / "lending_box.yaml").read_text()
    box_version = yaml.safe_load(box_text)["version"]
    st.header("Lending box")
    st.caption(f"version **{box_version}** — rules are config, not code")
    with st.expander("show lending_box.yaml"):
        st.code(box_text, language="yaml")

    st.divider()
    if st.button("Reset demo (clear all deals)"):
        for p in list(store.root.glob("*.json")) + list(DEALS_DIR.glob("*.md")) + list(RUNS_DIR.glob("*.json")):
            p.unlink()
        st.session_state.processed_files = set()
        st.session_state.detail_deal = None
        st.rerun()

# --- Header ----------------------------------------------------------------

st.title("Deal screening queue")
st.caption(
    "First-pass screen against the lending box. Outcomes are **suggestions** "
    "with a full rule trace — an analyst owns every decision. Facts marked ✱ "
    "are LLM-extracted with provenance; every outcome, metric, and rank is "
    "deterministic code."
)

# --- Live screening of newly uploaded packages ----------------------------

def screen_uploads(files) -> None:
    """Save uploaded packages and run the live pipeline on each."""
    from src.extract import extract_file  # deferred import: needs API key
    box = load_lending_box()
    for up in files:
        path = DEALS_DIR / up.name
        path.write_bytes(up.getvalue())
        with st.status(f"screening {up.name}…", expanded=True) as status:
            st.write("✱ extracting facts (LLM)…")
            deal = extract_file(path)
            st.write("deriving metrics + applying rules (deterministic)…")
            result = evaluate(deal, box)
            narration = None
            if do_narrate:
                st.write("✱ narrating trace (LLM)…")
                narration = narrate_trace(result.trace, result.discrepancies)
            rec = store.record_run(deal, result, narration=narration)
            status.update(
                label=f"{up.name} → {rec['deal_id']}: {result.trace.outcome.value}",
                state="complete", expanded=False,
            )
        st.session_state.processed_files.add(up.name)
    rank_all(store)


if new_files:
    screen_uploads(new_files)

# --- Queue header: filters (left) + rescreen (right), weights bar ---------

left, _, right = st.columns([1, 3, 1], vertical_alignment="bottom")
with left:
    choice = st.selectbox("Suggested outcome", ["all", "advance", "needs_review", "decline"])
    outcome_filter = ["advance", "needs_review", "decline"] if choice == "all" else [choice]
with right:
    if st.button("Rescreen all", type="primary", width="stretch",
                 help="Re-evaluate every stored deal against the current YAML. "
                      "No LLM — cached extractions, pure Python, sub-second."):
        rescreen_all(store=store, narrate=False)
        st.toast(f"rescreened against box {box_version}")

# weights: how the queue is ordered, as small pills
rank_cfg = load_ranking_config()
weights = {k: v for k, v in rank_cfg["weights"].items() if v > 0}
pills_html = "".join(
    "<span style='display:inline-flex;align-items:center;gap:5px;"
    "background:rgba(128,128,128,0.14);border-radius:999px;padding:2px 10px;"
    "font-size:0.74rem;margin:0 6px 4px 0;white-space:nowrap;'>"
    f"<span style='width:8px;height:8px;border-radius:50%;background:{WEIGHT_LABELS[k][1]};'></span>"
    f"{WEIGHT_LABELS[k][0]} {v:.0%}</span>"
    for k, v in weights.items()
)
st.markdown(
    "<div style='margin:0.5rem 0 0.35rem 0;'>"
    "<span style='font-size:0.875rem;margin-right:8px;'>Ranking weights:</span>"
    f"{pills_html}</div>",
    unsafe_allow_html=True,
)
st.caption("Queue order within a tier is the weighted blend above.")

# --- Queue rows ------------------------------------------------------------

st.subheader(
    "Deal queue",
    help="Trace glyphs per row: asset · exclusions · market │ size · ltv · dscr · "
         "occupancy · net-worth · liquidity — ✓ pass ✗ fail ? not evaluable · skipped",
)

entries = []
for deal_id in store.all_ids():
    rec = store.get(deal_id)
    if rec["runs"]:
        entries.append((rec, rec["runs"][-1]))
entries.sort(key=lambda t: (t[1].get("rank") or 999))

if not entries:
    st.caption("No deals in queue — upload package files (.md) to see screening suggestions.")
    # Strip the uploader down to its button: no full-width dropzone chrome.
    st.markdown(
        """<style>
        .st-key-queue_uploader [data-testid="stFileUploaderDropzone"] {
            background: none; border: none; padding: 0; min-height: 0;
            width: fit-content; justify-content: flex-start;
        }
        .st-key-queue_uploader [data-testid="stFileUploaderDropzoneInstructions"] {
            display: none;
        }
        </style>""",
        unsafe_allow_html=True,
    )
    more = st.file_uploader(
        "Upload deals", type=["md"], accept_multiple_files=True,
        key="queue_uploader", label_visibility="collapsed",
    )
    new_here = [u for u in (more or []) if u.name not in st.session_state.processed_files]
    if new_here:
        screen_uploads(new_here)
        st.rerun()
    st.stop()

for rec, run in entries:
    deal_id = rec["deal_id"]
    deal = run["screen"]["deal"]
    trace = run["screen"]["trace"]
    outcome = run["outcome"]
    if outcome not in outcome_filter:
        continue

    prop = field_val(deal["property"], "address") or "address n/a"
    amount = field_val(deal["loan"], "requested_amount")
    amount_s = f"\\${amount/1e6:,.1f}M" if isinstance(amount, (int, float)) else "$ n/a"
    glyphs = "".join(STATUS_ICON.get(r["status"], "·") for r in trace["results"][:3]) + "│" + \
             "".join(STATUS_ICON.get(r["status"], "·") for r in trace["results"][3:])
    label = (
        f"**#{run.get('rank')}** · **{deal_id}** · {prop} · {amount_s} · "
        f"{OUTCOME_BADGE.get(outcome, outcome)} · `{glyphs}`"
    )

    with st.expander(label, expanded=False):
        narration = next(
            (r["narration"] for r in reversed(rec["runs"]) if r.get("narration")), None
        )
        st.write((narration or run.get("rank_reason") or "").replace("$", "\\$"))
        if narration:
            st.caption("✱ LLM narration, written from the rule trace only — plus: "
                       f"{run.get('rank_reason')}")
        for flag in trace.get("correlated_flags", []):
            st.warning(flag)
        if st.button("See more", key=f"more-{deal_id}"):
            st.session_state.detail_deal = deal_id

# --- Full audit detail (opens via "See more") ------------------------------

selected = st.session_state.detail_deal
if selected and selected in store.all_ids():
    rec = store.get(selected)
    run = rec["runs"][-1]
    screen = run["screen"]
    trace = screen["trace"]

    st.divider()
    head, close = st.columns([5, 1], vertical_alignment="center")
    with head:
        st.subheader(f"{selected} — suggested: {run['outcome'].upper()}")
        st.caption(
            f"box {run['box_version']} · run {run['run_at']} · rank #{run.get('rank')} · "
            "screen suggestion, not a credit decision"
        )
    with close:
        if st.button("Close", width="stretch"):
            st.session_state.detail_deal = None
            st.rerun()

    st.markdown("**Rule trace** — the audit artifact")
    st.dataframe(
        pd.DataFrame([{
            "criterion": r["criterion"],
            "enforcement": r["enforcement"],
            "status": r["status"],
            "value": str(r["value"]),
            "threshold": str(r["threshold"]),
            "note": r.get("note") or "",
        } for r in trace["results"]]),
        hide_index=True, width="stretch",
    )

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Derived metrics** (computed by code, inputs named)")
        derived = screen.get("derived")
        if derived:
            st.dataframe(
                pd.DataFrame([{
                    "metric": m["name"],
                    "value": round(m["value"], 4) if m.get("computable") else None,
                    "computable": m.get("computable"),
                    "reason / inputs": m.get("reason") or ", ".join(m.get("inputs_used", [])),
                } for m in [derived["ltv"], derived["dscr"], derived["occupancy_physical"],
                            derived["occupancy_paying_only"], derived["liquidity_months"]]]),
                hide_index=True, width="stretch",
            )
        else:
            st.caption("not computed — hard decline fired before derivation")
    with col2:
        st.markdown("**Discrepancies** (stated vs derived — surfaced, never averaged)")
        discs = screen.get("discrepancies", [])
        if discs:
            st.dataframe(
                pd.DataFrame([{
                    "field": d["field_name"], "severity": d["severity"],
                    "stated": d["stated_value"], "derived": d["derived_value"],
                    "note": d.get("note") or "",
                } for d in discs]),
                hide_index=True, width="stretch",
            )
        else:
            st.caption("none")

