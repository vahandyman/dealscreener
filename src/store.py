"""Deal records and status log. JSON on disk, one file per deal.

This log is the point of the whole tool: no record of screening decisions
exists today, which is why nobody can measure the false-decline rate.

- A deal gets ONE stable record. New documents about the same deal resolve
  to it (by the package's own deal id, else by normalized property address).
- Every screening run is APPENDED — nothing is overwritten. Each run pins
  the lending-box version it was evaluated under.
- Status changes append to status_log; the log never rewrites history.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from src.schema import ExtractedDeal, ScreenResult

DEFAULT_ROOT = Path(__file__).resolve().parent.parent / "data" / "store"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


class Store:
    def __init__(self, root: Path | str = DEFAULT_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # -- record resolution ---------------------------------------------------

    def _path(self, deal_id: str) -> Path:
        return self.root / f"{deal_id}.json"

    def _load(self, deal_id: str) -> dict | None:
        p = self._path(deal_id)
        return json.loads(p.read_text()) if p.exists() else None

    def _save(self, record: dict) -> None:
        self._path(record["deal_id"]).write_text(json.dumps(record, indent=2))

    def all_ids(self) -> list[str]:
        return sorted(p.stem for p in self.root.glob("*.json"))

    def resolve_deal_id(self, deal: ExtractedDeal) -> str:
        """Stable id: the package's own id, else match by property address,
        else mint a new one. Multiple documents -> ONE record."""
        stated = deal.identity.deal_id.value
        if isinstance(stated, str) and stated.strip():
            return stated.strip().upper()
        addr = deal.property.address.value
        if isinstance(addr, str) and addr.strip():
            key = _norm_key(addr)
            for existing_id in self.all_ids():
                rec = self._load(existing_id)
                if rec and rec.get("address_key") == key:
                    return existing_id
        n = 1 + sum(1 for i in self.all_ids() if i.startswith("SCRN-"))
        return f"SCRN-{n:03d}"

    # -- writes --------------------------------------------------------------

    def record_run(
        self,
        deal: ExtractedDeal,
        result: ScreenResult,
        narration: str | None = None,
    ) -> dict:
        deal_id = self.resolve_deal_id(deal)
        record = self._load(deal_id)
        now = _now()
        if record is None:
            addr = deal.property.address.value
            record = {
                "deal_id": deal_id,
                "received_at": now,  # first package receipt; stable thereafter
                "address_key": _norm_key(addr) if isinstance(addr, str) else None,
                "runs": [],
                "status_log": [],
            }
        run = {
            "run_at": now,
            "box_version": result.trace.box_version,
            "outcome": result.trace.outcome.value,
            "screen": result.model_dump(mode="json"),  # extracted + derived + trace + discrepancies
            "narration": narration,
            "rank": None,
            "rank_reason": None,
        }
        record["runs"].append(run)

        prev = record["status_log"][-1]["to"] if record["status_log"] else None
        if prev != result.trace.outcome.value:
            record["status_log"].append({
                "at": now,
                "from": prev,
                "to": result.trace.outcome.value,
                "box_version": result.trace.box_version,
            })
        self._save(record)
        return record

    def update_rank(self, deal_id: str, rank: int, score: float, rank_reason: str) -> None:
        record = self._load(deal_id)
        if not record or not record["runs"]:
            raise KeyError(f"no runs recorded for {deal_id}")
        record["runs"][-1]["rank"] = rank
        record["runs"][-1]["score"] = score
        record["runs"][-1]["rank_reason"] = rank_reason
        self._save(record)

    # -- reads ---------------------------------------------------------------

    def get(self, deal_id: str) -> dict:
        record = self._load(deal_id)
        if record is None:
            raise KeyError(f"unknown deal: {deal_id}")
        return record

    def latest_run(self, deal_id: str) -> dict:
        return self.get(deal_id)["runs"][-1]

    def latest_screen(self, deal_id: str) -> ScreenResult:
        return ScreenResult.model_validate(self.latest_run(deal_id)["screen"])

    def latest_extracted(self, deal_id: str) -> ExtractedDeal:
        return ExtractedDeal.model_validate(self.latest_run(deal_id)["screen"]["deal"])

    def history(self, deal_id: str) -> list[dict]:
        return self.get(deal_id)["status_log"]

    def all_latest(self) -> list[tuple[str, ScreenResult, str]]:
        """(deal_id, latest ScreenResult, received_at) for every deal — the
        rank_queue input."""
        out = []
        for deal_id in self.all_ids():
            rec = self.get(deal_id)
            if rec["runs"]:
                out.append((deal_id, self.latest_screen(deal_id), rec["received_at"]))
        return out
