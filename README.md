# Deal Screening Triage Pipeline

Screens inbound commercial multifamily loan packages against a lending box
and orders a review queue. Deterministic pipeline with exactly two LLM steps:

```
package text --[LLM: extract]--> ExtractedDeal (typed facts + provenance)
             --[code: derive]--> LTV, DSCR, occupancy, liquidity months
             --[code: rules ]--> advance | decline | needs_review + RuleTrace
             --[code: rank  ]--> queue order + rank_reason
             --[LLM: narrate]--> two lines of prose FROM THE TRACE ONLY
```

The LLM never decides an outcome, computes a metric, or reads the rules.
It extracts, and it writes. Everything between is deterministic and testable.

## Setup

```
uv venv --python 3.12 .venv
uv pip install pydantic pyyaml pytest anthropic streamlit
export ANTHROPIC_API_KEY=...   # extraction + narration only
```

## Use

```
python cli.py screen data/deals/*.md   # full pipeline, persist, print queue
python cli.py queue                    # print current queue
python cli.py show DEAL-003            # full rule trace for one deal
python cli.py history DEAL-003         # append-only status log
python cli.py rescreen --all           # re-run vs CURRENT config — no LLM, ~0.3s
streamlit run app.py                   # read-only dashboard
python eval_extraction.py              # per-field extraction accuracy vs answer key
pytest tests/                          # 47 tests, no network
```

Change a threshold in `config/lending_box.yaml` (bump `version:`), run
`rescreen --all`, and the queue reorders in seconds — extraction is cached
in the store, so rescreening is pure Python.

## Where things live

- `config/lending_box.yaml` — Credit's box, verbatim; `enforcement: hard|graduated`
  per criterion (the source doc doesn't classify loan size — that carries an
  `enforcement_note` so the output shows the gap)
- `config/ranking.yaml` — queue-order weights; business preference, not credit rule
- `src/schema.py` — every extracted value is a wrapper with provenance or a
  `missing_reason`; there is deliberately NO ltv/dscr field (broker
  characterizations are quarantined in `broker_claims`)
- `src/derive.py` — metrics with named inputs; missing input never produces a
  number; NOI is never inferred from GPR − OpEx
- `src/rules.py` — hard fail on a supported fact → decline, stop (no LTV gets
  computed on a hotel); missing input → needs_review; graduated miss →
  needs_review; correlated graduated misses flagged, not resolved
- `src/store.py` — one stable record per deal; every run appended with its box
  version; status log never overwritten. **This log is the point**: it makes
  the false-decline rate measurable for the first time.
- `data/store/` — the records; `data/runs/` — raw per-screen JSON
