from src.rules import evaluate, load_lending_box
from src.schema import Field
from src.store import Store
from tests.fixtures import DEAL_001, DEAL_004

import copy


def test_stable_id_multiple_documents_one_record(tmp_path):
    store = Store(tmp_path)
    store.record_run(DEAL_001, evaluate(DEAL_001))
    # a second document about the same deal arrives -> same record, new run
    store.record_run(DEAL_001, evaluate(DEAL_001))
    assert store.all_ids() == ["DEAL-001"]
    assert len(store.get("DEAL-001")["runs"]) == 2


def test_deal_without_stated_id_resolves_by_address(tmp_path):
    store = Store(tmp_path)
    anon = DEAL_001.model_copy(deep=True)
    anon.identity.deal_id = Field.missing("no id in package")
    store.record_run(anon, evaluate(anon))
    ids = store.all_ids()
    assert len(ids) == 1 and ids[0].startswith("SCRN-")
    # same address again -> same record
    store.record_run(anon, evaluate(anon))
    assert store.all_ids() == ids
    assert len(store.get(ids[0])["runs"]) == 2


def test_status_log_appends_never_overwrites(tmp_path):
    store = Store(tmp_path)
    box = load_lending_box()
    store.record_run(DEAL_004, evaluate(DEAL_004, box))  # needs_review (LTV 0.82)

    loosened = copy.deepcopy(box)
    loosened["criteria"]["ltv"]["max"] = 0.85
    store.record_run(DEAL_004, evaluate(DEAL_004, loosened))  # -> advance

    log = store.history("DEAL-004")
    assert [(s["from"], s["to"]) for s in log] == [
        (None, "needs_review"),
        ("needs_review", "advance"),
    ]
    # both runs retained, each pinned to its box version
    runs = store.get("DEAL-004")["runs"]
    assert len(runs) == 2
    assert runs[0]["outcome"] == "needs_review"
    assert runs[1]["outcome"] == "advance"


def test_unchanged_outcome_does_not_spam_status_log(tmp_path):
    store = Store(tmp_path)
    store.record_run(DEAL_001, evaluate(DEAL_001))
    store.record_run(DEAL_001, evaluate(DEAL_001))
    assert len(store.history("DEAL-001")) == 1


def test_run_persists_full_audit_artifact(tmp_path):
    store = Store(tmp_path)
    store.record_run(DEAL_001, evaluate(DEAL_001))
    run = store.latest_run("DEAL-001")
    assert run["box_version"]
    screen = run["screen"]
    assert screen["deal"]["valuation"]["appraised_value"]["value"] == 32_000_000
    assert screen["derived"]["ltv"]["value"] == 0.75
    assert any(r["criterion"] == "ltv" for r in screen["trace"]["results"])
    # round-trips back into typed models
    assert store.latest_screen("DEAL-001").trace.outcome.value == "advance"


def test_update_rank_annotates_latest_run(tmp_path):
    store = Store(tmp_path)
    store.record_run(DEAL_001, evaluate(DEAL_001))
    store.update_rank("DEAL-001", 1, 0.9, "all criteria pass")
    run = store.latest_run("DEAL-001")
    assert run["rank"] == 1 and run["rank_reason"] == "all criteria pass"
