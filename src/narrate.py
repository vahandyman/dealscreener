"""LLM narration: RuleTrace -> two lines of analyst-readable prose.

The second of exactly two LLM steps. It receives ONLY the trace (plus the
discrepancy list, which is part of the audit output) — never the raw
package — so the explanation must follow the decision and cannot drift
from the rule that actually fired.
"""

from __future__ import annotations

import anthropic

from src.extract import load_extraction_config
from src.schema import Discrepancy, RuleTrace

SYSTEM_PROMPT = """\
You write queue annotations for loan screening analysts. You receive the
machine-readable trace of a deterministic rule evaluation (criteria tested,
values, thresholds, statuses, discrepancies). The decision is already made —
you explain it, you never second-guess or embellish it.

Write EXACTLY one sentence, at most 22 words, no markdown: the specific
rule fact that drove the outcome, then what would change it (the document
that closes the gap, the line a near-miss sits against, or "nothing — hard
criterion" for declines). Example: "LTV 82% sits just over the 80% line;
a higher appraisal or smaller request clears it."

Only state facts present in the trace. No speculation about the property,
sponsor, or market.
"""


def narrate_trace(
    trace: RuleTrace,
    discrepancies: list[Discrepancy] | None = None,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> str:
    cfg = load_extraction_config()
    model = model or cfg.get("narration_model", cfg["model"])
    client = client or anthropic.Anthropic()

    payload = trace.model_dump_json()
    if discrepancies:
        payload += "\n\nDiscrepancies:\n" + "\n".join(d.model_dump_json() for d in discrepancies)

    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": payload}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()
