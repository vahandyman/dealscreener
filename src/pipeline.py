"""Orchestration: extract -> derive/rules -> store -> rank (-> narrate).

Extraction is the only step that needs the package text. Rescreening runs
entirely from stored extractions — deterministic code only — so a YAML
threshold change reorders the queue in seconds, no LLM calls.
"""

from __future__ import annotations

from pathlib import Path

from src.extract import extract_file
from src.narrate import narrate_trace
from src.rank import QueueEntry, rank_queue
from src.rules import evaluate, load_lending_box
from src.store import Store


def rank_all(store: Store) -> list[QueueEntry]:
    queue = rank_queue(store.all_latest())
    for entry in queue:
        store.update_rank(entry.deal_id, entry.rank, entry.score, entry.rank_reason)
    return queue


def screen_paths(
    paths: list[str | Path],
    store: Store | None = None,
    narrate: bool = True,
) -> list[QueueEntry]:
    """Full pipeline over new package files: LLM extraction included."""
    store = store or Store()
    box = load_lending_box()
    for path in paths:
        deal = extract_file(path)
        result = evaluate(deal, box)
        narration = narrate_trace(result.trace, result.discrepancies) if narrate else None
        store.record_run(deal, result, narration=narration)
    return rank_all(store)


def rescreen_all(store: Store | None = None, narrate: bool = False) -> list[QueueEntry]:
    """Re-evaluate every stored deal against the CURRENT config.

    No extraction — stored facts are reused, so this is pure Python end to
    end (narration optional) and completes in seconds.
    """
    store = store or Store()
    box = load_lending_box()
    for deal_id in store.all_ids():
        deal = store.latest_extracted(deal_id)
        result = evaluate(deal, box)
        narration = narrate_trace(result.trace, result.discrepancies) if narrate else None
        store.record_run(deal, result, narration=narration)
    return rank_all(store)
