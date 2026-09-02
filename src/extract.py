"""LLM extraction: unstructured package text -> typed ExtractedDeal.

This is one of exactly two LLM steps in the pipeline (the other is
narration). The model extracts what the package STATES — it never computes
a metric, never reads the rules, never decides an outcome. Responses are
validated against the Pydantic schema at this boundary, so malformed or
schema-violating JSON cannot enter the pipeline.
"""

from __future__ import annotations

from pathlib import Path

import anthropic
import yaml
from pydantic import ValidationError

from src.schema import ExtractedDeal

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "extraction.yaml"

SYSTEM_PROMPT = """\
You extract facts from inbound commercial multifamily loan packages into a
typed schema for a deterministic screening pipeline. You are a transcriber,
not an analyst: downstream code computes every metric and applies every rule.

Rules:

1. Extract ONLY what the package states. Never calculate, estimate, or fill
   in a value the text does not contain.
2. Every field is a wrapper: `value`, `source` (a short quote or location in
   the package, e.g. "Rent roll snapshot: '232 occupied'"), `as_of` (the
   DOCUMENT's date if stated, ISO format), and `missing_reason`.
3. If a value is absent, set value to null and `missing_reason` to a specific
   explanation from the package when it gives one (e.g. "T-12 to follow",
   "appraisal ordered, not yet received"), otherwise "not stated in package".
   NEVER set value to null without a missing_reason.
4. LTV and DSCR are NOT facts. If the package states or characterizes them
   ("requested LTV: 71%", "conservatively leveraged at roughly 75%",
   "healthy coverage around 1.30x"), record them ONLY in `broker_claims`
   as numbers (0.75, 1.30) with the quote as source. They go nowhere else.
5. Occupancy buckets are distinct — do not merge them:
   - units_occupied_paying: occupied AND paying rent
   - units_occupied_nonpaying: occupied but NOT paying (down units, renovation
     units with no rent, delinquent/comped units)
   - units_vacant: vacant
   - units_occupied_total: the rent roll's plain occupied count when it does
     NOT split paying from nonpaying (the usual case). When only this exists,
     leave paying/nonpaying missing — never assume all occupied units pay.
   `stated_occupancy` is the sponsor's/broker's headline occupancy number
   (as a fraction, e.g. 0.96) — record it even when the rent roll disagrees;
   do not reconcile the two.
6. `economic_occupancy` is the collections-based figure from the operating
   statement, distinct from physical occupancy.
7. NOI belongs in operations.noi ONLY if stated. `trailing_period` records
   what period the operating figures cover (e.g. "T-12") — if the figures are
   sponsor estimates or pro forma rather than a trailing statement, say so
   there or in the field's missing_reason.
8. `sponsor_name` and `borrowing_entity` are separate: the borrowing entity
   is usually a single-purpose LLC; the sponsor is the principal behind it.
9. use_mix: list every use in the property with its share if stated
   (e.g. hotel keys vs residential units). A property can be more than one thing.
10. `asset_type` and each use_mix `use` must be one of this controlled
    vocabulary (pick by what the property actually is; keep the package's own
    wording in `source`): multifamily, hospitality, senior_living,
    assisted_living, student_housing, raw_land, single_family, office,
    retail, industrial, mixed_use, other. Do not let a broker's framing
    relabel a use (a hotel is hospitality no matter how it is described).
11. `msa` should be "City, ST" (e.g. "Columbus, OH") — take the state from
    the address if the MSA name doesn't include it.
12. documents_present / documents_missing: infer from what the package
    includes and what it says is outstanding.
13. Dollar amounts as plain numbers (32000000, not "$32M"). Rates and
    occupancies as fractions (0.71, 0.94). Dates as ISO strings.
"""


def load_extraction_config() -> dict:
    with open(CONFIG_PATH) as f:
        return yaml.safe_load(f)


def extract_deal(
    package_text: str,
    model: str | None = None,
    client: anthropic.Anthropic | None = None,
) -> ExtractedDeal:
    cfg = load_extraction_config()
    model = model or cfg["model"]
    client = client or anthropic.Anthropic()

    messages = [{
        "role": "user",
        "content": f"Extract the deal package below into the schema.\n\n<package>\n{package_text}\n</package>",
    }]

    # NOTE: API-side strict structured outputs (messages.parse) rejects this
    # schema — ~40 nested wrapper objects compile to an oversized grammar
    # ("The compiled grammar is too large"). So the schema is sent in the
    # prompt and validation happens here with Pydantic instead: the guarantee
    # that only schema-valid data enters the pipeline is unchanged, it is
    # just enforced at this boundary rather than during decoding.
    import json

    schema_json = json.dumps(ExtractedDeal.model_json_schema())
    system = (
        SYSTEM_PROMPT
        + "\n\nRespond with ONLY a JSON object valid against this JSON Schema"
        + " — no prose, no markdown fences:\n"
        + schema_json
    )

    attempts = cfg.get("max_retries_on_validation_error", 1) + 1
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=cfg.get("max_tokens", 16000),
                system=system,
                messages=messages,
            )
            text = "".join(b.text for b in response.content if b.type == "text").strip()
            if text.startswith("```"):
                text = text.strip("`\n")
                text = text.split("\n", 1)[1] if text.startswith("json") else text
            return ExtractedDeal.model_validate_json(text)
        except (ValidationError, ValueError) as e:
            last_err = e
            # Feed the validation error back once; schema validators (e.g.
            # value=None requires missing_reason) aren't expressible in JSON
            # Schema, so the model can violate them on a first pass.
            messages = messages + [
                {"role": "user", "content": f"Your previous output failed schema validation:\n{e}\nReturn a corrected extraction of the same package."},
            ]
    raise RuntimeError(f"extraction failed validation after {attempts} attempts: {last_err}")


def extract_file(path: str | Path, model: str | None = None) -> ExtractedDeal:
    return extract_deal(Path(path).read_text(), model=model)
