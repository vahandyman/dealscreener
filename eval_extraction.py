"""Per-field extraction accuracy against the hand-labeled answer key.

  python eval_extraction.py                 grade cached data/runs/*.extracted.json
  python eval_extraction.py --fresh         re-extract each package first (LLM calls)
  python eval_extraction.py --fresh --model claude-haiku-4-5    compare a cheaper model

Accuracy is per field, not per deal — a deal can route correctly for the
wrong reason. Model choice is a measured decision: run with --model to
compare candidates on the same key.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))

from src.schema import ExtractedDeal

KEY_PATH = Path(__file__).parent / "tests" / "extraction_answer_key.yaml"
REL_TOL = 1e-3


def get_field(deal: ExtractedDeal, path: str):
    if path.startswith("claims."):
        metric = path.split(".", 1)[1]
        vals = [c.value for c in deal.broker_claims if c.metric == metric]
        return vals[0] if vals else None
    group_name, field_name = path.split(".", 1)
    return getattr(getattr(deal, group_name), field_name).value


def matches(got, expected) -> bool:
    if isinstance(expected, list):
        return any(matches(got, e) for e in expected)
    if expected is None:
        return got is None
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        if not isinstance(got, (int, float)):
            return False
        return abs(got - expected) <= REL_TOL * max(abs(expected), 1)
    return isinstance(got, str) and got.strip().lower() == str(expected).strip().lower()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fresh", action="store_true", help="re-extract packages (LLM)")
    parser.add_argument("--model", default=None, help="override extraction model")
    args = parser.parse_args()

    key = yaml.safe_load(KEY_PATH.read_text())
    per_field: dict[str, list[bool]] = defaultdict(list)
    misses: list[str] = []
    total = correct = 0

    for deal_id, fields in key.items():
        stem = deal_id.lower()
        if args.fresh:
            from src.extract import extract_file
            deal = extract_file(f"data/deals/{stem}.md", model=args.model)
            Path(f"data/runs/{stem}.extracted.json").write_text(deal.model_dump_json(indent=2))
        else:
            p = Path(f"data/runs/{stem}.extracted.json")
            if not p.exists():
                print(f"{deal_id}: no cached extraction ({p}); run with --fresh")
                continue
            deal = ExtractedDeal.model_validate_json(p.read_text())

        for path, expected in fields.items():
            got = get_field(deal, path)
            ok = matches(got, expected)
            per_field[path].append(ok)
            total += 1
            correct += ok
            if not ok:
                misses.append(f"  {deal_id} {path}: expected {expected!r}, got {got!r}")

    print(f"\noverall field accuracy: {correct}/{total} = {correct/total:.1%}\n")
    print(f"{'field':<40}{'accuracy'}")
    print("-" * 55)
    for path in sorted(per_field, key=lambda p: (sum(per_field[p]) / len(per_field[p]), p)):
        oks = per_field[path]
        print(f"{path:<40}{sum(oks)}/{len(oks)}")
    if misses:
        print("\nmisses:")
        print("\n".join(misses))


if __name__ == "__main__":
    main()
