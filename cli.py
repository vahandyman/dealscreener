"""Deal screening CLI.

  python cli.py screen data/deals/*.md    run pipeline, persist, print queue
  python cli.py show DEAL-003             full trace for one deal
  python cli.py history DEAL-003          status log
  python cli.py rescreen --all            re-run against current config (no LLM)
  python cli.py queue                     print current queue without re-running
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.pipeline import rank_all, rescreen_all, screen_paths
from src.store import Store


def _fmt_money(v) -> str:
    return f"${v:,.0f}" if isinstance(v, (int, float)) else "—"


def print_queue(queue, store: Store) -> None:
    print(f"\n{'#':<4}{'deal':<12}{'outcome':<15}{'requested':<14}rank_reason")
    print("-" * 100)
    for e in queue:
        deal = store.latest_extracted(e.deal_id)
        amount = _fmt_money(deal.loan.requested_amount.value)
        print(f"{e.rank:<4}{e.deal_id:<12}{e.outcome.value:<15}{amount:<14}{e.rank_reason}")
    print()


def cmd_screen(args) -> None:
    store = Store()
    queue = screen_paths(args.paths, store=store, narrate=not args.no_narrate)
    print_queue(queue, store)


def cmd_rescreen(args) -> None:
    store = Store()
    queue = rescreen_all(store=store, narrate=args.narrate)
    print_queue(queue, store)


def cmd_queue(args) -> None:
    store = Store()
    print_queue(rank_all(store), store)


def cmd_show(args) -> None:
    store = Store()
    run = store.latest_run(args.deal_id)
    result = store.latest_screen(args.deal_id)
    t = result.trace
    print(f"\n{args.deal_id} — {t.outcome.value.upper()}  (box {t.box_version}, run {run['run_at']})")
    if run.get("rank"):
        print(f"rank #{run['rank']}: {run['rank_reason']}")
    if run.get("narration"):
        print(f"\n{run['narration']}")
    print(f"\n{'criterion':<22}{'enf':<6}{'status':<15}{'value':<40}threshold")
    print("-" * 110)
    for c in t.results:
        note = f"   <- {c.note}" if c.note else ""
        print(f"{c.criterion:<22}{c.enforcement[:4]:<6}{c.status.value:<15}{str(c.value)[:38]:<40}{str(c.threshold)[:30]}{note}")
    if result.derived:
        print("\nderived metrics:")
        for m in (result.derived.ltv, result.derived.dscr, result.derived.occupancy_physical,
                  result.derived.occupancy_paying_only, result.derived.liquidity_months):
            val = f"{m.value:.4f}" if m.computable else f"not computable — {m.reason}"
            print(f"  {m.name:<26}{val}   [inputs: {', '.join(m.inputs_used)}]")
    for d in result.discrepancies:
        print(f"\ndiscrepancy [{d.severity}] {d.field_name}: stated {d.stated_value} vs derived {d.derived_value} ({d.note})")
    for f in t.correlated_flags:
        print(f"\nFLAG: {f}")
    print()


def cmd_history(args) -> None:
    record = Store().get(args.deal_id)
    print(f"\n{args.deal_id} — received {record['received_at']}, {len(record['runs'])} run(s)")
    print("\nstatus log:")
    for s in record["status_log"]:
        print(f"  {s['at']}  {s['from'] or '(new)':<14} -> {s['to']:<14} (box {s['box_version']})")
    print("\nruns:")
    for r in record["runs"]:
        rank = f"rank #{r['rank']}" if r.get("rank") else "unranked"
        print(f"  {r['run_at']}  box {r['box_version']}  {r['outcome']:<14} {rank}")
    print()


def main() -> None:
    parser = argparse.ArgumentParser(description="Deal screening pipeline")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("screen", help="extract + screen package files, print queue")
    p.add_argument("paths", nargs="+")
    p.add_argument("--no-narrate", action="store_true", help="skip LLM narration")
    p.set_defaults(func=cmd_screen)

    p = sub.add_parser("rescreen", help="re-evaluate stored deals against current config")
    p.add_argument("--all", action="store_true", required=True)
    p.add_argument("--narrate", action="store_true", help="also regenerate narration (LLM)")
    p.set_defaults(func=cmd_rescreen)

    p = sub.add_parser("queue", help="print current queue")
    p.set_defaults(func=cmd_queue)

    p = sub.add_parser("show", help="full trace for one deal")
    p.add_argument("deal_id")
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("history", help="status log for one deal")
    p.add_argument("deal_id")
    p.set_defaults(func=cmd_history)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
